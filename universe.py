import requests
import pandas as pd
import os
import json
from datetime import datetime, timedelta

CACHE_FILE = "nse_stocks.json"
CACHE_EXPIRY_HOURS = 24  # Re-download only once a day

def get_nse_stocks():
    """
    Downloads the full NSE stock list and returns tickers in Yahoo Finance format.
    Uses a local cache so it doesn't re-download every time you run a screener.
    """

    # ── Use cache if it's fresh ──────────────────────────────────────────────
    if os.path.exists(CACHE_FILE):
        with open(CACHE_FILE, "r") as f:
            cache = json.load(f)
        saved_at = datetime.fromisoformat(cache["saved_at"])
        if datetime.now() - saved_at < timedelta(hours=CACHE_EXPIRY_HOURS):
            print(f"📦 Using cached NSE list ({len(cache['tickers'])} stocks)")
            return cache["tickers"]

    # ── Download fresh from NSE ──────────────────────────────────────────────
    print("🌐 Downloading NSE stock list...")
    url = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
    headers = {
        "User-Agent": "Mozilla/5.0",
        "Referer": "https://www.nseindia.com/"
    }

    try:
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        from io import StringIO
        df = pd.read_csv(StringIO(resp.text))

        # NSE CSV has a column called "SYMBOL"
        symbols = df["SYMBOL"].dropna().tolist()

        # Convert to Yahoo Finance format: RELIANCE → RELIANCE.NS
        tickers = [f"{s.strip()}.NS" for s in symbols]

        # Save cache
        with open(CACHE_FILE, "w") as f:
            json.dump({"saved_at": datetime.now().isoformat(), "tickers": tickers}, f)

        print(f"✅ Got {len(tickers)} NSE stocks")
        return tickers

    except Exception as e:
        print(f"❌ Failed to download NSE list: {e}")
        print("⚠️  Falling back to a small default list")
        return [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
            "ICICIBANK.NS", "WIPRO.NS", "SBIN.NS", "BAJFINANCE.NS"
        ]


if __name__ == "__main__":
    stocks = get_nse_stocks()
    print(f"First 10: {stocks[:10]}")