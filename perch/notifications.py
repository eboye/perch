"""X notifications tab."""

import re

from gi.repository import Adw, Gtk, Pango

from perch import format as fmt
from perch import images, motion, settings
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
    "birdwatch_icon": ("emoji-people-symbolic", "accent"),  # Community Notes
}
_DEFAULT_ICON = ("preferences-system-notifications-symbolic", "accent")
MAX_AVATARS = 8  # people shown on one row


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
        self.icon = item.get("icon")
        # The people it's about (first one opens when there's no post or usable link).
        self.people = [users[uid] for uid in item.get("from") or [] if uid in users]
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
        people = [users[uid] for uid in item.get("from") or [] if uid in users][:MAX_AVATARS]
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
        # Which post it's about (as X shows it): a short excerpt.
        if post_text := (item.get("postText") or "").strip():
            excerpt = Gtk.Label(label=post_text.replace("\n", " "), xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, lines=2, ellipsize=Pango.EllipsizeMode.END)
            excerpt.add_css_class("dim-label")
            excerpt.add_css_class("notification-post")
            content.append(excerpt)
        # Community Notes: the row opens the post here; rating the note is only possible on X.
        if self.icon == "birdwatch_icon" and self.url and "/birdwatch/" in self.url:
            rate = Gtk.Button(label="Rate on X", halign=Gtk.Align.START, tooltip_text="Open the note on X to rate it")
            rate.add_css_class("pill")
            rate.add_css_class("small-pill")
            rate.connect("clicked", lambda *_: Gtk.UriLauncher(uri=self.url).launch(self.get_root(), None, None, None))
            content.append(rate)
        when = Gtk.Label(label=fmt.relative_time(item.get("receivedAt")), xalign=0, css_classes=["dim-label", "caption"])
        when.set_tooltip_text(fmt.full_time(item.get("receivedAt")))
        content.append(when)
        row.append(content)

        if unread:
            dot = Gtk.Box(valign=Gtk.Align.CENTER, width_request=8, height_request=8)
            dot.add_css_class("unread-dot")
            row.append(dot)
        self.set_child(row)


# Likes and reposts of the same post that X sent as separate entries become one row.
_GROUPED = {"heart_icon": "liked", "retweet_icon": "reposted"}
_OTHERS = re.compile(r"\band (\d+) others?\b")


def _people_count(item: dict) -> int:
    """How many people an entry is about: X lists a few and says "and N others" for the rest."""
    shown = len(item.get("from") or [])
    match = _OTHERS.search(item.get("message") or "")
    return max(shown, 1 + int(match.group(1))) if match else max(shown, 1)


def group_notifications(items: list[dict], users: dict) -> list[dict]:
    """Merge likes (and reposts) of the same post into one entry, newest first, with everyone in it."""
    merged: list[dict] = []
    by_key: dict[tuple, dict] = {}
    for item in items:
        verb = _GROUPED.get(item.get("icon") or "")
        post = post_id_for(item)
        key = (item.get("icon"), post)
        if not verb or not post:
            merged.append(item)
            continue
        if key not in by_key:
            group = {**item, "from": list(item.get("from") or []), "parts": [item]}
            by_key[key] = group
            merged.append(group)
            continue
        group = by_key[key]
        group["parts"].append(item)
        group["from"] += [uid for uid in item.get("from") or [] if uid not in group["from"]]
        group["receivedAt"] = max(group.get("receivedAt") or "", item.get("receivedAt") or "")
        group["postText"] = group.get("postText") or item.get("postText")
    for group in by_key.values():
        parts = group.pop("parts")
        if len(parts) > 1:
            total = sum(_people_count(part) for part in parts)
            names = [(users.get(uid) or {}).get("fullName") or "Someone" for uid in group["from"][:2]]
            verb = _GROUPED[group["icon"]]
            others = total - len(names)
            if others <= 0:
                who = " and ".join(names)
            else:
                who = f"{', '.join(names)} and {others} other{'s' if others != 1 else ''}"
            group["message"] = f"{who} {verb} your post"
    return merged


# Filters over the All list (no extra requests): which notification icons each one keeps.
_FILTERS = {"likes": {"heart_icon"}, "reposts": {"retweet_icon"}, "follows": {"person_icon"}, "notes": {"birdwatch_icon"}}
_EMPTY = {"all": "No notifications yet", "mentions": "No mentions yet", "likes": "No likes yet", "reposts": "No reposts yet", "follows": "No new followers yet", "notes": "No Community Notes yet"}


class NotificationsView(PagedListView):
    """All notifications (or just likes / reposts / follows / Community Notes among them), or (``mode='mentions'``)
    the posts that mention you."""

    error_title = "Couldn't load notifications"
    empty_title = "No notifications yet"
    empty_icon = "preferences-system-notifications-symbolic"
    # The filter switch sits above the rows, so the list stays on screen even when empty.
    always_show_list = True
    hide_muted_words = True  # applies to the Mentions posts (notification rows have no post text)

    def __init__(self, controller):
        self.mode = "all"
        super().__init__(controller)
        self._seen_at = settings.account_get("notifications_seen_at") or ""
        self.newest_at = ""
        switch = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=4)
        switch.add_css_class("round")
        for name, label in (("all", "All"), ("mentions", "Mentions"), ("likes", "Likes"), ("reposts", "Reposts"), ("follows", "Follows"), ("notes", "Notes")):
            switch.add(Adw.Toggle(name=name, label=label))
        switch.set_active_name("all")
        switch.connect("notify::active-name", lambda group, _p: self.set_mode(group.get_active_name()))
        self.set_header(switch)
        self._list.set_filter_func(self._keep_row)
        self._placeholder = Gtk.Label(label=self.empty_label, margin_top=24, margin_bottom=24, css_classes=["dim-label"])
        self._list.set_placeholder(self._placeholder)

    @property
    def empty_label(self) -> str:  # noqa: D401 - read by PagedListView
        return _EMPTY.get(self.mode, "No notifications yet")

    def _source(self, mode: str) -> str:
        return "mentions" if mode == "mentions" else "all"

    def _keep_row(self, row: Gtk.ListBoxRow) -> bool:
        kinds = _FILTERS.get(self.mode)
        return kinds is None or getattr(row, "icon", None) in kinds

    def set_mode(self, mode: str) -> None:
        if mode == self.mode:
            return
        same_data = self._source(mode) == self._source(self.mode)
        self.mode = mode
        self._placeholder.set_label(self.empty_label)
        if same_data and self.loaded_once:
            self._list.invalidate_filter()  # Likes / Reposts / Follows / Notes: the All list, filtered
            motion.play(self._list, "refilter", 240)  # the filtered list fades in
        else:
            self.reload()

    def fetch_page(self, cursor, callback) -> None:
        if self.mode == "mentions":
            self._controller.sidecar.call("notifications.mentions", {"cursor": cursor}, callback)
            return
        if cursor is None:  # fresh load: unread dots relative to what was last marked seen
            self._seen_at = settings.account_get("notifications_seen_at") or ""

        def grouped(result, error):
            if result is not None:
                result = {**result, "list": group_notifications(result.get("list") or [], result.get("users") or {})}
            callback(result, error)

        self._controller.sidecar.call("notifications", {"cursor": cursor}, grouped)

    def make_row(self, item: dict, page: dict) -> Gtk.ListBoxRow:
        if self.mode == "mentions":
            return TweetRow(item, self._controller)
        received = item.get("receivedAt") or ""
        return NotificationRow(item, page.get("users") or {}, unread=bool(self._seen_at) and received > self._seen_at, controller=self._controller)

    def on_page_loaded(self, page: dict, first: bool) -> None:
        if self._source(self.mode) == "all" and first and page.get("list"):
            self.newest_at = max(item.get("receivedAt") or "" for item in page["list"])
            self._controller.on_notifications_loaded()

    def on_row_activated(self, row: Gtk.ListBoxRow) -> None:
        if isinstance(row, TweetRow):
            row.activate_post()
            return
        # Everything opens in Perch: likes, reposts, replies and mentions → the post; follows → the
        # profile; Spaces → the Space; others ("new posts from …", which link only an internal X
        # path) → the person they're about. The browser only for a link that isn't X at all.
        if row.post_id:
            self._controller.open_tweet_by_id(row.post_id, fallback_url=row.url)
        elif row.url and fmt.x_link_target(row.url):
            self._controller.open_link(row.url)
        elif row.people:
            self._controller.open_profile(row.people[0])
        elif row.url and not row.url.startswith("https://x.com/notifications"):
            Gtk.UriLauncher(uri=row.url).launch(self.get_root(), None, None, None)

    def mark_seen(self) -> None:
        """Remember the newest loaded notification as read (drives the tab badge and unread dots)."""
        if self.newest_at and self.newest_at > (settings.account_get("notifications_seen_at") or ""):
            settings.account_set("notifications_seen_at", self.newest_at)
