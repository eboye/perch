"""Edit history of a post: every version, newest first, each as a read-only card."""

from gi.repository import Adw, Gtk

from perch import format as fmt
from perch.paged_list import PagedListView
from perch.tweet_row import quoted_card


class _VersionRow(Gtk.ListBoxRow):
    def __init__(self, tweet: dict, latest: bool, controller):
        super().__init__(activatable=False)
        self.add_css_class("tweet")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        caption = "Current version" if latest else f"Earlier version · {fmt.full_time(tweet.get('createdAt'))}"
        box.append(Gtk.Label(label=caption, xalign=0, css_classes=["dim-label", "heading", "version-label"]))
        box.append(quoted_card(tweet, controller))
        self.set_child(box)


class _VersionsView(PagedListView):
    error_title = "Couldn't load the edit history"
    empty_title = "No earlier versions"
    empty_icon = "document-edit-symbolic"
    end_label = "This is the original version"

    def __init__(self, controller, tweet_id: str):
        self._tweet_id = tweet_id
        self._first_id: str | None = None
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        def remember_newest(result, error):
            if result and result.get("list"):
                self._first_id = result["list"][0].get("id")
            callback(result, error)

        self._controller.sidecar.call("tweet.history", {"id": self._tweet_id}, remember_newest)

    def make_row(self, item: dict, page: dict) -> _VersionRow:
        return _VersionRow(item, item.get("id") == self._first_id, self._controller)


class EditHistoryPage(Adw.NavigationPage):
    def __init__(self, controller, tweet: dict):
        super().__init__(title="Edit History", tag=f"history-{tweet['id']}")
        self._view = _VersionsView(controller, tweet["id"])
        self._view.connect("load-failed", lambda _v, error: controller.report_error(error, "Couldn't load the edit history"))
        handle = (tweet.get("tweetBy") or {}).get("userName", "")
        headerbar = Adw.HeaderBar(title_widget=Adw.WindowTitle(title="Edit History", subtitle=f"Post by @{handle}" if handle else ""))
        view = Adw.ToolbarView(content=self._view)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._view.reload()

    def reload(self) -> None:
        self._view.reload()
