"""
Opening-range + previous-day-high breakout screener.

Returns stocks whose current 15m candle high is above both levels, the first
15m candle (9:15–9:30) is above the 10 SMA, and that candle is above Parabolic SAR.
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
SMA_PERIOD = 10
INTRADAY_PERIOD = "5d"


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


def _sma(close: pd.Series, period: int) -> pd.Series:
    return close.rolling(window=period, min_periods=period).mean()


def _parabolic_sar(
    high: pd.Series,
    low: pd.Series,
    *,
    af_start: float = 0.02,
    af_step: float = 0.02,
    af_max: float = 0.2,
) -> pd.Series:
    length = len(high)
    if length < 2:
        return pd.Series(index=high.index, dtype=float)

    sar = [0.0] * length
    bull = high.iloc[1] >= high.iloc[0]
    af = af_start
    if bull:
        ep = high.iloc[1]
        sar[0] = low.iloc[0]
        sar[1] = low.iloc[0]
    else:
        ep = low.iloc[1]
        sar[0] = high.iloc[0]
        sar[1] = high.iloc[0]

    for i in range(2, length):
        prev_sar = sar[i - 1]
        if bull:
            sar[i] = prev_sar + af * (ep - prev_sar)
            sar[i] = min(sar[i], low.iloc[i - 1], low.iloc[i - 2])
            if low.iloc[i] < sar[i]:
                bull = False
                sar[i] = ep
                ep = low.iloc[i]
                af = af_start
            elif high.iloc[i] > ep:
                ep = high.iloc[i]
                af = min(af + af_step, af_max)
        else:
            sar[i] = prev_sar + af * (ep - prev_sar)
            sar[i] = max(sar[i], high.iloc[i - 1], high.iloc[i - 2])
            if high.iloc[i] > sar[i]:
                bull = True
                sar[i] = ep
                ep = high.iloc[i]
                af = af_start
            elif low.iloc[i] < ep:
                ep = low.iloc[i]
                af = min(af + af_step, af_max)

    return pd.Series(sar, index=high.index)


def _first_or_bar(intraday: pd.DataFrame) -> tuple[int, pd.Series] | None:
    """Index and row of the first 15-minute candle (9:15–9:30 IST) in today's session."""
    if intraday is None or intraday.empty:
        return None

    today_bars = _today_bars(intraday)
    if today_bars.empty:
        return None

    for ts, row in today_bars.iterrows():
        if MARKET_OPEN <= ts.time() < OR_CANDLE_END:
            idx = intraday.index.get_loc(ts)
            if isinstance(idx, slice):
                idx = idx.start
            return int(idx), row

    ts = today_bars.index[0]
    idx = intraday.index.get_loc(ts)
    if isinstance(idx, slice):
        idx = idx.start
    return int(idx), today_bars.iloc[0]


def _first_bar_high(intraday: pd.DataFrame) -> float | None:
    """High of the first 15-minute candle (9:15–9:30 IST)."""
    first = _first_or_bar(intraday)
    if first is None:
        return None
    return float(first[1]["High"])


def _first_bar_above_sma_and_sar(intraday: pd.DataFrame) -> bool:
    first = _first_or_bar(intraday)
    if first is None:
        return False

    idx, row = first
    if idx < SMA_PERIOD - 1:
        return False

    close = intraday["Close"]
    sma = _sma(close, SMA_PERIOD)
    sar = _parabolic_sar(intraday["High"], intraday["Low"])

    first_close = float(row["Close"])
    sma_val = float(sma.iloc[idx])
    sar_val = float(sar.iloc[idx])
    if pd.isna(sma_val) or pd.isna(sar_val):
        return False

    return first_close > sma_val and first_close > sar_val


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

    breakout = (
        candle_high > or_high
        and candle_high > pd_high
        and _first_bar_above_sma_and_sar(intra_df)
    )
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
    """Return stocks passing OR/prev-day breakout plus first-candle SMA & SAR filters."""
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
                period=INTRADAY_PERIOD,
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
