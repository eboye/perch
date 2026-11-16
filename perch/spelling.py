"""Spell checking for the places you write (the compose box, thread posts, image descriptions, DMs).

libspelling underlines misspelled words and puts suggestions, "Add to Dictionary" and a choice of
language in the right-click menu. It checks a GtkSource.View (a Gtk.TextView), so text boxes here
are made with ``text_view()``. Without libspelling (it's bundled in the Flatpak) they are plain
text views.
"""

import gi

from gi.repository import GObject, Gtk

try:
    gi.require_version("GtkSource", "5")
    gi.require_version("Spelling", "1")
    from gi.repository import GtkSource, Spelling

    GtkSource.init()
except (ImportError, ValueError):  # not installed
    GtkSource = Spelling = None


def available() -> bool:
    return Spelling is not None


def _checker():
    """The shared checker, in the desktop's language — or, without a dictionary for that, another
    variant of it (en_GB for en_AU), else English. The right-click menu switches language, and that
    choice is kept (settings "spell_language")."""
    checker = Spelling.Checker.get_default()
    codes = [language.get_code() for language in checker.get_provider().list_languages()]
    from perch import settings

    if (saved := settings.get("spell_language")) in codes:  # the one you picked last time
        if checker.get_language() != saved:
            checker.set_language(saved)
        return checker
    if checker.get_language():
        return checker
    from gi.repository import GLib

    wanted = [name.split(".")[0].split("@")[0] for name in GLib.get_language_names() if name not in ("C", "POSIX")]
    for code in wanted + [w.split("_")[0] for w in wanted] + ["en_US", "en_GB"]:
        match = next((c for c in codes if c == code or c.split("_")[0] == code), None)
        if match:
            checker.set_language(match)
            return checker
    return checker  # (no dictionary in a language you write: better no checking than a wrong one)


def _remember_language(adapter, _pspec) -> None:
    from perch import settings

    if (code := adapter.get_language()) and code != settings.get("spell_language"):
        settings.set("spell_language", code)


def _on_right_click(_gesture, _n_press, x, y, view) -> None:
    buffer = view.get_buffer()
    adapter = getattr(view, "spelling", None)
    if adapter is None or buffer.get_has_selection():
        return
    bx, by = view.window_to_buffer_coords(Gtk.TextWindowType.WIDGET, int(x), int(y))
    found, where = view.get_iter_at_location(bx, by)
    if found:
        buffer.place_cursor(where)
    adapter.update_corrections()


def text_view(**props) -> Gtk.TextView:
    """A text view with spell checking (in the language the right-click menu picks; the system's
    language at first)."""
    if Spelling is None:
        return Gtk.TextView(**props)
    buffer = props.pop("buffer", None)
    source_buffer = GtkSource.Buffer()
    if buffer is not None:  # (callers pass a buffer only to start with its text)
        source_buffer.set_text(buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False))
    view = GtkSource.View(buffer=source_buffer, **props)
    adapter = Spelling.TextBufferAdapter.new(source_buffer, _checker())
    view.set_extra_menu(adapter.get_menu_model())
    view.insert_action_group("spelling", adapter)
    adapter.set_enabled(True)
    # A language picked from the right-click menu is kept for next time.
    adapter.connect("notify::language", _remember_language)
    # Suggestions are for the word at the cursor, and a right-click doesn't move it: move it to the
    # clicked word first (unless text is selected), then let the menu open.
    right_click = Gtk.GestureClick(button=3, propagation_phase=Gtk.PropagationPhase.CAPTURE)
    right_click.connect("pressed", _on_right_click, view)
    view.add_controller(right_click)
    view.spelling = adapter  # keep it alive with the view
    return view


class MessageBox(Gtk.ScrolledWindow):
    """The DM message box: grows to a few lines as you type, Enter sends, Shift+Enter starts a new
    line, spell checked. Speaks enough of Gtk.Entry (get_text, set_text, "activate", "changed",
    placeholder) for the conversation page."""

    __gsignals__ = {
        "activate": (GObject.SignalFlags.RUN_LAST, None, ()),
        "changed": (GObject.SignalFlags.RUN_LAST, None, ()),
    }

    def __init__(self, placeholder: str = ""):
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True, max_content_height=140, hexpand=True)
        self.add_css_class("message-box")
        self._view = text_view(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=7, bottom_margin=7, left_margin=10, right_margin=10)
        self._view.update_property([Gtk.AccessibleProperty.LABEL, Gtk.AccessibleProperty.PLACEHOLDER], [placeholder or "Message", placeholder])
        self._placeholder = Gtk.Label(label=placeholder, xalign=0, valign=Gtk.Align.START, margin_top=7, margin_start=12, can_target=False, css_classes=["dim-label"])
        overlay = Gtk.Overlay(child=self._view)
        overlay.add_overlay(self._placeholder)
        self.set_child(overlay)
        self._view.get_buffer().connect("changed", lambda *_: self._on_changed())
        keys = Gtk.EventControllerKey(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self._view.add_controller(keys)

    @property
    def view(self) -> Gtk.TextView:
        return self._view

    def _on_key(self, _controller, keyval, _keycode, state) -> bool:
        from gi.repository import Gdk

        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter) and not state & (Gdk.ModifierType.SHIFT_MASK | Gdk.ModifierType.CONTROL_MASK):
            self.emit("activate")
            return True
        return False

    def _on_changed(self) -> None:
        self._placeholder.set_visible(not self.get_text())
        self.emit("changed")

    def get_text(self) -> str:
        buffer = self._view.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def set_text(self, text: str) -> None:
        self._view.get_buffer().set_text(text)

    def grab_focus(self) -> bool:
        return self._view.grab_focus()

    def set_editable(self, editable: bool) -> None:
        self._view.set_editable(editable)
