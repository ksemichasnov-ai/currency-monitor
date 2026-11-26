import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup

# =====================================
# НАСТРОЙКИ
# =====================================

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

SESSION = requests.Session()
SESSION.headers.update(HEADERS)


# =====================================
# TELEGRAM
# =====================================

def telegram_send(message):

    url = (
        f"https://api.telegram.org/"
        f"bot{BOT_TOKEN}/sendMessage"
    )

    response = SESSION.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": message[:4000],
            "disable_web_page_preview": True,
        },
        timeout=30
    )

    response.raise_for_status()

    data = response.json()

    if not data.get("ok"):
        raise RuntimeError(
            f"Telegram API error: {data}"
        )

    return data


# =====================================
# СОСТОЯНИЕ
# =====================================

def load_state():

    if not STATE_FILE.exists():
        return {}

    try:
        return json.loads(
            STATE_FILE.read_text(
                encoding="utf-8"
            )
        )

    except Exception as exc:

        print(f"Ошибка чтения state.json: {exc}")

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


# =====================================
# ЧИСЛА
# =====================================

def to_float(value):

    return float(
        value.replace(",", ".")
    )


def extract_price(text):

    match = re.search(
        r"\d{1,3}[,.]\d{2}",
        text
    )

    if not match:
        return None

    return to_float(match.group())


def format_rate(value):

    return f"{value:.2f}".replace(".", ",")


def format_difference(value):

    return f"{value:+.2f}".replace(".", ",")


# =====================================
# RAIFFEISEN
# =====================================

def parse_raif_table(soup):

    for table in soup.find_all("table"):

        rows = table.find_all("tr")

        if not rows:
            continue

        table_text = table.get_text(
            " ",
            strip=True
        ).lower()

        if not any(
            word in table_text
            for word in ("купівля", "покупка")
        ):
            continue

        if not any(
            word in table_text
            for word in ("продаж", "продажа")
        ):
            continue

        rates = {}

        for row in rows:

            cells = row.find_all(
                ["td", "th"]
            )

            if len(cells) < 3:
                continue

            currency = cells[0].get_text(
                " ",
                strip=True
            ).upper()

            if currency not in ("USD", "EUR"):
                continue

            buy = extract_price(
                cells[1].get_text(
                    " ",
                    strip=True
                )
            )

            sell = extract_price(
                cells[2].get_text(
                    " ",
                    strip=True
                )
            )

            if buy is None or sell is None:
                continue

            rates[currency] = {
                "buy": buy,
                "sell": sell
            }

        if len(rates) == 2:
            return rates

    return None


def parse_raif_text(text):

    heading = re.search(
        r"Курс\s+валют\s+в\s+Райффайзен\s+Банк(?:у|е)",
        text,
        flags=re.IGNORECASE
    )

    if not heading:

        raise RuntimeError(
            "Не найден заголовок курса Raiffeisen."
        )

    section = text[heading.end():]

    card = re.search(
        r"Карткові\s+курси|Карточные\s+курсы",
        section,
        flags=re.IGNORECASE
    )

    if card:
        section = section[:card.start()]

    rates = {}

    for currency in ("USD", "EUR"):

        pattern = (
            rf"\b{currency}\b\s*"
            r"(\d{1,3}[,.]\d{2})"
            r"(?:\s*[+-]?\s*\d+[,.]\d{4})?"
            r"\s+"
            r"(\d{1,3}[,.]\d{2})"
        )

        match = re.search(
            pattern,
            section,
            flags=re.IGNORECASE
        )

        if not match:
            continue

        rates[currency] = {
            "buy": to_float(match.group(1)),
            "sell": to_float(match.group(2))
        }

    if len(rates) != 2:

        raise RuntimeError(
            "Не удалось извлечь USD и EUR "
            "из таблицы Finance.ua. "
            f"Фрагмент: {section[:1500]}"
        )

    return rates


def fetch_raif_rates():

    print("Получаю наличные курсы Raiffeisen...")

    response = SESSION.get(
        FINANCE_URL,
        timeout=40
    )

    response.raise_for_status()

    print(
        "Finance.ua HTTP:",
        response.status_code
    )

    soup = BeautifulSoup(
        response.text,
        "html.parser"
    )

    print(
        "Заголовок страницы:",
        soup.title.get_text(strip=True)
        if soup.title else "не найден"
    )

    rates = parse_raif_table(soup)

    if rates:

        print("Raiffeisen:", rates)

        return rates

    print(
        "HTML-таблица не найдена. "
        "Пробую извлечь курсы из текста."
    )

    text = re.sub(
        r"\s+",
        " ",
        soup.get_text(" ", strip=True)
    )

    rates = parse_raif_text(text)

    print("Raiffeisen:", rates)

    return rates


# =====================================
# X-CHANGE
# =====================================

def fetch_channel_posts():

    print(
        f"Получаю публикации @{CHANNEL}..."
    )

    response = SESSION.get(
        CHANNEL_PREVIEW_URL,
        timeout=40
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

        post_ref = message.get(
            "data-post",
            ""
        )

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

    if not posts:

        raise RuntimeError(
            "Не удалось получить публикации X-Change."
        )

    posts.sort(
        key=lambda item: item["id"],
        reverse=True
    )

    print(
        "Получено публикаций:",
        len(posts)
    )

    return posts


# =====================================
# ИЗВЛЕЧЕНИЕ КУРСОВ X-CHANGE
# =====================================

def parse_xchange_rates(text):

    rates = {}

    for currency in ("USD", "EUR"):

        pattern = (
            rf"\b{currency}\b\s*:?\s*"
            r"(\d{1,3}[,.]\d{1,3})"
            r"\s*[-–—/|]\s*"
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

            print(
                "Найдена публикация X-Change:",
                post["url"]
            )

            print(
                "Курсы X-Change:",
                rates
            )

            return {
                "rates": rates,
                "post_id": post["id"],
                "date": post["date"],
                "url": post["url"]
            }

    raise RuntimeError(
        "Не найдена публикация X-Change "
        "с полными курсами USD и EUR."
    )


# =====================================
# РАСЧЁТ РАЗНИЦЫ
# =====================================

def calculate_spreads(raif, xchange):

    spreads = {}

    for currency in ("USD", "EUR"):

        difference = round(
            xchange[currency]["buy"]
            - raif[currency]["sell"],
            2
        )

        spreads[currency] = difference

    return spreads


# =====================================
# ФОРМИРОВАНИЕ СООБЩЕНИЯ
# =====================================

def format_comparison(
    raif,
    xchange,
    post,
    spreads
):

    lines = [
        "🏦 БАНК МЕДИЧИ",
        "СРАВНЕНИЕ КУРСОВ",
        ""
    ]

    for currency in ("USD", "EUR"):

        raif_sell = raif[currency]["sell"]
        xchange_buy = xchange[currency]["buy"]

        difference = spreads[currency]

        lines.extend([
            f"💱 {currency} / UAH",
            f"Райффайзен (продажа): "
            f"{format_rate(raif_sell)}",
            f"X-Change (покупка): "
            f"{format_rate(xchange_buy)}",
            f"Разница: "
            f"{format_difference(difference)} грн",
            ""
        ])

    lines.extend([
        "ИСТОЧНИКИ:",
        "Raiffeisen: Finance.ua",
        f"X-Change: {post['url']}",
        "",
        "Время публикации X-Change: "
        f"{post['date'] or 'не указано'}"
    ])

    return "\n".join(lines)


# =====================================
# ОСНОВНОЙ МОНИТОРИНГ
# =====================================

def main():

    state = load_state()

    # Получаем курсы.

    raif = fetch_raif_rates()

    xchange_data = fetch_xchange_rates()

    xchange = xchange_data["rates"]

    # Рассчитываем разницу.

    spreads = calculate_spreads(
        raif,
        xchange
    )

    print("RAIFFEISEN:", raif)
    print("X-CHANGE:", xchange)
    print("РАЗНИЦА:", spreads)

    # Сравниваем с предыдущими значениями.

    previous_spreads = state.get("spreads")

    first_run = not state.get(
        "comparison_initialized",
        False
    )

    changed = spreads != previous_spreads

    # Отправляем сообщение.

    if first_run or changed:

        message = format_comparison(
            raif,
            xchange,
            xchange_data,
            spreads
        )

        if first_run:

            message = (
                "✅ МОНИТОРИНГ ЗАПУЩЕН\n\n"
                + message
            )

        else:

            message = (
                "🔔 ИЗМЕНИЛАСЬ РАЗНИЦА КУРСОВ\n\n"
                + message
            )

        telegram_send(message)

        print("Уведомление отправлено.")

    else:

        print("Разница курсов не изменилась.")

    # Сохраняем состояние.

    state["comparison_initialized"] = True

    state["raif"] = raif
    state["xchange"] = xchange
    state["spreads"] = spreads

    state["xchange_post_id"] = (
        xchange_data["post_id"]
    )

    state["xchange_post_date"] = (
        xchange_data["date"]
    )

    state["initialized"] = True

    state["last_channel_id"] = max(
        int(state.get("last_channel_id", 0)),
        int(xchange_data["post_id"])
    )

    save_state(state)

    print("Мониторинг завершён успешно.")


# =====================================
# ЗАПУСК
# =====================================

if __name__ == "__main__":

    try:

        main()

    except Exception as exc:

        print(
            f"MONITOR ERROR: {exc}",
            file=sys.stderr
        )

        raise
    
