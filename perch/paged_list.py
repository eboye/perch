"""Cursor-paged list view shared by timelines, notifications and the DM inbox.

Subclasses implement ``fetch_page``, ``make_row`` and optionally ``item_id`` / ``on_page_loaded``.
"""

from gi.repository import Adw, GLib, GObject, Gtk

from perch.sidecar import RpcError


class PagedListView(Gtk.Stack):
    __gsignals__ = {
        # Emitted with the RpcError when loading fails (window decides: sign-in page, toast, ...).
        "load-failed": (GObject.SignalFlags.RUN_FIRST, None, (object,)),
    }

    error_title = "Couldn't load this list"
    empty_title = "Nothing here yet"
    empty_icon = "mail-inbox-symbolic"
    end_label = "You're all caught up"
    empty_label = "Nothing here yet"  # footer text when `always_show_list` and there are no rows
    list_css = "tweet-list"
    # Keep the list (and its header) on screen while loading, empty or failing, instead of switching
    # to the full-page spinner / empty / error pages. Used by the post page (header = the post).
    always_show_list = False

    def __init__(self, controller):
        super().__init__(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._controller = controller
        self._cursor: str | None = None
        self._seen: set[str] = set()
        self._loading = False
        self._retry_first = False
        self._generation = 0  # bumps on reload so stale responses are dropped
        self.loaded_once = False

        spinner = Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32)
        self.add_named(spinner, "loading")

        self._error_page = Adw.StatusPage(icon_name="network-error-symbolic", title=self.error_title)
        retry = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER)
        retry.add_css_class("pill")
        retry.connect("clicked", lambda *_: self.reload())
        self._error_page.set_child(retry)
        self.add_named(self._error_page, "error")

        self.add_named(Adw.StatusPage(icon_name=self.empty_icon, title=self.empty_title), "empty")

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class(self.list_css)
        self._list.connect("row-activated", lambda _list, row: self.on_row_activated(row))

        self._footer = Gtk.Stack(margin_top=12, margin_bottom=24, hhomogeneous=False, vhomogeneous=False)
        self._footer.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, width_request=24, height_request=24), "loading")
        more = Gtk.Button(label="Load More", halign=Gtk.Align.CENTER)
        more.add_css_class("pill")
        more.connect("clicked", lambda *_: self.load_more())
        self._footer.add_named(more, "more")
        self._end_label = Gtk.Label(label=self.end_label, css_classes=["dim-label"])
        self._footer.add_named(self._end_label, "end")

        self._column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._column.append(self._list)
        self._column.append(self._footer)
        column = self._column
        clamp = Adw.Clamp(maximum_size=640, tightening_threshold=480, child=column)

        self._scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True)
        self._scroller.connect("edge-reached", self._on_edge_reached)
        self.add_named(self._scroller, "list")
        self.set_visible_child_name("loading")

    def set_header(self, widget: Gtk.Widget) -> None:
        """A fixed widget above the rows, scrolling with them."""
        self._column.prepend(widget)

    # -- subclass hooks -----------------------------------------------------

    def fetch_page(self, cursor: str | None, callback) -> None:
        raise NotImplementedError

    def make_row(self, item: dict, page: dict) -> Gtk.ListBoxRow:
        raise NotImplementedError

    def item_id(self, item: dict) -> str | None:
        return item.get("id")

    def on_page_loaded(self, page: dict, first: bool) -> None:
        """Called after a page's rows were appended."""

    def on_row_activated(self, row: Gtk.ListBoxRow) -> None:
        url = getattr(row, "url", None)
        if url:
            Gtk.UriLauncher(uri=url).launch(self.get_root(), None, None, None)

    # -- loading ------------------------------------------------------------

    def reload(self) -> None:
        self._generation += 1
        self._cursor = None
        self._seen.clear()
        self._loading = False
        self._retry_first = False
        while row := self._list.get_row_at_index(0):
            self._list.remove(row)
        self.set_visible_child_name("list" if self.always_show_list else "loading")
        self._fetch()

    def load_more(self) -> None:
        if self._loading:
            return
        if self._retry_first:
            self._retry_first = False
            self._cursor = None
            self._fetch()
        elif self._cursor:
            self._fetch()

    def _fetch(self) -> None:
        self._loading = True
        self.loaded_once = True
        self._footer.set_visible_child_name("loading")
        generation = self._generation
        first = self._cursor is None
        self.fetch_page(self._cursor, lambda result, error: self._on_page(generation, first, result, error))

    def _on_page(self, generation: int, first: bool, result: dict | None, error: RpcError | None) -> None:
        if generation != self._generation:
            return
        self._loading = False
        if error:
            if self._list.get_row_at_index(0) is None and not self.always_show_list:
                self._error_page.set_description(GLib.markup_escape_text(str(error)))
                self.set_visible_child_name("error")
            else:
                # Keep what's on screen; "Load More" doubles as retry (of the first page if needed).
                self._retry_first = first
                self._footer.set_visible_child_name("more")
            self.emit("load-failed", error)
            return

        added = 0
        for item in result.get("list") or []:
            key = self.item_id(item)
            if not key or key in self._seen:
                continue
            self._seen.add(key)
            self._list.append(self.make_row(item, result))
            added += 1

        next_cursor = result.get("next") or None
        # A page with nothing new means the cursor is exhausted (X sometimes keeps returning one).
        self._cursor = next_cursor if added else None
        self._footer.set_visible_child_name("more" if self._cursor else "end")
        has_rows = self._list.get_row_at_index(0) is not None
        self._end_label.set_label(self.end_label if has_rows else self.empty_label)
        self.on_page_loaded(result, first)  # before the stack flips, so visibility listeners see final state
        self.set_visible_child_name("list" if has_rows or self.always_show_list else "empty")

    # -- misc ---------------------------------------------------------------

    def _on_edge_reached(self, _scroller, position: Gtk.PositionType) -> None:
        if position == Gtk.PositionType.BOTTOM:
            self.load_more()

    def scroll_to_top(self) -> None:
        self._scroller.get_vadjustment().set_value(0)
