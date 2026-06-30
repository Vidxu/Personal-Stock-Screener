#!/usr/bin/env python3
"""Start the Flask dashboard locally: python run_local.py"""

import os
import socket

from app import PORT, app


def _bindable_port(preferred: int, host: str = "127.0.0.1") -> int:
    for port in range(preferred, preferred + 10):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((host, port))
            except OSError:
                continue
            return port
    return preferred


def main() -> None:
    from dotenv import load_dotenv

    from app import LIVE_MODULES, _live_alerts_enabled

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    from market_data import upstox_token_status

    ok, token_msg = upstox_token_status()
    if not ok:
        print(f"⚠️  {token_msg}")
    elif "not set" not in token_msg:
        print(f"✅ {token_msg}")
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    port = int(os.environ.get("PORT", PORT))
    if not os.environ.get("PORT"):
        port = _bindable_port(port, "127.0.0.1" if host == "127.0.0.1" else "0.0.0.0")
    if _live_alerts_enabled() and (not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true"):
        from alert_messaging import telegram_configured
        from live_registry import start_live_monitors

        start_live_monitors(sorted(LIVE_MODULES))
        if not telegram_configured():
            print(
                "⚠️  Telegram not configured — set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID "
                "(run python telegram_setup.py)"
            )
        else:
            print("🔔 Live monitoring on · alerts via Telegram")
    else:
        print("ℹ️  Live alerts disabled (ENABLE_LIVE_ALERTS=false)")

    print(f"✅ Dashboard running at http://{host if host != '0.0.0.0' else 'localhost'}:{port}")
    app.run(debug=debug, port=port, host=host, use_reloader=debug)


if __name__ == "__main__":
    main()
