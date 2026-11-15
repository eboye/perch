"""Widget that renders one tweet (or repost) from the sidecar's plain-JSON Tweet."""

from gi.repository import Adw, Gtk, Pango

from perch import format as fmt
from perch import images


def _label(text: str = "", *classes: str, **props) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, **props)
    for css in classes:
        label.add_css_class(css)
    return label


def _set_avatar(avatar: Adw.Avatar, user: dict) -> None:
    images.load(fmt.avatar_url(user.get("profileImage")), lambda tex: tex and avatar.set_custom_image(tex))


def _header(user: dict, created_at: str | None) -> Gtk.Box:
    box = Gtk.Box(spacing=4)
    name = _label(user.get("fullName") or user.get("userName") or "?", "heading", ellipsize=Pango.EllipsizeMode.END)
    name.set_hexpand(False)
    box.append(name)
    if user.get("isVerified"):
        badge = Gtk.Image.new_from_icon_name("perch-verified-symbolic")
        badge.add_css_class("verified")
        badge.set_tooltip_text("Verified")
        box.append(badge)
    handle = _label(f"@{user.get('userName', '')}", "dim-label", ellipsize=Pango.EllipsizeMode.END)
    handle.set_hexpand(True)
    handle.set_xalign(0)
    box.append(handle)
    when = _label(fmt.relative_time(created_at), "dim-label", "numeric")
    when.set_tooltip_text(fmt.full_time(created_at))
    box.append(when)
    return box


def _body(tweet: dict) -> Gtk.Label | None:
    strip = bool(tweet.get("media") or tweet.get("quoted"))
    markup = fmt.tweet_markup(tweet.get("fullText", ""), strip_trailing_link=strip)
    if not markup:
        return None
    body = _label("", "tweet-text", wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, use_markup=True)
    body.set_markup(markup)
    return body


def _media(items: list[dict], compact: bool = False) -> Gtk.Widget | None:
    items = [m for m in items or [] if fmt.media_thumb(m)]
    if not items:
        return None
    if compact:
        items = items[:1]
    grid = Gtk.Grid(column_spacing=4, row_spacing=4, column_homogeneous=True)
    grid.add_css_class("media-grid")
    height = 120 if compact else (260 if len(items) == 1 else 150)
    for index, media in enumerate(items[:4]):
        picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, hexpand=True)
        picture.set_size_request(-1, height)
        picture.set_overflow(Gtk.Overflow.HIDDEN)
        picture.add_css_class("media")
        images.load(fmt.media_thumb(media), lambda tex, p=picture: tex and p.set_paintable(tex))
        cell: Gtk.Widget = picture
        if media.get("type") in ("VIDEO", "GIF"):
            overlay = Gtk.Overlay(child=picture)
            badge = _label("GIF" if media["type"] == "GIF" else "▶ Video", "media-badge")
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.END)
            badge.set_margin_start(8)
            badge.set_margin_bottom(8)
            overlay.add_overlay(badge)
            cell = overlay
        span = 2 if len(items) == 1 or (len(items) == 3 and index == 0) else 1
        if len(items) == 3:
            col, row = (0, 0) if index == 0 else (index - 1, 1)
        else:
            col, row = index % 2, index // 2
        grid.attach(cell, col, row, span, 1)
    return grid


class _CountButton(Gtk.Button):
    """Flat icon + count button; ``active`` adds a CSS class (liked / reposted / bookmarked)."""

    def __init__(self, icon: str, tooltip: str, active_class: str | None = None):
        super().__init__(tooltip_text=tooltip)
        self.add_css_class("flat")
        self.add_css_class("tweet-action")
        self._active_class = active_class
        box = Gtk.Box(spacing=6)
        box.append(Gtk.Image.new_from_icon_name(icon))
        self._count = _label("", "numeric")
        box.append(self._count)
        self.set_child(box)

    def update(self, count, active: bool = False) -> None:
        self._count.set_label(fmt.compact_count(count))
        if self._active_class:
            (self.add_css_class if active else self.remove_css_class)(self._active_class)


class ActionBar(Gtk.Box):
    """Reply / repost / like / views / bookmark, with optimistic toggles through the controller."""

    def __init__(self, tweet: dict, controller):
        super().__init__(homogeneous=True)
        self.add_css_class("tweet-stats")
        self._tweet = tweet
        self._controller = controller
        self._busy: set[str] = set()

        self._reply = _CountButton("mail-reply-sender-symbolic", "Reply")
        self._reply.connect("clicked", lambda *_: controller.compose(reply_to=tweet))

        self._repost = _CountButton("media-playlist-repeat-symbolic", "Repost or quote", "reposted")
        self._repost.connect("clicked", self._on_repost_clicked)
        self._repost_popover = self._build_repost_popover()

        self._like = _CountButton("emote-love-symbolic", "Like", "liked")
        self._like.connect("clicked", lambda *_: self._toggle("liked", "likeCount", "like", "unlike"))

        views = Gtk.Box(spacing=6, halign=Gtk.Align.START, valign=Gtk.Align.CENTER, margin_start=10)
        views.add_css_class("dim-label")
        views.set_tooltip_text(f"{tweet.get('viewCount') or 0:,} views")
        views.append(Gtk.Image.new_from_icon_name("view-reveal-symbolic"))
        views.append(_label(fmt.compact_count(tweet.get("viewCount")), "numeric"))

        self._bookmark = _CountButton("user-bookmarks-symbolic", "Bookmark", "bookmarked")
        self._bookmark.connect("clicked", lambda *_: self._toggle("bookmarked", "bookmarkCount", "bookmark", "unbookmark"))

        for child in (self._reply, self._repost, self._like, views, self._bookmark):
            child.set_halign(Gtk.Align.START)
            self.append(child)
        self._refresh()

    def _build_repost_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._repost_toggle = Gtk.Button(has_frame=False)
        self._repost_toggle.connect("clicked", lambda *_: self._run_repost_toggle())
        quote = Gtk.Button(label="Quote", has_frame=False)
        quote.connect("clicked", lambda *_: (popover.popdown(), self._controller.compose(quote=self._tweet)))
        box.append(self._repost_toggle)
        box.append(quote)
        popover = Gtk.Popover(child=box, has_arrow=True)
        popover.add_css_class("menu")
        popover.set_parent(self._repost)
        return popover

    def _on_repost_clicked(self, _button) -> None:
        self._repost_toggle.set_label("Undo Repost" if self._tweet.get("retweeted") else "Repost")
        self._repost_popover.popup()

    def _run_repost_toggle(self) -> None:
        self._repost_popover.popdown()
        self._toggle("retweeted", "retweetCount", "retweet", "unretweet")

    def _refresh(self) -> None:
        t = self._tweet
        self._reply.update(t.get("replyCount"))
        self._repost.update(t.get("retweetCount"), t.get("retweeted"))
        self._like.update(t.get("likeCount"), t.get("liked"))
        self._bookmark.update(None, t.get("bookmarked"))
        self._like.set_tooltip_text("Unlike" if t.get("liked") else "Like")
        self._bookmark.set_tooltip_text("Remove Bookmark" if t.get("bookmarked") else "Bookmark")

    def _toggle(self, flag: str, count_key: str, on_action: str, off_action: str) -> None:
        if flag in self._busy:
            return
        t = self._tweet
        was = bool(t.get(flag))
        old_count = t.get(count_key) or 0

        def apply(state: bool, count) -> None:
            t[flag] = state
            t[count_key] = count
            self._refresh()

        apply(not was, max(0, old_count + (-1 if was else 1)))  # optimistic
        self._busy.add(flag)

        def done(_result, error) -> None:
            self._busy.discard(flag)
            if error:
                apply(was, old_count)
                self._controller.report_error(error, "Couldn't update the post")

        self._controller.sidecar.call("tweet.action", {"action": off_action if was else on_action, "id": t["id"]}, done)


def _space_card(space_id: str, controller) -> Gtk.Widget:
    card = Gtk.Box(spacing=10)
    card.add_css_class("space-card")
    icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
    icon.set_pixel_size(24)
    card.append(icon)
    text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
    text.append(_label("Space", "heading"))
    text.append(_label("Live audio conversation", "dim-label", "caption"))
    card.append(text)
    listen = Gtk.Button(label="Listen", valign=Gtk.Align.CENTER)
    listen.add_css_class("pill")
    listen.add_css_class("suggested-action")
    listen.connect("clicked", lambda *_: controller.open_space(space_id))
    card.append(listen)
    return card


def quoted_card(tweet: dict) -> Gtk.Box:
    card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
    card.add_css_class("quoted")
    user = tweet.get("tweetBy") or {}
    top = Gtk.Box(spacing=6)
    avatar = Adw.Avatar(size=20, text=user.get("fullName") or "", show_initials=True)
    _set_avatar(avatar, user)
    top.append(avatar)
    top.append(_header(user, tweet.get("createdAt")))
    top.get_last_child().set_hexpand(True)
    card.append(top)
    if body := _body(tweet):
        card.append(body)
    if media := _media(tweet.get("media"), compact=True):
        card.append(media)
    return card


class TweetRow(Gtk.ListBoxRow):
    def __init__(self, tweet: dict, controller):
        super().__init__(activatable=True)
        self.add_css_class("tweet")
        shown = tweet.get("retweetedTweet") or tweet
        self.url = shown.get("url")
        self.tweet_id = tweet.get("id")
        user = shown.get("tweetBy") or {}

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        if tweet.get("retweetedTweet"):
            reposter = (tweet.get("tweetBy") or {}).get("fullName", "Someone")
            repost = Gtk.Box(spacing=6, margin_start=36)
            repost.add_css_class("repost-header")
            repost.add_css_class("dim-label")
            repost.append(Gtk.Image.new_from_icon_name("media-playlist-repeat-symbolic"))
            repost.append(_label(f"{reposter} reposted", ellipsize=Pango.EllipsizeMode.END))
            outer.append(repost)

        row = Gtk.Box(spacing=12)
        avatar = Adw.Avatar(size=44, text=user.get("fullName") or user.get("userName") or "", show_initials=True)
        avatar.set_valign(Gtk.Align.START)
        _set_avatar(avatar, user)
        row.append(avatar)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        content.append(_header(user, shown.get("createdAt")))
        if body := _body(shown):
            content.append(body)
        if media := _media(shown.get("media")):
            content.append(media)
        for space_id in fmt.space_ids(shown):
            content.append(_space_card(space_id, controller))
        if shown.get("quoted"):
            content.append(quoted_card(shown["quoted"]))
        content.append(ActionBar(shown, controller))
        row.append(content)
        outer.append(row)
        self.set_child(outer)
