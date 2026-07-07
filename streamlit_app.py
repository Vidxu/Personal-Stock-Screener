"""Streamlit Cloud entry — serves the same index.html UI as the Flask app."""

from __future__ import annotations

import importlib
import json
import os
import time
from datetime import timedelta
from typing import Any

import streamlit as st

from ui import get_screener_list, render_dashboard_html
from universe import get_nifty500_stocks

MONITOR_INTERVAL_SEC = 30
TICKER_KEYS = ("symbol", "ticker", "Symbol", "Ticker", "SYMBOL", "Stock")


def _inject_streamlit_secrets() -> None:
    """Map Streamlit Cloud secrets into os.environ for market_data.py."""
    try:
        for key in (
            "UPSTOX_ACCESS_TOKEN",
            "UPSTOX_API_KEY",
            "UPSTOX_API_SECRET",
            "UPSTOX_REDIRECT_URI",
        ):
            if key in st.secrets:
                os.environ[key] = str(st.secrets[key])
    except Exception:
        pass


def _symbol_from_row(row: dict[str, Any]) -> str:
    for key in TICKER_KEYS:
        val = row.get(key)
        if val is not None and val != "":
            return str(val)
    if row:
        return str(next(iter(row.values())))
    return ""


def _symbols_from_results(results: list[dict[str, Any]]) -> set[str]:
    return {sym for row in results if (sym := _symbol_from_row(row))}


def _alert_body(row: dict[str, Any], screener_name: str) -> str:
    price = row.get("Price", row.get("price"))
    change = row.get("% change", row.get("change", ""))
    parts: list[str] = []
    if price is not None:
        parts.append(f"₹{price}")
    if change:
        parts.append(str(change))
    return " · ".join(parts) or screener_name


def _run_scan(module_name: str) -> dict:
    module = importlib.import_module(f"screeners.{module_name}")
    tickers = get_nifty500_stocks()
    results = module.run(tickers)

    return {
        "name": module.NAME,
        "results": results,
    }


def _init_session_state() -> None:
    defaults: dict[str, Any] = {
        "scan_cache": {},
        "active_module": None,
        "monitor_module": None,
        "monitor_known_symbols": set(),
        "monitor_baseline_done": False,
        "monitor_cycle": 0,
        "monitor_last_updated": None,
        "pending_alerts": [],
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def _stop_monitoring() -> None:
    st.session_state.monitor_module = None
    st.session_state.monitor_known_symbols = set()
    st.session_state.monitor_baseline_done = False
    st.session_state.monitor_cycle = 0
    st.session_state.monitor_last_updated = None


def _monitor_scan(module: str) -> list[dict[str, str]]:
    """Run one monitor cycle; return alert payloads for newly matched symbols."""
    new_alerts: list[dict[str, str]] = []
    try:
        result = _run_scan(module)
        st.session_state.scan_cache[module] = result
    except Exception as exc:
        st.session_state.scan_cache[module] = {
            "name": module,
            "results": [],
            "error": str(exc),
        }
        st.session_state.monitor_last_updated = time.time()
        return new_alerts

    results = result.get("results") or []
    current = _symbols_from_results(results)
    known = st.session_state.monitor_known_symbols
    screener_name = str(result.get("name", module))

    if st.session_state.monitor_baseline_done:
        for row in results:
            sym = _symbol_from_row(row)
            if sym and sym not in known:
                new_alerts.append(
                    {
                        "symbol": sym,
                        "screener_name": screener_name,
                        "body": _alert_body(row, screener_name),
                    }
                )
    else:
        st.session_state.monitor_baseline_done = True

    st.session_state.monitor_known_symbols = current
    st.session_state.monitor_last_updated = time.time()
    return new_alerts


def _render_alert_bridge(alerts: list[dict[str, str]], *, monitoring: bool) -> None:
    """Top-level alert bridge — desktop notifications work here (not in the dashboard iframe)."""
    if not monitoring and not alerts:
        return

    alerts_json = json.dumps(alerts)
    height: int | str = 44 if monitoring else "content"
    st.iframe(
        f"""
<!DOCTYPE html>
<html><head><meta charset="utf-8"></head><body style="margin:0;font-family:system-ui,sans-serif;">
<div id="bar" style="display:{'flex' if monitoring else 'none'};align-items:center;gap:10px;padding:6px 12px;
  background:#12151c;border-bottom:1px solid #1e2430;font-size:13px;color:#9aa3b2;">
  <span>Monitoring active — keep this tab open.</span>
  <button id="enable-alerts" type="button" style="margin-left:auto;padding:4px 10px;border-radius:6px;
    border:1px solid #2dd4a8;background:#0d2820;color:#2dd4a8;cursor:pointer;font-size:12px;">
    Enable desktop alerts
  </button>
</div>
<script>
(function() {{
  const alerts = {alerts_json};
  let audioCtx = null;

  function getAudioContext() {{
    if (!audioCtx) {{
      const Ctx = window.AudioContext || window.webkitAudioContext;
      if (Ctx) audioCtx = new Ctx();
    }}
    return audioCtx;
  }}

  function playAlertSound() {{
    const ctx = getAudioContext();
    if (!ctx) return;
    if (ctx.state === 'suspended') ctx.resume();
    const now = ctx.currentTime;
    [
      {{ freq: 880, start: 0, dur: 0.14 }},
      {{ freq: 1100, start: 0.16, dur: 0.14 }},
      {{ freq: 1320, start: 0.32, dur: 0.22 }},
    ].forEach(({{ freq, start, dur }}) => {{
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.type = 'sine';
      osc.frequency.value = freq;
      gain.gain.setValueAtTime(0.0001, now + start);
      gain.gain.exponentialRampToValueAtTime(0.28, now + start + 0.02);
      gain.gain.exponentialRampToValueAtTime(0.0001, now + start + dur);
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.start(now + start);
      osc.stop(now + start + dur + 0.02);
    }});
  }}

  async function requestNotificationPermission() {{
    if (!('Notification' in window)) return false;
    if (Notification.permission === 'granted') return true;
    if (Notification.permission === 'denied') return false;
    const perm = await Notification.requestPermission();
    return perm === 'granted';
  }}

  function showStockAlert(alert) {{
    playAlertSound();
    if ('Notification' in window && Notification.permission === 'granted') {{
      try {{
        const n = new Notification('Alert: ' + alert.symbol, {{
          body: alert.body || alert.screener_name,
          tag: 'screener-' + alert.symbol,
        }});
        n.onclick = () => {{ window.focus(); n.close(); }};
      }} catch (_) {{}}
    }}
  }}

  const enableBtn = document.getElementById('enable-alerts');
  if (enableBtn) {{
    enableBtn.addEventListener('click', async () => {{
      getAudioContext();
      const ok = await requestNotificationPermission();
      enableBtn.textContent = ok ? 'Alerts enabled' : 'Alerts blocked';
      enableBtn.disabled = ok || Notification.permission === 'denied';
    }});
    if ('Notification' in window && Notification.permission === 'granted') {{
      enableBtn.textContent = 'Alerts enabled';
      enableBtn.disabled = true;
    }} else if ('Notification' in window && Notification.permission === 'denied') {{
      enableBtn.textContent = 'Alerts blocked';
      enableBtn.disabled = true;
    }}
  }}

  alerts.forEach(showStockAlert);
}})();
</script>
</body></html>
        """,
        height=height,
    )


@st.fragment(run_every=timedelta(seconds=MONITOR_INTERVAL_SEC))
def _monitor_tick(valid_modules: set[str]) -> None:
    module = st.session_state.get("monitor_module")
    if not module or module not in valid_modules:
        return

    st.session_state.monitor_cycle = int(st.session_state.get("monitor_cycle", 0)) + 1
    new_alerts = _monitor_scan(module)
    if new_alerts:
        st.session_state.pending_alerts = st.session_state.get("pending_alerts", []) + new_alerts
        for alert in new_alerts:
            st.toast(f"🔔 {alert['symbol']}: {alert['body']}")
    st.rerun()


def run_streamlit_ui() -> None:
    _inject_streamlit_secrets()
    _init_session_state()

    st.set_page_config(
        page_title="Stock Screener",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="collapsed",
    )

    st.markdown(
        """
        <style>
          #MainMenu, header, footer, [data-testid="stToolbar"],
          [data-testid="stDecoration"], [data-testid="stStatusWidget"] {
            display: none !important;
          }
          .block-container {
            padding: 0 !important;
            max-width: 100% !important;
          }
          iframe { border: none !important; }
        </style>
        """,
        unsafe_allow_html=True,
    )

    valid_modules = {s["module"] for s in get_screener_list()}

    if st.query_params.get("stop_monitor") == "1":
        _stop_monitoring()
        st.query_params.clear()
        st.rerun()

    monitor_start = st.query_params.get("monitor")
    if monitor_start and monitor_start in valid_modules:
        st.session_state.monitor_module = monitor_start
        st.session_state.active_module = monitor_start
        st.session_state.monitor_known_symbols = set()
        st.session_state.monitor_baseline_done = False
        st.session_state.monitor_cycle = 0
        st.query_params.clear()
        with st.spinner(f"Starting monitor · scanning {len(get_nifty500_stocks())} stocks…"):
            _monitor_scan(monitor_start)
            st.session_state.monitor_cycle = 1
        st.rerun()

    run_module = st.query_params.get("run")
    if run_module and run_module in valid_modules:
        if st.session_state.get("monitor_module"):
            _stop_monitoring()
        with st.spinner(f"Scanning {len(get_nifty500_stocks())} stocks…"):
            try:
                st.session_state.scan_cache[run_module] = _run_scan(run_module)
                st.session_state.active_module = run_module
            except Exception as exc:
                st.session_state.scan_cache[run_module] = {
                    "name": run_module,
                    "results": [],
                    "error": str(exc),
                }
        st.query_params.clear()
        st.rerun()

    pending_alerts = st.session_state.pop("pending_alerts", [])
    monitor_module = st.session_state.get("monitor_module")
    _render_alert_bridge(pending_alerts, monitoring=bool(monitor_module))

    html = render_dashboard_html(
        streamlit_mode=True,
        scan_cache=st.session_state.scan_cache,
        active_module=st.session_state.get("active_module"),
        monitor_module=monitor_module,
        monitor_cycle=st.session_state.get("monitor_cycle", 0),
        monitor_last_updated=st.session_state.get("monitor_last_updated"),
    )
    st.iframe(html, height=920)

    if monitor_module:
        _monitor_tick(valid_modules)


if __name__ == "__main__":
    run_streamlit_ui()
