"""Freeing widgets Perch is done with.

Perch connects Python closures to widget signals (``button.connect("clicked", lambda *_:
self.reload())``). GTK keeps those closures on its own side, where Python's garbage collector
can't see them, so a page or row whose child holds a closure that refers back to it is part of a
reference cycle that's never collected: closed post pages, conversations and every timeline row
replaced by a reload stayed in memory for good. Perch runs for days, so that added up to gigabytes.

``release()`` breaks those cycles once a widget tree is no longer used, by dropping the signal
handlers in it (and on the actions Perch attached to it).
"""

from gi.repository import Adw, GLib, GObject, Gtk

# GTK / libadwaita parts that disconnect their own handlers while being destroyed (Perch connects
# nothing to them): leave their handlers alone, or GLib warns "no handler with id".
_KEEP_HANDLERS = (Gtk.Text, Adw.PreferencesGroup, Adw.Spinner)


def release_dialog(dialog) -> None:
    """A closed Adw.Dialog: free Perch's content and the dialog's own keyboard shortcuts, but not
    libadwaita's internals (its sheet disconnects its own handlers while being destroyed)."""
    if isinstance(dialog, Adw.AlertDialog):
        # An alert's child is libadwaita's own layout (heading, response buttons): only the extra
        # child is Perch's. Detach it first, so the alert drops the handler it keeps on it.
        extra = dialog.get_extra_child()
        dialog.set_extra_child(None)
        release(extra)
    else:
        release(dialog.get_child())
    controllers = dialog.observe_controllers()
    for controller in [controllers.get_item(i) for i in range(controllers.get_n_items())]:
        if isinstance(controller, Gtk.ShortcutController):
            GObject.signal_handlers_destroy(controller)
            dialog.remove_controller(controller)


def _disconnect_signals(instance, names) -> None:
    for name in names:
        signal_id = GObject.signal_lookup(name, type(instance))
        handler_id = GObject.signal_handler_find(instance, GObject.SignalMatchType.ID, signal_id, 0, None, None, None)
        while handler_id:
            GObject.signal_handler_disconnect(instance, handler_id)
            handler_id = GObject.signal_handler_find(instance, GObject.SignalMatchType.ID, signal_id, 0, None, None, None)


def release(widget: Gtk.Widget | None) -> None:
    """Drop every signal handler in ``widget``'s tree. Only for widgets that are gone for good
    (popped pages, rows removed from a list): they won't react to anything afterwards.

    The work waits until GTK is idle: a page is often popped by an event still travelling through
    it (a click on its own back button), and removing controllers GTK is running crashes it."""
    if widget is not None:
        GLib.idle_add(lambda: _release_now(widget) or GLib.SOURCE_REMOVE)


def _release_now(widget: Gtk.Widget) -> None:
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
        if isinstance(current, _KEEP_HANDLERS):
            continue
        if isinstance(current, Adw.EntryRow):  # libadwaita keeps handlers on it too: drop only Perch's
            _disconnect_signals(current, ("changed", "apply", "entry-activated"))
            continue
        # Keyboard shortcuts with Python callbacks (Gtk.CallbackAction) live in shortcut controllers.
        controllers = current.observe_controllers()
        shortcut_controllers = [controllers.get_item(i) for i in range(controllers.get_n_items())]
        for controller in shortcut_controllers:
            GObject.signal_handlers_destroy(controller)
            if isinstance(controller, Gtk.ShortcutController):
                current.remove_controller(controller)
        GObject.signal_handlers_destroy(current)
