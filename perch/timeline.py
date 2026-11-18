"""A paged tweet timeline backed by a sidecar ``timeline`` call."""

from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow


class TimelineView(PagedListView):
    error_title = "Couldn't load the timeline"
    hide_muted_words = True

    def __init__(self, controller, kind: str):
        self.kind = kind
        self.snapshot_kind = kind  # saved for the next start (see snapshot.py)
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("timeline", {"kind": self.kind, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: TweetRow) -> None:
        row.activate_post()
