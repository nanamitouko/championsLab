import os
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from db import get_sprite_asset, mark_sprite_cached, pending_sprite_assets

SPRITE_DIR = Path(os.environ.get("SPRITE_DIR", "/data/sprites"))
ALLOWED_HOST = "championsbattledata.com"
ALLOWED_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}
MAX_BYTES = 2 * 1024 * 1024

_key_locks = {}
_key_locks_guard = threading.Lock()
_warmup_guard = threading.Lock()
_warmup_state = {"running": False, "completed": 0, "total": 0, "failed": 0}


def _lock_for(cache_key):
    with _key_locks_guard:
        return _key_locks.setdefault(cache_key, threading.Lock())


def _safe_url(source_url):
    parsed = urllib.parse.urlsplit(source_url)
    if parsed.scheme != "https" or parsed.hostname != ALLOWED_HOST:
        raise ValueError("不允许的图标来源")
    path = urllib.parse.quote(urllib.parse.unquote(parsed.path), safe="/%")
    return urllib.parse.urlunsplit((parsed.scheme, parsed.netloc, path, parsed.query, ""))


def sprite_path(asset):
    return SPRITE_DIR / asset["local_path"]


def cache_sprite(asset):
    target = sprite_path(asset)
    if target.is_file() and target.stat().st_size:
        return target, asset.get("mime_type") or "image/png"
    with _lock_for(asset["cache_key"]):
        if target.is_file() and target.stat().st_size:
            return target, asset.get("mime_type") or "image/png"
        SPRITE_DIR.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(
            _safe_url(asset["source_url"]),
            headers={"User-Agent": "ChampionGrid/0.5", "Accept": "image/png,image/webp,image/jpeg,image/gif"},
        )
        temporary = None
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                mime_type = response.headers.get_content_type().lower()
                if mime_type not in ALLOWED_TYPES:
                    raise ValueError(f"不支持的图标类型：{mime_type}")
                content = response.read(MAX_BYTES + 1)
                if not content or len(content) > MAX_BYTES:
                    raise ValueError("图标为空或超过 2MB")
            with tempfile.NamedTemporaryFile(dir=SPRITE_DIR, delete=False) as handle:
                handle.write(content)
                temporary = Path(handle.name)
            os.replace(temporary, target)
            mark_sprite_cached(asset["cache_key"], mime_type, len(content))
            return target, mime_type
        finally:
            if temporary and temporary.exists():
                temporary.unlink(missing_ok=True)


def cached_sprite(cache_key):
    asset = get_sprite_asset(cache_key)
    if not asset:
        return None
    return cache_sprite(asset)


def _run_warmup():
    assets = [asset for asset in pending_sprite_assets() if not sprite_path(asset).is_file()]
    with _warmup_guard:
        _warmup_state.update(running=True, completed=0, total=len(assets), failed=0)
    try:
        with ThreadPoolExecutor(max_workers=6, thread_name_prefix="sprite-cache") as executor:
            futures = [executor.submit(cache_sprite, asset) for asset in assets]
            for future in as_completed(futures):
                failed = 0
                try:
                    future.result()
                except (OSError, ValueError, urllib.error.URLError, TimeoutError):
                    failed = 1
                with _warmup_guard:
                    _warmup_state["completed"] += 1
                    _warmup_state["failed"] += failed
    finally:
        with _warmup_guard:
            _warmup_state["running"] = False


def schedule_warmup():
    with _warmup_guard:
        if _warmup_state["running"]:
            return False
        _warmup_state["running"] = True
    threading.Thread(target=_run_warmup, name="sprite-warmup", daemon=True).start()
    return True


def warmup_status():
    with _warmup_guard:
        return dict(_warmup_state)
