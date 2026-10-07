"""Prepare a history snapshot and compare tracker arrays without network access."""

import argparse
from datetime import date
from decimal import Decimal
import json
import math
from pathlib import Path
import re


def validate_run(items):
    if not isinstance(items, list):
        raise ValueError("Прогон должен быть JSON-массивом")
    seen = set()
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get("url"), str) or not item["url"]:
            raise ValueError("Каждая запись должна содержать непустой URL")
        url = item["url"]
        if url in seen:
            raise ValueError(f"Повторный URL: {url}")
        seen.add(url)
        if item.get("status") == "extraction_failed":
            if not isinstance(item.get("reason"), str) or not item["reason"]:
                raise ValueError(f"Ошибка без причины: {url}")
            continue
        if item.get("status") not in (None, "success"):
            raise ValueError(f"Неизвестный статус: {url}")
        if type(item.get("has_credit")) is not bool:
            raise ValueError(f"Неизвестная рассрочка в успешной записи: {url}")
        for field in ("regular_price", "sale_price"):
            if field not in item:
                raise ValueError(f"Отсутствует {field}: {url}")
            value = item[field]
            if value is not None and (
                type(value) not in (int, float) or not math.isfinite(value) or value < 0
            ):
                raise ValueError(f"Некорректная цена {field}: {url}")
    return items


def read_run(path, allow_first_run=False):
    try:
        items = json.loads(Path(path).read_text(encoding="utf-8"))
        if items is None and allow_first_run:
            return None
        return validate_run(items)
    except (OSError, ValueError) as error:
        raise ValueError(f"Не удалось прочитать прогон {path}: {error}") from error


def select_previous(history_dir, run_date):
    target_date = date.fromisoformat(run_date)
    candidates = []
    for path in Path(history_dir).iterdir():
        if not path.is_file() or not re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", path.name):
            continue
        try:
            file_date = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if file_date <= target_date:
            candidates.append((file_date, path))
    return max(candidates, key=lambda item: item[0])[1] if candidates else None


def compare_runs(previous, current):
    validate_run(current)
    if previous is not None:
        validate_run(previous)
    changes, diagnostics = [], []
    old_by_url = {item["url"]: item for item in previous or []}
    new_by_url = {item["url"]: item for item in current}

    def add(url, change_type, field, old, new, reason, significant=True, percent=None):
        changes.append({
            "url": url, "change_type": change_type, "field": field,
            "old_value": old, "new_value": new, "percent_change": percent,
            "significant": significant, "reason": reason,
        })

    for url, new in new_by_url.items():
        if new.get("status") == "extraction_failed":
            diagnostics.append({"url": url, "status": "extraction_failed", "reason": new["reason"]})
        if previous is None:
            continue
        if url not in old_by_url:
            add(url, "product_added", "url", None, url, "URL добавлен в список отслеживания")
            continue
        old = old_by_url[url]
        if new.get("status") == "extraction_failed":
            continue
        if old.get("status") == "extraction_failed":
            diagnostics.append({"url": url, "status": "data_recovered", "reason": "Извлечение восстановлено; достоверной прошлой цены нет"})
            continue
        for field in ("regular_price", "sale_price"):
            before, after = old[field], new[field]
            if before == after:
                continue
            if before is None or after is None:
                if field == "sale_price":
                    appeared = before is None
                    add(url, "sale_appeared" if appeared else "sale_disappeared",
                        field, before, after, "Скидка появилась" if appeared else "Скидка исчезла")
                else:
                    diagnostics.append({"url": url, "status": "price_data_changed", "field": field,
                                        "old_value": before, "new_value": after,
                                        "reason": "Обычная цена неизвестна в одном из прогонов"})
                continue
            old_decimal, new_decimal = Decimal(str(before)), Decimal(str(after))
            percent = None if old_decimal == 0 else (new_decimal - old_decimal) / old_decimal * 100
            significant = percent is None or abs(percent) >= Decimal("1")
            label = "Обычная" if field == "regular_price" else "Скидочная"
            add(url, field + "_changed", field, before, after,
                f"{label} цена изменилась; " + ("предыдущая цена равна нулю" if percent is None else
                "изменение не менее 1%" if significant else "изменение менее 1%"),
                significant, None if percent is None else float(percent))
        if old["has_credit"] != new["has_credit"]:
            appeared = new["has_credit"]
            add(url, "credit_appeared" if appeared else "credit_disappeared", "has_credit",
                old["has_credit"], new["has_credit"], "Рассрочка появилась" if appeared else "Рассрочка исчезла")
    if previous is not None:
        for url in old_by_url:
            if url not in new_by_url:
                add(url, "product_removed", "url", url, None, "URL удалён из списка отслеживания")
    return {"first_run": previous is None, "changes": changes,
            "significant_changes": [change for change in changes if change["significant"]],
            "diagnostics": diagnostics}


def write_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Snapshot previous run before extraction")
    prepare.add_argument("--history-dir", required=True)
    prepare.add_argument("--run-date", required=True)
    prepare.add_argument("--output", required=True)
    diff = commands.add_parser("diff", help="Compare snapshot and fresh results")
    diff.add_argument("--previous", required=True)
    diff.add_argument("--current", required=True)
    diff.add_argument("--output", required=True)
    args = parser.parse_args()
    try:
        if args.command == "prepare":
            history = Path(args.history_dir).resolve()
            if Path(args.output).resolve().is_relative_to(history):
                raise ValueError("Снимок предыдущего прогона сохраняй вне tracker-data")
            selected = select_previous(history, args.run_date)
            previous = read_run(selected) if selected else None
            write_json(args.output, previous)
            print(json.dumps({"previous_file": str(selected) if selected else None,
                              "first_run": selected is None}, ensure_ascii=False))
        else:
            output = Path(args.output).resolve()
            if output in (Path(args.previous).resolve(), Path(args.current).resolve()):
                raise ValueError("Diff не должен перезаписывать входные файлы")
            write_json(output, compare_runs(read_run(args.previous, True), read_run(args.current)))
    except (OSError, ValueError) as error:
        parser.exit(1, f"{error}\n")


if __name__ == "__main__":
    main()
