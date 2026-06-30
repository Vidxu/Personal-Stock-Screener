"""Streamlit Cloud entry — serves the same index.html UI as the Flask app."""

from __future__ import annotations

import importlib
import os
from datetime import timedelta

import streamlit as st
import streamlit.components.v1 as components

from app import LIVE_MODULES, _live_alerts_enabled
from live_registry import get_hits, get_status, start_live_monitors, stop_monitor
from runtime_env import is_streamlit_runtime
from ui import get_screener_list, render_dashboard_html
from universe import get_nifty500_stocks

LIVE_REFRESH_SECONDS = 10


def _inject_streamlit_secrets() -> None:
    """Map Streamlit Cloud secrets into os.environ for market_data.py."""
    try:
        for key in (
            "UPSTOX_ACCESS_TOKEN",
            "UPSTOX_API_KEY",
            "UPSTOX_API_SECRET",
            "UPSTOX_REDIRECT_URI",
            "ENABLE_LIVE_ALERTS",
            "TELEGRAM_BOT_TOKEN",
            "TELEGRAM_CHAT_ID",
        ):
            if key in st.secrets:
                os.environ[key] = str(st.secrets[key])
    except Exception:
        pass


def _ensure_live_monitor() -> None:
    if not _live_alerts_enabled():
        return
    if st.session_state.get("live_monitor_started"):
        return
    start_live_monitors(sorted(LIVE_MODULES))
    st.session_state.live_monitor_started = True


def _sync_live_state() -> dict:
    """Refresh monitor status and hits for the embedded dashboard."""
    state: dict = {}
    for module in LIVE_MODULES:
        state[module] = {
            "status": get_status(module),
            "hits": get_hits(module),
        }

    st.session_state.live_state = state
    return state


def _run_scan(module_name: str) -> dict:
    from market_data import UpstoxConfigError

    module = importlib.import_module(f"screeners.{module_name}")
    tickers = get_nifty500_stocks()
    results = module.run(tickers)

    if module_name in LIVE_MODULES and _live_alerts_enabled():
        from live_registry import get_monitor, start_monitor

        mon = get_monitor(module_name)
        start_monitor(module_name)
        mon.seed_hits(results, total=len(tickers))

    return {
        "name": module.NAME,
        "results": results,
        "live": module_name in LIVE_MODULES and _live_alerts_enabled(),
    }


@st.fragment(run_every=timedelta(seconds=LIVE_REFRESH_SECONDS))
def _live_refresh_tick() -> None:
    if not _live_alerts_enabled():
        return
    _sync_live_state()
    st.rerun()


def run_streamlit_ui() -> None:
    _inject_streamlit_secrets()
    _ensure_live_monitor()

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

    if "scan_cache" not in st.session_state:
        st.session_state.scan_cache = {}

    valid_modules = {s["module"] for s in get_screener_list()}

    stop_module = st.query_params.get("stop")
    if stop_module and stop_module in valid_modules and stop_module in LIVE_MODULES:
        stop_monitor(stop_module)
        st.query_params.clear()
        st.rerun()

    run_module = st.query_params.get("run")
    if run_module and run_module in valid_modules:
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

    live_state = st.session_state.get("live_state") or {}
    if _live_alerts_enabled():
        live_state = _sync_live_state()

    if _live_alerts_enabled() and is_streamlit_runtime():
        st.caption(
            f"Live OR monitoring · refreshes every {LIVE_REFRESH_SECONDS}s · "
            "alerts via Telegram"
        )

    html = render_dashboard_html(
        streamlit_mode=True,
        scan_cache=st.session_state.scan_cache,
        active_module=st.session_state.get("active_module"),
        live_state=live_state if _live_alerts_enabled() else None,
    )
    components.html(html, height=920, scrolling=True)

    if _live_alerts_enabled():
        _live_refresh_tick()


if __name__ == "__main__":
    run_streamlit_ui()
