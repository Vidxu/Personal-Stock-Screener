#!/usr/bin/env python3
"""Print your TELEGRAM_CHAT_ID after you message your bot."""

from __future__ import annotations

import os
import sys

import requests


def main() -> None:
    from dotenv import load_dotenv

    load_dotenv()
    token = os.environ.get("TELEGRAM_BOT_TOKEN", "").strip()
    if not token:
        print("Set TELEGRAM_BOT_TOKEN in .env first (from @BotFather).")
        sys.exit(1)

    print("1. Open Telegram and send any message to your bot")
    print("2. Press Enter here…")
    try:
        input()
    except KeyboardInterrupt:
        print()
        sys.exit(0)

    resp = requests.get(
        f"https://api.telegram.org/bot{token}/getUpdates",
        timeout=30,
    )
    if not resp.ok:
        print(f"Error {resp.status_code}: {resp.text[:300]}")
        sys.exit(1)

    updates = resp.json().get("result", [])
    if not updates:
        print("No messages found. Send a message to your bot, then run this again.")
        sys.exit(1)

    chat_id = updates[-1]["message"]["chat"]["id"]
    name = updates[-1]["message"]["chat"].get("first_name", "")
    print(f"\nTELEGRAM_CHAT_ID={chat_id}  ({name})")
    print("\nAdd that line to your .env file.")


if __name__ == "__main__":
    main()
