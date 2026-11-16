"""Scheduled posts: the list of your posts waiting to go out, each with a way to cancel it."""

from gi.repository import Adw, Gtk, Pango

from perch import format as fmt
from perch.paged_list import PagedListView


class _ScheduledRow(Gtk.ListBoxRow):
    def __init__(self, item: dict, on_cancel):
        super().__init__(activatable=False)
        self.add_css_class("tweet")
        self.item = item
        box = Gtk.Box(spacing=12)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, hexpand=True)
        when = fmt.schedule_phrase(item.get("executeAt"))
        caption = f"Posts {when}" if when else "Scheduled"
        media = item.get("mediaCount") or 0
        if media:
            caption += f" · {media} {'image' if media == 1 else 'images'}"
        text.append(Gtk.Label(label=caption, xalign=0, css_classes=["dim-label", "caption", "schedule-when"]))
        body = item.get("text") or "(images only)"
        text.append(Gtk.Label(label=body, xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=6, ellipsize=Pango.EllipsizeMode.END, selectable=True))
        box.append(text)
        cancel = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Cancel Scheduled Post", valign=Gtk.Align.START)
        cancel.add_css_class("flat")
        cancel.connect("clicked", lambda *_: on_cancel(self))
        box.append(cancel)
        self.set_child(box)


class _ScheduledView(PagedListView):
    error_title = "Couldn't load scheduled posts"
    empty_title = "No scheduled posts"
    empty_icon = "alarm-symbolic"
    end_label = "No more scheduled posts"

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("tweet.scheduled", {}, callback)

    def make_row(self, item: dict, page: dict) -> _ScheduledRow:
        return _ScheduledRow(item, self._confirm_cancel)

    def on_row_activated(self, row) -> None:
        pass

    def _confirm_cancel(self, row: _ScheduledRow) -> None:
        dialog = Adw.AlertDialog(heading="Cancel scheduled post?", body="It won't be posted. This can't be undone.")
        dialog.add_response("keep", "Keep")
        dialog.add_response("cancel-post", "Cancel Post")
        dialog.set_response_appearance("cancel-post", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("keep")
        dialog.set_close_response("keep")

        def done(_result, error):
            if error:
                self._controller.report_error(error, "Couldn't cancel the scheduled post")
                return
            self._list.remove(row)
            if self._list.get_row_at_index(0) is None:
                self.set_visible_child_name("empty")
            self._controller.toast("Scheduled post cancelled")

        def on_response(_dialog, response):
            if response == "cancel-post":
                self._controller.sidecar.call("tweet.unschedule", {"id": row.item["id"]}, done)

        dialog.connect("response", on_response)
        dialog.present(self.get_root())


class ScheduledPage(Adw.NavigationPage):
    def __init__(self, controller):
        super().__init__(title="Scheduled Posts", tag="scheduled")
        self._view = _ScheduledView(controller)
        self._view.connect("load-failed", lambda _v, error: controller.report_error(error, "Couldn't load scheduled posts"))
        headerbar = Adw.HeaderBar()
        new = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="New Post (Ctrl+N)", action_name="win.compose")
        headerbar.pack_start(new)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: self.reload())
        headerbar.pack_end(refresh)
        view = Adw.ToolbarView(content=self._view)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._view.reload()

    def reload(self) -> None:
        self._view.reload()
