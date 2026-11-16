"""Cursor-paged list view shared by timelines, notifications and the DM inbox.

Subclasses implement ``fetch_page``, ``make_row`` and optionally ``item_id`` / ``on_page_loaded``.
"""

from gi.repository import Adw, GLib, GObject, Graphene, Gtk

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
    # Hide posts containing a muted word (timelines, lists, mentions: where X applies muted words).
    hide_muted_words = False

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
        retry.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
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
        # A pill over the top of the list for "N new posts" (timelines; see offer_new_posts).
        overlay = Gtk.Overlay(child=self._scroller)
        self._new_posts = Gtk.Button(halign=Gtk.Align.CENTER, valign=Gtk.Align.START, margin_top=10, visible=False, css_classes=["pill", "suggested-action", "new-posts"])
        self._new_posts.connect("clicked", lambda *_: self._show_new_posts())
        self._new_posts_loading = False
        overlay.add_overlay(self._new_posts)
        self.add_named(overlay, "list")
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
        self._new_posts.set_visible(False)
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
            row = self.make_row(item, result)
            self._list.append(row)
            self._filter_row(row)
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

    # -- new posts pill ---------------------------------------------------------

    def count_new(self, items: list[dict]) -> int:
        """How many of ``items`` (a fresh first page) are newer than everything loaded here."""
        loaded = [int(key) for key in self._seen if str(key).isdigit()]
        newest = max(loaded) if loaded else None
        return sum(
            1
            for item in items
            if (key := self.item_id(item)) and key not in self._seen and str(key).isdigit() and (newest is None or int(key) > newest)
        )

    def offer_new_posts(self, count: int) -> None:
        if count <= 0 or not self.loaded_once:
            return
        self._new_posts.set_label(f"↑ {count} new post{'s' if count != 1 else ''}")
        self._new_posts.set_tooltip_text("Show the new posts")
        self._new_posts.set_visible(True)

    def _show_new_posts(self) -> None:
        """Add the new posts above the ones already shown (no full reload), with a spinner in the
        pill meanwhile. The first page normally comes from the cache the poll just filled."""
        if self._new_posts_loading:
            return
        self._new_posts_loading = True
        busy = Gtk.Box(spacing=8)
        busy.append(Adw.Spinner(width_request=16, height_request=16))
        busy.append(Gtk.Label(label="Loading…"))
        self._new_posts.set_child(busy)
        generation = self._generation
        self.fetch_page(None, lambda result, error: self._on_new_posts(generation, result, error))

    def _on_new_posts(self, generation: int, result: dict | None, error) -> None:
        self._new_posts_loading = False
        self._new_posts.set_visible(False)
        if generation != self._generation:
            return  # the list was reloaded meanwhile
        if error:
            self.emit("load-failed", error)
            return
        items = result.get("list") or []
        # Everything above the first post already shown is new. If none of the page is shown yet,
        # more came in than one page holds: there'd be a gap, so start over instead.
        first_known = next((index for index, item in enumerate(items) if self.item_id(item) in self._seen), None)
        if first_known is None:
            self.reload()
            return
        position = 0
        for item in items[:first_known]:
            key = self.item_id(item)
            if not key or key in self._seen:
                continue
            self._seen.add(key)
            row = self.make_row(item, result)
            self._list.insert(row, position)
            self._filter_row(row)
            position += 1
        self.scroll_to_top()

    # -- keyboard navigation (see window._on_key) ------------------------------

    @property
    def list_box(self) -> Gtk.ListBox:
        return self._list

    def visible_rows(self) -> list[Gtk.ListBoxRow]:
        """Rows on the page in order (hidden / filtered-out rows skipped)."""
        rows, index = [], 0
        while row := self._list.get_row_at_index(index):
            if row.get_visible() and row.get_child_visible() and row.get_mapped():
                rows.append(row)
            index += 1
        return rows

    def first_row_on_screen(self) -> Gtk.ListBoxRow | None:
        for row in self.visible_rows():
            ok, point = row.compute_point(self._scroller, Graphene.Point().init(0, 0))
            if ok and point.y + row.get_height() / 2 >= 0:
                return row
        return None

    def scroll_to_row(self, row: Gtk.ListBoxRow) -> None:
        """Scroll just enough to show the whole row (or its top, if it's taller than the view)."""
        ok, point = row.compute_point(self._scroller, Graphene.Point().init(0, 0))
        if not ok:
            return
        adjustment = self._scroller.get_vadjustment()
        view_height = self._scroller.get_height()
        if point.y < 0 or row.get_height() > view_height:
            adjustment.set_value(adjustment.get_value() + point.y - 8)
        elif point.y + row.get_height() > view_height:
            adjustment.set_value(adjustment.get_value() + point.y + row.get_height() - view_height + 8)

    def hide_rows(self, predicate) -> None:
        """Hide the loaded rows ``predicate(row)`` picks (e.g. posts by someone you just muted)."""
        index = 0
        while row := self._list.get_row_at_index(index):
            if predicate(row):
                row.hidden_by_author = True
                row.set_visible(False)
            index += 1

    # -- muted words --------------------------------------------------------------

    def _filter_row(self, row: Gtk.ListBoxRow) -> None:
        """Hide a post that contains one of your muted words (views with ``hide_muted_words``)."""
        if not self.hide_muted_words or not hasattr(row, "tweet"):
            return
        row.hidden_by_word = self._controller.hides_by_muted_word(row.tweet)
        row.set_visible(not row.hidden_by_word and not getattr(row, "hidden_by_author", False))

    def refilter(self) -> None:
        """Muted words changed: re-check every loaded post (unmuting a word brings its posts back)."""
        index = 0
        while row := self._list.get_row_at_index(index):
            self._filter_row(row)
            index += 1

    def scroll_to_top(self) -> None:
        self._scroller.get_vadjustment().set_value(0)
