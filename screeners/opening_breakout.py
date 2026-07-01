"""
Opening-range + previous-day-high breakout screener.

Returns stocks whose current 15m candle high is above both levels.
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd

from market_data import download

NAME = "OR + Prev Day High Breakout"
MODULE = "opening_breakout"
BATCH_SIZE = 60

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
OR_CANDLE_END = time(9, 30)


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


def _today_bars(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    df = _to_ist(df)
    today = datetime.now(IST).date()
    return df[df.index.date == today]


def _first_bar_high(intraday: pd.DataFrame) -> float | None:
    """High of the first 15-minute candle (9:15–9:30 IST)."""
    today_bars = _today_bars(intraday)
    if today_bars.empty:
        return None

    for ts, row in today_bars.iterrows():
        t = ts.time()
        if MARKET_OPEN <= t < OR_CANDLE_END:
            return float(row["High"])

    return float(today_bars.iloc[0]["High"])


def _prev_day_high(daily: pd.DataFrame) -> float | None:
    if daily is None or daily.empty:
        return None

    daily = _to_ist(daily)
    today = datetime.now(IST).date()
    past = daily[daily.index.date < today]
    if past.empty:
        return None
    return float(past.iloc[-1]["High"])


def _prev_day_close(daily: pd.DataFrame) -> float | None:
    if daily is None or daily.empty:
        return None
    daily = _to_ist(daily)
    today = datetime.now(IST).date()
    past = daily[daily.index.date < today]
    if past.empty:
        return None
    return float(past.iloc[-1]["Close"])


def _today_volume(intraday: pd.DataFrame) -> int | None:
    today_bars = _today_bars(intraday)
    if today_bars.empty or "Volume" not in today_bars.columns:
        return None
    return int(today_bars["Volume"].sum())


def _pct_change(price: float, prev_close: float | None) -> str:
    if not prev_close or prev_close == 0:
        return "—"
    pct = (price - prev_close) / prev_close * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _current_candle(intraday: pd.DataFrame) -> tuple[float | None, float | None]:
    """Return (current 15m candle high, latest close) for today."""
    today_bars = _today_bars(intraday)
    if today_bars.empty:
        return None, None
    bar = today_bars.iloc[-1]
    return float(bar["High"]), float(bar["Close"])


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

    # Single-ticker download — flat columns
    if len(candidates) == 1 or sym in str(ticker):
        return data.dropna(how="all")
    return data.dropna(how="all")


def _levels_for_ticker(
    ticker: str,
    intraday: pd.DataFrame | None,
    daily: pd.DataFrame | None,
) -> dict | None:
    intra_df = _extract_ticker_df(intraday, ticker) if intraday is not None else None
    daily_df = _extract_ticker_df(daily, ticker) if daily is not None else None

    or_high = _first_bar_high(intra_df) if intra_df is not None else None
    pd_high = _prev_day_high(daily_df) if daily_df is not None else None
    candle_high, price = _current_candle(intra_df) if intra_df is not None else (None, None)
    prev_close = _prev_day_close(daily_df) if daily_df is not None else None
    volume = _today_volume(intra_df) if intra_df is not None else None

    if or_high is None or pd_high is None or candle_high is None or price is None:
        return None

    breakout = candle_high > or_high and candle_high > pd_high
    return {
        "Symbol": _symbol(ticker),
        "Price": round(price, 2),
        "% change": _pct_change(price, prev_close),
        "Volume": volume if volume is not None else 0,
        "_breakout": breakout,
        "_or_high": or_high,
        "_pd_high": pd_high,
        "_candle_high": candle_high,
    }


def scan_tickers(tickers: list[str], on_progress=None) -> list[dict]:
    """Return all stocks whose current candle high is above both levels."""
    hits: list[dict] = []
    total = len(tickers)

    for start in range(0, total, BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        if on_progress:
            on_progress(start + len(batch), total)

        try:
            intra = download(
                batch,
                interval="15m",
                period="1d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            daily = download(
                batch,
                interval="1d",
                period="10d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for ticker in batch:
            row = _levels_for_ticker(ticker, intra, daily)
            if row and row["_breakout"]:
                hits.append({k: v for k, v in row.items() if not k.startswith("_")})

    return hits


def run(tickers: list[str]) -> list[dict]:
    return scan_tickers(tickers)
