"""User profile page: banner, avatar, bio, counts, follow / mute and Posts / Replies / Media
(+ Highlights, and Likes on your own) tabs."""

from gi.repository import Adw, Gio, GLib, Gtk, Pango

from perch import format as fmt
from perch import images
from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow

TABS = (("posts", "Posts"), ("replies", "Replies"), ("media", "Media"))
# X shows likes only to their owner, so this tab is on your own profile only.
OWN_TABS = TABS + (("likes", "Likes"),)


def _counts_markup(user: dict) -> str:
    """Following / Followers (links to those lists) and the post count."""
    return (
        f'<a href="perch:following" title="See who they follow"><b>{user.get("followingsCount") or 0:,}</b> Following</a>    '
        f'<a href="perch:followers" title="See their followers"><b>{user.get("followersCount") or 0:,}</b> Followers</a>    '
        f"<b>{user.get('statusesCount') or 0:,}</b> Posts"
    )


class _ProfilePostsView(PagedListView):
    error_title = "Couldn't load posts"
    end_label = "No more posts"
    always_show_list = True

    def __init__(self, controller, user_id: str):
        self._user_id = user_id
        self.kind = "posts"
        super().__init__(controller)

    @property
    def empty_label(self) -> str:  # noqa: D401 - read by PagedListView
        return {"replies": "No replies yet", "media": "No photos or videos yet", "likes": "No likes yet", "highlights": "No highlights"}.get(
            self.kind, "No posts yet"
        )

    def set_kind(self, kind: str) -> None:
        if kind != self.kind:
            self.kind = kind
            self.reload()

    def fetch_page(self, cursor, callback) -> None:
        self._controller.sidecar.call("user.posts", {"id": self._user_id, "kind": self.kind, "cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> TweetRow:
        return TweetRow(item, self._controller)

    def on_row_activated(self, row: TweetRow) -> None:
        row.activate_post()


class ProfilePage(Adw.NavigationPage):
    def __init__(self, controller, handle: str, preview: dict | None = None):
        handle = handle.lstrip("@")
        super().__init__(title=f"@{handle}", tag=f"profile-{handle.lower()}")
        self._controller = controller
        self.handle = handle
        self.user: dict | None = None
        self._posts: _ProfilePostsView | None = None

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32), "loading")
        self._error = Adw.StatusPage(icon_name="avatar-default-symbolic", title="Couldn't load this profile")
        retry = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER, css_classes=["pill"])
        retry.connect("clicked", lambda *_: controller.sidecar.fresh(self._load))
        self._error.set_child(retry)
        self._stack.add_named(self._error, "error")

        self._title = Adw.WindowTitle(title=(preview or {}).get("fullName") or f"@{handle}", subtitle=f"@{handle}")
        headerbar = Adw.HeaderBar(title_widget=self._title)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        headerbar.pack_end(refresh)
        browser = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open in Browser")
        browser.connect("clicked", lambda *_: Gtk.UriLauncher(uri=f"https://x.com/{self.handle}").launch(self.get_root(), None, None, None))
        headerbar.pack_end(browser)

        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(headerbar)
        self.set_child(view)
        self._load()

    # -- loading ----------------------------------------------------------------

    def _load(self) -> None:
        self._stack.set_visible_child_name("loading")
        self._controller.sidecar.call("user.profile", {"handle": self.handle}, self._on_profile)

    def reload(self) -> None:
        if self._posts is None:
            self._load()
            return
        self._controller.sidecar.call("user.profile", {"handle": self.handle}, lambda user, error: user and self._fill(user))
        self._posts.reload()

    def _on_profile(self, user, error) -> None:
        if error:
            self._error.set_description(GLib.markup_escape_text(self._controller.describe_error(error, "")))
            if error.kind == "not_found":
                self._error.set_title("This account doesn’t exist")
            self._stack.set_visible_child_name("error")
            return
        self.user = user
        self.handle = user.get("userName") or self.handle
        if self._posts is None:
            self._posts = _ProfilePostsView(self._controller, user["id"])
            self._header = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
            self._posts.set_header(self._header)
            self._posts.connect("load-failed", lambda _v, err: self._controller.report_error(err, "Couldn't load posts"))
            self._stack.add_named(self._posts, "profile")
            self._posts.reload()
        self._fill(user)
        self._stack.set_visible_child_name("profile")

    # -- header -----------------------------------------------------------------

    def _fill(self, user: dict) -> None:
        self.user = user
        self._title.set_title(user.get("fullName") or f"@{self.handle}")
        while child := self._header.get_first_child():
            self._header.remove(child)
        self._header.append(self._build_card(user))
        self._header.append(self._build_tabs())

    def _build_card(self, user: dict) -> Gtk.Widget:
        card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

        # Banner with the avatar hanging off its bottom edge, and the follow button beside it.
        banner = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, height_request=150, hexpand=True)
        banner.add_css_class("profile-banner")
        banner_url = fmt.profile_banner_url(user.get("profileBanner"))
        images.load(banner_url, lambda tex: tex and banner.set_paintable(tex), cache=False)
        top = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        top.append(banner)
        action_row = Gtk.Box(halign=Gtk.Align.END, spacing=8, margin_end=16, margin_top=10, height_request=44)
        lists = Gtk.Button(icon_name="view-list-bullet-symbolic", tooltip_text="Add to List…", valign=Gtk.Align.CENTER)
        lists.add_css_class("circular")
        lists.connect("clicked", lambda *_: self._controller.add_to_list(self.user or user))
        action_row.append(lists)
        self._follow_button = self._build_follow_button(user)
        if self._follow_button:
            action_row.prepend(self._build_more_menu(user))
            action_row.append(self._follow_button)
        elif user.get("id") == self._controller.me_id:
            edit = Gtk.Button(label="Edit Profile", valign=Gtk.Align.CENTER, css_classes=["pill"])
            edit.connect("clicked", lambda *_: self._controller.edit_profile(self.user or user))
            action_row.append(edit)
        top.append(action_row)

        avatar = Adw.Avatar(size=88, text=user.get("fullName") or self.handle, show_initials=True)
        avatar.add_css_class("profile-avatar")
        avatar.set_halign(Gtk.Align.START)
        avatar.set_valign(Gtk.Align.END)
        avatar.set_margin_start(16)
        images.load(fmt.profile_avatar_url(user.get("profileImage")), lambda tex: tex and avatar.set_custom_image(tex), cache=False)
        stage = Gtk.Overlay(child=top)
        stage.add_overlay(avatar)
        card.append(stage)

        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_start=16, margin_end=16, margin_top=4)
        name_row = Gtk.Box(spacing=6)
        name = Gtk.Label(label=user.get("fullName") or self.handle, xalign=0, wrap=True, css_classes=["title-2"])
        name_row.append(name)
        if user.get("isVerified"):
            name_row.append(Gtk.Image(icon_name="perch-verified-symbolic", tooltip_text="Verified", css_classes=["verified"]))
        if user.get("protected"):
            name_row.append(Gtk.Image(icon_name="changes-prevent-symbolic", tooltip_text="Protected account"))
        info.append(name_row)

        handle_row = Gtk.Box(spacing=8)
        handle_row.append(Gtk.Label(label=f"@{self.handle}", xalign=0, css_classes=["dim-label"]))
        if user.get("followsYou") and user.get("id") != self._controller.me_id:
            handle_row.append(Gtk.Label(label="Follows you", css_classes=["follows-you", "caption"]))
        if user.get("muted"):
            handle_row.append(Gtk.Label(label="Muted", tooltip_text="Their posts are hidden from your timelines", css_classes=["follows-you", "caption"]))
        info.append(handle_row)

        if user.get("description"):
            bio = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, use_markup=True, margin_top=4)
            bio.set_markup(fmt.tweet_markup(user["description"]))
            bio.connect("activate-link", lambda _l, uri: self._controller.open_link(uri))
            info.append(bio)

        meta = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=14, row_spacing=4, max_children_per_line=4, margin_top=2)
        if user.get("location"):
            meta.append(self._meta_item("mark-location-symbolic", GLib.markup_escape_text(user["location"])))
        if website := user.get("website"):
            shown = website.split("://", 1)[-1].rstrip("/")
            meta.append(self._meta_item("insert-link-symbolic", f'<a href="{GLib.markup_escape_text(website)}">{GLib.markup_escape_text(shown)}</a>'))
        if joined := fmt.joined(user.get("createdAt")):
            meta.append(self._meta_item("x-office-calendar-symbolic", GLib.markup_escape_text(joined)))
        info.append(meta)

        counts = Gtk.Label(xalign=0, use_markup=True, margin_top=2)
        counts.set_markup(_counts_markup(user))
        counts.add_css_class("profile-counts")
        counts.connect("activate-link", self._on_count_link)
        self._counts = counts
        info.append(counts)
        card.append(info)
        return card

    def _on_count_link(self, _label, uri: str) -> bool:
        if uri.startswith("perch:") and self.user:
            self._controller.open_connections(self.user, uri.removeprefix("perch:"))
            return True
        return False

    def _meta_item(self, icon: str, markup: str) -> Gtk.Widget:
        box = Gtk.Box(spacing=4, css_classes=["dim-label"])
        box.append(Gtk.Image.new_from_icon_name(icon))
        label = Gtk.Label(use_markup=True, ellipsize=Pango.EllipsizeMode.END, max_width_chars=32)
        label.set_markup(markup)
        box.append(label)
        return box

    def _build_tabs(self) -> Gtk.Widget:
        group = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=14, margin_bottom=6)
        group.add_css_class("round")
        user = self.user or {}
        own = user.get("id") == self._controller.me_id
        tabs = list(OWN_TABS if own else TABS)
        # Highlights (posts the account pinned to its Highlights tab, an X Premium feature).
        if user.get("highlightCount"):
            tabs.insert(3, ("highlights", "Highlights"))
        names = {name for name, _label in tabs}
        for name, label in tabs:
            group.add(Adw.Toggle(name=name, label=label))
        current = self._posts.kind if self._posts else "posts"
        if current not in names and self._posts:
            self._posts.set_kind(current := "posts")
        group.set_active_name(current)
        group.connect("notify::active-name", lambda g, _p: self._posts.set_kind(g.get_active_name()))
        return group

    def _build_more_menu(self, user: dict) -> Gtk.MenuButton:
        """⋯ on someone else's profile: mute / unmute."""
        menu = Gio.Menu()
        muted = bool(user.get("muted"))
        menu.append(f"Unmute @{self.handle}" if muted else f"Mute @{self.handle}", "profile.mute")
        actions = Gio.SimpleActionGroup()
        mute = Gio.SimpleAction.new("mute", None)
        mute.connect("activate", lambda *_: self.user and self._controller.set_muted(self.user, not self.user.get("muted")))
        actions.add_action(mute)
        button = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="More", menu_model=menu, valign=Gtk.Align.CENTER)
        button.add_css_class("circular")
        # On the button itself: its popover menu always finds the action there.
        button.insert_action_group("profile", actions)
        return button

    # -- follow -----------------------------------------------------------------

    def _build_follow_button(self, user: dict) -> Gtk.Button | None:
        if user.get("id") == self._controller.me_id:
            return None  # your own profile
        button = Gtk.Button(valign=Gtk.Align.CENTER)
        button.add_css_class("pill")
        button.connect("clicked", lambda *_: self._on_follow_clicked())
        self._sync_follow_button(button, bool(user.get("youFollow")))
        return button

    def _sync_follow_button(self, button: Gtk.Button, following: bool) -> None:
        button.set_label("Following" if following else "Follow")
        button.set_tooltip_text(f"Unfollow @{self.handle}" if following else f"Follow @{self.handle}")
        (button.remove_css_class if following else button.add_css_class)("suggested-action")

    def _on_follow_clicked(self) -> None:
        if not self.user:
            return
        if self.user.get("youFollow"):
            dialog = Adw.AlertDialog(heading=f"Unfollow @{self.handle}?", body="Their posts will no longer show up in your Following timeline.")
            dialog.add_response("cancel", "Cancel")
            dialog.add_response("unfollow", "Unfollow")
            dialog.set_response_appearance("unfollow", Adw.ResponseAppearance.DESTRUCTIVE)
            dialog.connect("response", lambda _d, response: response == "unfollow" and self._set_following(False))
            dialog.present(self)
        else:
            self._set_following(True)

    def _set_following(self, follow: bool) -> None:
        user, button = self.user, self._follow_button
        before_follow, before_count = bool(user.get("youFollow")), user.get("followersCount") or 0

        def apply(following: bool, count: int) -> None:
            user["youFollow"], user["followersCount"] = following, count
            self._sync_follow_button(button, following)
            self._counts.set_markup(_counts_markup(user))

        apply(follow, max(0, before_count + (1 if follow else -1)))  # optimistic
        button.set_sensitive(False)

        def done(_result, error):
            button.set_sensitive(True)
            if error:
                apply(before_follow, before_count)
                self._controller.report_error(error, f"Couldn't {'follow' if follow else 'unfollow'} @{self.handle}")

        self._controller.sidecar.call("user.follow", {"id": user["id"], "follow": follow}, done)
