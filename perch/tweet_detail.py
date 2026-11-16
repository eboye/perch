"""A single post on its own page: the posts it replies to, the post itself, and its replies."""

from gi.repository import Adw, GLib, Gtk

from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow

MAX_ANCESTORS = 5  # how far up a reply chain to show


class _RepliesView(PagedListView):
    """The page body: header (ancestors + focused post + reply prompt) above a paged reply list."""

    error_title = "Couldn't load replies"
    end_label = "No more replies"
    empty_label = "No replies yet"
    always_show_list = True

    def __init__(self, controller, tweet_id: str):
        self._tweet_id = tweet_id
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("tweet.replies", {"id": self._tweet_id, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: TweetRow) -> None:
        row.activate_post()


class TweetDetailPage(Adw.NavigationPage):
    def __init__(self, controller, tweet: dict):
        super().__init__(title="Post", tag=f"post-{tweet['id']}")
        self._controller = controller
        self.tweet = tweet
        self.tweet_id = tweet["id"]

        self._ancestors = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["tweet-list", "thread-ancestors"])
        self._ancestors.connect("row-activated", lambda _l, row: row.activate_post() if hasattr(row, "activate_post") else None)
        self._ancestors_spinner = Adw.Spinner(halign=Gtk.Align.CENTER, width_request=20, height_request=20, visible=False, margin_top=8)

        self._focused = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE, css_classes=["tweet-list"])
        self._focused.append(TweetRow(tweet, controller, detailed=True))

        reply = Gtk.Button(halign=Gtk.Align.FILL, margin_start=16, margin_end=16, margin_top=8, margin_bottom=8)
        reply.add_css_class("reply-prompt")
        handle = (tweet.get("tweetBy") or {}).get("userName", "")
        reply.set_child(Gtk.Label(label=f"Reply to @{handle}…", xalign=0, css_classes=["dim-label"]))
        reply.connect("clicked", lambda *_: controller.compose(reply_to=tweet))

        replies_title = Gtk.Label(label="Replies", xalign=0, margin_start=16, margin_top=8, margin_bottom=4, css_classes=["heading"])

        header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        for widget in (self._ancestors_spinner, self._ancestors, self._focused, reply, replies_title):
            header.append(widget)

        self._replies = _RepliesView(controller, self.tweet_id)
        self._replies.set_header(header)
        self._replies.connect("load-failed", lambda _v, error: controller.report_error(error, "Couldn't load replies"))

        headerbar = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        headerbar.pack_end(refresh)
        if url := tweet.get("url"):
            browser = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open in Browser")
            browser.connect("clicked", lambda *_: Gtk.UriLauncher(uri=url).launch(self.get_root(), None, None, None))
            headerbar.pack_end(browser)

        view = Adw.ToolbarView(content=self._replies)
        view.add_top_bar(headerbar)
        self.set_child(view)

        self._replies.reload()
        self._load_ancestors(tweet.get("replyTo"), [])

    # -- reply chain ------------------------------------------------------------

    def _load_ancestors(self, parent_id: str | None, chain: list[dict]) -> None:
        """Walk up `replyTo` one post at a time (oldest shown first), at most MAX_ANCESTORS."""
        if not parent_id or len(chain) >= MAX_ANCESTORS:
            self._show_ancestors(chain, truncated=bool(parent_id))
            return
        self._ancestors_spinner.set_visible(True)

        def done(result, error):
            if error:  # deleted / protected parent: show what we have
                self._show_ancestors(chain, truncated=False, missing=True)
                return
            self._load_ancestors(result.get("replyTo"), [result, *chain])

        self._controller.sidecar.call("tweet.details", {"id": parent_id}, done)

    def _show_ancestors(self, chain: list[dict], truncated: bool, missing: bool = False) -> None:
        self._ancestors_spinner.set_visible(False)
        while row := self._ancestors.get_row_at_index(0):
            self._ancestors.remove(row)
        if truncated or missing:
            note = Gtk.Label(
                label="Earlier posts in this thread are on X" if truncated else "The post this replies to is unavailable",
                margin_top=8, margin_bottom=4, css_classes=["dim-label", "caption"],
            )
            row = Gtk.ListBoxRow(activatable=False, child=note)
            self._ancestors.append(row)
        for tweet in chain:
            row = TweetRow(tweet, self._controller)
            row.add_css_class("thread-parent")
            self._ancestors.append(row)

    # -- refresh ----------------------------------------------------------------

    def reload(self) -> None:
        self._replies.reload()

    def on_reply_posted(self) -> None:
        # X takes a moment to index a new reply; refresh after a short delay so it shows up.
        GLib.timeout_add_seconds(2, lambda: self.reload() or False)
