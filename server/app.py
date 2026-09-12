import hashlib
import os
import re
import threading

from flask import Flask, Response, jsonify, request, send_file, send_from_directory
from flask_compress import Compress

from db import DB_PATH, health_info, init_database, load_bootstrap, load_calculator, load_usage
from importer import refresh
from sprites import cached_sprite, schedule_warmup, warmup_status

DIST_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "dist"))
SPRITE_KEY = re.compile(r"^[0-9a-f]{24}$")
PLACEHOLDER_SVG = b"""<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><rect width="64" height="64" rx="12" fill="#101726"/><circle cx="32" cy="32" r="18" fill="none" stroke="#7ce7ff" stroke-width="3"/><path d="M14 32h36" stroke="#7ce7ff" stroke-width="3"/><circle cx="32" cy="32" r="6" fill="#f5f7ff" stroke="#7ce7ff" stroke-width="3"/></svg>"""

refresh_lock = threading.Lock()
app = Flask(__name__, static_folder=None)
app.json.ensure_ascii = False
app.config.update(
    COMPRESS_MIMETYPES=["application/json", "text/css", "application/javascript", "text/javascript"],
    COMPRESS_MIN_SIZE=1024,
    COMPRESS_LEVEL=6,
)
Compress(app)


def _json_response(payload, cache_control):
    response = jsonify(payload)
    etag = hashlib.sha256(response.get_data()).hexdigest()
    response.set_etag(etag)
    response.headers["Cache-Control"] = cache_control
    response.headers.add("Vary", "Accept-Encoding")
    validators = request.headers.get("If-None-Match", "")
    if any(token.strip().removeprefix("W/").strip('"').split(":", 1)[0] == etag for token in validators.split(",")):
        response.status_code = 304
        response.set_data(b"")
    return response


@app.get("/api/health")
def health():
    return jsonify({"status": "ok", **health_info(), "spriteWarmup": warmup_status()})


@app.get("/api/bootstrap")
def bootstrap():
    return _json_response(load_bootstrap(), "no-cache")


@app.get("/api/usage")
def usage():
    format_name = request.args.get("format", "Doubles")
    try:
        payload = load_usage(format_name)
    except ValueError as error:
        return jsonify({"error": str(error)}), 400
    return _json_response(payload, "public, max-age=300, stale-while-revalidate=86400")


@app.get("/api/calculator")
def calculator():
    return _json_response(load_calculator(), "public, max-age=300, stale-while-revalidate=86400")


@app.post("/api/refresh")
def refresh_data():
    if not refresh_lock.acquire(blocking=False):
        return jsonify({"error": "已有数据更新正在进行"}), 409
    try:
        refresh()
        meta = load_bootstrap()["meta"]
        schedule_warmup()
        response = jsonify({"meta": meta})
        response.headers["Cache-Control"] = "no-store"
        return response
    except Exception as error:
        app.logger.exception("refresh failed")
        return jsonify({"error": str(error)}), 502
    finally:
        refresh_lock.release()


@app.get("/api/sprites/<cache_key>")
def sprite(cache_key):
    if not SPRITE_KEY.fullmatch(cache_key):
        return jsonify({"error": "无效的图标标识"}), 404
    try:
        cached = cached_sprite(cache_key)
    except Exception:
        app.logger.exception("sprite cache failed for %s", cache_key)
        cached = None
    if not cached:
        response = Response(PLACEHOLDER_SVG, mimetype="image/svg+xml")
        response.headers["Cache-Control"] = "no-store"
        return response
    path, mime_type = cached
    response = send_file(path, mimetype=mime_type, conditional=True, etag=cache_key)
    response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
    return response


@app.get("/")
def index():
    response = send_from_directory(DIST_DIR, "index.html")
    response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/<path:path>")
def static_files(path):
    response = send_from_directory(DIST_DIR, path)
    if path.endswith((".js", ".css")):
        response.headers["Cache-Control"] = "public, max-age=604800"
    elif path.endswith(".html"):
        response.headers["Cache-Control"] = "no-cache"
    return response


def initialize():
    init_database()
    if not load_bootstrap()["meta"] and os.environ.get("AUTO_REFRESH", "1") == "1":
        try:
            refresh()
        except Exception:
            app.logger.exception("initial data refresh failed; use the update button to retry")
    if load_bootstrap()["meta"]:
        schedule_warmup()


initialize()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "8000")))
