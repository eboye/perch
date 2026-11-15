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
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.DEFAULT_FLAGS)
        self.sidecar = Sidecar()
        self.connect("shutdown", lambda *_: self.sidecar.stop())

    def do_startup(self):
        Adw.Application.do_startup(self)
        css = Gtk.CssProvider()
        css.load_from_path(str(Path(__file__).with_name("style.css")))
        display = Gdk.Display.get_default()
        Gtk.StyleContext.add_provider_for_display(display, css, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)
        Gtk.IconTheme.get_for_display(display).add_search_path(str(Path(__file__).with_name("icons")))

        for name, handler, accels in (
            ("quit", lambda *_: self.quit(), ["<Control>q"]),
            ("about", lambda *_: self._about(), []),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", handler)
            self.add_action(action)
            self.set_accels_for_action(f"app.{name}", accels)

        # Target of desktop notifications: brings the window up on the given tab.
        show_tab = Gio.SimpleAction.new("show-tab", GLib.VariantType.new("s"))
        show_tab.connect("activate", lambda _a, tab: self._window().show_tab(tab.get_string()))
        self.add_action(show_tab)

    def _window(self) -> PerchWindow:
        return self.get_active_window() or PerchWindow(self, self.sidecar)

    def do_activate(self):
        self._window().present()

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
