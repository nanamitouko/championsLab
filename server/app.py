import os
import threading

from flask import Flask, jsonify, send_from_directory

from db import DB_PATH, health_info, init_database, load_bootstrap
from importer import refresh

DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dist"))
refresh_lock = threading.Lock()
app = Flask(__name__, static_folder=None)


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", **health_info()})


@app.get("/api/bootstrap")
def bootstrap():
    return jsonify(load_bootstrap())


@app.post("/api/refresh")
def refresh_data():
    if not refresh_lock.acquire(blocking=False):
        return jsonify({"error": "已有数据更新正在进行"}), 409
    try:
        meta = refresh()
        return jsonify({"meta": meta, "data": load_bootstrap()})
    except Exception as error:
        app.logger.exception("refresh failed")
        return jsonify({"error": str(error)}), 502
    finally:
        refresh_lock.release()


@app.get("/")
def index():
    return send_from_directory(DIST_DIR, "index.html")


@app.get("/<path:path>")
def static_files(path):
    return send_from_directory(DIST_DIR, path)


def initialize():
    init_database()
    if not load_bootstrap()["meta"] and os.environ.get("AUTO_REFRESH", "1") == "1":
        try:
            refresh()
        except Exception:
            app.logger.exception("initial data refresh failed; use the update button to retry")


initialize()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
