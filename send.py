"""Send one plain-text Telegram message using only the Python standard library."""

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request


class NoRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def safe_error(code):
    descriptions = {
        400: "Telegram отклонил параметры сообщения.",
        401: "Telegram отклонил авторизацию бота.",
        403: "Telegram запретил отправку в этот чат.",
        404: "Telegram не нашёл бота или метод отправки.",
        429: "Telegram ограничил частоту отправки; повторите позднее.",
    }
    return descriptions.get(code, "Telegram вернул ошибку отправки.")


def main():
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    missing = [name for name, value in (
        ("TELEGRAM_BOT_TOKEN", token), ("TELEGRAM_CHAT_ID", chat_id)
    ) if not value]
    if missing:
        print("Отсутствуют переменные: " + ", ".join(missing), file=sys.stderr)
        return 1
    if len(sys.argv) != 2 or not sys.argv[1].strip():
        print('Использование: python3 send.py "текст"', file=sys.stderr)
        return 1
    text = sys.argv[1]
    if len(text.encode("utf-16-le")) // 2 > 4096:
        print("Сообщение превышает лимит Telegram; сократите сводку.", file=sys.stderr)
        return 1
    try:
        body = json.dumps({
            "chat_id": chat_id,
            "text": text,
            "link_preview_options": {"is_disabled": True},
        }, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            "https://api.telegram.org/bot" + urllib.parse.quote(token, safe=":") + "/sendMessage",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        opener = urllib.request.build_opener(NoRedirects())
        with opener.open(request, timeout=20) as response:
            result = json.loads(response.read(1024 * 1024))
        if not isinstance(result, dict) or result.get("ok") is not True:
            code = result.get("error_code") if isinstance(result, dict) else None
            print(safe_error(code if type(code) is int else None), file=sys.stderr)
            return 1
    except urllib.error.HTTPError as error:
        print(safe_error(error.code), file=sys.stderr)
        return 1
    except urllib.error.URLError as error:
        if "Tunnel connection failed: 403" in str(error.reason):
            print("Сетевой прокси запретил подключение к Telegram.", file=sys.stderr)
        else:
            print("Не удалось подключиться к Telegram; проверьте сеть и доверие TLS.", file=sys.stderr)
        return 1
    except Exception:
        # Exception messages and Telegram response bodies may contain credentials.
        print("Не удалось отправить сообщение или проверить ответ Telegram.", file=sys.stderr)
        return 1
    print("Отправлено.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
