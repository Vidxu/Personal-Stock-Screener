"""Streamlit Cloud UI (local dev with full features: `python app.py`)."""

from __future__ import annotations

import importlib
import os

import streamlit as st

from universe import get_nifty500_stocks


def load_screeners() -> dict[str, dict]:
    screeners: dict[str, dict] = {}
    screener_dir = os.path.join(os.path.dirname(__file__), "screeners")
    if not os.path.isdir(screener_dir):
        return screeners

    for filename in sorted(os.listdir(screener_dir)):
        if not filename.endswith(".py") or filename == "__init__.py":
            continue
        module_name = filename[:-3]
        module = importlib.import_module(f"screeners.{module_name}")
        if hasattr(module, "run") and hasattr(module, "NAME"):
            screeners[module.NAME] = {"func": module.run, "module": module_name}
    return screeners


def run_streamlit_ui() -> None:
    st.set_page_config(page_title="Stock Screener", page_icon="📈", layout="wide")

    st.title("Stock Screener")
    st.caption("Nifty 500 universe · run on demand (live alerts are local-only)")

    screeners = load_screeners()
    if not screeners:
        st.error("No screeners found in the screeners/ folder.")
        st.stop()

    names = list(screeners.keys())
    choice = st.selectbox("Strategy", names)
    run_fn = screeners[choice]["func"]

    tickers = get_nifty500_stocks()
    st.write(f"Universe: **{len(tickers)}** stocks")

    if st.button("Run scan", type="primary"):
        with st.spinner(f"Scanning {len(tickers)} stocks…"):
            try:
                results = run_fn(tickers)
            except Exception as exc:
                st.error(f"Scan failed: {exc}")
                st.stop()

        st.success(f"Found **{len(results)}** matches")
        if results:
            st.dataframe(results, use_container_width=True, hide_index=True)
        else:
            st.info("No stocks matched this screener right now.")


if __name__ == "__main__":
    run_streamlit_ui()
