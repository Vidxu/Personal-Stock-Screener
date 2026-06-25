"""
SYSTEM 1 — Positive RSI Divergence (daily Heikin Ashi + inside body).

Daily only:
  - Heikin Ashi candles; RSI(2) on HA close (bands 80 / 50 / 20)
  - Positive divergence: two consecutive HA swing lows — price lower low,
    RSI higher low (points A → B in the reference diagram)
  - Inside-body HA candle within a few sessions after B
  - Long entry: from next session, price crosses above inside-body HA high
"""

from __future__ import annotations

from datetime import datetime, time
from zoneinfo import ZoneInfo

import pandas as pd
import yfinance as yf

NAME = "Positive Divergence (Live)"
MODULE = "positive_divergence"
BATCH_SIZE = 40
LOOKBACK_DAYS = 120

RSI_LENGTH = 2
RSI_UPPER = 80
RSI_MIDDLE = 50
RSI_LOWER = 20

IST = ZoneInfo("Asia/Kolkata")
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

SWING_WINDOW = 2
MIN_SWING_GAP = 3
MAX_SWING_GAP = 25
MIN_RSI_LIFT = 0.5
IB_MAX_BARS_AFTER_DIV = 8
ENTRY_WINDOW_BARS = 10
SETUP_MAX_IB_AGE = 20


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


def _heikin_ashi(df: pd.DataFrame) -> pd.DataFrame:
    o = df["Open"].astype(float)
    h = df["High"].astype(float)
    l = df["Low"].astype(float)
    c = df["Close"].astype(float)

    ha_close = (o + h + l + c) / 4.0
    ha_open = pd.Series(index=df.index, dtype=float)
    ha_open.iloc[0] = (o.iloc[0] + c.iloc[0]) / 2.0
    for i in range(1, len(df)):
        ha_open.iloc[i] = (ha_open.iloc[i - 1] + ha_close.iloc[i - 1]) / 2.0

    ha_high = pd.concat([h, ha_open, ha_close], axis=1).max(axis=1)
    ha_low = pd.concat([l, ha_open, ha_close], axis=1).min(axis=1)

    out = df.copy()
    out["HA_Open"] = ha_open
    out["HA_High"] = ha_high
    out["HA_Low"] = ha_low
    out["HA_Close"] = ha_close
    return out


def _rsi(series: pd.Series, length: int = RSI_LENGTH) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = (-delta).clip(lower=0)
    avg_gain = gain.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / length, min_periods=length, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, 1e-10)
    return 100 - (100 / (1 + rs))


def _body_top(row) -> float:
    return float(max(row["HA_Open"], row["HA_Close"]))


def _body_bottom(row) -> float:
    return float(min(row["HA_Open"], row["HA_Close"]))


def _is_inside_body(curr, prev) -> bool:
    return _body_top(curr) <= _body_top(prev) and _body_bottom(curr) >= _body_bottom(prev)


def _swing_low_indices(lows: pd.Series, window: int = SWING_WINDOW) -> list[int]:
    vals = lows.astype(float).squeeze()
    if isinstance(vals, pd.DataFrame):
        vals = vals.iloc[:, 0]
    idxs: list[int] = []
    for i in range(window, len(vals) - window):
        segment = vals.iloc[i - window : i + window + 1]
        if float(vals.iloc[i]) <= float(segment.min()):
            idxs.append(i)
    return idxs


def _bullish_divergence(ha_lows: pd.Series, rsis: pd.Series, i_a: int, i_b: int) -> bool:
    """Point A then B: HA price lower low, RSI higher low (both below middle band)."""
    if i_b <= i_a:
        return False
    gap = i_b - i_a
    if not (MIN_SWING_GAP <= gap <= MAX_SWING_GAP):
        return False

    p_a, p_b = float(ha_lows.iloc[i_a]), float(ha_lows.iloc[i_b])
    r_a, r_b = float(rsis.iloc[i_a]), float(rsis.iloc[i_b])
    if pd.isna(r_a) or pd.isna(r_b):
        return False

    if not (p_b < p_a):
        return False
    if not (r_b > r_a):
        return False
    if r_b - r_a < MIN_RSI_LIFT:
        return False
    if r_a > RSI_MIDDLE or r_b > RSI_MIDDLE:
        return False
    return True


def _detect_setup(ha: pd.DataFrame, raw: pd.DataFrame) -> dict | None:
    if len(ha) < 25:
        return None

    ha = ha.copy()
    ha["RSI"] = _rsi(ha["HA_Close"], RSI_LENGTH)
    ha_lows = ha["HA_Low"].astype(float)
    rsis = ha["RSI"]

    swings = _swing_low_indices(ha_lows)
    if len(swings) < 2:
        return None

    last_idx = len(ha) - 1

    for k in range(len(swings) - 1, 0, -1):
        i_b = swings[k]
        i_a = swings[k - 1]

        if not _bullish_divergence(ha_lows, rsis, i_a, i_b):
            continue

        ib_idx = None
        for j in range(i_b + 1, min(i_b + 1 + IB_MAX_BARS_AFTER_DIV, len(ha))):
            if j > 0 and _is_inside_body(ha.iloc[j], ha.iloc[j - 1]):
                ib_idx = j
                break

        if ib_idx is None:
            continue

        entry_start_idx = ib_idx + 1
        if entry_start_idx > last_idx:
            continue

        entry_end_idx = min(entry_start_idx + ENTRY_WINDOW_BARS - 1, last_idx)
        ib_high = float(ha.iloc[ib_idx]["HA_High"])

        return {
            "div_a_idx": i_a,
            "div_b_idx": i_b,
            "div_a_date": ha.index[i_a].date(),
            "div_b_date": ha.index[i_b].date(),
            "ib_idx": ib_idx,
            "ib_high": ib_high,
            "ib_date": ha.index[ib_idx].date(),
            "entry_start_idx": entry_start_idx,
            "entry_end_idx": entry_end_idx,
            "entry_start_date": ha.index[entry_start_idx].date(),
            "rsi_at_a": round(float(rsis.iloc[i_a]), 2),
            "rsi_at_div": round(float(rsis.iloc[i_b]), 2),
        }

    return None


def _setup_recent(setup: dict, ha: pd.DataFrame) -> bool:
    last_idx = len(ha) - 1
    return last_idx - setup["ib_idx"] <= SETUP_MAX_IB_AGE


def _in_entry_window(setup: dict, ha: pd.DataFrame) -> bool:
    today = datetime.now(IST).date()
    start = setup["entry_start_date"]
    end = ha.index[setup["entry_end_idx"]].date()
    return start <= today <= end


def _session_high_close(
    daily: pd.DataFrame,
    intraday: pd.DataFrame | None,
) -> tuple[float | None, float | None, int]:
    if intraday is not None and not intraday.empty:
        intra = _to_ist(intraday)
        today = datetime.now(IST).date()
        today_bars = intra[intra.index.date == today]
        if not today_bars.empty:
            vol = int(today_bars["Volume"].sum()) if "Volume" in today_bars else 0
            return (
                float(today_bars["High"].max()),
                float(today_bars.iloc[-1]["Close"]),
                vol,
            )

    daily = _to_ist(daily)
    if daily.empty:
        return None, None, 0
    bar = daily.iloc[-1]
    vol = int(bar["Volume"]) if "Volume" in daily.columns else 0
    return float(bar["High"]), float(bar["Close"]), vol


def _pct_change(price: float, daily: pd.DataFrame) -> str:
    if len(daily) < 2:
        return "—"
    prev = float(daily.iloc[-2]["Close"])
    if not prev:
        return "—"
    pct = (price - prev) / prev * 100
    sign = "+" if pct >= 0 else ""
    return f"{sign}{pct:.2f}%"


def _row_from_setup_dict(
    symbol: str,
    setup: dict,
    daily: pd.DataFrame,
    intraday: pd.DataFrame | None,
) -> dict | None:
    ha = _heikin_ashi(daily)
    if not _setup_recent(setup, ha):
        return None

    session_high, price, volume = _session_high_close(daily, intraday)
    if session_high is None or price is None:
        return None

    ib_high = setup["ib_high"]
    triggered = session_high > ib_high
    in_window = _in_entry_window(setup, ha)

    return {
        "Symbol": symbol,
        "Price": round(price, 2),
        "% change": _pct_change(price, daily),
        "Volume": volume,
        "_triggered": triggered,
        "_in_entry_window": in_window,
        "_ib_high": ib_high,
        "_rsi": setup["rsi_at_div"],
        "_setup_date": str(setup["ib_date"]),
    }


def build_setup_cache(ticker: str, daily: pd.DataFrame | None) -> dict | None:
    """Cache setups in the active entry window (for live alert monitoring)."""
    if daily is None or daily.empty or len(daily) < 25:
        return None
    daily = _to_ist(daily).sort_index()
    ha = _heikin_ashi(daily)
    setup = _detect_setup(ha, daily)
    if not setup or not _in_entry_window(setup, ha):
        return None
    return {
        "Symbol": _symbol(ticker),
        "_setup": setup,
        "_ib_high": setup["ib_high"],
        "_rsi": setup["rsi_at_div"],
        "_setup_date": str(setup["ib_date"]),
        "_daily": daily,
    }


def row_from_setup(
    ticker: str,
    cache: dict,
    intraday: pd.DataFrame | None,
) -> dict | None:
    daily = cache.get("_daily")
    setup = cache.get("_setup")
    if daily is None or setup is None:
        return None
    return _row_from_setup_dict(cache["Symbol"], setup, daily, intraday)


def evaluate_ticker(
    ticker: str,
    daily: pd.DataFrame | None,
    intraday: pd.DataFrame | None = None,
) -> dict | None:
    if daily is None or daily.empty or len(daily) < 25:
        return None

    daily = _to_ist(daily).sort_index()
    ha = _heikin_ashi(daily)
    setup = _detect_setup(ha, daily)
    if not setup:
        return None

    return _row_from_setup_dict(_symbol(ticker), setup, daily, intraday)


def scan_tickers(tickers: list[str]) -> list[dict]:
    """Recent valid setups (inside-body found within last N sessions)."""
    hits: list[dict] = []
    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        try:
            daily = yf.download(
                batch,
                interval="1d",
                period=f"{LOOKBACK_DAYS}d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            intra = yf.download(
                batch,
                interval="1m",
                period="1d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for ticker in batch:
            ddf = _extract_ticker_df(daily, ticker)
            idf = _extract_ticker_df(intra, ticker)
            row = evaluate_ticker(ticker, ddf, idf)
            if row:
                hits.append({k: v for k, v in row.items() if not k.startswith("_")})

    return hits


def scan_watchlist(tickers: list[str]) -> list[dict]:
    """Setups in entry window, not yet triggered."""
    hits: list[dict] = []
    for start in range(0, len(tickers), BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        try:
            daily = yf.download(
                batch,
                interval="1d",
                period=f"{LOOKBACK_DAYS}d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            intra = yf.download(
                batch,
                interval="1m",
                period="1d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception:
            continue

        for ticker in batch:
            ddf = _extract_ticker_df(daily, ticker)
            idf = _extract_ticker_df(intra, ticker)
            row = evaluate_ticker(ticker, ddf, idf)
            if row and row.get("_in_entry_window") and not row.get("_triggered"):
                hits.append({k: v for k, v in row.items() if not k.startswith("_")})

    return hits


def run(tickers: list[str]) -> list[dict]:
    return scan_tickers(tickers)


def list_row(row: dict) -> bool:
    return bool(row.get("_triggered"))


def is_market_hours() -> bool:
    now = datetime.now(IST)
    if now.weekday() >= 5:
        return False
    return MARKET_OPEN <= now.time() <= MARKET_CLOSE


def levels_ready() -> bool:
    now = datetime.now(IST)
    return now.weekday() < 5
