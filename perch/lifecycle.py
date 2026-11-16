"""Freeing widgets Perch is done with.

Perch connects Python closures to widget signals (``button.connect("clicked", lambda *_:
self.reload())``). GTK keeps those closures on its own side, where Python's garbage collector
can't see them, so a page or row whose child holds a closure that refers back to it is part of a
reference cycle that's never collected: closed post pages, conversations and every timeline row
replaced by a reload stayed in memory for good. Perch runs for days, so that added up to gigabytes.

``release()`` breaks those cycles once a widget tree is no longer used, by dropping the signal
handlers in it (and on the actions Perch attached to it).
"""

from gi.repository import GObject, Gtk


def release(widget: Gtk.Widget | None) -> None:
    """Drop every signal handler in ``widget``'s tree. Only for widgets that are gone for good
    (popped pages, rows removed from a list): they won't react to anything afterwards."""
    if widget is None:
        return
    stack = [widget]
    while stack:
        current = stack.pop()
        child = current.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
        for extra in getattr(current, "releasable", ()):  # e.g. Gio actions whose callbacks capture a page
            GObject.signal_handlers_destroy(extra)
        if isinstance(current, Gtk.TextView):  # handlers on the text buffer (not a widget)
            GObject.signal_handlers_destroy(current.get_buffer())
        # Keyboard shortcuts with Python callbacks (Gtk.CallbackAction) live in shortcut controllers.
        controllers = current.observe_controllers()
        shortcut_controllers = [controllers.get_item(i) for i in range(controllers.get_n_items())]
        for controller in shortcut_controllers:
            GObject.signal_handlers_destroy(controller)
            if isinstance(controller, Gtk.ShortcutController):
                current.remove_controller(controller)
        GObject.signal_handlers_destroy(current)
