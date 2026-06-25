"""
Unified live monitor — one engine, alert-first polling.

  • Full scan (~3 min): refresh level/setup cache for all NSE stocks
  • Alert poll (~6 s): 1m price check on hot + rotating cold symbols → fast desktop alerts
  • Listing refresh (~90 s): slower hit-list update for the UI (alerts are independent)
"""

from __future__ import annotations

import importlib
import threading
import time as time_mod
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

import yfinance as yf

from alert_notify import alert
from universe import get_nifty500_stocks

ALERT_POLL_SECONDS = 6
LISTING_FAST_SECONDS = 90
FULL_SCAN_SECONDS = 180
HOT_PROXIMITY = 0.97
COLD_SLICE_SIZE = 350
ALERT_BATCH_SIZE = 60
ALERT_WORKERS = 4
FULL_BATCH_SIZE = 100
FULL_WORKERS = 6

_registry: dict[str, "MonitorState"] = {}
_engine: "UnifiedEngine | None" = None
_started = False


class MonitorState:
    """Per-screener hits, alerts, and status."""

    def __init__(self, module_name: str):
        self.module_name = module_name
        self.module = importlib.import_module(f"screeners.{module_name}")
        self._lock = threading.Lock()
        self._state = {
            "running": False,
            "stopped": False,
            "scanning": False,
            "scan_mode": "idle",
            "last_scan": None,
            "last_fast_scan": None,
            "last_alert_poll": None,
            "last_error": None,
            "scanned": 0,
            "total": 0,
            "with_data": 0,
            "hits": [],
            "trigger_state": {},
            "baseline_done": False,
            "alert_meta": {},
        }

    def get_status(self) -> dict:
        with self._lock:
            return {
                "running": self._state["running"],
                "stopped": self._state.get("stopped", False),
                "scanning": self._state["scanning"],
                "scan_mode": self._state.get("scan_mode", "idle"),
                "last_scan": self._state["last_scan"],
                "last_fast_scan": self._state.get("last_fast_scan"),
                "last_alert_poll": self._state.get("last_alert_poll"),
                "last_error": self._state["last_error"],
                "scanned": self._state["scanned"],
                "total": self._state["total"],
                "with_data": self._state["with_data"],
                "universe_size": self._state["total"],
                "hits_count": len(self._state["hits"]),
                "market_open": self.module.is_market_hours(),
                "levels_ready": self.module.levels_ready(),
                "baseline_done": self._state["baseline_done"],
                "alert_poll_seconds": ALERT_POLL_SECONDS,
                "module": self.module_name,
                "name": self.module.NAME,
            }

    def _display_row(self, row: dict) -> dict:
        return {k: v for k, v in row.items() if not k.startswith("_")}

    def upsert_hit(self, row: dict) -> None:
        display = self._display_row(row)
        sym = display.get("Symbol")
        if not sym:
            return
        with self._lock:
            merged = {h["Symbol"]: h for h in self._state["hits"]}
            merged[sym] = display
            self._state["hits"] = list(merged.values())

    def remove_hit(self, sym: str) -> None:
        with self._lock:
            self._state["hits"] = [h for h in self._state["hits"] if h.get("Symbol") != sym]

    def sync_hits_from_triggers(self) -> None:
        """Ensure actively triggered symbols stay visible in the hit list."""
        with self._lock:
            merged = {h["Symbol"]: h for h in self._state["hits"]}
            for sym, active in self._state["trigger_state"].items():
                if not active:
                    continue
                meta = self._state["alert_meta"].get(sym)
                if meta:
                    merged[sym] = self._display_row(meta)
            self._state["hits"] = list(merged.values())

    def get_hits(self) -> list[dict]:
        with self._lock:
            return list(self._state["hits"])

    def _send_alert(self, sym: str, row: dict, meta: dict) -> None:
        if self.module_name == "opening_breakout":
            msg = (
                f"{sym} crossed OR high ₹{meta.get('_or_high', '?')} "
                f"& prev-day high ₹{meta.get('_pd_high', '?')}"
            )
            title = f"Breakout: {sym}"
        else:
            msg = (
                f"{sym} crossed inside-body high ₹{meta.get('_ib_high', '?')} "
                f"(RSI div setup {meta.get('_setup_date', '')})"
            )
            title = f"Divergence Entry: {sym}"

        threading.Thread(
            target=alert, args=(title, msg), kwargs={"sound_seconds": 2.5}, daemon=True
        ).start()
        print(f"🔔 [{self.module_name}] Alert: {sym}")

    def process_row(self, row: dict | None) -> bool:
        if row is None:
            return False
        sym = row["Symbol"]
        key = "_breakout" if "_breakout" in row else "_triggered"
        now_active = bool(row.get(key, False))
        fired = False

        with self._lock:
            was = self._state["trigger_state"].get(sym, False)
            self._state["alert_meta"][sym] = row
            if not self._state["baseline_done"]:
                self._state["trigger_state"][sym] = now_active
                if now_active:
                    self._state["hits"] = list(
                        {**{h["Symbol"]: h for h in self._state["hits"]}, sym: self._display_row(row)}.values()
                    )
                return False
            if now_active and not was:
                fired = True
            self._state["trigger_state"][sym] = now_active

        if now_active:
            self.upsert_hit(row)
        elif was and not now_active:
            self.remove_hit(sym)

        if fired:
            self._send_alert(sym, self._display_row(row), row)
        return fired

    def begin_scan(self, total: int, mode: str) -> None:
        with self._lock:
            self._state["scanning"] = True
            self._state["scan_mode"] = mode
            self._state["total"] = total

    def end_scan(self, scanned: int, with_data: int, *, mode: str = "full") -> None:
        with self._lock:
            if mode == "full" and not self._state["baseline_done"]:
                self._state["baseline_done"] = True
                print(f"📊 [{self.module_name}] Baseline set")
            self._state["scanned"] = scanned
            self._state["with_data"] = with_data
            self._state["scanning"] = False

    def reset_baseline(self) -> None:
        with self._lock:
            self._state["baseline_done"] = False
            self._state["trigger_state"] = {}
            self._state["alert_meta"] = {}

    def set_running(self) -> None:
        with self._lock:
            self._state["running"] = True

    def stop(self) -> None:
        with self._lock:
            self._state["stopped"] = True
            self._state["scanning"] = False

    def start(self) -> None:
        with self._lock:
            self._state["stopped"] = False

    def is_active(self) -> bool:
        """Listing/UI scans paused — alerts are unaffected."""
        with self._lock:
            return not self._state.get("stopped", False)


class UnifiedEngine:
    def __init__(self, module_names: list[str]):
        self.module_names = module_names
        self.monitors = {m: MonitorState(m) for m in module_names}
        self.ob_cache: dict[str, dict] = {}
        self.pd_cache: dict[str, dict] = {}
        self._lock = threading.Lock()
        self._scan_lock = threading.Lock()
        self._last_full = 0.0
        self._last_listing = 0.0
        self._cold_offset = 0
        self._cancel_scan: set[str] = set()

    def request_cancel(self, module_name: str) -> None:
        self._cancel_scan.add(module_name)

    def clear_cancel(self, module_name: str) -> None:
        self._cancel_scan.discard(module_name)

    def _listing_active(self, module: str) -> bool:
        if module not in self.monitors:
            return False
        return self.monitors[module].is_active()

    def _any_listing_active(self) -> bool:
        return any(self._listing_active(m) for m in self.module_names)

    def _scan_aborted(self) -> bool:
        return bool(self._cancel_scan)

    def _chunks(self, tickers: list[str], size: int) -> list[list[str]]:
        return [tickers[i : i + size] for i in range(0, len(tickers), size)]

    def _full_batch(self, batch: list[str]) -> tuple[list[dict], list[dict]]:
        from screeners.opening_breakout import (
            _extract_ticker_df,
            _levels_for_ticker,
            _session_high_close,
            build_level_cache,
            levels_ready as ob_ready,
            stamp_cache_prices,
        )
        from screeners.positive_divergence import build_setup_cache, evaluate_ticker

        ob_hits: list[dict] = []
        pd_hits: list[dict] = []

        try:
            with ThreadPoolExecutor(max_workers=2) as dl:
                f_intra = dl.submit(
                    yf.download,
                    batch, interval="15m", period="5d",
                    group_by="ticker", threads=True, progress=False,
                )
                f_daily = dl.submit(
                    yf.download,
                    batch, interval="1d", period="90d",
                    group_by="ticker", threads=True, progress=False,
                )
                intra = f_intra.result()
                daily = f_daily.result()
        except Exception as exc:
            print(f"⚠️  full batch download: {exc}")
            return ob_hits, pd_hits

        for ticker in batch:
            ddf = _extract_ticker_df(daily, ticker)
            idf = _extract_ticker_df(intra, ticker)

            if "opening_breakout" in self.monitors and ob_ready():
                cache = build_level_cache(ticker, intra, daily)
                if cache:
                    if idf is not None:
                        sh, lp = _session_high_close(idf)
                        stamp_cache_prices(cache, sh, lp)
                    with self._lock:
                        self.ob_cache[cache["Symbol"]] = cache
                row = _levels_for_ticker(ticker, intra, daily)
                if row:
                    self.monitors["opening_breakout"].process_row(row)
                    if self._listing_active("opening_breakout") and row.get("_breakout"):
                        ob_hits.append({k: v for k, v in row.items() if not k.startswith("_")})

            if "positive_divergence" in self.monitors:
                sc = build_setup_cache(ticker, ddf)
                sym = ticker.replace(".NS", "").replace(".BO", "")
                with self._lock:
                    if sc:
                        self.pd_cache[sym] = sc
                    elif sym in self.pd_cache:
                        del self.pd_cache[sym]
                row = evaluate_ticker(ticker, ddf, idf)
                if row:
                    if row.get("_in_entry_window"):
                        self.monitors["positive_divergence"].process_row(row)
                    if self._listing_active("positive_divergence"):
                        pd_hits.append({k: v for k, v in row.items() if not k.startswith("_")})

        return ob_hits, pd_hits

    def _price_batch(self, batch: list[str], *, alerts_only: bool) -> tuple[list[dict], list[dict]]:
        from screeners.opening_breakout import (
            levels_ready as ob_ready,
            row_from_cache,
            stamp_cache_prices,
        )
        from screeners.positive_divergence import row_from_setup

        ob_hits: list[dict] = []
        pd_hits: list[dict] = []

        try:
            intra = yf.download(
                batch, interval="1m", period="1d",
                group_by="ticker", threads=True, progress=False,
            )
        except Exception:
            return ob_hits, pd_hits

        for ticker in batch:
            sym = ticker.replace(".NS", "").replace(".BO", "")

            if "opening_breakout" in self.monitors and ob_ready():
                with self._lock:
                    cache = self.ob_cache.get(sym)
                if cache:
                    row = row_from_cache(ticker, cache, intra, use_session_high=True)
                    if row:
                        self.monitors["opening_breakout"].process_row(row)
                        stamp_cache_prices(
                            cache,
                            row.get("_candle_high"),
                            row.get("Price"),
                        )
                        if not alerts_only and self._listing_active("opening_breakout") and row.get("_breakout"):
                            ob_hits.append({k: v for k, v in row.items() if not k.startswith("_")})

            if "positive_divergence" in self.monitors:
                with self._lock:
                    sc = self.pd_cache.get(sym)
                if sc:
                    row = row_from_setup(ticker, sc, intra)
                    if row:
                        if row.get("_in_entry_window"):
                            self.monitors["positive_divergence"].process_row(row)
                        if not alerts_only and self._listing_active("positive_divergence"):
                            pd_hits.append({k: v for k, v in row.items() if not k.startswith("_")})

        return ob_hits, pd_hits

    def _merge_hits(self, acc: dict[str, dict], hits: list[dict]) -> None:
        for h in hits:
            acc[h["Symbol"]] = h

    def _set_module_hits(self, module: str, hits: list[dict], scanned: int, with_data: int, total: int, mode: str) -> None:
        mon = self.monitors[module]
        with mon._lock:
            merged = {h["Symbol"]: h for h in hits}
            for sym, active in mon._state["trigger_state"].items():
                if not active:
                    continue
                meta = mon._state["alert_meta"].get(sym)
                if meta:
                    merged[sym] = mon._display_row(meta)
            mon._state["hits"] = list(merged.values())
            mon._state["scanned"] = scanned
            mon._state["with_data"] = with_data
            mon._state["total"] = total
            mon._state["scan_mode"] = mode
            if mode == "full":
                mon._state["last_scan"] = datetime.now().isoformat(timespec="seconds")
            elif mode == "fast":
                mon._state["last_fast_scan"] = datetime.now().isoformat(timespec="seconds")
            elif mode == "alert":
                mon._state["last_alert_poll"] = datetime.now().isoformat(timespec="seconds")

    def _alert_symbols(self) -> list[str]:
        from screeners.opening_breakout import is_near_breakout, levels_ready as ob_ready

        chosen: set[str] = set()

        with self._lock:
            if "positive_divergence" in self.monitors:
                chosen.update(self.pd_cache)

            if "opening_breakout" in self.monitors and ob_ready():
                cold: list[str] = []
                for sym, cache in self.ob_cache.items():
                    if is_near_breakout(cache, HOT_PROXIMITY):
                        chosen.add(sym)
                    else:
                        cold.append(sym)

                if cold:
                    cold.sort()
                    start = self._cold_offset % len(cold)
                    for i in range(min(COLD_SLICE_SIZE, len(cold))):
                        chosen.add(cold[(start + i) % len(cold)])
                    self._cold_offset = (start + COLD_SLICE_SIZE) % len(cold)

        return [f"{s}.NS" for s in chosen]

    def alert_poll(self) -> int:
        """Fast price check for desktop alerts; also upserts active symbols into the hit list."""
        tickers = self._alert_symbols()
        if not tickers:
            return 0

        batches = self._chunks(tickers, ALERT_BATCH_SIZE)
        workers = min(ALERT_WORKERS, len(batches))

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._price_batch, b, alerts_only=True) for b in batches]
            for fut in as_completed(futures):
                try:
                    fut.result()
                except Exception as exc:
                    print(f"⚠️  alert batch: {exc}")

        for m in self.monitors.values():
            m.sync_hits_from_triggers()
            with m._lock:
                if not m._state.get("stopped"):
                    m._state["scan_mode"] = "alert"
                m._state["last_alert_poll"] = datetime.now().isoformat(timespec="seconds")

        return len(tickers)

    def full_scan(self, tickers: list[str], *, incremental: bool = True) -> dict[str, list[dict]]:
        if not self._scan_lock.acquire(blocking=False):
            return {m: self.monitors[m].get_hits() for m in self.module_names}

        total = len(tickers)
        t0 = time_mod.time()
        all_ob: dict[str, dict] = {}
        all_pd: dict[str, dict] = {}
        aborted = False

        try:
            if incremental:
                for m in self.module_names:
                    if self._listing_active(m):
                        self.monitors[m].begin_scan(total, "full")

            batches = self._chunks(tickers, FULL_BATCH_SIZE)
            scanned = 0
            workers = min(FULL_WORKERS, len(batches))

            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = {pool.submit(self._full_batch, b): b for b in batches}
                for fut in as_completed(futures):
                    if self._scan_aborted():
                        aborted = True
                        break
                    ob_hits, pd_hits = fut.result()
                    if self._listing_active("opening_breakout"):
                        self._merge_hits(all_ob, ob_hits)
                    if self._listing_active("positive_divergence"):
                        self._merge_hits(all_pd, pd_hits)
                    scanned += len(futures[fut])
                    if incremental:
                        if "opening_breakout" in self.monitors and self._listing_active("opening_breakout"):
                            self._set_module_hits(
                                "opening_breakout", list(all_ob.values()), scanned,
                                len(self.ob_cache), total, "full",
                            )
                        if "positive_divergence" in self.monitors and self._listing_active("positive_divergence"):
                            self._set_module_hits(
                                "positive_divergence", list(all_pd.values()), scanned,
                                len(self.pd_cache), total, "full",
                            )

            for m in self.module_names:
                if not self._listing_active(m):
                    with self.monitors[m]._lock:
                        self.monitors[m]._state["scanning"] = False
                    continue
                wd = len(self.ob_cache if m == "opening_breakout" else self.pd_cache)
                self.monitors[m].end_scan(total, wd, mode="full")

            if "opening_breakout" in self.monitors and self._listing_active("opening_breakout"):
                self._set_module_hits("opening_breakout", list(all_ob.values()), total, len(self.ob_cache), total, "full")
            if "positive_divergence" in self.monitors and self._listing_active("positive_divergence"):
                self._set_module_hits("positive_divergence", list(all_pd.values()), total, len(self.pd_cache), total, "full")

            if not aborted:
                self._last_full = time_mod.time()
            print(
                f"{'⏹' if aborted else '✅'} Full scan {total} stocks in {time_mod.time() - t0:.0f}s — "
                f"OB:{len(all_ob)} PD:{len(all_pd)}"
            )
        finally:
            self._scan_lock.release()

        return {
            "opening_breakout": list(all_ob.values()) if "opening_breakout" in self.monitors else [],
            "positive_divergence": list(all_pd.values()) if "positive_divergence" in self.monitors else [],
        }

    def listing_refresh(self, tickers: list[str]) -> None:
        """Slower hit-list update for the UI — scans full Nifty 500 universe."""
        if not self._any_listing_active():
            return

        total = len(tickers)
        all_ob: dict[str, dict] = {}
        all_pd: dict[str, dict] = {}

        for m in self.module_names:
            if self._listing_active(m):
                self.monitors[m].begin_scan(total, "fast")

        batches = self._chunks(tickers, FULL_BATCH_SIZE)
        workers = min(FULL_WORKERS, len(batches))
        scanned = 0

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._full_batch, b): b for b in batches}
            for fut in as_completed(futures):
                if self._scan_aborted():
                    break
                ob_hits, pd_hits = fut.result()
                if self._listing_active("opening_breakout"):
                    self._merge_hits(all_ob, ob_hits)
                if self._listing_active("positive_divergence"):
                    self._merge_hits(all_pd, pd_hits)
                scanned += len(futures[fut])
                if self._listing_active("opening_breakout"):
                    self._set_module_hits(
                        "opening_breakout", list(all_ob.values()), scanned,
                        len(self.ob_cache), total, "fast",
                    )
                if self._listing_active("positive_divergence"):
                    self._set_module_hits(
                        "positive_divergence", list(all_pd.values()), scanned,
                        len(self.pd_cache), total, "fast",
                    )

        if "opening_breakout" in self.monitors and self._listing_active("opening_breakout"):
            self._set_module_hits(
                "opening_breakout", list(all_ob.values()), total,
                len(self.ob_cache), total, "fast",
            )
            self.monitors["opening_breakout"].end_scan(total, len(self.ob_cache), mode="fast")
        elif "opening_breakout" in self.monitors:
            with self.monitors["opening_breakout"]._lock:
                self.monitors["opening_breakout"]._state["scanning"] = False

        if "positive_divergence" in self.monitors and self._listing_active("positive_divergence"):
            self._set_module_hits(
                "positive_divergence", list(all_pd.values()), total,
                len(self.pd_cache), total, "fast",
            )
            self.monitors["positive_divergence"].end_scan(total, len(self.pd_cache), mode="fast")
        elif "positive_divergence" in self.monitors:
            with self.monitors["positive_divergence"]._lock:
                self.monitors["positive_divergence"]._state["scanning"] = False

    def monitor_loop(self) -> None:
        from screeners.opening_breakout import IST, is_market_hours, levels_ready

        tickers = get_nifty500_stocks()
        last_day = None

        for m in self.monitors.values():
            m.set_running()
            with m._lock:
                m._state["total"] = len(tickers)

        print(
            f"🚀 Unified monitor — {len(tickers)} stocks | "
            f"alerts every {ALERT_POLL_SECONDS}s | listing every {LISTING_FAST_SECONDS}s | "
            f"full every {FULL_SCAN_SECONDS}s"
        )

        while True:
            loop_start = time_mod.time()
            try:
                today = datetime.now(IST).date()
                if last_day != today:
                    for m in self.monitors.values():
                        m.reset_baseline()
                    with self._lock:
                        self.ob_cache.clear()
                        self.pd_cache.clear()
                    self._last_full = 0
                    self._last_listing = 0
                    self._cold_offset = 0
                    last_day = today

                if not is_market_hours():
                    time_mod.sleep(ALERT_POLL_SECONDS)
                    continue

                if not levels_ready():
                    time_mod.sleep(15)
                    continue

                now = time_mod.time()
                need_full = now - self._last_full >= FULL_SCAN_SECONDS
                if not self.ob_cache and "opening_breakout" in self.monitors:
                    need_full = True
                if need_full:
                    self.full_scan(tickers, incremental=True)
                else:
                    self.alert_poll()
                    if self._any_listing_active() and now - self._last_listing >= LISTING_FAST_SECONDS:
                        self.listing_refresh(tickers)
                        self._last_listing = time_mod.time()

            except Exception as exc:
                for m in self.monitors.values():
                    with m._lock:
                        m._state["last_error"] = str(exc)
                print(f"⚠️  engine error: {exc}")

            elapsed = time_mod.time() - loop_start
            time_mod.sleep(max(1.0, ALERT_POLL_SECONDS - elapsed))


def register(module_name: str) -> MonitorState:
    if module_name not in _registry:
        if _engine and module_name in _engine.monitors:
            _registry[module_name] = _engine.monitors[module_name]
        else:
            _registry[module_name] = MonitorState(module_name)
    return _registry[module_name]


def get_monitor(module_name: str) -> MonitorState:
    return register(module_name)


def get_status(module_name: str) -> dict:
    return get_monitor(module_name).get_status()


def get_hits(module_name: str) -> list[dict]:
    return get_monitor(module_name).get_hits()


def scan_universe(module_name: str, tickers: list[str], *, incremental: bool = True) -> list[dict]:
    global _engine
    if _engine is None:
        _engine = UnifiedEngine([module_name])
    start_monitor(module_name)
    results = _engine.full_scan(tickers, incremental=incremental)
    return results.get(module_name, [])


def stop_monitor(module_name: str) -> dict:
    mon = get_monitor(module_name)
    mon.stop()
    if _engine:
        _engine.request_cancel(module_name)
    print(f"⏹ [{module_name}] Listing paused (alerts still on)")
    return mon.get_status()


def start_monitor(module_name: str) -> dict:
    mon = get_monitor(module_name)
    mon.start()
    if _engine:
        _engine.clear_cancel(module_name)
    print(f"▶ [{module_name}] Listing resumed")
    return mon.get_status()


def start_live_monitors(module_names: list[str]) -> None:
    global _started, _engine
    if _started:
        return
    _started = True
    _engine = UnifiedEngine(module_names)
    for name in module_names:
        _registry[name] = _engine.monitors[name]
    threading.Thread(target=_engine.monitor_loop, daemon=True, name="unified-live").start()


def start_live_monitor() -> None:
    start_live_monitors(["opening_breakout"])
