import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")

from pathlib import Path  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gst, Gtk  # noqa: E402

from perch import APP_ID, APP_NAME, VERSION, images, settings  # noqa: E402
from perch.sidecar import Sidecar  # noqa: E402
from perch.updates import UpdateMonitor  # noqa: E402
from perch.window import PerchWindow  # noqa: E402


class PerchApplication(Adw.Application):
    """Closing the window only minimizes it, so Perch stays in the dock (it keeps polling, notifying
    and playing Spaces; a click on the dock icon brings it back);
    the app ends only through the "quit" action — the dock's right-click Quit (GNOME Shell
    activates app.quit when an app exports it), Ctrl+Q, or Quit in the main menu."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.sidecar = Sidecar()
        self.quitting = False
        self._main_window: PerchWindow | None = None
        self.connect("shutdown", lambda *_: self.sidecar.stop())
        # `perch --quit` (the desktop file's "Quit Completely" action, shown on the dock icon's
        # right-click menu) ends the running instance.
        self.add_main_option("quit", 0, GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Quit the running Perch completely", None)

    def do_handle_local_options(self, options: GLib.VariantDict) -> int:
        if not options.contains("quit"):
            return -1  # normal start-up
        try:
            self.register(None)
        except GLib.Error as err:
            print(f"[perch] --quit: {err.message}")
            return 1
        if self.get_is_remote():
            # Forwarded over D-Bus to the running instance; flush so it's sent before we exit.
            self.activate_action("quit", None)
            if connection := self.get_dbus_connection():
                connection.flush_sync(None)
        else:
            # Nothing was running, so registering made *us* the primary instance and ran start-up,
            # which holds the app alive; g_application_run would then sit in its loop forever.
            self._quit()
        return 0

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(str(Path(__file__).with_name("icons")))
        self._watch_user_css(display)
        images.set_disk_limit(settings.get("image_cache_mb") or 300)

        # Stay alive however the window is closed; released again in _quit().
        self.hold()

        for name, handler, accels in (
            ("quit", lambda *_: self._quit(), ["<Control>q"]),
            ("about", lambda *_: self._about(), []),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
            self.set_accels_for_action(f"app.{name}", accels)

        # Ctrl+W minimizes to the dock (GtkWindow's built-in close action, which goes through close-request).
        self.set_accels_for_action("window.close", ["<Control>w"])

        # Target of desktop notifications: brings the window up on the given tab.
        show_tab = Gio.SimpleAction.new("show-tab", GLib.VariantType.new("s"))
        show_tab.connect("activate", lambda _a, tab: self._window().show_tab(tab.get_string()))
        self.add_action(show_tab)

        # A newer Perch installed while this one runs (it keeps running in the background, so
        # updates would otherwise go unnoticed): offer a restart into it.
        self.updates = UpdateMonitor(self._on_update_available)
        restart = Gio.SimpleAction.new("restart-update", None)
        restart.connect("activate", lambda *_: self.updates.restart(self._quit))
        self.add_action(restart)
        self.updates.start()

    def _watch_user_css(self, display: Gdk.Display) -> None:
        """Re-apply ~/.config/gtk-4.0/gtk.css (and what it imports) whenever it changes.

        GTK reads that file only at start-up. Theming tools such as Rewaita rewrite it, with light or
        dark colours, when the system switches between light and dark; Perch keeps running in the
        dock, so without this it would keep the colours from when it started. A copy is loaded just
        above GTK's own (stale) one and reloaded on every change in that folder."""
        folder = Path(GLib.get_user_config_dir()) / "gtk-4.0"
        self._user_css = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(display, self._user_css, Gtk.STYLE_PROVIDER_PRIORITY_USER + 1)
        self._user_css_reload = 0

        def reload() -> bool:
            self._user_css_reload = 0
            css_file = folder / "gtk.css"
            if css_file.is_file():
                self._user_css.load_from_path(str(css_file))
            else:
                self._user_css.load_from_string("")
            return GLib.SOURCE_REMOVE

        def changed(*_args) -> None:
            # A theme tool writes several files in a row: reload once they're done.
            if self._user_css_reload:
                GLib.source_remove(self._user_css_reload)
            self._user_css_reload = GLib.timeout_add(250, reload)

        try:
            self._user_css_monitor = Gio.File.new_for_path(str(folder)).monitor_directory(Gio.FileMonitorFlags.WATCH_MOVES, None)
            self._user_css_monitor.connect("changed", changed)
        except GLib.Error as err:
            print(f"[perch] can't watch {folder}: {err.message}")

    def _on_update_available(self) -> None:
        window = self._window()
        window.show_update_banner()
        if not window.is_on_screen():
            notification = Gio.Notification.new("Perch was updated")
            notification.set_body("Restart Perch to use the new version.")
            notification.set_icon(Gio.ThemedIcon.new(APP_ID))
            notification.add_button("Restart", "app.restart-update")
            notification.set_default_action("app.restart-update")
            self.send_notification("perch-update", notification)

    def _window(self) -> PerchWindow:
        # Keep our own reference: a hidden window is not reliably the "active" one.
        if self._main_window is None:
            self._main_window = PerchWindow(self, self.sidecar)
        return self._main_window

    def do_activate(self):
        # Also what launching Perch again ends up calling: bring the (minimized) window back.
        self._window().present()
        # Coming back to Perch is when a fresh install matters; don't wait for the portal's poll.
        if hasattr(self, "updates"):
            self.updates.check_now()

    def _quit(self) -> None:
        if self.quitting:
            return
        self.quitting = True
        if self._main_window is not None:
            self._main_window.prepare_quit()
            self._main_window.destroy()
            self._main_window = None
        self.release()
        self.quit()

    def _about(self):
        Adw.AboutDialog(
            application_name=APP_NAME,
            application_icon=APP_ID,
            version=VERSION,
            comments="A GTK client for X — timelines, notifications, messages and Spaces — powered by rettiwt-api.",
            license_type=Gtk.License.MIT_X11,
        ).present(self.get_active_window())


def main(argv: list[str]) -> int:
    Gst.init(None)
    return PerchApplication().run(argv)
