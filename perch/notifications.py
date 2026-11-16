"""X notifications tab."""

import re

from gi.repository import Adw, Gtk, Pango

from perch import format as fmt
from perch import images, settings
from perch.paged_list import PagedListView
from perch.tweet_row import TweetRow, link_to_profile

# notification_icon -> (symbolic icon, css class)
_ICONS = {
    "heart_icon": ("emote-love-symbolic", "liked"),
    "retweet_icon": ("media-playlist-repeat-symbolic", "reposted"),
    "person_icon": ("avatar-default-symbolic", "accent"),
    "bell_icon": ("preferences-system-notifications-symbolic", "accent"),
    "live_icon": ("audio-input-microphone-symbolic", "liked"),
    "recommendation_icon": ("starred-symbolic", "accent"),
    "bird_icon": ("dialog-information-symbolic", "accent"),
}
_DEFAULT_ICON = ("preferences-system-notifications-symbolic", "accent")


def icon_for(kind: str | None) -> str:
    return _ICONS.get(kind or "", _DEFAULT_ICON)[0]


_STATUS_URL = re.compile(r"/status(?:es)?/(\d+)")


def post_id_for(item: dict) -> str | None:
    """The post a notification is about: its numeric `target`, else a /status/<id> url."""
    for target in item.get("target") or []:
        if str(target).isdigit():
            return str(target)
    match = _STATUS_URL.search(item.get("url") or "")
    return match.group(1) if match else None


def web_url_for(item: dict) -> str | None:
    """A browser-openable link. X sometimes sends a relative API path (/2/notifications/view/…),
    which isn't a web page; fall back to the notifications page for those."""
    url = item.get("url") or ""
    if url.startswith(("https://", "http://")):
        return url
    return "https://x.com/notifications" if url else None


class NotificationRow(Gtk.ListBoxRow):
    def __init__(self, item: dict, users: dict, unread: bool, controller=None):
        self.post_id = post_id_for(item)
        self.url = web_url_for(item)
        super().__init__(activatable=bool(self.post_id or self.url))
        self.add_css_class("tweet")
        icon_name, css = _ICONS.get(item.get("icon") or "", _DEFAULT_ICON)

        row = Gtk.Box(spacing=12)
        icon = Gtk.Image.new_from_icon_name(icon_name)
        icon.set_pixel_size(22)
        icon.set_valign(Gtk.Align.START)
        icon.set_margin_top(4)
        icon.add_css_class("notification-icon")
        icon.add_css_class(css)
        row.append(icon)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        people = [users[uid] for uid in item.get("from") or [] if uid in users][:6]
        if people:
            avatars = Gtk.Box(spacing=4)
            for person in people:
                avatar = Adw.Avatar(size=30, text=person.get("fullName") or "", show_initials=True)
                images.load(fmt.avatar_url(person.get("profileImage")), lambda tex, a=avatar: tex and a.set_custom_image(tex))
                link_to_profile(avatar, person, controller)
                avatars.append(avatar)
            content.append(avatars)

        message = Gtk.Label(label=item.get("message") or "", xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR)
        content.append(message)
        when = Gtk.Label(label=fmt.relative_time(item.get("receivedAt")), xalign=0, css_classes=["dim-label", "caption"])
        when.set_tooltip_text(fmt.full_time(item.get("receivedAt")))
        content.append(when)
        row.append(content)

        if unread:
            dot = Gtk.Box(valign=Gtk.Align.CENTER, width_request=8, height_request=8)
            dot.add_css_class("unread-dot")
            row.append(dot)
        self.set_child(row)


class NotificationsView(PagedListView):
    """All notifications, or (``mode='mentions'``) the posts that mention you."""

    error_title = "Couldn't load notifications"
    empty_title = "No notifications yet"
    empty_icon = "preferences-system-notifications-symbolic"
    # The All / Mentions switch sits above the rows, so the list stays on screen even when empty.
    always_show_list = True
    hide_muted_words = True  # applies to the Mentions posts (notification rows have no post text)

    def __init__(self, controller):
        self.mode = "all"
        super().__init__(controller)
        self._seen_at = settings.account_get("notifications_seen_at") or ""
        self.newest_at = ""
        switch = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=4)
        switch.add_css_class("round")
        switch.add(Adw.Toggle(name="all", label="All"))
        switch.add(Adw.Toggle(name="mentions", label="Mentions"))
        switch.set_active_name("all")
        switch.connect("notify::active-name", lambda group, _p: self.set_mode(group.get_active_name()))
        self.set_header(switch)

    @property
    def empty_label(self) -> str:  # noqa: D401 - read by PagedListView
        return "No mentions yet" if self.mode == "mentions" else "No notifications yet"

    def set_mode(self, mode: str) -> None:
        if mode != self.mode:
            self.mode = mode
            self.reload()

    def fetch_page(self, cursor, callback) -> None:
        if self.mode == "mentions":
            self._controller.sidecar.call("notifications.mentions", {"cursor": cursor}, callback)
            return
        if cursor is None:  # fresh load: unread dots relative to what was last marked seen
            self._seen_at = settings.account_get("notifications_seen_at") or ""
        self._controller.sidecar.call("notifications", {"cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> Gtk.ListBoxRow:
        if self.mode == "mentions":
            return TweetRow(item, self._controller)
        received = item.get("receivedAt") or ""
        return NotificationRow(item, page.get("users") or {}, unread=bool(self._seen_at) and received > self._seen_at, controller=self._controller)

    def on_page_loaded(self, page: dict, first: bool) -> None:
        if self.mode == "all" and first and page.get("list"):
            self.newest_at = max(item.get("receivedAt") or "" for item in page["list"])
            self._controller.on_notifications_loaded()

    def on_row_activated(self, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, TweetRow):
            row.activate_post()
            return
        # Likes, reposts, replies, mentions → the post page; follows → the profile page;
        # anything else → the browser.
        if row.post_id:
            self._controller.open_tweet_by_id(row.post_id, fallback_url=row.url)
        elif row.url and not self._controller.open_link(row.url):
            Gtk.UriLauncher(uri=row.url).launch(self.get_root(), None, None, None)

    def mark_seen(self) -> None:
        """Remember the newest loaded notification as read (drives the tab badge and unread dots)."""
        if self.newest_at and self.newest_at > (settings.account_get("notifications_seen_at") or ""):
            settings.account_set("notifications_seen_at", self.newest_at)
