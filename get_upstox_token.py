#!/usr/bin/env python3
"""Exchange an Upstox OAuth code for an access token and save it to .env."""

from __future__ import annotations

import os
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlencode

import requests
from dotenv import load_dotenv, set_key

ENV_PATH = Path(__file__).resolve().parent / ".env"
TOKEN_URL = "https://api.upstox.com/v2/login/authorization/token"
AUTH_URL = "https://api.upstox.com/v2/login/authorization/dialog"


def _require(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        print(f"Missing {name} in .env", file=sys.stderr)
        sys.exit(1)
    return value


def main() -> None:
    load_dotenv(ENV_PATH)

    client_id = _require("UPSTOX_API_KEY")
    client_secret = _require("UPSTOX_API_SECRET")
    redirect_uri = os.environ.get("UPSTOX_REDIRECT_URI", "http://127.0.0.1").strip()

    params = urlencode(
        {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
        }
    )
    login_url = f"{AUTH_URL}?{params}"

    print("1. Log in to Upstox in your browser:")
    print(f"   {login_url}\n")
    webbrowser.open(login_url)

    print("2. After login you'll be redirected to something like:")
    print(f"   {redirect_uri}?code=XXXXX")
    print("   Copy the code value from the address bar.\n")
    code = input("Paste the code here: ").strip()
    if not code:
        print("No code provided.", file=sys.stderr)
        sys.exit(1)

    response = requests.post(
        TOKEN_URL,
        headers={
            "accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        data={
            "code": code,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        },
        timeout=30,
    )

    if response.status_code != 200:
        print(f"Token request failed ({response.status_code}):", response.text, file=sys.stderr)
        sys.exit(1)

    access_token = response.json().get("access_token", "").strip()
    if not access_token:
        print("No access_token in response:", response.json(), file=sys.stderr)
        sys.exit(1)

    set_key(ENV_PATH, "UPSTOX_ACCESS_TOKEN", access_token)
    print("\n✅ Saved UPSTOX_ACCESS_TOKEN to .env")
    print("   Token expires around 3:30 AM IST the next day — re-run this script when needed.")


if __name__ == "__main__":
    main()
