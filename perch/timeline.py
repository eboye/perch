"""A paged tweet timeline backed by a sidecar ``timeline`` call."""

from gi.repository import GLib

from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow


class TimelineView(PagedListView):
    error_title = "Couldn't load the timeline"
    hide_muted_words = True

    def __init__(self, controller, kind: str):
        self.kind = kind
        self.restored_pending = False  # opened on its saved copy before X answered (see window._check_newer)
        super().__init__(controller)
        # What's loaded and where you are is saved (see snapshot.py) a moment after it changes.
        self._save_source = 0
        self._scroller.get_vadjustment().connect("value-changed", lambda *_: self.queue_save())

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("timeline", {"kind": self.kind, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: TweetRow) -> None:
        row.activate_post()

    def on_page_loaded(self, page: dict, first: bool) -> None:
        self.queue_save()

    def queue_save(self) -> None:
        if self._save_source:
            GLib.source_remove(self._save_source)
        self._save_source = GLib.timeout_add(1500, self._save_now)

    def _save_now(self) -> bool:
        self._save_source = 0
        self._controller.save_timeline(self)
        return GLib.SOURCE_REMOVE

    def save_pending(self) -> None:
        """Save now if a save is waiting (closing to the dock, quitting)."""
        if self._save_source:
            GLib.source_remove(self._save_source)
            self._save_now()
