"""
System 3: EMA pullback + Inside Body Candle screener.

Identifies stocks where an Inside Body Candle forms under a valid 3-EMA trend
(EMA 13 > EMA 21 > EMA 24). RSI is shown for manual review — not used as a filter.
"""

from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from market_data import download

NAME = "System 3: EMA Pullback + Inside Body"
MODULE = "system_3"
BATCH_SIZE = 60

IST = ZoneInfo("Asia/Kolkata")

EMA_FAST = 13
EMA_MID = 21
EMA_SLOW = 24
RSI_PERIOD = 14

# Bullish trend: fast EMA above mid above slow
BULLISH_TREND = True


def _symbol(ticker: str) -> str:
    return ticker.replace(".NS", "").replace(".BO", "")


def _to_ist(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if out.index.tz is None:
        out.index = out.index.tz_localize(IST)
    else:
        out.index = out.index.tz_convert(IST)
    return out


def _extract_ticker_df(data: pd.DataFrame, ticker: str) -> pd.DataFrame | None:
    if data is None or not isinstance(data, pd.DataFrame) or data.empty:
        return None

    sym = _symbol(ticker)
    candidates = [ticker, sym, f"{sym}.NS", f"{sym}.BO"]

    if isinstance(data.columns, pd.MultiIndex):
        for level in (0, -1):
            level_vals = data.columns.get_level_values(level)
            for key in candidates:
                if key in level_vals:
                    return data.xs(key, axis=1, level=level).dropna(how="all")
        return None

    return data.dropna(how="all")


def _ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False).mean()


def _rsi(close: pd.Series, period: int = RSI_PERIOD) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss
    return 100 - (100 / (1 + rs))


def _body_bounds(open_: float, close: float) -> tuple[float, float]:
    return min(open_, close), max(open_, close)


def _is_inside_body_candle(prev: pd.Series, curr: pd.Series) -> bool:
    """True when the current candle's body lies entirely inside the prior candle's body."""
    prev_lo, prev_hi = _body_bounds(float(prev["Open"]), float(prev["Close"]))
    curr_lo, curr_hi = _body_bounds(float(curr["Open"]), float(curr["Close"]))
    return curr_lo >= prev_lo and curr_hi <= prev_hi


def _ema_trend_valid(ema_fast: float, ema_mid: float, ema_slow: float) -> bool:
    if BULLISH_TREND:
        return ema_fast > ema_mid > ema_slow
    return ema_fast < ema_mid < ema_slow


def _signal_time(ts) -> str:
    if hasattr(ts, "tz_convert"):
        ts = ts.tz_convert(IST)
    return ts.strftime("%Y-%m-%d %H:%M IST")


def _scan_df(df: pd.DataFrame) -> dict | None:
    if df is None or len(df) < EMA_SLOW + RSI_PERIOD + 2:
        return None

    df = _to_ist(df)
    close = df["Close"]
    df = df.copy()
    df["EMA_13"] = _ema(close, EMA_FAST)
    df["EMA_21"] = _ema(close, EMA_MID)
    df["EMA_24"] = _ema(close, EMA_SLOW)
    df["RSI"] = _rsi(close, RSI_PERIOD)

    curr = df.iloc[-1]
    prev = df.iloc[-2]

    if pd.isna(curr["EMA_13"]) or pd.isna(curr["RSI"]):
        return None

    if not _ema_trend_valid(float(curr["EMA_13"]), float(curr["EMA_21"]), float(curr["EMA_24"])):
        return None

    if not _is_inside_body_candle(prev, curr):
        return None

    rsi_val = round(float(curr["RSI"]), 2)
    return {
        "Symbol": None,  # filled by caller
        "Signal Time": _signal_time(df.index[-1]),
        "RSI Value": rsi_val,
        "RSI < 50": "Yes" if rsi_val < 50 else "No",
        "RSI < 20": "Yes" if rsi_val < 20 else "No",
    }


def _levels_for_ticker(ticker: str, daily: pd.DataFrame | None) -> dict | None:
    daily_df = _extract_ticker_df(daily, ticker) if daily is not None else None
    row = _scan_df(daily_df)
    if row is None:
        return None
    row["Symbol"] = _symbol(ticker)
    return row


def scan_tickers(tickers: list[str], on_progress=None) -> list[dict]:
    hits: list[dict] = []
    total = len(tickers)

    for start in range(0, total, BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        if on_progress:
            on_progress(start + len(batch), total)

        try:
            daily = download(
                batch,
                interval="1d",
                period="120d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for ticker in batch:
            row = _levels_for_ticker(ticker, daily)
            if row:
                hits.append(row)

    return hits


def run(tickers: list[str]) -> list[dict]:
    return scan_tickers(tickers)
