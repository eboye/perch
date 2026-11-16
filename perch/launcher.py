"""The unread count on Perch's dock icon.

Docks such as Dash to Dock (and Ubuntu Dock, Plank, KDE's task manager) show a count badge that an
app announces with the Unity LauncherEntry D-Bus signal. GNOME Shell's own dash doesn't show these;
without such a dock the signal is simply ignored.
"""

from gi.repository import Gio, GLib

from perch import APP_ID

APP_URI = f"application://{APP_ID}.desktop"
INTERFACE = "com.canonical.Unity.LauncherEntry"
OBJECT_PATH = "/" + APP_ID.replace(".", "/") + "/LauncherEntry"


class LauncherBadge:
    def __init__(self, app: Gio.Application):
        self._app = app
        self._count: int | None = None

    def set_count(self, count: int) -> None:
        """Show ``count`` on the dock icon (hidden at 0). Only sends when it changes."""
        count = max(0, int(count))
        if count == self._count:
            return
        connection = self._app.get_dbus_connection()
        if connection is None:
            return
        self._count = count
        properties = {"count": GLib.Variant("x", count), "count-visible": GLib.Variant("b", count > 0)}
        try:
            connection.emit_signal(None, OBJECT_PATH, INTERFACE, "Update", GLib.Variant("(sa{sv})", (APP_URI, properties)))
        except GLib.Error as err:
            print(f"[perch] dock badge update failed: {err.message}")
