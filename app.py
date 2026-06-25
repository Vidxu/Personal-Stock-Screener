from flask import Flask, jsonify, render_template
from flask_cors import CORS
from universe import get_nifty500_stocks
from live_registry import get_hits, get_status, scan_universe, start_live_monitors, stop_monitor, start_monitor
import importlib
import os
import sys

app = Flask(__name__)
CORS(app)

PORT = int(os.environ.get("PORT", 5001))
LIVE_MODULES = {"opening_breakout", "positive_divergence"}


def get_all_screeners():
    screeners = {}
    screener_dir = os.path.join(os.path.dirname(__file__), "screeners")
    if not os.path.isdir(screener_dir):
        return screeners

    for filename in sorted(os.listdir(screener_dir)):
        if filename.endswith(".py") and filename != "__init__.py":
            module_name = filename[:-3]
            module = importlib.import_module(f"screeners.{module_name}")
            if hasattr(module, "run") and hasattr(module, "NAME"):
                screeners[module.NAME] = {
                    "func": module.run,
                    "module": module_name,
                    "live": module_name in LIVE_MODULES,
                }
    return screeners


@app.route("/")
def dashboard():
    screeners = get_all_screeners()
    screener_list = [
        {"name": name, "module": info["module"], "live": info.get("live", False)}
        for name, info in screeners.items()
    ]
    universe_size = len(get_nifty500_stocks())
    return render_template(
        "index.html",
        screeners=screener_list,
        universe_size=universe_size,
    )


@app.route("/api/run/<module_name>")
def run_one(module_name):
    try:
        module = importlib.import_module(f"screeners.{module_name}")
        tickers = get_nifty500_stocks()
        if module_name in LIVE_MODULES:
            results = scan_universe(module_name, tickers, incremental=True)
        else:
            results = module.run(tickers)
        return jsonify({"name": module.NAME, "results": results, "live": module_name in LIVE_MODULES})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live/status")
@app.route("/api/live/<module_name>/status")
def live_status(module_name=None):
    if module_name is None:
        module_name = "opening_breakout"
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Unknown live module"}), 404
    return jsonify(get_status(module_name))


@app.route("/api/live/hits")
@app.route("/api/live/<module_name>/hits")
def live_hits(module_name=None):
    if module_name is None:
        module_name = "opening_breakout"
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Unknown live module"}), 404
    status = get_status(module_name)
    return jsonify({
        "name": status["name"],
        "results": get_hits(module_name),
        "live": True,
        "status": status,
    })


@app.route("/api/live/<module_name>/stop", methods=["POST"])
def live_stop(module_name):
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Unknown live module"}), 404
    return jsonify(stop_monitor(module_name))


@app.route("/api/live/<module_name>/start", methods=["POST"])
def live_start(module_name):
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Unknown live module"}), 404
    return jsonify(start_monitor(module_name))


def _is_streamlit_runtime() -> bool:
    """True when this file is executed by Streamlit (including Streamlit Cloud)."""
    try:
        from streamlit.runtime.scriptrunner import get_script_run_ctx

        if get_script_run_ctx() is not None:
            return True
    except Exception:
        pass

    for var in (
        "STREAMLIT_SERVER_PORT",
        "STREAMLIT_RUNTIME_ENVIRONMENT",
        "STREAMLIT_CLOUD_ENV",
    ):
        if os.environ.get(var):
            return True

    # Streamlit Community Cloud clones repos under /mount/src/
    if "/mount/src/" in os.path.abspath(__file__):
        return True

    return "streamlit.runtime" in sys.modules


if _is_streamlit_runtime():
    from streamlit_app import run_streamlit_ui

    run_streamlit_ui()
elif __name__ == "__main__":
    from run_local import main

    main()
