"""Runtime flags shared by Flask, Streamlit, and the live monitor."""

from __future__ import annotations

import os


def is_streamlit_runtime() -> bool:
    for var in (
        "STREAMLIT_SERVER_PORT",
        "STREAMLIT_RUNTIME_ENVIRONMENT",
        "STREAMLIT_CLOUD_ENV",
    ):
        if os.environ.get(var):
            return True
    return "/mount/src/" in os.path.abspath(__file__)


def live_alerts_enabled() -> bool:
    return os.environ.get("ENABLE_LIVE_ALERTS", "true").lower() in ("1", "true", "yes")
