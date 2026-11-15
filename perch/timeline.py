"""A paged tweet timeline backed by a sidecar ``timeline`` call."""

from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow


class TimelineView(PagedListView):
    error_title = "Couldn't load the timeline"

    def __init__(self, controller, kind: str):
        self._kind = kind
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("timeline", {"kind": self._kind, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)
