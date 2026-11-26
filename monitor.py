import json
import os
import re
import sys
from pathlib import Path
from datetime import datetime

import requests
from bs4 import BeautifulSoup

STATE_FILE = Path("state.json")

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

FINANCE_URL = (
    "https://finance.ua/ua/banks/"
    "raiffeisen-bank-aval/currency"
)

CHANNEL = "X_Change_Vinnitsa"
CHANNEL_URL = f"https://t.me/{CHANNEL}"
CHANNEL_PREVIEW_URL = f"https://t.me/s/{CHANNEL}"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 Chrome/131.0.0.0 Safari/537.36"
    )
}


def telegram_send(message):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"

    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message[:4000],
            "disable_web_page_preview": True,
        },
        timeout=25,
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(f"Telegram error: {data}")

    return data


def load_state():
    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(encoding="utf-8")
        )
    except Exception:
        return {}


def save_state(state):
    STATE_FILE.write_text(
        json.dumps(
            state,
            ensure_ascii=False,
            indent=2
        ),
        encoding="utf-8"
    )


def to_float(value):
    return float(value.replace(",", "."))


# ---------------------------------
# RAIFFEISEN — FINANCE.UA
# ---------------------------------

def fetch_raif_rates():

    response = requests.get(
        FINANCE_URL,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    text = re.sub(
        r"\s+",
        " ",
        soup.get_text(" ", strip=True)
    )

    start = text.find(
        "Курс валют в Райффайзен Банку"
    )

    if start == -1:
        start = text.find(
            "Курс валют в Райффайзен Банке"
        )

    if start == -1:
        raise RuntimeError(
            "Не найден блок курсов Raiffeisen на Finance.ua"
        )

    end = text.find(
        "Карткові курси",
        start
    )

    if end == -1:
        end = text.find(
            "Карточные курсы",
            start
        )

    if end == -1:
        raise RuntimeError(
            "Не удалось определить границу наличных курсов"
        )

    cash_text = text[start:end]

    rates = {}

    for currency in ("USD", "EUR"):

        pattern = (
            rf"\b{currency}\b\s+"
            r"(\d{1,3}[,.]\d{2})"
            r"(?:\s*[+-]?\s*\d+[,.]\d{4})?"
            r"\s+"
            r"(\d{1,3}[,.]\d{2})"
        )

        match = re.search(
            pattern,
            cash_text,
            flags=re.IGNORECASE
        )

        if not match:
            raise RuntimeError(
                f"Не найден наличный курс {currency} Raiffeisen"
            )

        rates[currency] = {
            "buy": to_float(match.group(1)),
            "sell": to_float(match.group(2))
        }

    return rates


# ---------------------------------
# X-CHANGE — TELEGRAM
# ---------------------------------

def fetch_channel_posts():

    response = requests.get(
        CHANNEL_PREVIEW_URL,
        headers=HEADERS,
        timeout=30
    )

    response.raise_for_status()

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    posts = []

    for wrap in soup.select(
        ".tgme_widget_message_wrap"
    ):

        message = wrap.select_one(
            ".tgme_widget_message"
        )

        if not message:
            continue

        post_ref = message.get("data-post", "")

        if "/" not in post_ref:
            continue

        try:
            post_id = int(
                post_ref.rsplit("/", 1)[1]
            )
        except ValueError:
            continue

        text_node = wrap.select_one(
            ".tgme_widget_message_text"
        )

        if not text_node:
            continue

        post_text = text_node.get_text(
            "\n",
            strip=True
        )

        date_node = wrap.select_one(
            ".tgme_widget_message_date"
        )

        post_date = (
            date_node.get("datetime", "")
            if date_node
            else ""
        )

        posts.append({
            "id": post_id,
            "text": post_text,
            "date": post_date,
            "url": f"{CHANNEL_URL}/{post_id}"
        })

    return sorted(
        posts,
        key=lambda item: item["id"],
        reverse=True
    )


def parse_xchange_rates(text):

    rates = {}

    for currency in ("USD", "EUR"):

        pattern = (
            rf"\b{currency}\b\s*"
            r"(\d{1,3}[,.]\d{1,3})"
            r"\s*[-–—]\s*"
            r"(\d{1,3}[,.]\d{1,3})"
        )

        match = re.search(
            pattern,
            text,
            flags=re.IGNORECASE
        )

        if not match:
            continue

        rates[currency] = {
            "buy": to_float(match.group(1)),
            "sell": to_float(match.group(2))
        }

    if len(rates) != 2:
        return None

    return rates


def fetch_xchange_rates():

    posts = fetch_channel_posts()

    for post in posts:

        rates = parse_xchange_rates(
            post["text"]
        )

        if rates:

            return {
                "rates": rates,
                "post_id": post["id"],
                "date": post["date"],
                "url": post["url"]
            }

    raise RuntimeError(
        "Не найдена публикация X-Change "
        "с полными курсами USD и EUR"
    )


# ---------------------------------
# СРАВНЕНИЕ
# ---------------------------------

def format_comparison(raif, xchange, post):

    lines = [
        "🏦 БАНК МЕДИЧИ",
        "СРАВНЕНИЕ КУРСОВ",
        "",
        "НАЛИЧНЫЕ КУРСЫ",
        ""
    ]

    for currency in ("USD", "EUR"):

        r = raif[currency]
        x = xchange[currency]

        buy_diff = round(
            x["buy"] - r["buy"], 2
        )

        sell_diff = round(
            x["sell"] - r["sell"], 2
        )

        lines.append(f"💱 {currency}")

        lines.append(
            f"Raiffeisen: "
            f"{r['buy']:.2f} / {r['sell']:.2f}"
        )

        lines.append(
            f"X-Change: "
            f"{x['buy']:.2f} / {x['sell']:.2f}"
        )

        lines.append(
            f"Разница покупки: {buy_diff:+.2f} грн"
        )

        lines.append(
            f"Разница продажи: {sell_diff:+.2f} грн"
        )

        # Где выгоднее продать валюту:
        if buy_diff > 0:
            sell_advice = "X-Change платит больше"
        elif buy_diff < 0:
            sell_advice = "Raiffeisen платит больше"
        else:
            sell_advice = "Одинаковый курс"

        # Где выгоднее купить валюту:
        if sell_diff < 0:
            buy_advice = "X-Change продаёт дешевле"
        elif sell_diff > 0:
            buy_advice = "Raiffeisen продаёт дешевле"
        else:
            buy_advice = "Одинаковый курс"

        lines.append(
            f"Продать валюту: {sell_advice}"
        )

        lines.append(
            f"Купить валюту: {buy_advice}"
        )

        lines.append("")

    lines.extend([
        "Источники:",
        "Raiffeisen: Finance.ua",
        f"X-Change: {post['url']}",
        "",
        "⚠️ Курс Finance.ua может отличаться "
        "от курса конкретного отделения."
    ])

    return "\n".join(lines)


# ---------------------------------
# ОСНОВНОЙ МОНИТОРИНГ
# ---------------------------------

def main():

    state = load_state()

    print("Получаю наличные курсы Raiffeisen...")

    raif = fetch_raif_rates()

    print("Raiffeisen:", raif)

    print("Получаю курсы X-Change...")

    xchange_data = fetch_xchange_rates()

    xchange = xchange_data["rates"]

    print("X-Change:", xchange)

    print(
        "Публикация:",
        xchange_data["url"]
    )

    previous_raif = state.get("raif")
    previous_xchange = state.get("xchange")

    first_comparison = not state.get(
        "comparison_initialized",
        False
    )

    changed = (
        raif != previous_raif
        or xchange != previous_xchange
    )

    if first_comparison or changed:

        message = format_comparison(
            raif,
            xchange,
            xchange_data
        )

        if first_comparison:
            message = (
                "✅ ПЕРВЫЙ ЗАПУСК МОНИТОРИНГА\n\n"
                + message
            )
        else:
            message = (
                "🔔 ОБНАРУЖЕНО ИЗМЕНЕНИЕ КУРСА\n\n"
                + message
            )

        telegram_send(message)

        print("Сравнение отправлено в Telegram.")

    else:

        print("Изменений курсов нет.")

    state["comparison_initialized"] = True
    state["raif"] = raif
    state["xchange"] = xchange
    state["xchange_post_id"] = xchange_data["post_id"]
    state["xchange_post_date"] = xchange_data["date"]

    # Сохраняем также старые поля для совместимости.
    state["initialized"] = True
    state["last_channel_id"] = max(
        int(state.get("last_channel_id", 0)),
        int(xchange_data["post_id"])
    )

    save_state(state)

    print("Мониторинг завершён.")


if __name__ == "__main__":

    try:
        main()

    except Exception as exc:

        print(
            f"MONITOR ERROR: {exc}",
            file=sys.stderr
        )

        raise
        
