"""A pinned list shown as a main tab next to Following and For You."""

from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow


class ListTabView(PagedListView):
    error_title = "Couldn't load this list"
    empty_title = "No posts in this list yet"
    empty_icon = "view-list-bullet-symbolic"
    hide_muted_words = True

    def __init__(self, controller, lst: dict):
        self.list_id = lst["id"]
        self.snapshot_kind = f"list-{lst['id']}"  # saved for the next start (see snapshot.py)
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("list.tweets", {"id": self.list_id, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: TweetRow) -> None:
        row.activate_post()
