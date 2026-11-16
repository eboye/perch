"""Search page: a search box with Top / Latest / People / Media results."""

import re

from gi.repository import Adw, GLib, Gtk, Pango

from perch import format as fmt
from perch import settings
from perch import images
from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow

MODES = (("top", "Top"), ("latest", "Latest"), ("people", "People"), ("media", "Media"))


class UserRow(Gtk.ListBoxRow):
    """A person in search results; activating it opens their profile."""

    def __init__(self, user: dict, me_id: str | None):
        super().__init__(activatable=True)
        self.add_css_class("tweet")
        self.user = user
        row = Gtk.Box(spacing=12)
        avatar = Adw.Avatar(size=44, text=user.get("fullName") or user.get("userName") or "", show_initials=True)
        avatar.set_valign(Gtk.Align.START)
        images.load(fmt.avatar_url(user.get("profileImage")), lambda tex: tex and avatar.set_custom_image(tex))
        row.append(avatar)

        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        name_row = Gtk.Box(spacing=4)
        name_row.append(Gtk.Label(label=user.get("fullName") or user.get("userName") or "?", xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"]))
        if user.get("isVerified"):
            name_row.append(Gtk.Image(icon_name="perch-verified-symbolic", css_classes=["verified"]))
        text.append(name_row)
        handle_row = Gtk.Box(spacing=8)
        handle_row.append(Gtk.Label(label=f"@{user.get('userName', '')}", xalign=0, css_classes=["dim-label"]))
        if user.get("followsYou") and user.get("id") != me_id:
            handle_row.append(Gtk.Label(label="Follows you", css_classes=["follows-you", "caption"]))
        text.append(handle_row)
        if user.get("description"):
            bio = Gtk.Label(label=user["description"].replace("\n", " "), xalign=0, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END)
            text.append(bio)
        row.append(text)

        followers = user.get("followersCount") or 0
        counts = Gtk.Label(
            label=f"{fmt.compact_count(followers) or '0'} {'follower' if followers == 1 else 'followers'}",
            valign=Gtk.Align.START,
            css_classes=["dim-label", "caption"],
        )
        row.append(counts)
        self._box = row
        self.set_child(row)

    def add_suffix(self, widget: Gtk.Widget) -> None:
        """A widget at the row's end (e.g. a Remove button)."""
        widget.set_valign(Gtk.Align.START)
        self._box.append(widget)


class _SearchResults(PagedListView):
    error_title = "Search failed"
    empty_title = "No results"
    empty_icon = "system-search-symbolic"
    end_label = "No more results"

    def __init__(self, controller):
        self.query = ""
        self.mode = "top"
        super().__init__(controller)

    def search(self, query: str, mode: str) -> None:
        self.query, self.mode = query, mode
        self.reload()

    def fetch_page(self, cursor, callback) -> None:
        if self.mode == "people":
            self._controller.sidecar.call("search.users", {"query": self.query, "cursor": cursor}, callback)
        else:
            self._controller.sidecar.call("search.posts", {"query": self.query, "mode": self.mode, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> Gtk.ListBoxRow:
        if self.mode == "people":
            return UserRow(item, self._controller.me_id)
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, UserRow):
            self._controller.open_profile(row.user)
        else:
            row.activate_post()


class SearchPage(Adw.NavigationPage):
    def __init__(self, controller, query: str = ""):
        super().__init__(title="Search", tag="search")
        self._controller = controller

        self._entry = Gtk.SearchEntry(placeholder_text="Search X — try from:user, #tag or \"exact words\"", hexpand=True)
        self._entry.connect("activate", lambda *_: self._run())
        # Ctrl+F / Escape inside the box behave like other GNOME search fields.
        self._entry.connect("stop-search", lambda *_: self._entry.set_text(""))
        self._entry.connect("changed", lambda *_: self._sync_star())
        headerbar = Adw.HeaderBar(title_widget=Adw.Clamp(maximum_size=520, child=self._entry, hexpand=True))
        # Save this search (☆), and a Filters popover that writes X's search operators.
        self._star = Gtk.Button(icon_name="non-starred-symbolic", tooltip_text="Save This Search", sensitive=False)
        self._star.connect("clicked", lambda *_: self._toggle_saved())
        headerbar.pack_end(self._star)
        self._filters = Gtk.MenuButton(icon_name="view-filter-symbolic", tooltip_text="Filters", popover=self._build_filters())
        headerbar.pack_end(self._filters)

        self._modes = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6)
        self._modes.add_css_class("round")
        for name, label in MODES:
            self._modes.add(Adw.Toggle(name=name, label=label))
        self._modes.set_active_name("top")
        self._modes.connect("notify::active-name", lambda *_: self._run(switching_mode=True))

        # Before a search: your saved searches, recent ones, and a short how-to.
        intro = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title="Search X",
            description="Find posts and people. Press Enter to search, or use Filters.\n"
            "Operators work too: <b>from:</b>user, <b>to:</b>user, <b>#</b>tag, <b>\"exact phrase\"</b>, <b>-</b>word, <b>lang:</b>en",
        )
        intro.add_css_class("compact")
        self._saved_group = Adw.PreferencesGroup(title="Saved Searches")
        self._recent_group = Adw.PreferencesGroup(title="Recent Searches")
        clear_recent = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER, css_classes=["flat"])
        clear_recent.connect("clicked", lambda *_: self._clear_recent())
        self._recent_group.set_header_suffix(clear_recent)
        self._list_rows: list[tuple[Adw.PreferencesGroup, Gtk.Widget]] = []
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_bottom=24)
        column.append(intro)
        column.append(self._saved_group)
        column.append(self._recent_group)
        idle = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=Adw.Clamp(maximum_size=560, child=column, margin_start=12, margin_end=12))
        self._render_lists()
        self._results = _SearchResults(controller)
        self._results.connect("load-failed", lambda _v, error: controller.report_error(error, "Search failed"))
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(idle, "idle")
        self._stack.add_named(self._results, "results")

        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(headerbar)
        view.add_top_bar(self._modes)
        self.set_child(view)
        self.connect("shown", lambda *_: self._entry.grab_focus())

        if query:
            self.search(query)

    def search(self, query: str, mode: str | None = None) -> None:
        """Run a search from outside (hashtag / search links)."""
        self._entry.set_text(query)
        self._entry.set_position(-1)
        if mode:
            self._modes.set_active_name(mode)  # triggers _run via notify
        self._run()

    def reload(self) -> None:
        if self._results.query:
            self._results.reload()

    def _run(self, switching_mode: bool = False) -> None:
        query = self._entry.get_text().strip()
        if not query:
            if not switching_mode:
                self._stack.set_visible_child_name("idle")
            return
        mode = self._modes.get_active_name() or "top"
        if switching_mode and query == self._results.query and mode == self._results.mode:
            return
        self._stack.set_visible_child_name("results")
        self._results.search(query, mode)
        self.set_title(f"Search: {query}")
        self._remember(query)

    # -- saved and recent searches (per account) -------------------------------------

    def _saved(self) -> list[str]:
        return list(settings.account_get("saved_searches") or [])

    def _recent(self) -> list[str]:
        return list(settings.account_get("recent_searches") or [])

    def _remember(self, query: str) -> None:
        recent = [q for q in self._recent() if q != query]
        settings.account_set("recent_searches", ([query] + recent)[:MAX_RECENT])
        self._render_lists()
        self._sync_star()

    def _toggle_saved(self) -> None:
        query = self._entry.get_text().strip()
        if not query:
            return
        saved = self._saved()
        if query in saved:
            saved.remove(query)
            self._controller.toast(f"Removed “{query}” from saved searches")
        else:
            saved.append(query)
            self._controller.toast(f"Saved “{query}”")
        settings.account_set("saved_searches", saved)
        self._render_lists()
        self._sync_star()

    def _sync_star(self) -> None:
        query = self._entry.get_text().strip()
        saved = query in self._saved()
        self._star.set_sensitive(bool(query))
        self._star.set_icon_name("starred-symbolic" if saved else "non-starred-symbolic")
        self._star.set_tooltip_text("Remove from Saved Searches" if saved else "Save This Search")

    def _clear_recent(self) -> None:
        settings.account_set("recent_searches", [])
        self._render_lists()

    def _forget(self, key: str, query: str) -> None:
        settings.account_set(key, [q for q in (settings.account_get(key) or []) if q != query])
        self._render_lists()
        self._sync_star()

    def _render_lists(self) -> None:
        for group, row in self._list_rows:
            group.remove(row)
        self._list_rows = []
        for group, key, queries, icon in (
            (self._saved_group, "saved_searches", self._saved(), "starred-symbolic"),
            (self._recent_group, "recent_searches", self._recent(), "document-open-recent-symbolic"),
        ):
            group.set_visible(bool(queries))
            for query in queries:
                row = Adw.ActionRow(title=GLib.markup_escape_text(query), activatable=True)
                row.add_prefix(Gtk.Image(icon_name=icon))
                remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text=f"Remove “{query}”", valign=Gtk.Align.CENTER, css_classes=["flat", "circular"])
                remove.connect("clicked", lambda *_, k=key, q=query: self._forget(k, q))
                row.add_suffix(remove)
                row.connect("activated", lambda *_, q=query: self.search(q))
                group.add(row)
                self._list_rows.append((group, row))

    # -- filters --------------------------------------------------------------

    def _build_filters(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=8, margin_bottom=8, margin_start=8, margin_end=8, width_request=300)
        box.append(Gtk.Label(label="Filters", css_classes=["heading"]))
        group = Adw.PreferencesGroup()
        self._f_from = Adw.EntryRow(title="From (@user)")
        self._f_likes = Adw.SpinRow.new_with_range(0, 1_000_000, 10)
        self._f_likes.set_title("Minimum likes")
        self._f_since = Adw.EntryRow(title="Since (YYYY-MM-DD)")
        self._f_until = Adw.EntryRow(title="Until (YYYY-MM-DD)")
        self._f_lang = Adw.EntryRow(title="Language (e.g. en, sr)")
        self._f_noreplies = Adw.SwitchRow(title="Leave out replies")
        for row in (self._f_from, self._f_likes, self._f_since, self._f_until, self._f_lang, self._f_noreplies):
            group.add(row)
        box.append(group)
        self._f_error = Gtk.Label(css_classes=["error", "caption"], visible=False, wrap=True)
        box.append(self._f_error)
        buttons = Gtk.Box(spacing=8, homogeneous=True)
        reset = Gtk.Button(label="Clear Filters")
        reset.connect("clicked", lambda *_: self._apply_filters(clear=True))
        apply = Gtk.Button(label="Search", css_classes=["suggested-action"])
        apply.connect("clicked", lambda *_: self._apply_filters())
        buttons.append(reset)
        buttons.append(apply)
        box.append(buttons)
        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda *_: self._prefill_filters())
        self._filters_popover = popover
        return popover

    def _prefill_filters(self) -> None:
        words, ops = split_operators(self._entry.get_text())
        self._f_from.set_text(ops.get("from", ""))
        self._f_likes.set_value(int(ops.get("min_faves", 0) or 0))
        self._f_since.set_text(ops.get("since", ""))
        self._f_until.set_text(ops.get("until", ""))
        self._f_lang.set_text(ops.get("lang", ""))
        self._f_noreplies.set_active("noreplies" in ops)
        self._f_error.set_visible(False)

    def _apply_filters(self, clear: bool = False) -> None:
        words, _ops = split_operators(self._entry.get_text())
        if clear:
            self._entry.set_text(words)
            self._filters_popover.popdown()
            self._run()
            return
        since, until = self._f_since.get_text().strip(), self._f_until.get_text().strip()
        for value in (since, until):
            if value and not _DATE.fullmatch(value):
                self._f_error.set_label("Dates are written YYYY-MM-DD, e.g. 2026-11-01.")
                self._f_error.set_visible(True)
                return
        parts = [words] if words else []
        if handle := self._f_from.get_text().strip().lstrip("@"):
            parts.append(f"from:{handle}")
        if likes := int(self._f_likes.get_value()):
            parts.append(f"min_faves:{likes}")
        if since:
            parts.append(f"since:{since}")
        if until:
            parts.append(f"until:{until}")
        if lang := self._f_lang.get_text().strip().lower():
            parts.append(f"lang:{lang}")
        if self._f_noreplies.get_active():
            parts.append("-filter:replies")
        query = " ".join(parts)
        if not query:
            self._f_error.set_label("Type something to search for, or pick at least one filter.")
            self._f_error.set_visible(True)
            return
        self._entry.set_text(query)
        self._entry.set_position(-1)
        self._filters_popover.popdown()
        self._run()


MAX_RECENT = 10
_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
_OPERATOR = re.compile(r"(?:^|\s)(from|min_faves|since|until|lang):(\S+)|(?:^|\s)(-filter:replies)(?=\s|$)")


def split_operators(query: str) -> tuple[str, dict]:
    """The words of a search and the filter operators in it (from, min_faves, since, until, lang,
    -filter:replies as "noreplies")."""
    ops: dict[str, str] = {}

    def take(match: re.Match) -> str:
        if match.group(3):
            ops["noreplies"] = "1"
        else:
            ops[match.group(1)] = match.group(2)
        return " "

    words = _OPERATOR.sub(take, query or "")
    return " ".join(words.split()), ops
