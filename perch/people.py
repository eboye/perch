"""A page listing people: a profile's followers / following, or who liked / reposted a post."""

from gi.repository import Adw, Gtk

from perch.paged_list import PagedListView
from perch.search import UserRow


class _PeopleView(PagedListView):
    error_title = "Couldn't load these accounts"
    empty_title = "Nobody here yet"
    empty_icon = "system-users-symbolic"
    end_label = "No more accounts"

    def __init__(self, controller, method: str, params: dict, empty_title: str):
        self._method = method
        self._params = params
        self.empty_title = empty_title  # read by PagedListView.__init__
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call(self._method, {**self._params, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> UserRow:
        return UserRow(item, self._controller.me_id)

    def on_row_activated(self, row: UserRow) -> None:
        self._controller.open_profile(row.user)


class PeoplePage(Adw.NavigationPage):
    """``method`` / ``params`` name the sidecar call (``user.connections`` or ``tweet.engagers``)."""

    def __init__(self, controller, tag: str, title: str, subtitle: str, method: str, params: dict, empty_title: str):
        super().__init__(title=title, tag=tag)
        self._view = _PeopleView(controller, method, params, empty_title)
        self._view.connect("load-failed", lambda _v, error: controller.report_error(error, f"Couldn't load {title.lower()}"))

        headerbar = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=title, subtitle=subtitle))
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: self.reload())
        headerbar.pack_end(refresh)

        view = Adw.ToolbarView(content=self._view)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._view.reload()

    def reload(self) -> None:
        self._view.reload()
