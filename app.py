from flask import Flask, jsonify, render_template
from flask_cors import CORS
from universe import get_nse_stocks
from live_monitor import get_hits, get_status, start_live_monitor
import importlib
import os

app = Flask(__name__)
CORS(app)

PORT = 5001  # macOS AirPlay uses 5000 — do not use 5000
LIVE_MODULES = {"opening_breakout"}


def get_all_screeners():
    screeners = {}
    screener_dir = os.path.join(os.path.dirname(__file__), "screeners")
    if not os.path.isdir(screener_dir):
        return screeners

    for filename in os.listdir(screener_dir):
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
    return render_template("index.html", screeners=screener_list)


@app.route("/api/run/<module_name>")
def run_one(module_name):
    try:
        module = importlib.import_module(f"screeners.{module_name}")
        tickers = get_nse_stocks()
        if module_name in LIVE_MODULES:
            from live_monitor import scan_universe
            results = scan_universe(tickers, incremental=True)
        else:
            results = module.run(tickers)
        return jsonify({"name": module.NAME, "results": results, "live": module_name in LIVE_MODULES})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


@app.route("/api/live/status")
def live_status():
    return jsonify(get_status())


@app.route("/api/live/hits")
def live_hits():
    status = get_status()
    return jsonify({
        "name": status["name"],
        "results": get_hits(),
        "live": True,
        "status": status,
    })


if __name__ == "__main__":
    debug = True
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_live_monitor()
    print(f"✅ Dashboard running at http://localhost:{PORT}")
    app.run(debug=debug, port=PORT, host="127.0.0.1")
