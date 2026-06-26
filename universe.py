import requests
import pandas as pd
import os
import json
from datetime import datetime, timedelta

CACHE_FILE = "nse_stocks.json"
NIFTY500_FILE = "nifty_500.json"
NIFTY500_URL = "https://nsearchives.nseindia.com/content/indices/ind_nifty500list.csv"
CACHE_EXPIRY_HOURS = 24  # Re-download only once a day
NIFTY500_MIN_COUNT = 480


def _nse_session() -> requests.Session:
    headers = {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/market-data/live-equity-market",
    }
    session = requests.Session()
    session.headers.update(headers)
    session.get("https://www.nseindia.com", timeout=15)
    return session


def download_nifty500_list() -> list[str]:
    """Fetch official Nifty 500 constituents from NSE."""
    from io import StringIO

    session = _nse_session()
    resp = session.get(NIFTY500_URL, timeout=20)
    resp.raise_for_status()
    df = pd.read_csv(StringIO(resp.text))
    symbols = df["Symbol"].dropna().tolist()
    return [f"{s.strip()}.NS" for s in symbols]


def _save_nifty500(tickers: list[str]) -> None:
    path = os.path.join(os.path.dirname(__file__), NIFTY500_FILE)
    with open(path, "w") as f:
        json.dump(
            {
                "name": "Nifty 500 (monitor universe)",
                "saved_at": datetime.now().isoformat(timespec="seconds"),
                "tickers": tickers,
            },
            f,
            indent=2,
        )


def get_nifty500_stocks() -> list[str]:
    """Active scan/monitor universe — official Nifty 500 from nifty_500.json."""
    path = os.path.join(os.path.dirname(__file__), NIFTY500_FILE)
    tickers: list[str] = []

    if os.path.exists(path):
        with open(path, "r") as f:
            data = json.load(f)
        tickers = data.get("tickers", data if isinstance(data, list) else [])
        saved_at = data.get("saved_at")
        stale = False
        if saved_at:
            try:
                stale = datetime.now() - datetime.fromisoformat(saved_at) > timedelta(days=7)
            except ValueError:
                stale = True

        if len(tickers) >= NIFTY500_MIN_COUNT and not stale:
            print(f"📦 Using Nifty 500 list ({len(tickers)} stocks)")
            return tickers

    try:
        print("🌐 Refreshing Nifty 500 list from NSE…")
        tickers = download_nifty500_list()
        _save_nifty500(tickers)
        print(f"✅ Nifty 500 list updated ({len(tickers)} stocks)")
        return tickers
    except Exception as exc:
        if tickers:
            print(f"⚠️  Nifty 500 refresh failed ({exc}) — using cached {len(tickers)} stocks")
            return tickers
        print(f"⚠️  {NIFTY500_FILE} missing and download failed — using small default list")
        return [
            "RELIANCE.NS", "TCS.NS", "INFY.NS", "HDFCBANK.NS",
            "ICICIBANK.NS", "WIPRO.NS", "SBIN.NS", "BAJFINANCE.NS",
        ]


def get_nse_stocks():
    """
    Full NSE list (cached in nse_stocks.json). Not used by screeners currently —
    kept for reference / future use.
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