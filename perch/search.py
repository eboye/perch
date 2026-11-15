"""Search page: a search box with Top / Latest / People / Media results."""

from gi.repository import Adw, GLib, Gtk, Pango

from perch import format as fmt
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
        self.set_child(row)


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
        headerbar = Adw.HeaderBar(title_widget=Adw.Clamp(maximum_size=520, child=self._entry, hexpand=True))

        self._modes = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6)
        self._modes.add_css_class("round")
        for name, label in MODES:
            self._modes.add(Adw.Toggle(name=name, label=label))
        self._modes.set_active_name("top")
        self._modes.connect("notify::active-name", lambda *_: self._run(switching_mode=True))

        idle = Adw.StatusPage(
            icon_name="system-search-symbolic",
            title="Search X",
            description="Find posts and people. Press Enter to search.\n"
            "Operators work too: <b>from:</b>user, <b>to:</b>user, <b>#</b>tag, <b>\"exact phrase\"</b>, <b>-</b>word, <b>lang:</b>en",
        )
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
