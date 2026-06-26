"""Streamlit Cloud entry — serves the same index.html UI as the Flask app."""

from __future__ import annotations

import importlib

import streamlit as st
import streamlit.components.v1 as components

from ui import get_screener_list, render_dashboard_html
from universe import get_nifty500_stocks


def _run_scan(module_name: str) -> dict:
    module = importlib.import_module(f"screeners.{module_name}")
    tickers = get_nifty500_stocks()
    results = module.run(tickers)
    return {
        "name": module.NAME,
        "results": results,
        "live": module_name in {"opening_breakout"},
    }


def run_streamlit_ui() -> None:
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
                    "live": False,
                }
        st.query_params.clear()
        st.rerun()

    html = render_dashboard_html(
        streamlit_mode=True,
        scan_cache=st.session_state.scan_cache,
        active_module=st.session_state.get("active_module"),
    )
    components.html(html, height=920, scrolling=True)


if __name__ == "__main__":
    run_streamlit_ui()
