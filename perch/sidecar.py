"""Client for the Node sidecar that wraps rettiwt-api (newline-delimited JSON-RPC over stdio)."""

import json
import os
import shutil
import subprocess
import threading
from pathlib import Path
from typing import Any, Callable

from gi.repository import GLib

from perch import crash

Callback = Callable[[Any, "RpcError | None"], None]


class RpcError(Exception):
    def __init__(self, message: str, kind: str = "error", status: int | None = None):
        super().__init__(message)
        self.kind = kind
        self.status = status


def _sidecar_script() -> Path:
    if env := os.environ.get("PERCH_SIDECAR"):
        return Path(env)
    # Same relative layout in the repo and in the Flatpak (/app/share/perch/...).
    return Path(__file__).resolve().parent.parent / "sidecar" / "dist" / "sidecar.cjs"


def _node_binary() -> str:
    return os.environ.get("PERCH_NODE") or shutil.which("node") or "node"


class Sidecar:
    """Owns the Node process. Callbacks always run on the GLib main loop."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._pending: dict[int, Callback] = {}
        self._progress: dict[int, Callable[[dict], None]] = {}  # request id -> progress callback
        self._next_id = 1
        self._lock = threading.Lock()
        self._api_key: str | None = None
        self._fresh_depth = 0
        # Called with the copy's age in seconds when X failed (rate limit, offline) and the sidecar
        # answered from its cache instead.
        self.on_stale: Callable[[int], None] | None = None

    # -- process management -------------------------------------------------

    def _ensure_running(self) -> None:
        if self._proc and self._proc.poll() is None:
            return
        script = _sidecar_script()
        if not script.exists():
            raise RpcError(f"Sidecar not found at {script}", "sidecar")
        try:
            self._proc = subprocess.Popen(
                [_node_binary(), str(script)],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as err:
            raise RpcError(f"Could not start Node: {err}", "sidecar") from err
        threading.Thread(target=self._read_loop, args=(self._proc,), daemon=True).start()
        if self._api_key:
            # Re-authenticate a restarted sidecar before anything else is sent.
            self._send("init", {"apiKey": self._api_key}, lambda *_: None)

    def stop(self) -> None:
        if self._proc and self._proc.poll() is None:
            try:
                self._proc.stdin.close()
                self._proc.wait(timeout=2)
            except (OSError, subprocess.TimeoutExpired):
                self._proc.kill()

    def _read_loop(self, proc: subprocess.Popen) -> None:
        for line in proc.stdout:
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                continue
            if msg.get("event") == "progress":
                with self._lock:
                    on_progress = self._progress.get(msg.get("id"))
                if on_progress is not None:
                    GLib.idle_add(lambda cb=on_progress, data=msg.get("data") or {}: cb(data) or GLib.SOURCE_REMOVE)
                continue
            with self._lock:
                callback = self._pending.pop(msg.get("id"), None)
                self._progress.pop(msg.get("id"), None)
            if callback is None:
                continue
            if err := msg.get("error"):
                error = RpcError(err.get("message", "Unknown error"), err.get("kind", "error"), err.get("status"))
                GLib.idle_add(callback, None, error)
            else:
                GLib.idle_add(callback, msg.get("result"), None)
        # Process exited: fail whatever was still waiting.
        with self._lock:
            orphans = list(self._pending.values())
            self._pending.clear()
            self._progress.clear()
        for callback in orphans:
            GLib.idle_add(callback, None, RpcError("The sidecar process exited unexpectedly", "sidecar"))

    def _send(self, method: str, params: dict, callback: Callback, on_progress=None) -> None:
        with self._lock:
            request_id = self._next_id
            self._next_id += 1
            self._pending[request_id] = callback
            if on_progress is not None:
                self._progress[request_id] = on_progress
        frame = json.dumps({"id": request_id, "method": method, "params": params}) + "\n"
        try:
            self._proc.stdin.write(frame)
            self._proc.stdin.flush()
        except OSError as err:
            with self._lock:
                self._pending.pop(request_id, None)
                self._progress.pop(request_id, None)
            GLib.idle_add(callback, None, RpcError(f"Sidecar write failed: {err}", "sidecar"))

    # -- public API ---------------------------------------------------------

    def call(self, method: str, params: dict | None, callback: Callback, on_progress: Callable[[dict], None] | None = None) -> None:
        """The sidecar caches reads for a short while; calls made inside ``fresh()`` skip that.
        ``on_progress({"phase", "fraction"})`` follows long calls (uploads) on the main loop."""

        def deliver(result, error):
            if error is not None:
                crash.note(f"{method} failed ({error.kind})")
            if isinstance(result, dict) and result.get("stale") and self.on_stale:
                self.on_stale(int(result["stale"]))
            callback(result, error)
            return GLib.SOURCE_REMOVE

        try:
            self._ensure_running()
        except RpcError as err:
            GLib.idle_add(deliver, None, err)
            return
        params = dict(params or {})
        if self._fresh_depth:
            params["fresh"] = True
        self._send(method, params, deliver, on_progress)

    def fresh(self, fn: Callable, *args) -> None:
        """Run ``fn`` so that every sidecar call it makes asks X instead of the cache (refresh, retry)."""
        self._fresh_depth += 1
        try:
            fn(*args)
        finally:
            self._fresh_depth -= 1

    def sign_in(self, api_key: str, callback: Callback) -> None:
        def done(result, error):
            self._api_key = None if error else api_key
            callback(result, error)

        self.call("init", {"apiKey": api_key}, done)

    def sign_out(self, callback: Callback) -> None:
        self._api_key = None
        self.call("signout", None, callback)
