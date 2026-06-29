"""
Market data via Upstox API (drop-in replacement for yfinance.download).

Requires UPSTOX_ACCESS_TOKEN in the environment (Bearer token from Upstox OAuth).

Instrument keys are resolved from the public NSE instruments file, cached locally.
"""

from __future__ import annotations

import gzip
import json
import os
import threading

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from urllib.parse import quote

import pandas as pd
import requests

IST = __import__("zoneinfo").ZoneInfo("Asia/Kolkata")

UPSTOX_BASE = "https://api.upstox.com/v3"
UPSTOX_V2 = "https://api.upstox.com/v2"
INSTRUMENTS_URL = "https://assets.upstox.com/market-quote/instruments/exchange/NSE.json.gz"
CACHE_PATH = os.path.join(os.path.dirname(__file__), "upstox_instruments_cache.json")
CACHE_MAX_AGE_HOURS = 24
MAX_WORKERS = 6
REQUEST_GAP_SEC = 0.05
OHLC_BATCH_SIZE = 500

_map_lock = threading.Lock()
_symbol_to_key: dict[str, str] | None = None
_last_request = 0.0
_req_lock = threading.Lock()


class UpstoxConfigError(RuntimeError):
    pass


def _access_token() -> str:
    token = os.environ.get("UPSTOX_ACCESS_TOKEN", "").strip()
    if not token:
        raise UpstoxConfigError(
            "UPSTOX_ACCESS_TOKEN is not set in .env. "
            "Run: python get_upstox_token.py"
        )
    return token


def _normalize_ticker(ticker: str) -> str:
    return ticker.replace(".NS", "").replace(".BO", "").strip().upper()


def _ticker_label(ticker: str) -> str:
    sym = _normalize_ticker(ticker)
    return ticker if "." in ticker else f"{sym}.NS"


def _throttle() -> None:
    global _last_request
    with _req_lock:
        now = time_mod.time()
        wait = REQUEST_GAP_SEC - (now - _last_request)
        if wait > 0:
            time_mod.sleep(wait)
        _last_request = time_mod.time()


def _load_instrument_map() -> dict[str, str]:
    global _symbol_to_key
    with _map_lock:
        if _symbol_to_key is not None:
            return _symbol_to_key

        if os.path.exists(CACHE_PATH):
            try:
                with open(CACHE_PATH, "r") as f:
                    cached = json.load(f)
                saved_at = datetime.fromisoformat(cached["saved_at"])
                if datetime.now() - saved_at < timedelta(hours=CACHE_MAX_AGE_HOURS):
                    _symbol_to_key = cached["map"]
                    print(f"📦 Upstox instrument map ({len(_symbol_to_key)} NSE EQ symbols)")
                    return _symbol_to_key
            except (ValueError, KeyError, json.JSONDecodeError):
                pass

        print("🌐 Downloading Upstox NSE instrument list…")
        resp = requests.get(INSTRUMENTS_URL, timeout=60)
        resp.raise_for_status()
        raw = gzip.decompress(resp.content)
        instruments = json.loads(raw)

        mapping: dict[str, str] = {}
        for inst in instruments:
            if inst.get("segment") != "NSE_EQ":
                continue
            if inst.get("instrument_type") != "EQ":
                continue
            sym = (inst.get("trading_symbol") or "").strip().upper()
            key = inst.get("instrument_key")
            if sym and key:
                mapping[sym] = key

        with open(CACHE_PATH, "w") as f:
            json.dump(
                {"saved_at": datetime.now().isoformat(timespec="seconds"), "map": mapping},
                f,
            )

        _symbol_to_key = mapping
        print(f"✅ Upstox instrument map ready ({len(mapping)} NSE EQ symbols)")
        return _symbol_to_key


def instrument_key(ticker: str) -> str | None:
    sym = _normalize_ticker(ticker)
    return _load_instrument_map().get(sym)


def _parse_period_days(period: str) -> int:
    p = period.strip().lower()
    if p.endswith("d"):
        return max(1, int(p[:-1]))
    if p.endswith("mo"):
        return max(1, int(p[:-2])) * 30
    if p.endswith("y"):
        return max(1, int(p[:-1])) * 365
    return max(1, int(p))


def _interval_spec(interval: str) -> tuple[str, str]:
    iv = interval.strip().lower()
    if iv in ("1m", "1min"):
        return "minutes", "1"
    if iv in ("2m", "2min"):
        return "minutes", "2"
    if iv in ("3m", "3min"):
        return "minutes", "3"
    if iv in ("5m", "5min"):
        return "minutes", "5"
    if iv in ("15m", "15min"):
        return "minutes", "15"
    if iv in ("30m", "30min"):
        return "minutes", "30"
    if iv in ("1h", "60m"):
        return "hours", "1"
    if iv in ("1d", "1day", "1wk", "1mo"):
        return "days", "1"
    raise ValueError(f"Unsupported interval: {interval}")


def _candles_to_df(candles: list) -> pd.DataFrame:
    if not candles:
        return pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])

    rows = []
    for c in candles:
        ts = pd.Timestamp(c[0])
        if ts.tzinfo is None:
            ts = ts.tz_localize(IST)
        else:
            ts = ts.tz_convert(IST)
        rows.append(
            {
                "Open": float(c[1]),
                "High": float(c[2]),
                "Low": float(c[3]),
                "Close": float(c[4]),
                "Volume": int(c[5]) if len(c) > 5 and c[5] is not None else 0,
                "_ts": ts,
            }
        )

    df = pd.DataFrame(rows).set_index("_ts").sort_index()
    return df


def _fetch_candles(
    inst_key: str,
    unit: str,
    step: str,
    *,
    intraday: bool,
    from_date: str | None = None,
    to_date: str | None = None,
) -> pd.DataFrame:
    token = _access_token()
    encoded = quote(inst_key, safe="")
    if intraday:
        url = f"{UPSTOX_BASE}/historical-candle/intraday/{encoded}/{unit}/{step}"
    else:
        if not to_date:
            to_date = datetime.now(IST).date().isoformat()
        if from_date:
            url = f"{UPSTOX_BASE}/historical-candle/{encoded}/{unit}/{step}/{to_date}/{from_date}"
        else:
            url = f"{UPSTOX_BASE}/historical-candle/{encoded}/{unit}/{step}/{to_date}"

    _throttle()
    resp = requests.get(
        url,
        headers={
            "Accept": "application/json",
            "Authorization": f"Bearer {token}",
        },
        timeout=30,
    )
    if resp.status_code == 401:
        raise UpstoxConfigError("Upstox access token expired or invalid (HTTP 401)")
    if resp.status_code != 200:
        print(f"⚠️  Upstox candles HTTP {resp.status_code}: {inst_key} ({unit}/{step})")
        return pd.DataFrame()

    body = resp.json()
    if body.get("status") != "success":
        err = body.get("errors") or body.get("message") or body
        print(f"⚠️  Upstox candles error: {inst_key} — {err}")
        return pd.DataFrame()

    candles = (body.get("data") or {}).get("candles") or []
    return _candles_to_df(candles)


def fetch_ticker(
    ticker: str,
    interval: str = "1d",
    period: str = "1d",
) -> pd.DataFrame:
    """Fetch OHLCV for one ticker. Returns flat-column DataFrame."""
    key = instrument_key(ticker)
    if not key:
        return pd.DataFrame()

    unit, step = _interval_spec(interval)
    days = _parse_period_days(period)
    today = datetime.now(IST).date()

    try:
        # Minute/hour candles for the live session must use the intraday endpoint;
        # the historical API does not include today's developing bars.
        if unit in ("minutes", "hours"):
            today_df = _fetch_candles(key, unit, step, intraday=True)
            if days <= 1:
                return today_df
            from_date = (today - timedelta(days=days)).isoformat()
            hist_df = _fetch_candles(
                key, unit, step,
                intraday=False,
                from_date=from_date,
                to_date=today.isoformat(),
            )
            if hist_df.empty:
                return today_df
            if today_df.empty:
                return hist_df
            combined = pd.concat([hist_df, today_df]).sort_index()
            return combined[~combined.index.duplicated(keep="last")]

        from_date = (today - timedelta(days=days)).isoformat()
        to_date = today.isoformat()
        return _fetch_candles(
            key, unit, step,
            intraday=False,
            from_date=from_date,
            to_date=to_date,
        )
    except UpstoxConfigError:
        raise
    except Exception:
        return pd.DataFrame()


def fetch_ohlc_batch(
    tickers: list[str],
    interval: str = "I1",
) -> dict[str, dict]:
    """
    Batch session OHLC for up to 500 instruments per API call.

    Returns {symbol: {open, high, low, close, last_price}} keyed by bare symbol.
    interval: I1 (1m session), I30, or 1d.
    """
    if not tickers:
        return {}

    _load_instrument_map()
    key_to_sym: dict[str, str] = {}
    for t in tickers:
        k = instrument_key(t)
        if k:
            key_to_sym[k] = _normalize_ticker(t)

    if not key_to_sym:
        return {}

    token = _access_token()
    out: dict[str, dict] = {}
    keys = list(key_to_sym)

    for i in range(0, len(keys), OHLC_BATCH_SIZE):
        chunk = keys[i : i + OHLC_BATCH_SIZE]
        _throttle()
        resp = requests.get(
            f"{UPSTOX_V2}/market-quote/ohlc",
            params={"instrument_key": ",".join(chunk), "interval": interval},
            headers={
                "Accept": "application/json",
                "Authorization": f"Bearer {token}",
            },
            timeout=30,
        )
        if resp.status_code == 401:
            raise UpstoxConfigError("Upstox access token expired or invalid (HTTP 401)")
        if resp.status_code != 200:
            print(f"⚠️  Upstox OHLC batch HTTP {resp.status_code} ({len(chunk)} keys)")
            continue

        body = resp.json()
        if body.get("status") != "success":
            print(f"⚠️  Upstox OHLC batch error: {body.get('errors') or body}")
            continue

        for _label, payload in (body.get("data") or {}).items():
            inst = payload.get("instrument_token") or ""
            sym = key_to_sym.get(inst)
            if not sym:
                continue
            ohlc = payload.get("ohlc") or {}
            out[sym] = {
                "open": float(ohlc.get("open") or 0),
                "high": float(ohlc.get("high") or 0),
                "low": float(ohlc.get("low") or 0),
                "close": float(ohlc.get("close") or 0),
                "last_price": float(payload.get("last_price") or ohlc.get("close") or 0),
            }

    return out


def download(
    tickers: list[str] | str,
    interval: str = "1d",
    period: str = "1d",
    group_by: str = "ticker",
    threads: bool = True,
    progress: bool = False,
    **_,
) -> pd.DataFrame:
    """
    yfinance-compatible batch download using Upstox historical / intraday APIs.

    Returns a DataFrame with MultiIndex columns (ticker, OHLCV) when group_by='ticker'.
    """
    if isinstance(tickers, str):
        tickers = [tickers]

    if not tickers:
        return pd.DataFrame()

    _load_instrument_map()

    frames: dict[str, pd.DataFrame] = {}
    workers = MAX_WORKERS if threads and len(tickers) > 1 else 1

    def _one(t: str) -> tuple[str, pd.DataFrame]:
        return _ticker_label(t), fetch_ticker(t, interval=interval, period=period)

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_one, t) for t in tickers]
            for fut in as_completed(futures):
                label, df = fut.result()
                if df is not None and not df.empty:
                    frames[label] = df
    else:
        for t in tickers:
            label, df = _one(t)
            if df is not None and not df.empty:
                frames[label] = df

    if not frames:
        return pd.DataFrame()

    if len(frames) == 1 and group_by != "ticker":
        return next(iter(frames.values()))

    # Multi-ticker: columns like yfinance group_by='ticker'
    parts = []
    for label, df in frames.items():
        tagged = df.copy()
        tagged.columns = pd.MultiIndex.from_product([[label], tagged.columns])
        parts.append(tagged)

    combined = pd.concat(parts, axis=1).sort_index()
    return combined
