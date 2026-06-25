#!/usr/bin/env python3
"""Start the Flask dashboard locally: python run_local.py"""

import os

from app import PORT, LIVE_MODULES, app
from live_registry import start_live_monitors


def main() -> None:
    debug = os.environ.get("FLASK_DEBUG", "true").lower() == "true"
    if not debug or os.environ.get("WERKZEUG_RUN_MAIN") == "true":
        start_live_monitors(list(LIVE_MODULES))
    host = "0.0.0.0" if os.environ.get("PORT") else "127.0.0.1"
    print(f"✅ Dashboard running at http://{host}:{PORT}")
    app.run(debug=debug, port=PORT, host=host, use_reloader=debug)


if __name__ == "__main__":
    main()
