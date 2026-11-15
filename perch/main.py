import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version("Gst", "1.0")

from pathlib import Path  # noqa: E402

from gi.repository import Adw, Gdk, Gio, GLib, Gst, Gtk  # noqa: E402

from perch import APP_ID, APP_NAME, VERSION  # noqa: E402
from perch.sidecar import Sidecar  # noqa: E402
from perch.window import PerchWindow  # noqa: E402


class PerchApplication(Adw.Application):
    """Closing the window only hides it (Perch keeps polling, notifying and playing Spaces);
    the app ends only through the "quit" action — the dock's right-click Quit (GNOME Shell
    activates app.quit when an app exports it), Ctrl+Q, or Quit in the main menu."""

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.sidecar = Sidecar()
        self.quitting = False
        self._main_window: PerchWindow | None = None
        self.connect("shutdown", lambda *_: self.sidecar.stop())

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(str(Path(__file__).with_name("icons")))

        # Stay alive while the window is hidden; released again in _quit().
        self.hold()

        for name, handler, accels in (
            ("quit", lambda *_: self._quit(), ["<Control>q"]),
            ("about", lambda *_: self._about(), []),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
            self.set_accels_for_action(f"app.{name}", accels)

        # Ctrl+W hides the window (GtkWindow's built-in close action, which goes through close-request).
        self.set_accels_for_action("window.close", ["<Control>w"])

        # Target of desktop notifications: brings the window up on the given tab.
        show_tab = Gio.SimpleAction.new("show-tab", GLib.VariantType.new("s"))
        show_tab.connect("activate", lambda _a, tab: self._window().show_tab(tab.get_string()))
        self.add_action(show_tab)

    def _window(self) -> PerchWindow:
        # Keep our own reference: a hidden window is not reliably the "active" one.
        if self._main_window is None:
            self._main_window = PerchWindow(self, self.sidecar)
        return self._main_window

    def do_activate(self):
        # Also what a dock click on the running app ends up calling: re-show the hidden window.
        self._window().present()

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
