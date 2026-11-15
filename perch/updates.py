"""Notice when a newer Perch is installed while this one is running, and restart into it.

Uses the Flatpak portal (org.freedesktop.portal.Flatpak), which every sandboxed app can talk to:
- CreateUpdateMonitor → its UpdateAvailable signal fires when the installed version (local-commit)
  differs from the one running (running-commit), e.g. after `flatpak install --reinstall`.
- Spawn with FLATPAK_SPAWN_FLAGS_LATEST_VERSION starts the newest installed version.

Outside Flatpak (running from source) this is a no-op.
"""

import os
import threading

from gi.repository import Gio, GLib

PORTAL_BUS_NAME = "org.freedesktop.portal.Flatpak"
PORTAL_PATH = "/org/freedesktop/portal/Flatpak"
PORTAL_IFACE = "org.freedesktop.portal.Flatpak"
MONITOR_IFACE = "org.freedesktop.portal.Flatpak.UpdateMonitor"
SPAWN_LATEST_VERSION = 2  # FLATPAK_SPAWN_FLAGS_LATEST_VERSION

# Environment the restarted app needs to show up on the same display (the portal starts it with
# the session's default environment otherwise).
_FORWARDED_ENV = ("WAYLAND_DISPLAY", "DISPLAY", "GDK_BACKEND", "XDG_CONFIG_HOME", "PERCH_DEBUG_RPC", "PERCH_AUDIO_SINK")


def in_flatpak() -> bool:
    return os.path.exists("/.flatpak-info")


def _bytes_z(text: str) -> bytes:
    return text.encode() + b"\0"  # the portal wants NUL-terminated byte strings


class UpdateMonitor:
    def __init__(self, on_available):
        self._on_available = on_available
        self._bus: Gio.DBusConnection | None = None
        self._subscription = 0
        self.available = False
        self._checking = False

    def start(self) -> None:
        if not in_flatpak():
            return
        try:
            self._bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        except GLib.Error as err:
            print(f"[perch] update monitor: no session bus ({err.message})")
            return
        self._bus.call(
            PORTAL_BUS_NAME, PORTAL_PATH, PORTAL_IFACE, "CreateUpdateMonitor",
            GLib.Variant("(a{sv})", ({},)), GLib.VariantType("(o)"),
            Gio.DBusCallFlags.NONE, -1, None, self._on_created,
        )

    def _on_created(self, bus: Gio.DBusConnection, result) -> None:
        try:
            (path,) = bus.call_finish(result).unpack()
        except GLib.Error as err:
            print(f"[perch] update monitor unavailable: {err.message}")
            return
        self._subscription = bus.signal_subscribe(
            None, MONITOR_IFACE, "UpdateAvailable", path, None, Gio.DBusSignalFlags.NONE, self._on_update_available
        )

    def _on_update_available(self, _bus, _sender, _path, _iface, _signal, params: GLib.Variant) -> None:
        (info,) = params.unpack()
        running, local = info.get("running-commit"), info.get("local-commit")
        # The portal also reports remote (repository) updates; only an *installed* newer version
        # can be restarted into, which is what local-commit != running-commit means.
        if running and local and running != local and not self.available:
            self.available = True
            self._on_available()

    # -- on-demand check ------------------------------------------------------------------
    # The portal only polls every 30 minutes (DEFAULT_UPDATE_POLL_TIMEOUT_SEC), so on its own a
    # fresh install could go unnoticed for half an hour. check_now() asks the portal to start the
    # *latest installed* Perch just long enough to print its commit, and compares it with ours.

    @staticmethod
    def running_commit() -> str | None:
        info = GLib.KeyFile()
        try:
            info.load_from_file("/.flatpak-info", GLib.KeyFileFlags.NONE)
            return info.get_string("Instance", "app-commit")
        except GLib.Error:
            return None

    def check_now(self) -> None:
        if self.available or self._bus is None or self._checking:
            return
        running = self.running_commit()
        if not running:
            return
        self._checking = True
        read_fd, write_fd = os.pipe()
        fds = Gio.UnixFDList()
        fds.append(write_fd)  # dups it
        os.close(write_fd)

        def collect():
            # Runs in a thread: the probe's stdout closes when it exits (or the portal drops it).
            chunks = []
            try:
                while chunk := os.read(read_fd, 4096):
                    chunks.append(chunk)
            finally:
                os.close(read_fd)
            text = b"".join(chunks).decode(errors="replace")
            latest = next((line.split("=", 1)[1].strip() for line in text.splitlines() if line.startswith("app-commit=")), None)
            GLib.idle_add(finish, latest)

        def finish(latest):
            self._checking = False
            if latest and latest != running and not self.available:
                self.available = True
                self._on_available()
            return GLib.SOURCE_REMOVE

        def spawned(bus, result):
            try:
                bus.call_with_unix_fd_list_finish(result)
            except GLib.Error as err:
                print(f"[perch] update check failed: {err.message}")
            # The message carrying our copy of the write end is gone now, so the reader sees EOF
            # once the probe exits.
            threading.Thread(target=collect, daemon=True).start()

        self._bus.call_with_unix_fd_list(
            PORTAL_BUS_NAME, PORTAL_PATH, PORTAL_IFACE, "Spawn",
            GLib.Variant(
                "(ayaaya{uh}a{ss}ua{sv})",
                (_bytes_z("/"), [_bytes_z("grep"), _bytes_z("-m1"), _bytes_z("^app-commit="), _bytes_z("/.flatpak-info")], {1: 0}, {}, SPAWN_LATEST_VERSION, {}),
            ),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, fds, None, spawned,
        )

    def restart(self, quit_app) -> None:
        """Start the newest installed Perch, then quit this one.

        The new process waits a moment before launching so this instance has released Perch's
        single-instance D-Bus name; otherwise it would just hand over to us and exit.
        """
        command = os.environ.get("PERCH_RESTART_COMMAND") or "sleep 2; exec perch"  # override for testing
        argv = ["sh", "-c", command]
        env = {k: os.environ[k] for k in _FORWARDED_ENV if k in os.environ}
        cwd = os.environ.get("HOME", "/")

        def done(bus, result):
            try:
                bus.call_finish(result)
            except GLib.Error as err:
                print(f"[perch] restart failed: {err.message}")
                return
            quit_app()

        self._bus.call(
            PORTAL_BUS_NAME, PORTAL_PATH, PORTAL_IFACE, "Spawn",
            GLib.Variant(
                "(ayaaya{uh}a{ss}ua{sv})",
                (_bytes_z(cwd), [_bytes_z(arg) for arg in argv], {}, env, SPAWN_LATEST_VERSION, {}),
            ),
            GLib.VariantType("(u)"), Gio.DBusCallFlags.NONE, -1, None, done,
        )
