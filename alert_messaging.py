"""
Send screener alerts via Telegram (free).

Setup:
  1. Message @BotFather on Telegram → /newbot → copy the bot token
  2. Start a chat with your bot (tap Start / send any message)
  3. python telegram_setup.py          # prints your TELEGRAM_CHAT_ID
  4. Add to .env:
       TELEGRAM_BOT_TOKEN=123456:ABC...
       TELEGRAM_CHAT_ID=your_chat_id

Test:
  python alert_messaging.py
"""

from __future__ import annotations

import os
import sys

import requests

from alert_templates import RenderedAlert

TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"


def telegram_configured() -> bool:
    return bool(
        os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
        and os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    )


def format_alert_text(rendered: RenderedAlert) -> str:
    return f"{rendered.title}\n{rendered.message}"


def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "").strip()
    if not token or not chat_id:
        print("⚠️  Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID")
        return False

    try:
        resp = requests.post(
            TELEGRAM_API.format(token=token),
            json={"chat_id": chat_id, "text": text[:4096]},
            timeout=30,
        )
    except requests.RequestException as exc:
        print(f"⚠️  Telegram request failed: {exc}")
        return False

    if resp.ok:
        print(f"📱 Telegram alert sent to chat {chat_id}")
        return True

    print(f"⚠️  Telegram error {resp.status_code}: {resp.text[:300]}")
    return False


def dispatch_alert(rendered: RenderedAlert) -> None:
    if not telegram_configured():
        print(f"ℹ️  Alert (not sent): {format_alert_text(rendered)}")
        return
    send_telegram(format_alert_text(rendered))


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv()
    msg = sys.argv[1] if len(sys.argv) > 1 else (
        "OR Breakout · TEST\nTEST crossed OR high ₹100 and prev-day high ₹95. LTP ₹102 (+2%)"
    )
    print(f"Configured: {telegram_configured()}")
    sys.exit(0 if send_telegram(msg) else 1)
