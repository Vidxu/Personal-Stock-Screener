"""Shared dashboard HTML rendering for Flask and Streamlit."""

from __future__ import annotations

import importlib
import json
from typing import Any

from flask import Flask, render_template

from universe import get_nifty500_stocks

_flask = Flask(__name__, template_folder="templates")


LIVE_MODULES = {"opening_breakout"}


def get_screener_list() -> list[dict[str, Any]]:
    screeners: list[dict[str, Any]] = []
    import os

    screener_dir = os.path.join(os.path.dirname(__file__), "screeners")
    if not os.path.isdir(screener_dir):
        return screeners

    for filename in sorted(os.listdir(screener_dir)):
        if not filename.endswith(".py") or filename == "__init__.py":
            continue
        module_name = filename[:-3]
        module = importlib.import_module(f"screeners.{module_name}")
        if hasattr(module, "run") and hasattr(module, "NAME"):
            screeners.append(
                {
                    "name": module.NAME,
                    "module": module_name,
                    "live": module_name in LIVE_MODULES,
                }
            )
    return screeners


def render_dashboard_html(
    *,
    streamlit_mode: bool = False,
    scan_cache: dict[str, dict] | None = None,
    active_module: str | None = None,
    live_state: dict | None = None,
) -> str:
    screeners = get_screener_list()
    with _flask.app_context():
        return render_template(
            "index.html",
            screeners=screeners,
            universe_size=len(get_nifty500_stocks()),
            streamlit_mode=streamlit_mode,
            scan_cache_json=json.dumps(scan_cache or {}),
            active_module=active_module or "",
            live_state_json=json.dumps(live_state or {}),
        )
