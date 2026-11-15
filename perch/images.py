"""Background image fetching with a small in-memory texture cache."""

import urllib.request
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from gi.repository import Gdk, GLib

_USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Perch/0.1"
_MAX_CACHE = 500

_pool = ThreadPoolExecutor(max_workers=6, thread_name_prefix="perch-img")
_cache: "OrderedDict[str, Gdk.Texture]" = OrderedDict()
_waiters: dict[str, list[Callable[[Gdk.Texture | None], None]]] = {}


def _fetch(url: str) -> bytes | None:
    try:
        request = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.read()
    except Exception as err:  # noqa: BLE001 - any failure just means "no image"
        print(f"[perch] image fetch failed for {url}: {err}")
        return None


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


def load(url: str | None, callback: Callable[[Gdk.Texture | None], None], cache: bool = True) -> None:
    """Call ``callback(texture)`` on the main loop once ``url`` is loaded (None on failure).

    ``cache=False`` is for large one-off images (the media viewer) that shouldn't sit in memory.
    """
    if not url:
        return
    if not cache:
        def work():  # fetch + decode off the main thread; textures are immutable and thread-safe to create
            texture = _decode(url, _fetch(url))
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
    _pool.submit(lambda: GLib.idle_add(_finish, url, _fetch(url)))
