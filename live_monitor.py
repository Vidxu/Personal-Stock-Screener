"""Backward-compatible shim — use live_registry instead."""
from live_registry import (  # noqa: F401
    get_hits,
    get_status,
    scan_universe,
    start_live_monitor,
    start_live_monitors,
)

def get_hits_legacy():
    return get_hits("opening_breakout")

def get_status_legacy():
    return get_status("opening_breakout")
