"""Bookmarks, lists (yours and followed), a list's page, and list editing / membership dialogs."""

from gi.repository import Adw, GLib, Gtk, Pango

from perch import format as fmt
from perch import images
from perch.paged_list import PagedListView
from perch.search import UserRow
from perch.tweet_row import TweetRow


# -- bookmarks ------------------------------------------------------------------


class _BookmarksView(PagedListView):
    error_title = "Couldn't load bookmarks"
    empty_title = "No bookmarks yet"
    empty_icon = "user-bookmarks-symbolic"
    end_label = "No more bookmarks"

    def __init__(self, controller):
        self.folder_id: str | None = None
        super().__init__(controller)

    def set_folder(self, folder_id: str | None) -> None:
        if folder_id != self.folder_id:
            self.folder_id = folder_id
            self.reload()

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("bookmarks", {"folderId": self.folder_id, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: TweetRow) -> None:
        row.activate_post()


class BookmarksPage(Adw.NavigationPage):
    def __init__(self, controller):
        super().__init__(title="Bookmarks", tag="bookmarks")
        self._controller = controller
        self._view = _BookmarksView(controller)
        self._view.connect("load-failed", lambda _v, error: controller.report_error(error, "Couldn't load bookmarks"))

        headerbar = Adw.HeaderBar()
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        headerbar.pack_end(refresh)

        self._toolbar = Adw.ToolbarView(content=self._view)
        self._toolbar.add_top_bar(headerbar)
        self.set_child(self._toolbar)
        self._view.reload()
        # Folders are an X Premium feature; only show the switcher when there are some.
        controller.sidecar.call("bookmarks.folders", {}, self._on_folders)

    def _on_folders(self, result, error) -> None:
        folders = (result or {}).get("list") or []
        if error or not folders:
            return
        group = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=6, margin_bottom=6, css_classes=["round"])
        group.add(Adw.Toggle(name="all", label="All"))
        for folder in folders:
            group.add(Adw.Toggle(name=f"f{folder['id']}", label=folder.get("name") or "Folder"))
        group.set_active_name("all")
        group.connect("notify::active-name", lambda g, _p: self._view.set_folder(None if g.get_active_name() == "all" else g.get_active_name()[1:]))
        self._toolbar.add_top_bar(group)

    def reload(self) -> None:
        self._view.reload()


# -- lists ----------------------------------------------------------------------


def list_subtitle(lst: dict) -> str:
    members = lst.get("memberCount") or 0
    parts = [f"{members:,} {'member' if members == 1 else 'members'}"]
    if lst.get("subscriberCount"):
        followers = lst["subscriberCount"]
        parts.append(f"{followers:,} {'follower' if followers == 1 else 'followers'}")
    if lst.get("ownerHandle"):
        parts.append(f"by @{lst['ownerHandle']}")
    return " · ".join(parts)


class ListRow(Gtk.ListBoxRow):
    def __init__(self, lst: dict):
        super().__init__(activatable=True)
        self.add_css_class("tweet")
        self.list = lst
        row = Gtk.Box(spacing=12)
        icon = Gtk.Image(icon_name="view-list-bullet-symbolic", pixel_size=24, valign=Gtk.Align.START, margin_top=4, css_classes=["accent"])
        row.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True)
        title = Gtk.Box(spacing=6)
        title.append(Gtk.Label(label=lst.get("name") or "List", xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"]))
        if lst.get("private"):
            title.append(Gtk.Image(icon_name="changes-prevent-symbolic", tooltip_text="Private list"))
        text.append(title)
        if lst.get("description"):
            text.append(Gtk.Label(label=lst["description"], xalign=0, wrap=True, lines=2, ellipsize=Pango.EllipsizeMode.END))
        text.append(Gtk.Label(label=list_subtitle(lst), xalign=0, css_classes=["dim-label", "caption"]))
        row.append(text)
        self.set_child(row)


class _ListsView(PagedListView):
    error_title = "Couldn't load your lists"
    empty_title = "No lists yet"
    empty_icon = "view-list-bullet-symbolic"
    end_label = ""

    def fetch_page(self, cursor, callback) -> None:
        def sort_mine_first(result, error):
            if result is not None:
                me = self._controller.me_id
                result = {**result, "list": sorted(result.get("list") or [], key=lambda l: l.get("ownerId") != me)}
            callback(result, error)

        self._controller.sidecar.call("lists", {"cursor": cursor}, sort_mine_first)

    def make_row(self, item: dict, page: dict) -> ListRow:
        return ListRow(item)

    def on_row_activated(self, row: ListRow) -> None:
        self._controller.open_list(row.list)


class ListsPage(Adw.NavigationPage):
    def __init__(self, controller):
        super().__init__(title="Lists", tag="lists")
        self._controller = controller
        self._view = _ListsView(controller)
        self._view.connect("load-failed", lambda _v, error: controller.report_error(error, "Couldn't load your lists"))

        headerbar = Adw.HeaderBar()
        new = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="New List")
        new.connect("clicked", lambda *_: ListEditorDialog(controller, None, on_saved=lambda _l: self.reload()).present(self))
        headerbar.pack_start(new)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        headerbar.pack_end(refresh)

        view = Adw.ToolbarView(content=self._view)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._view.reload()

    def reload(self) -> None:
        self._view.reload()


# -- one list --------------------------------------------------------------------


class _MemberRow(UserRow):
    """A list member; on your own list it gets a remove button."""

    def __init__(self, user: dict, me_id: str | None, on_remove=None):
        super().__init__(user, me_id)
        if on_remove is not None:
            remove = Gtk.Button(icon_name="list-remove-symbolic", tooltip_text=f"Remove @{user.get('userName', '')} from this list", valign=Gtk.Align.CENTER)
            remove.add_css_class("flat")
            remove.add_css_class("circular")
            remove.connect("clicked", lambda *_: on_remove(self))
            self.get_child().append(remove)


class _ListBodyView(PagedListView):
    error_title = "Couldn't load this list"
    always_show_list = True

    def __init__(self, controller, page: "ListPage"):
        self._page = page
        self.kind = "posts"
        super().__init__(controller)

    @property
    def end_label(self) -> str:  # noqa: D401 - read by PagedListView
        return "No more members" if self.kind == "members" else "No more posts"

    @property
    def empty_label(self) -> str:  # noqa: D401
        return "No members yet" if self.kind == "members" else "No posts yet"

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.reload()

    def fetch_page(self, cursor, callback) -> None:
        method = "list.members" if self.kind == "members" else "list.tweets"
        self._controller.sidecar.call(method, {"id": self._page.list_id, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> Gtk.ListBoxRow:
        if self.kind == "members":
            return _MemberRow(item, self._controller.me_id, self._page.remove_member if self._page.is_mine else None)
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, UserRow):
            self._controller.open_profile(row.user)
        else:
            row.activate_post()

    def remove_row(self, row: Gtk.ListBoxRow) -> None:
        self._list.remove(row)


class ListPage(Adw.NavigationPage):
    def __init__(self, controller, lst: dict):
        super().__init__(title=lst.get("name") or "List", tag=f"list-{lst['id']}")
        self._controller = controller
        self.list = lst
        self.list_id = lst["id"]

        self._body = _ListBodyView(controller, self)
        self._header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._body.set_header(self._header)
        self._body.connect("load-failed", lambda _v, error: controller.report_error(error, "Couldn't load this list"))

        self._title = Adw.WindowTitle(title=lst.get("name") or "List", subtitle=list_subtitle(lst))
        headerbar = Adw.HeaderBar(title_widget=self._title)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        headerbar.pack_end(refresh)
        self._edit = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="Edit List")
        self._edit.connect("clicked", lambda *_: ListEditorDialog(controller, self.list, on_saved=self._on_saved).present(self))
        self._delete = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Delete List")
        self._delete.connect("clicked", lambda *_: self._confirm_delete())
        headerbar.pack_end(self._delete)
        headerbar.pack_end(self._edit)

        view = Adw.ToolbarView(content=self._body)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._fill(lst)
        self._body.reload()

    @property
    def is_mine(self) -> bool:
        return self.list.get("ownerId") == self._controller.me_id

    def _fill(self, lst: dict) -> None:
        self.list = lst
        self.set_title(lst.get("name") or "List")
        self._title.set_title(lst.get("name") or "List")
        self._title.set_subtitle(list_subtitle(lst))
        self._edit.set_visible(self.is_mine)
        self._delete.set_visible(self.is_mine)
        while child := self._header.get_first_child():
            self._header.remove(child)

        if lst.get("bannerUrl"):
            banner = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, height_request=130, css_classes=["profile-banner"])
            images.load(lst["bannerUrl"], lambda tex: tex and banner.set_paintable(tex), cache=False, disk=True)
            self._header.append(banner)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=16, margin_end=16, margin_top=12)
        name_row = Gtk.Box(spacing=6, halign=Gtk.Align.CENTER)
        name_row.append(Gtk.Label(label=lst.get("name") or "List", wrap=True, justify=Gtk.Justification.CENTER, css_classes=["title-2"]))
        if lst.get("private"):
            name_row.append(Gtk.Image(icon_name="changes-prevent-symbolic", tooltip_text="Private list"))
        info.append(name_row)
        if lst.get("description"):
            info.append(Gtk.Label(label=lst["description"], wrap=True, justify=Gtk.Justification.CENTER))
        if lst.get("ownerHandle"):
            owner = Gtk.Button(halign=Gtk.Align.CENTER, css_classes=["flat"])
            owner.set_child(Gtk.Label(label=f"by {lst.get('ownerName') or ''} @{lst['ownerHandle']}".replace("by  ", "by "), css_classes=["dim-label"]))
            owner.connect("clicked", lambda *_: self._controller.open_profile(lst["ownerHandle"]))
            info.append(owner)
        self._header.append(info)

        group = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=8, margin_bottom=6, css_classes=["round"])
        group.add(Adw.Toggle(name="posts", label="Posts"))
        group.add(Adw.Toggle(name="members", label=f"Members ({lst.get('memberCount') or 0:,})"))
        group.set_active_name(self._body.kind)
        group.connect("notify::active-name", lambda g, _p: self._body.set_kind(g.get_active_name()))
        self._header.append(group)

    def reload(self) -> None:
        self._controller.sidecar.call("list.details", {"id": self.list_id}, lambda lst, error: lst and self._fill(lst))
        self._body.reload()

    def _on_saved(self, lst: dict) -> None:
        self._fill({**self.list, **lst})
        self._controller.on_lists_changed()

    def remove_member(self, row: "_MemberRow") -> None:
        user = row.user
        row.set_sensitive(False)

        def done(result, error):
            if error:
                row.set_sensitive(True)
                self._controller.report_error(error, f"Couldn't remove @{user.get('userName', '')}")
                return
            self._body.remove_row(row)
            count = result.get("memberCount")
            if count is not None:
                self._fill({**self.list, "memberCount": count})
            self._controller.toast(f"Removed @{user.get('userName', '')} from {self.list.get('name')}")

        self._controller.sidecar.call("list.member", {"listId": self.list_id, "userId": user["id"], "add": False}, done)

    def _confirm_delete(self) -> None:
        dialog = Adw.AlertDialog(heading=f"Delete “{self.list.get('name')}”?", body="The list and its member list will be deleted for everyone. This can’t be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)

        def on_response(_d, response):
            if response != "delete":
                return

            def done(_result, error):
                if error:
                    self._controller.report_error(error, "Couldn't delete the list")
                    return
                self._controller.toast(f"Deleted “{self.list.get('name')}”")
                self._controller.on_lists_changed()
                if nav := self.get_ancestor(Adw.NavigationView):
                    nav.pop()

            self._controller.sidecar.call("list.delete", {"id": self.list_id}, done)

        dialog.connect("response", on_response)
        dialog.present(self)


# -- dialogs ---------------------------------------------------------------------


class ListEditorDialog(Adw.Dialog):
    """Create a list (``lst`` is None) or edit one of yours."""

    def __init__(self, controller, lst: dict | None, on_saved=None):
        super().__init__(title="Edit List" if lst else "New List", content_width=440)
        self._controller = controller
        self._list = lst
        self._on_saved = on_saved

        headerbar = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        headerbar.pack_start(cancel)
        self._save = Gtk.Button(label="Save" if lst else "Create", css_classes=["suggested-action"])
        self._save.connect("clicked", lambda *_: self._submit())
        headerbar.pack_end(self._save)

        page = Adw.PreferencesPage()
        group = Adw.PreferencesGroup()
        self._name = Adw.EntryRow(title="Name", text=(lst or {}).get("name") or "")
        self._name.connect("changed", lambda *_: self._save.set_sensitive(bool(self._name.get_text().strip())))
        self._name.connect("entry-activated", lambda *_: self._submit())
        self._description = Adw.EntryRow(title="Description (optional)", text=(lst or {}).get("description") or "")
        self._private = Adw.SwitchRow(title="Private", subtitle="Only you can see this list", active=bool((lst or {}).get("private", True)))
        for row in (self._name, self._description, self._private):
            group.add(row)
        page.add(group)
        self._error = Adw.Banner(revealed=False, use_markup=False)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        box.append(self._error)
        box.append(page)
        view = Adw.ToolbarView(content=box)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._save.set_sensitive(bool(self._name.get_text().strip()))
        self.set_focus(self._name)

    def _submit(self) -> None:
        name = self._name.get_text().strip()
        if not name or not self._save.get_sensitive():
            return
        self._save.set_sensitive(False)
        params = {"name": name, "description": self._description.get_text().strip(), "isPrivate": self._private.get_active()}

        def done(result, error):
            self._save.set_sensitive(True)
            if error:
                self._error.set_title(self._controller.describe_error(error, "Couldn't save the list"))
                self._error.set_revealed(True)
                return
            self.close()
            if self._list is None:
                self._controller.toast(f"Created “{name}”")
                self._controller.on_lists_changed()
            if self._on_saved:
                self._on_saved(result if self._list else {"id": result.get("id"), "name": name})

        if self._list is None:
            self._controller.sidecar.call("list.create", params, done)
        else:
            self._controller.sidecar.call("list.update", {"id": self._list["id"], **params}, done)


class AddToListDialog(Adw.Dialog):
    """Tick which of your lists a person is on (adds / removes as you toggle)."""

    def __init__(self, controller, user: dict):
        super().__init__(title=f"Lists for @{user.get('userName', '')}", content_width=420, content_height=480)
        self._controller = controller
        self._user = user
        self._reverting: set = set()

        headerbar = Adw.HeaderBar()
        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32), "loading")
        self._empty = Adw.StatusPage(icon_name="view-list-bullet-symbolic", title="You have no lists", description="Create one from Lists in the main menu.")
        self._stack.add_named(self._empty, "empty")
        self._group = Adw.PreferencesGroup(description="Changes are saved as you toggle.")
        page = Adw.PreferencesPage()
        page.add(self._group)
        self._stack.add_named(page, "lists")

        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(headerbar)
        self.set_child(view)
        controller.sidecar.call("lists", {}, self._on_lists)

    def _on_lists(self, result, error) -> None:
        if error:
            self._empty.set_title("Couldn't load your lists")
            self._empty.set_description(GLib.markup_escape_text(str(error)))
            self._stack.set_visible_child_name("empty")
            return
        mine = [l for l in (result.get("list") or []) if l.get("ownerId") == self._controller.me_id]
        if not mine:
            self._stack.set_visible_child_name("empty")
            return
        for lst in mine:
            row = Adw.SwitchRow(title=GLib.markup_escape_text(lst.get("name") or "List"), subtitle="Checking…", sensitive=False)
            if lst.get("private"):
                row.add_prefix(Gtk.Image(icon_name="changes-prevent-symbolic", tooltip_text="Private list"))
            self._group.add(row)
            self._controller.sidecar.call(
                "list.hasMember", {"listId": lst["id"], "userId": self._user["id"]}, lambda res, err, r=row, l=lst: self._on_membership(r, l, res, err)
            )
        self._stack.set_visible_child_name("lists")

    def _on_membership(self, row: Adw.SwitchRow, lst: dict, result, error) -> None:
        members = lst.get("memberCount") or 0
        row.set_subtitle(f"{members:,} {'member' if members == 1 else 'members'}")
        if error:
            row.set_subtitle("Couldn't check membership")
            return
        row.set_active(bool(result.get("member")))
        if not result.get("certain"):
            row.set_subtitle(f"{row.get_subtitle()} · large list, may already include them")
        row.set_sensitive(True)
        row.connect("notify::active", lambda r, _p: self._toggle(r, lst))

    def _toggle(self, row: Adw.SwitchRow, lst: dict) -> None:
        if row in self._reverting:
            return
        add = row.get_active()
        row.set_sensitive(False)

        def done(result, error):
            row.set_sensitive(True)
            if error:
                self._reverting.add(row)  # flipping it back must not send another add/remove
                row.set_active(not add)
                self._reverting.discard(row)
                self._controller.report_error(error, f"Couldn't update “{lst.get('name')}”")
                return
            count = result.get("memberCount")
            if count is not None:
                row.set_subtitle(f"{count:,} {'member' if count == 1 else 'members'}")
            verb = "Added to" if add else "Removed from"
            self._controller.toast(f"{verb} “{lst.get('name')}”")
            self._controller.on_lists_changed()

        self._controller.sidecar.call("list.member", {"listId": lst["id"], "userId": self._user["id"], "add": add}, done)
