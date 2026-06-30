from flask import Flask, jsonify
from flask_cors import CORS
from universe import get_nifty500_stocks
import importlib
import os

from runtime_env import live_alerts_enabled

app = Flask(__name__)
CORS(app)

PORT = int(os.environ.get("PORT", 5001))
LIVE_MODULES = {"opening_breakout"}


def _live_alerts_enabled() -> bool:
    return live_alerts_enabled()


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
                }
    return screeners


@app.route("/")
def dashboard():
    from ui import render_dashboard_html

    return render_dashboard_html()


@app.route("/api/run/<module_name>")
def run_one(module_name):
    try:
        from market_data import UpstoxConfigError

        module = importlib.import_module(f"screeners.{module_name}")
        tickers = get_nifty500_stocks()
        results = module.run(tickers)

        if module_name in LIVE_MODULES and _live_alerts_enabled():
            from live_registry import get_monitor, start_monitor

            mon = get_monitor(module_name)
            start_monitor(module_name)
            mon.seed_hits(results, total=len(tickers))

        return jsonify({
            "name": module.NAME,
            "results": results,
            "live": module_name in LIVE_MODULES and _live_alerts_enabled(),
        })
    except UpstoxConfigError as e:
        return jsonify({"error": str(e)}), 401
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live/<module_name>/status")
def live_status(module_name):
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Not a live screener"}), 404
    from live_registry import get_status

    return jsonify(get_status(module_name))


@app.route("/api/live/<module_name>/hits")
def live_hits(module_name):
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Not a live screener"}), 404
    from live_registry import get_hits

    return jsonify({"hits": get_hits(module_name)})


@app.route("/api/live/<module_name>/stop", methods=["POST"])
def live_stop(module_name):
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Not a live screener"}), 404
    from live_registry import stop_monitor

    return jsonify(stop_monitor(module_name))


@app.route("/api/live/<module_name>/start", methods=["POST"])
def live_start(module_name):
    if module_name not in LIVE_MODULES:
        return jsonify({"error": "Not a live screener"}), 404
    from live_registry import start_monitor

    return jsonify(start_monitor(module_name))


def _is_streamlit_runtime() -> bool:
    from runtime_env import is_streamlit_runtime

    return is_streamlit_runtime()


if __name__ == "__main__":
    if _is_streamlit_runtime():
        from streamlit_app import run_streamlit_ui

        run_streamlit_ui()
    else:
        from run_local import main

        main()
