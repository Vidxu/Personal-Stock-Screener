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

    load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
    if not os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip():
        print(
            "⚠️  UPSTOX_ACCESS_TOKEN is not set — market data requests will fail.\n"
            "   Generate one: python get_upstox_token.py\n"
            "   (API key/secret are already in .env; you still need the access token.)"
        )
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    port = int(os.environ.get("PORT", PORT))
    if not os.environ.get("PORT"):
        port = _bindable_port(port, "127.0.0.1" if host == "127.0.0.1" else "0.0.0.0")
    print(f"✅ Dashboard running at http://{host if host != '0.0.0.0' else 'localhost'}:{port}")
    app.run(debug=debug, port=port, host=host, use_reloader=debug)


if __name__ == "__main__":
    main()
