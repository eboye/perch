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
        # Your own followers list: each row can remove that follower.
        self._removable = method == "user.connections" and params.get("kind") == "followers" and params.get("id") == controller.me_id
        super().__init__(controller)

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call(self._method, {**self._params, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> UserRow:
        row = UserRow(item, self._controller.me_id)
        if self._removable and item.get("id") != self._controller.me_id:
            remove = Gtk.Button(label="Remove", tooltip_text=f"Remove @{item.get('userName', '')} from your followers")
            remove.add_css_class("pill")
            remove.add_css_class("small-pill")
            remove.connect("clicked", lambda *_: self._confirm_remove(row))
            row.add_suffix(remove)
        return row

    def on_row_activated(self, row: UserRow) -> None:
        self._controller.open_profile(row.user)

    def _confirm_remove(self, row: UserRow) -> None:
        handle = row.user.get("userName", "")
        dialog = Adw.AlertDialog(
            heading=f"Remove @{handle}?",
            body="They'll stop following you and won't see your posts in their Following timeline. X doesn't notify them, and they can follow you again.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("remove", "Remove")
        dialog.set_response_appearance("remove", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def done(_result, error):
            if error:
                self._controller.report_error(error, f"Couldn't remove @{handle}")
                return
            self._list.remove(row)
            if self._list.get_row_at_index(0) is None:
                self.set_visible_child_name("empty")
            self._controller.toast(f"Removed @{handle} from your followers")

        def on_response(_dialog, response):
            if response == "remove":
                self._controller.sidecar.call("user.removeFollower", {"id": row.user["id"]}, done)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())


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
