"""
Background live monitor — scans NSE stocks and fires alerts on fresh crossovers.
Hits are published incrementally so the UI gets results before the full scan ends.
"""

from __future__ import annotations

import threading
import time as time_mod
from datetime import datetime

from alert_notify import alert
from screeners.opening_breakout import (
    BATCH_SIZE,
    NAME,
    MODULE,
    _levels_for_ticker,
    is_market_hours,
    levels_ready,
)
from universe import get_nse_stocks

import yfinance as yf

POLL_SECONDS = 90

_lock = threading.Lock()
_scan_lock = threading.Lock()
_state = {
    "running": False,
    "scanning": False,
    "last_scan": None,
    "last_error": None,
    "scanned": 0,
    "total": 0,
    "with_data": 0,
    "hits": [],
    "breakout_state": {},   # symbol -> currently in breakout (True/False)
    "baseline_done": False,
    "alert_meta": {},
}


def get_status() -> dict:
    with _lock:
        universe = _state["total"]
        with_data = _state["with_data"]
        return {
            "running": _state["running"],
            "scanning": _state["scanning"],
            "last_scan": _state["last_scan"],
            "last_error": _state["last_error"],
            "scanned": _state["scanned"],
            "total": universe,
            "with_data": with_data,
            "universe_size": universe,
            "hits_count": len(_state["hits"]),
            "market_open": is_market_hours(),
            "levels_ready": levels_ready(),
            "baseline_done": _state["baseline_done"],
            "module": MODULE,
            "name": NAME,
        }


def get_hits() -> list[dict]:
    with _lock:
        return list(_state["hits"])


def _set_scanning(scanning: bool) -> None:
    with _lock:
        _state["scanning"] = scanning


def _clear_hits_for_scan(total: int) -> None:
    """Start a new scan — refresh hit list but keep breakout baseline for alerts."""
    with _lock:
        _state["hits"] = []
        _state["scanned"] = 0
        _state["with_data"] = 0
        _state["total"] = total


def _append_hits(batch_hits: list[dict], scanned: int, with_data: int, total: int) -> None:
    with _lock:
        existing = {h["Symbol"]: h for h in _state["hits"]}
        for h in batch_hits:
            existing[h["Symbol"]] = h
        _state["hits"] = list(existing.values())
        _state["scanned"] = scanned
        _state["with_data"] = with_data
        _state["total"] = total


def _finish_scan(scanned: int, with_data: int, total: int, error: str | None = None) -> None:
    with _lock:
        _state["scanned"] = scanned
        _state["with_data"] = with_data
        _state["total"] = total
        _state["scanning"] = False
        _state["last_scan"] = datetime.now().isoformat(timespec="seconds")
        _state["last_error"] = error


def _send_alert(sym: str, row: dict, meta: dict) -> None:
    price = row["Price"]
    ch = meta.get("_candle_high", price)
    or_h = meta.get("_or_high", "?")
    pd_h = meta.get("_pd_high", "?")
    threading.Thread(
        target=alert,
        args=(
            f"Breakout: {sym}",
            f"{sym} crossed OR high ₹{or_h} & prev-day high ₹{pd_h} — candle high ₹{ch}",
        ),
        kwargs={"sound_seconds": 2.5},
        daemon=True,
    ).start()
    print(f"🔔 Alert: {sym}")


def _process_ticker(row: dict | None) -> bool:
    """Returns True if a new-crossover alert was fired."""
    if row is None:
        return False

    sym = row["Symbol"]
    now_breakout = row["_breakout"]
    fired = False

    with _lock:
        was_breakout = _state["breakout_state"].get(sym, False)
        _state["alert_meta"][sym] = row

        if not _state["baseline_done"]:
            _state["breakout_state"][sym] = now_breakout
            return False

        if now_breakout and not was_breakout:
            meta = row
            fired = True
        _state["breakout_state"][sym] = now_breakout

    if fired:
        _send_alert(sym, {k: v for k, v in row.items() if not k.startswith("_")}, row)

    return fired


def _finish_baseline() -> None:
    with _lock:
        if _state["baseline_done"]:
            return
        _state["baseline_done"] = True
        n = sum(1 for v in _state["breakout_state"].values() if v)
        print(f"📊 Baseline set — monitoring {_state['with_data']} stocks, {n} already broken out (no alerts)")


def scan_universe(tickers: list[str], *, incremental: bool = True) -> list[dict]:
    if not _scan_lock.acquire(blocking=False):
        print("⏳ Scan already in progress — skipping")
        return get_hits()

    try:
        return _scan_universe_locked(tickers, incremental=incremental)
    finally:
        _scan_lock.release()


def _scan_universe_locked(tickers: list[str], *, incremental: bool = True) -> list[dict]:
    total = len(tickers)
    hits: list[dict] = []
    scanned = 0
    with_data = 0
    alerts_fired = 0

    if incremental:
        _clear_hits_for_scan(total)
        _set_scanning(True)

    for start in range(0, total, BATCH_SIZE):
        batch = tickers[start : start + BATCH_SIZE]
        try:
            intra = yf.download(
                batch,
                interval="15m",
                period="5d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
            daily = yf.download(
                batch,
                interval="1d",
                period="10d",
                group_by="ticker",
                threads=True,
                progress=False,
            )
        except Exception as exc:
            print(f"⚠️  Batch {start}-{start + len(batch)} failed: {exc}")
            scanned += len(batch)
            continue

        batch_hits: list[dict] = []
        for ticker in batch:
            scanned += 1
            row = _levels_for_ticker(ticker, intra, daily)
            if row is not None:
                with_data += 1
                if _process_ticker(row):
                    alerts_fired += 1
                if row["_breakout"]:
                    entry = {k: v for k, v in row.items() if not k.startswith("_")}
                    batch_hits.append(entry)
                    hits.append(entry)

        if incremental and (batch_hits or scanned % (BATCH_SIZE * 3) == 0):
            _append_hits(batch_hits, scanned, with_data, total)
            if batch_hits:
                print(f"  … {scanned}/{total} scanned ({with_data} with data), {len(hits)} hits")

    if incremental:
        _finish_baseline()
        _append_hits([], scanned, with_data, total)
        _finish_scan(scanned, with_data, total)
        if alerts_fired:
            print(f"🔔 {alerts_fired} new crossover alert(s) this scan")

    return hits


def reset_baseline() -> None:
    """Call at start of each trading day so first scan re-baselines."""
    with _lock:
        _state["baseline_done"] = False
        _state["breakout_state"] = {}
        _state["alert_meta"] = {}


def _monitor_loop() -> None:
    tickers = get_nse_stocks()
    last_trading_day = None

    with _lock:
        _state["running"] = True
        _state["total"] = len(tickers)

    print(f"🔴 Live monitor started — {len(tickers)} NSE stocks, poll every {POLL_SECONDS}s")

    while True:
        try:
            from screeners.opening_breakout import IST
            today = datetime.now(IST).date()

            if last_trading_day != today:
                reset_baseline()
                last_trading_day = today

            if not is_market_hours():
                _finish_scan(0, 0, len(tickers))
                time_mod.sleep(POLL_SECONDS)
                continue

            if not levels_ready():
                _finish_scan(0, 0, len(tickers))
                time_mod.sleep(30)
                continue

            print(f"🔍 Scanning {len(tickers)} NSE stocks…")
            hits = scan_universe(tickers, incremental=True)
            status = get_status()
            print(
                f"✅ Done — {status['with_data']}/{status['total']} stocks with data, "
                f"{len(hits)} breakouts"
            )

        except Exception as exc:
            _finish_scan(
                _state.get("scanned", 0),
                _state.get("with_data", 0),
                _state.get("total", 0),
                str(exc),
            )
            print(f"⚠️  Live monitor error: {exc}")

        time_mod.sleep(POLL_SECONDS)


_started = False


def start_live_monitor() -> None:
    global _started
    if _started:
        return
    _started = True
    thread = threading.Thread(target=_monitor_loop, daemon=True, name="live-monitor")
    thread.start()
