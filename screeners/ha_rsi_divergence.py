"""
Daily Heikin Ashi + RSI(2) bullish divergence screener.

Detects bullish divergence (price lower lows, RSI higher lows) on daily
Heikin Ashi candles.

RSI bands: lower 20.
Inside-body-candle entry logic is intentionally excluded.
"""

from __future__ import annotations

import pandas as pd

from market_data import download

NAME = "HA RSI Divergence"
MODULE = "ha_rsi_divergence"
BATCH_SIZE = 60

DAILY_PERIOD = "6mo"
RSI_LENGTH = 2
LOWER_BAND = 20
SWING_ORDER = 2
MAX_BARS_SINCE_SWING = 5


def _symbol(ticker: str) -> str:
    return ticker.replace(".NS", "").replace(".BO", "")


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


def _heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    """Convert OHLC to Heikin Ashi candles."""
    ha = pd.DataFrame(index=df.index)
    ha_close = (df["Open"] + df["High"] + df["Low"] + df["Close"]) / 4.0
    ha_open = pd.Series(index=df.index, dtype=float)
    ha_open.iloc[0] = (df["Open"].iloc[0] + df["Close"].iloc[0]) / 2.0
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0

    ha["Open"] = ha_open
    ha["Close"] = ha_close
    ha["High"] = pd.concat([df["High"], ha_open, ha_close], axis=1).max(axis=1)
    ha["Low"] = pd.concat([df["Low"], ha_open, ha_close], axis=1).min(axis=1)
    if "Volume" in df.columns:
        ha["Volume"] = df["Volume"]
    return ha


def _rsi(close: pd.Series, period: int) -> pd.Series:
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, float("nan"))
    return 100 - (100 / (1 + rs))


def _swing_lows(low: pd.Series, order: int) -> list[int]:
    """Return indices of swing lows (local minima)."""
    lows: list[int] = []
    for i in range(order, len(low) - order):
        window = low.iloc[i - order : i + order + 1]
        if low.iloc[i] == window.min() and (low.iloc[i] < low.iloc[i - 1] or low.iloc[i] < low.iloc[i + 1]):
            lows.append(i)
    return lows


def _detect_bullish_divergence(ha: pd.DataFrame) -> dict | None:
    if len(ha) < RSI_LENGTH + SWING_ORDER * 2 + 5:
        return None

    rsi = _rsi(ha["Close"], RSI_LENGTH)
    swing_low_idx = _swing_lows(ha["Low"], SWING_ORDER)
    last_bar = len(ha) - 1

    if len(swing_low_idx) < 2:
        return None

    i2 = swing_low_idx[-1]
    if last_bar - i2 > MAX_BARS_SINCE_SWING:
        return None

    low = ha["Low"]
    high = ha["High"]

    # Walk back through prior swing lows; i1's low must hold until i2 breaks it.
    for i1 in reversed(swing_low_idx[:-1]):
        if low.iloc[i2] >= low.iloc[i1]:
            continue
        if rsi.iloc[i2] <= rsi.iloc[i1]:
            continue
        if pd.isna(rsi.iloc[i1]) or pd.isna(rsi.iloc[i2]):
            continue

        between_lows = low.iloc[i1 + 1 : i2]
        if not between_lows.empty and between_lows.min() < low.iloc[i1]:
            continue

        between_highs = high.iloc[i1 + 1 : i2]
        if between_highs.empty or between_highs.max() <= low.iloc[i1]:
            continue

        rsi_oversold = rsi.iloc[i1] <= LOWER_BAND or rsi.iloc[i2] <= LOWER_BAND
        if not rsi_oversold:
            continue

        return {
            "rsi": float(rsi.iloc[i2]),
            "entry_level": float(ha["High"].iloc[i2]),
        }

    return None


def _pct_change(price: float, prev_close: float | None) -> str:
    if not prev_close or prev_close == 0:
        return "—"
    pct = (price - prev_close) / prev_close * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _evaluate_ticker(ticker: str, daily: pd.DataFrame | None) -> dict | None:
    daily_df = _extract_ticker_df(daily, ticker) if daily is not None else None
    if daily_df is None or daily_df.empty:
        return None

    required = {"Open", "High", "Low", "Close"}
    if not required.issubset(daily_df.columns):
        return None

    ha = _heikin_ashi(daily_df.sort_index())
    div = _detect_bullish_divergence(ha)
    if div is None:
        return None

    price = float(daily_df["Close"].iloc[-1])
    prev_close = float(daily_df["Close"].iloc[-2]) if len(daily_df) > 1 else None

    return {
        "Symbol": _symbol(ticker),
        "Price": round(price, 2),
        "RSI": round(div["rsi"], 1),
        "Entry level": round(div["entry_level"], 2),
        "% change": _pct_change(price, prev_close),
        "_hit": True,
    }


def scan_tickers(tickers: list[str], on_progress=None) -> list[dict]:
    """Return stocks with recent daily HA + RSI(2) divergence."""
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
                period=DAILY_PERIOD,
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for ticker in batch:
            row = _evaluate_ticker(ticker, daily)
            if row and row.get("_hit"):
                hits.append({k: v for k, v in row.items() if not k.startswith("_")})

    return hits


def run(tickers: list[str]) -> list[dict]:
    return scan_tickers(tickers)
