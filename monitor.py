import json
import os
import re
import sys
from pathlib import Path

import requests
from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright

STATE_FILE = Path("state.json")
BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = os.environ["CHAT_ID"]

RAIF_URL = "https://raiffeisen.ua/currency"
CHANNEL = "X_Change_Vinnitsa"
CHANNEL_URL = f"https://t.me/{CHANNEL}"
CHANNEL_PREVIEW_URL = f"https://t.me/s/{CHANNEL}"

def telegram_send(text):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    response = requests.post(
        url,
        json={
            "chat_id": CHAT_ID,
            "text": text[:4000],
            "disable_web_page_preview": True,
        },
        timeout=25,
    )
    response.raise_for_status()
    data = response.json()
    if not data.get("ok"):
        raise RuntimeError(f"Telegram API error: {data}")
    return data

def load_state():
    if not STATE_FILE.exists():
        return {"initialized": False, "last_channel_id": 0, "raif": {}}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {"initialized": False, "last_channel_id": 0, "raif": {}}

def save_state(state):
    STATE_FILE.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8"
    )

def fetch_raif_text():
    # The bank renders some page elements dynamically, so use a real browser.
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True, args=["--no-sandbox"])
        page = browser.new_page(locale="uk-UA")
        page.goto(RAIF_URL, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(5000)
        body_text = page.locator("body").inner_text(timeout=20000)
        browser.close()
    return body_text

def parse_pair(text, pair):
    # Example visible layout: USD/UAH 45.00 / 45.53
    pattern = (
        re.escape(pair)
        + r"\s*([0-9]+(?:[.,][0-9]+)?)\s*/\s*"
        + r"([0-9]+(?:[.,][0-9]+)?)"
    )
    match = re.search(pattern, text, flags=re.IGNORECASE)
    if not match:
        return None
    return {
        "buy": float(match.group(1).replace(",", ".")),
        "sell": float(match.group(2).replace(",", ".")),
    }

def fetch_raif_rates():
    text = fetch_raif_text()

    # Prefer the cash-desk block ("У касі банку"), excluding MyRaif rates.
    cash_block = text
    start = re.search(r"У\s+касі\s+банку", text, flags=re.IGNORECASE)
    if start:
        end = re.search(r"У\s+MyRaif", text[start.end():], flags=re.IGNORECASE)
        if end:
            cash_block = text[start.end(): start.end() + end.start()]

    rates = {}
    for currency in ("USD", "EUR"):
        value = parse_pair(cash_block, f"{currency}/UAH")
        if value is None:
            # Fallback to the whole page if the bank changes the order of labels.
            value = parse_pair(text, f"{currency}/UAH")
        if value is None:
            raise RuntimeError(
                f"Не удалось найти {currency}/UAH на странице Raiffeisen. "
                "Возможно, банк изменил структуру страницы."
            )
        rates[currency] = value
    return rates

def fetch_channel_posts():
    response = requests.get(
        CHANNEL_PREVIEW_URL,
        headers={"User-Agent": "Mozilla/5.0 (compatible; CurrencyMonitor/1.0)"},
        timeout=30,
    )
    response.raise_for_status()
    soup = BeautifulSoup(response.text, "html.parser")
    posts = []

    for wrap in soup.select(".tgme_widget_message_wrap"):
        message = wrap.select_one(".tgme_widget_message")
        if not message:
            continue
        post_ref = message.get("data-post", "")
        if "/" not in post_ref:
            continue
        try:
            post_id = int(post_ref.rsplit("/", 1)[1])
        except ValueError:
            continue

        text_node = wrap.select_one(".tgme_widget_message_text")
        post_text = text_node.get_text("\n", strip=True) if text_node else "[Медиа без текста]"
        posts.append({
            "id": post_id,
            "text": post_text,
            "url": f"https://t.me/{CHANNEL}/{post_id}",
        })

    return sorted(posts, key=lambda item: item["id"])

def format_rate_change(currency, old, new):
    lines = []
    for side, label in (("buy", "покупка"), ("sell", "продажа")):
        before, after = old[side], new[side]
        if before != after:
            delta = after - before
            sign = "+" if delta > 0 else ""
            lines.append(
                f"{currency} {label}: {before:.2f} → {after:.2f} грн "
                f"({sign}{delta:.2f})"
            )
    return lines

def main():
    state = load_state()

    print("Получаю курсы Raiffeisen...")
    current_rates = fetch_raif_rates()
    print("Курсы:", current_rates)

    print("Проверяю публичную ленту Telegram...")
    posts = fetch_channel_posts()
    latest_id = max((p["id"] for p in posts), default=0)

    if not state.get("initialized"):
        state["initialized"] = True
        state["raif"] = current_rates
        state["last_channel_id"] = latest_id
        save_state(state)
        telegram_send(
            "✅ Мониторинг запущен.\n\n"
            "Raiffeisen: отслеживаю покупку и продажу USD/EUR "
            "в кассе банка.\n"
            f"Telegram: отслеживаю новые публикации @{CHANNEL}.\n\n"
            "Проверка выполняется примерно раз в 5 минут. "
            "Первый запуск сохраняет исходные значения без рассылки старых постов."
        )
        print("Первичная настройка завершена.")
        return

    # Detect Raiffeisen rate changes.
    old_rates = state.get("raif", {})
    changes = []
    for currency in ("USD", "EUR"):
        old = old_rates.get(currency)
        new = current_rates.get(currency)
        if old and new:
            changes.extend(format_rate_change(currency, old, new))

    if changes:
        telegram_send(
            "💱 ИЗМЕНЕНИЕ КУРСА RAIFFEISEN\n\n"
            + "\n".join(changes)
            + "\n\nИсточник: касса банка\n"
            + RAIF_URL
        )

    # Detect new public-channel posts since the previous successful check.
    last_id = int(state.get("last_channel_id", 0))
    new_posts = [p for p in posts if p["id"] > last_id]
    for post in new_posts:
        telegram_send(
            f"📢 НОВАЯ ПУБЛИКАЦИЯ @{CHANNEL}\n\n"
            f"{post['text'][:3000]}\n\n"
            f"Открыть публикацию: {post['url']}"
        )

    state["raif"] = current_rates
    state["last_channel_id"] = max(last_id, latest_id)
    save_state(state)
    print(f"Готово. Изменений курсов: {len(changes)}; новых постов: {len(new_posts)}")

if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"MONITOR ERROR: {exc}", file=sys.stderr)
        # Do not overwrite the last known good state after a failed check.
        raise
