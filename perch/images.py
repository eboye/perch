"""Background image fetching with an in-memory texture cache and an on-disk byte cache.

Avatars and post photos are kept on disk (``~/.cache/perch/images``, or the Flatpak's cache
directory), so they show at once after a restart instead of being downloaded again. The disk
cache is trimmed to ``_DISK_LIMIT`` bytes, oldest first, once per run.
"""

import hashlib
import os
import tempfile
import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable

from gi.repository import Gdk, GLib

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Perch/0.1"
_MAX_CACHE = 500
_DISK_LIMIT = 300 * 1024 * 1024
_DISK_TARGET = 250 * 1024 * 1024  # trim down to this once over the limit

_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="perch-img")
_cache: "OrderedDict[str, Gdk.Texture]" = OrderedDict()
_waiters: dict[str, list[Callable[[Gdk.Texture | None], None]]] = {}
_disk_dir = Path(os.environ.get("PERCH_IMAGE_CACHE") or Path(GLib.get_user_cache_dir()) / "perch" / "images")
_trimmed = False

Callback = Callable[[Gdk.Texture | None], None]


# -- disk cache -------------------------------------------------------------


def _disk_path(url: str) -> Path:
    return _disk_dir / hashlib.sha256(url.encode()).hexdigest()[:40]


def _disk_read(url: str) -> bytes | None:
    path = _disk_path(url)
    try:
        data = path.read_bytes()
        os.utime(path)  # recently used: kept longest when trimming
        return data
    except OSError:
        return None


def _disk_write(url: str, data: bytes) -> None:
    try:
        _disk_dir.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a crash never leaves a half-written file that looks valid.
        fd, tmp = tempfile.mkstemp(dir=_disk_dir, prefix=".tmp-")
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, _disk_path(url))
    except OSError as err:
        print(f"[perch] image cache write failed: {err}")


def _trim_disk() -> None:
    """Delete the least recently used files once the cache is over its size limit."""
    try:
        files = [(entry.stat().st_mtime, entry.stat().st_size, entry) for entry in _disk_dir.iterdir() if entry.is_file()]
    except OSError:
        return
    total = sum(size for _, size, _ in files)
    if total <= _DISK_LIMIT:
        return
    for _, size, entry in sorted(files, key=lambda item: item[0]):
        try:
            entry.unlink()
        except OSError:
            continue
        total -= size
        if total <= _DISK_TARGET:
            break


def set_disk_limit(megabytes: int) -> None:
    """The most the disk cache may hold (Preferences); trims now if it's over."""
    global _DISK_LIMIT, _DISK_TARGET
    _DISK_LIMIT = max(50, int(megabytes)) * 1024 * 1024
    _DISK_TARGET = int(_DISK_LIMIT * 0.83)
    _pool.submit(_trim_disk)


def disk_usage() -> int:
    """Bytes the disk cache holds now."""
    try:
        return sum(entry.stat().st_size for entry in _disk_dir.iterdir() if entry.is_file())
    except OSError:
        return 0


def clear_disk() -> None:
    """Empty the disk cache (images already on screen stay; they download again when needed)."""
    try:
        for entry in _disk_dir.iterdir():
            if entry.is_file():
                entry.unlink(missing_ok=True)
    except OSError as err:
        print(f"[perch] clearing the image cache failed: {err}")


def _schedule_trim() -> None:
    global _trimmed
    if not _trimmed:
        _trimmed = True
        _pool.submit(_trim_disk)


# -- fetching -----------------------------------------------------------------


def _download(url: str) -> bytes | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except Exception as err:  # noqa: BLE001 - any failure just means "no image"
        print(f"[perch] image fetch failed for {url}: {err}")
        return None


def _fetch(url: str, disk: bool) -> bytes | None:
    if disk and (data := _disk_read(url)) is not None:
        return data
    data = _download(url)
    if data and disk:
        _disk_write(url, data)
    return data


def _decode(url: str, data: bytes | None) -> Gdk.Texture | None:
    if not data:
        return None
    try:
        return Gdk.Texture.new_from_bytes(GLib.Bytes.new(data))
    except GLib.Error as err:
        print(f"[perch] image decode failed for {url}: {err.message}")
        return None


def _finish(url: str, data: bytes | None) -> bool:
    texture = _decode(url, data)
    if texture is not None:
        _cache[url] = texture
        while len(_cache) > _MAX_CACHE:
            _cache.popitem(last=False)
    for callback in _waiters.pop(url, []):
        callback(texture)
    return GLib.SOURCE_REMOVE


def load(url: str | None, callback: Callback, cache: bool = True, disk: bool | None = None) -> None:
    """Call ``callback(texture)`` on the main loop once ``url`` is loaded (None on failure).

    ``cache=False`` keeps the texture out of memory (large or one-off images). ``disk`` (default:
    same as ``cache``) keeps the downloaded bytes on disk for the next run.
    """
    if not url:
        return
    disk = cache if disk is None else disk
    _schedule_trim()
    if not cache:
        def work():  # fetch + decode off the main thread; textures are immutable and thread-safe to create
            texture = _decode(url, _fetch(url, disk))
            GLib.idle_add(lambda: callback(texture) or GLib.SOURCE_REMOVE)

        _pool.submit(work)
        return
    if (texture := _cache.get(url)) is not None:
        _cache.move_to_end(url)
        callback(texture)
        return
    if url in _waiters:
        _waiters[url].append(callback)
        return
    _waiters[url] = [callback]
    _pool.submit(lambda: GLib.idle_add(_finish, url, _fetch(url, disk)))


def load_via(url: str, fetch: Callable[[str, Callable[[bytes | None], None]], None], callback: Callback) -> None:
    """Like ``load``, but the bytes come from ``fetch(url, done)`` (e.g. through the sidecar for
    images that need the X session) instead of a plain download. Cached in memory and on disk."""
    if (texture := _cache.get(url)) is not None:
        _cache.move_to_end(url)
        callback(texture)
        return
    if url in _waiters:
        _waiters[url].append(callback)
        return
    _waiters[url] = [callback]
    _schedule_trim()

    def from_disk():
        data = _disk_read(url)
        GLib.idle_add(lambda: (_finish(url, data) if data else fetch(url, fetched)) and False)

    def fetched(data: bytes | None) -> None:
        if data:
            _pool.submit(_disk_write, url, data)
        _finish(url, data)

    _pool.submit(from_disk)
