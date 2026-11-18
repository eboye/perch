"""Small animations: one-shot CSS animations started by adding a class (see style.css, "Motion").

They follow the desktop's "Reduce animation" setting by themselves: with gtk-enable-animations off,
GTK doesn't run CSS animations or transitions, so the widgets simply show their final state.
"""

from gi.repository import GLib, Gtk

from perch import images


def play(widget: Gtk.Widget, css_class: str, duration_ms: int) -> None:
    """Run the CSS animation that ``css_class`` starts on ``widget`` (again, if it's running),
    then take the class off so it can be played the next time."""
    if widget.has_css_class(css_class):
        widget.remove_css_class(css_class)  # restart: GTK starts an animation when its class is added
    widget.add_css_class(css_class)

    def done() -> bool:
        widget.remove_css_class(css_class)
        return GLib.SOURCE_REMOVE

    # (A little longer than the animation, so it never gets cut short.)
    GLib.timeout_add(duration_ms + 50, done)


def load_picture(url: str | None, picture: Gtk.Picture, **kwargs) -> None:
    """``images.load`` into ``picture``; a picture that had to be downloaded fades in, while one
    already in memory just appears (fading those in would flicker the whole list as it scrolls)."""
    waiting = False

    def show(texture) -> None:
        if not texture:
            return
        picture.set_paintable(texture)
        if waiting:
            play(picture, "fade-in", 260)

    images.load(url, show, **kwargs)
    waiting = True
