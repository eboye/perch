"""X notifications tab."""

from gi.repository import Adw, Gtk, Pango

from perch import format as fmt
from perch import images, settings
from perch.paged_list import PagedListView

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


class NotificationRow(Gtk.ListBoxRow):
    def __init__(self, item: dict, users: dict, unread: bool):
        super().__init__(activatable=bool(item.get("url")))
        self.add_css_class("tweet")
        self.url = item.get("url")
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
                avatar.set_tooltip_text(f"{person.get('fullName', '')} @{person.get('userName', '')}")
                images.load(fmt.avatar_url(person.get("profileImage")), lambda tex, a=avatar: tex and a.set_custom_image(tex))
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
    error_title = "Couldn't load notifications"
    empty_title = "No notifications yet"
    empty_icon = "preferences-system-notifications-symbolic"

    def __init__(self, controller):
        super().__init__(controller)
        self._seen_at = settings.get("notifications_seen_at") or ""
        self.newest_at = ""

    def fetch_page(self, cursor, callback) -> None:
        if cursor is None:  # fresh load: unread dots relative to what was last marked seen
            self._seen_at = settings.get("notifications_seen_at") or ""
        self._controller.sidecar.call("notifications", {"cursor": cursor}, callback)

    def make_row(self, item: dict, page: dict) -> NotificationRow:
        received = item.get("receivedAt") or ""
        return NotificationRow(item, page.get("users") or {}, unread=bool(self._seen_at) and received > self._seen_at)

    def on_page_loaded(self, page: dict, first: bool) -> None:
        if first and page.get("list"):
            self.newest_at = max(item.get("receivedAt") or "" for item in page["list"])

    def mark_seen(self) -> None:
        """Remember the newest loaded notification as read (drives the tab badge and unread dots)."""
        if self.newest_at and self.newest_at > (settings.get("notifications_seen_at") or ""):
            settings.set("notifications_seen_at", self.newest_at)
