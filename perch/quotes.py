"""The posts that quote a post (X search ``quoted_tweet_id:<id>``, newest first)."""

from gi.repository import Adw, Gtk

from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow


class _QuotesView(PagedListView):
    error_title = "Couldn't load the quotes"
    empty_title = "No quotes yet"
    empty_icon = "mail-reply-all-symbolic"
    end_label = "No more quotes"

    def __init__(self, controller, tweet_id: str):
        self._tweet_id = tweet_id
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        # "Latest" only: X's "Top" ordering mixes in posts that don't quote this one.
        self._controller.sidecar.call("search.posts", {"query": f"quoted_tweet_id:{self._tweet_id}", "mode": "latest", "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: TweetRow) -> None:
        row.activate_post()


class QuotesPage(Adw.NavigationPage):
    def __init__(self, controller, tweet: dict):
        super().__init__(title="Quotes", tag=f"quotes-{tweet['id']}")
        self._view = _QuotesView(controller, tweet["id"])
        self._view.connect("load-failed", lambda _v, error: controller.report_error(error, "Couldn't load the quotes"))
        handle = (tweet.get("tweetBy") or {}).get("userName", "")
        headerbar = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Quotes", subtitle=f"Post by @{handle}"))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        headerbar.pack_end(refresh)
        view = Adw.ToolbarView(content=self._view)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._view.reload()

    def reload(self) -> None:
        self._view.reload()
