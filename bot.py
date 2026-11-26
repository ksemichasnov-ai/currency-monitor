import os
import time
import requests

import monitor

BOT_TOKEN = os.environ["BOT_TOKEN"]

API_URL = f"https://api.telegram.org/bot{BOT_TOKEN}"

offset = 0


def send_message(chat_id, message):
    response = requests.post(
        f"{API_URL}/sendMessage",
        json={
            "chat_id": chat_id,
            "text": message[:4000],
            "parse_mode": "HTML",
            "disable_web_page_preview": True
        },
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(data)


def get_current_rates():
    # Получаем курсы двух источников
    raif = monitor.fetch_raif_rates()

    xchange_data = monitor.fetch_xchange_rates()
    xchange = xchange_data["rates"]

    # Рассчитываем разницу
    spreads = monitor.calculate_spreads(
        raif,
        xchange
    )

    # Формируем сообщение
    message = monitor.format_comparison(
        raif,
        xchange,
        xchange_data,
        spreads
    )

    # Добавляем источники
    message += (
        "\n\n<b>ИСТОЧНИКИ:</b>\n"
        "Raiffeisen: Finance.ua\n"
        f"X-Change: {xchange_data['url']}"
    )

    if xchange_data.get("date"):
        message += (
            "\nВремя публикации X-Change: "
            + xchange_data["date"]
        )

    return message


def handle_message(message):
    chat_id = message["chat"]["id"]
    text = message.get("text", "").strip()

    if text == "/start" or text.startswith("/start "):
        send_message(
            chat_id,
            "🏦 <b>БАНК МЕДИЧИ</b>\n"
            "Получаю актуальные курсы..."
        )

    elif text == "+":
        send_message(
            chat_id,
            "⏳ Получаю актуальные курсы..."
        )

    else:
        return

    try:
        result = get_current_rates()
        send_message(chat_id, result)

    except Exception as exc:
        print("Ошибка получения курсов:", exc)

        send_message(
            chat_id,
            "⚠️ Не удалось получить курсы.\n"
            "Попробуй немного позже."
        )


def main():
    global offset

    print("БАНК МЕДИЧИ: Telegram-бот запущен")

    while True:
        try:
            response = requests.get(
                f"{API_URL}/getUpdates",
                params={
                    "offset": offset,
                    "timeout": 50,
                    "allowed_updates": ["message"]
                },
                timeout=60
            )

            response.raise_for_status()

            data = response.json()

            if not data.get("ok"):
                raise RuntimeError(data)

            for update in data["result"]:
                offset = update["update_id"] + 1

                message = update.get("message")

                if message:
                    handle_message(message)

        except Exception as exc:
            print("Ошибка Telegram:", exc)
            time.sleep(5)


if __name__ == "__main__":
    main()

