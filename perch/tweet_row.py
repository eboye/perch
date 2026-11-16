"""Widget that renders one tweet (or repost) from the sidecar's plain-JSON Tweet."""

import html

from gi.repository import Adw, Gdk, GLib, Gtk, Pango

from perch import format as fmt
from perch import images
from perch.focal_picture import FocalPicture


def _label(text: str = "", *classes: str, **props) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0, **props)
    for css in classes:
        label.add_css_class(css)
    return label


def _set_avatar(avatar: Adw.Avatar, user: dict) -> None:
    images.load(fmt.avatar_url(user.get("profileImage")), lambda tex: tex and avatar.set_custom_image(tex))


def link_to_profile(widget: Gtk.Widget, user: dict, controller) -> None:
    """Make an avatar / name open the user's profile. Claims the click so the post row doesn't
    also open."""
    if controller is None or not hasattr(controller, "open_profile") or not user.get("userName"):
        return
    click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
    click.connect("pressed", lambda gesture, *_: gesture.set_state(Gtk.EventSequenceState.CLAIMED))
    click.connect("released", lambda *_: controller.open_profile(user))
    widget.add_controller(click)
    widget.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
    widget.set_tooltip_text(f"View @{user['userName']}’s profile")


def _post_menu(tweet: dict, controller, on_deleted, on_translate=None) -> Gtk.MenuButton:
    """The ⋯ button on a post: copy its link, open it in the browser, translate it (another
    language), delete it (your own posts) or mute / block its author (anyone else's)."""
    # The button sits at the row's right edge: align the popover's end with it so it opens
    # leftwards instead of running off the window.
    popover = Gtk.Popover(has_arrow=True, halign=Gtk.Align.END)
    popover.add_css_class("menu")
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

    def item(label: str, callback, destructive: bool = False) -> None:
        button = Gtk.Button(label=label, has_frame=False)
        button.get_child().set_xalign(0)
        if destructive:
            button.add_css_class("error")
        button.connect("clicked", lambda *_: (popover.popdown(), callback()))
        box.append(button)

    url = tweet.get("url")
    if url:
        item("Copy Link", lambda: (Gdk.Display.get_default().get_clipboard().set(url), controller.toast("Link copied")))
        item("Open in Browser", lambda: Gtk.UriLauncher(uri=url).launch(menu.get_root(), None, None, None))
    if text := html.unescape(tweet.get("fullText") or "").strip():
        item("Copy Text", lambda: (Gdk.Display.get_default().get_clipboard().set(text), controller.toast("Text copied")))
    if on_translate and fmt.needs_translation(tweet.get("lang")):
        item("Translate Post", on_translate)
    author = tweet.get("tweetBy") or {}
    if author.get("id") == controller.me_id:
        item("Delete Post…", lambda: controller.delete_post(tweet, on_deleted), destructive=True)
    elif author.get("id") and author.get("userName") and hasattr(controller, "set_muted"):
        item(f"Mute @{author['userName']}", lambda: controller.set_muted(author, True))
        if hasattr(controller, "set_blocked"):
            item(f"Block @{author['userName']}…", lambda: controller.set_blocked(author, True), destructive=True)
    popover.set_child(box)

    menu = Gtk.MenuButton(icon_name="view-more-symbolic", popover=popover, tooltip_text="More", valign=Gtk.Align.CENTER)
    menu.add_css_class("flat")
    menu.add_css_class("post-menu")
    return menu


def _header(user: dict, created_at: str | None, controller=None, edited: bool = False, menu: Gtk.Widget | None = None) -> Gtk.Box:
    box = Gtk.Box(spacing=4)
    who = Gtk.Box(spacing=4)  # name + badge + handle: one click target for the profile
    name = _label(user.get("fullName") or user.get("userName") or "?", "heading", ellipsize=Pango.EllipsizeMode.END)
    name.set_hexpand(False)
    who.append(name)
    if user.get("isVerified"):
        badge = Gtk.Image.new_from_icon_name("perch-verified-symbolic")
        badge.add_css_class("verified")
        who.append(badge)
    handle = _label(f"@{user.get('userName', '')}", "dim-label", ellipsize=Pango.EllipsizeMode.END)
    handle.set_xalign(0)
    who.append(handle)
    link_to_profile(who, user, controller)
    box.append(who)
    box.append(Gtk.Box(hexpand=True))
    when = _label(fmt.relative_time(created_at), "dim-label", "numeric")
    when.set_tooltip_text(fmt.full_time(created_at))
    box.append(when)
    if edited:
        marker = _label("· Edited", "dim-label", "edited-marker")
        marker.set_tooltip_text("This post was edited")
        box.append(marker)
    if menu is not None:
        box.append(menu)
    return box


def _body(tweet: dict, controller=None) -> Gtk.Label | None:
    strip = bool(tweet.get("media") or tweet.get("quoted"))
    markup = fmt.tweet_markup(tweet.get("fullText", ""), strip_trailing_link=strip)
    if not markup:
        return None
    body = _label("", "tweet-text", wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, use_markup=True)
    body.set_markup(markup)
    if controller is not None and hasattr(controller, "open_link"):
        # @mentions and x.com post/profile links open inside Perch; anything else in the browser.
        body.connect("activate-link", lambda _label, uri: controller.open_link(uri))
    return body


class _Slide(Gtk.Box):
    """Clickable gallery slide. Not a Gtk.Button: a button claims the pointer on press, which
    stops Adw.Carousel from ever seeing a drag. This acts on release, ignores releases after the
    pointer moved (a swipe), never takes focus on click, and opens with Enter/Space when focused."""

    DRAG_SLOP = 8  # px

    def __init__(self, child: Gtk.Widget, tooltip: str, on_activate):
        super().__init__(focusable=True, hexpand=True, tooltip_text=tooltip)
        self.add_css_class("media-button")
        self.append(child)
        self.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
        self._on_activate = on_activate
        self._press = None

        click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        click.connect("pressed", lambda _g, _n, x, y: setattr(self, "_press", (x, y)))
        click.connect("released", self._on_released)
        click.connect("cancel", lambda *_: setattr(self, "_press", None))  # the carousel took over
        self.add_controller(click)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    def _on_released(self, _gesture, _n_press: int, x: float, y: float) -> None:
        press, self._press = self._press, None
        if press and abs(x - press[0]) <= self.DRAG_SLOP and abs(y - press[1]) <= self.DRAG_SLOP:
            self._on_activate()

    def _on_key(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter, Gdk.KEY_space):
            self._on_activate()
            return True
        return False


def _media_cell(
    media: dict, index: int, gallery: list[dict], controller, post_url: str | None, height: int, compact: bool, slide: bool = False
) -> Gtk.Widget:
    """One thumbnail (with video/GIF badges) that opens the viewer at ``index``: a Gtk.Button, or a
    swipe-friendly _Slide when it lives in a gallery carousel."""
    # Crops around X's point of interest (faces, subject) rather than the middle.
    picture = FocalPicture(media, hexpand=True)
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
        if not compact:
            play = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
            play.set_pixel_size(28)
            play.set_halign(Gtk.Align.CENTER)
            play.set_valign(Gtk.Align.CENTER)
            play.add_css_class("media-play")
            overlay.add_overlay(play)
        cell = overlay
    if controller is None:
        return cell
    kind = {"VIDEO": "Play video", "GIF": "Play GIF"}.get(media.get("type"), "View image")
    if slide:
        return _Slide(cell, kind, lambda: controller.open_media(gallery, index, post_url))
    # A button (not a click gesture) so it is keyboard reachable and doesn't activate the row.
    button = Gtk.Button(child=cell, tooltip_text=kind, hexpand=True)
    button.add_css_class("flat")
    button.add_css_class("media-button")
    button.connect("clicked", lambda *_: controller.open_media(gallery, index, post_url))
    return button


class _MediaGallery(Gtk.Box):
    """Horizontal, swipeable gallery for posts with several photos/videos.

    Swipe or drag to move; ←/→ buttons appear on hover. The scroll wheel is left to the timeline
    (allow_scroll_wheel=False) so scrolling past a post never flips its slides."""

    HEIGHT = 280

    def __init__(self, items: list[dict], controller, post_url: str | None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._count = len(items)
        self._carousel = Adw.Carousel(allow_scroll_wheel=False, allow_long_swipes=False, spacing=8, hexpand=True)
        self._carousel.set_overflow(Gtk.Overflow.HIDDEN)
        self._carousel.add_css_class("media-gallery")
        for index, media in enumerate(items):
            self._carousel.append(_media_cell(media, index, items, controller, post_url, self.HEIGHT, compact=False, slide=True))
        self._carousel.connect("page-changed", lambda _c, i: self._sync(i))

        self._counter = _label("", "media-counter")
        self._counter.set_halign(Gtk.Align.END)
        self._counter.set_valign(Gtk.Align.START)
        self._counter.set_margin_top(8)
        self._counter.set_margin_end(8)
        self._prev = self._arrow("go-previous-symbolic", "Previous", Gtk.Align.START, -1)
        self._next = self._arrow("go-next-symbolic", "Next", Gtk.Align.END, 1)

        stage = Gtk.Overlay(child=self._carousel)
        for widget in (self._counter, self._prev, self._next):
            stage.add_overlay(widget)
        hover = Gtk.EventControllerMotion()
        hover.connect("enter", lambda *_: self._set_hover(True))
        hover.connect("leave", lambda *_: self._set_hover(False))
        stage.add_controller(hover)
        # Adw.Carousel's swipe tracker consumes scroll events even when they are vertical, which
        # would freeze the timeline under the pointer. Hand vertical scrolling to the timeline.
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.BOTH_AXES)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._on_scroll)
        stage.add_controller(wheel)
        self.append(stage)
        self.append(Adw.CarouselIndicatorDots(carousel=self._carousel))
        self._hovered = False
        self._sync(0)

    def _arrow(self, icon: str, tooltip: str, side: Gtk.Align, delta: int) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, halign=side, valign=Gtk.Align.CENTER, margin_start=8, margin_end=8)
        for css in ("osd", "circular", "media-nav", "gallery-nav"):
            button.add_css_class(css)
        button.connect("clicked", lambda *_: self._step(delta))
        return button

    def _on_scroll(self, controller: Gtk.EventControllerScroll, dx: float, dy: float) -> bool:
        if abs(dx) >= abs(dy):
            return False  # horizontal (touchpad swipe): let the carousel page
        scroller = self.get_ancestor(Gtk.ScrolledWindow)
        if scroller is None:
            return False
        adjustment = scroller.get_vadjustment()
        if controller.get_unit() == Gdk.ScrollUnit.WHEEL:
            step = adjustment.get_page_size() ** (2 / 3)  # GtkScrolledWindow's own wheel step
        else:
            step = 1.0  # touchpad: deltas are already in pixels
        adjustment.set_value(adjustment.get_value() + dy * step)
        return True

    def _step(self, delta: int) -> None:
        target = round(self._carousel.get_position()) + delta
        if 0 <= target < self._count:
            self._carousel.scroll_to(self._carousel.get_nth_page(target), True)

    def _set_hover(self, hovered: bool) -> None:
        self._hovered = hovered
        self._sync(round(self._carousel.get_position()))

    def _sync(self, index: int) -> None:
        self._counter.set_label(f"{index + 1} / {self._count}")
        self._prev.set_visible(self._hovered and index > 0)
        self._next.set_visible(self._hovered and index < self._count - 1)


def _media(items: list[dict], controller=None, post_url: str | None = None, compact: bool = False) -> Gtk.Widget | None:
    items = [m for m in items or [] if fmt.media_thumb(m)][:4]
    if not items:
        return None
    if len(items) > 1 and not compact:
        return _MediaGallery(items, controller, post_url)
    # A single item, or the compact (quoted post) preview showing the first of several;
    # the viewer still gets every item.
    return _media_cell(items[0], 0, items, controller, post_url, 120 if compact else 260, compact)


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
        """Repost / Quote, made when the button is clicked and detached again when it closes (a popover
        left attached to the button kept rows from being freed)."""
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
        popover.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() or GLib.SOURCE_REMOVE))
        return popover

    def _on_repost_clicked(self, _button) -> None:
        self._repost_popover = self._build_repost_popover()
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

    def toggle_like(self) -> None:
        self._toggle("liked", "likeCount", "like", "unlike")

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


def quoted_card(tweet: dict, controller=None) -> Gtk.Box:
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
    if body := _body(tweet, controller):
        card.append(body)
    if media := _media(tweet.get("media"), controller, tweet.get("url"), compact=True):
        card.append(media)
    return card


# Detail-page counts; the ones with a sidecar `tweet.engagers` kind open the list of those people.
_DETAIL_COUNTS = (
    ("retweetCount", "Repost", "Reposts", "retweeters"),
    ("quoteCount", "Quote", "Quotes", "quotes"),
    ("likeCount", "Like", "Likes", "likers"),
    ("bookmarkCount", "Bookmark", "Bookmarks", None),
    ("viewCount", "View", "Views", None),
)


def _detail_stats(tweet: dict, controller=None) -> Gtk.Widget:
    """Full date and the complete counts, as on a post's own page. Reposts / Likes open who did
    it, Quotes the posts quoting it; "Edited" opens the edit history."""
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, margin_top=4)
    when = Gtk.Label(xalign=0, use_markup=True, css_classes=["dim-label", "detail-stats"])
    when_markup = GLib.markup_escape_text(fmt.full_time(tweet.get("createdAt")))
    if tweet.get("edited"):
        when_markup += '   ·   <a href="perch:history" title="Show earlier versions">Edited — view history</a>'
    when.set_markup(when_markup)
    box.append(when)
    parts = []
    # X keeps likes private: only a post's author can see who liked it.
    mine = controller is not None and (tweet.get("tweetBy") or {}).get("id") == getattr(controller, "me_id", None)
    for key, singular, plural, kind in _DETAIL_COUNTS:
        count = tweet.get(key) or 0
        if not count:
            continue
        text = f"<b>{count:,}</b> {singular if count == 1 else plural}"
        if kind == "likers" and not mine:
            kind = None
        if kind and controller is not None:
            title = "See the quotes" if kind == "quotes" else "See who"
            text = f'<a href="perch:{kind}" title="{title}">{text}</a>'
        parts.append(text)
    stats = None
    if parts:
        stats = _label("", "detail-stats", wrap=True, use_markup=True)
        stats.set_markup("   ".join(parts))
        box.append(stats)

    def on_link(_label, uri: str) -> bool:
        if controller is None or not uri.startswith("perch:"):
            return False
        kind = uri.removeprefix("perch:")
        if kind == "history":
            controller.open_edit_history(tweet)
        elif kind == "quotes":
            controller.open_quotes(tweet)
        else:
            controller.open_engagers(tweet, kind)
        return True

    for label in (when, stats):
        if label is not None:
            label.connect("activate-link", on_link)
    return box


def _clickable_quote(card: Gtk.Widget, quoted: dict, controller) -> Gtk.Widget:
    """Clicking a quoted post opens it (media inside it keeps its own buttons)."""
    if controller is None or not hasattr(controller, "open_tweet"):
        return card
    click = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
    # Claim on press so the surrounding row doesn't also activate (and open the outer post).
    click.connect("pressed", lambda gesture, *_: gesture.set_state(Gtk.EventSequenceState.CLAIMED))
    click.connect("released", lambda *_: controller.open_tweet(quoted))
    card.add_controller(click)
    card.set_cursor(Gdk.Cursor.new_from_name("pointer", None))
    card.set_tooltip_text("Open quoted post")
    return card


def _link_under_pointer(widget: Gtk.Widget) -> str | None:
    """The link (in any post-text label inside ``widget``) currently under the mouse pointer."""
    if isinstance(widget, Gtk.Label) and widget.has_css_class("tweet-text"):
        return widget.get_current_uri()
    child = widget.get_first_child()
    while child is not None:
        if uri := _link_under_pointer(child):
            return uri
        child = child.get_next_sibling()
    return None


class TweetRow(Gtk.ListBoxRow):
    """One post. ``detailed=True`` is the focused post on its own page: larger text, full date
    and counts, and not clickable (it's already open)."""

    def activate_post(self) -> None:
        """What clicking the row does. The list box takes the click before the post text's own
        link handling sees it, so a click on a link (@mention, #tag, URL) would otherwise open the
        post: follow the link under the pointer instead, if there is one."""
        if uri := _link_under_pointer(self):
            if not self._controller.open_link(uri):
                Gtk.UriLauncher(uri=uri).launch(self.get_root(), None, None, None)
            return
        self._controller.open_tweet(self.tweet)

    def __init__(self, tweet: dict, controller, detailed: bool = False):
        super().__init__(activatable=not detailed)
        self.add_css_class("tweet")
        if detailed:
            self.add_css_class("tweet-detail")
        shown = tweet.get("retweetedTweet") or tweet
        self.tweet = shown  # what this row displays (the original, for a repost)
        self.url = shown.get("url")
        self.tweet_id = tweet.get("id")
        self._controller = controller
        user = shown.get("tweetBy") or {}
        # Who this row is "from": the author, and the reposter for a repost (muting either hides it).
        self.author_ids = {i for i in (user.get("id"), (tweet.get("tweetBy") or {}).get("id")) if i}

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
        link_to_profile(avatar, user, controller)
        row.append(avatar)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6, hexpand=True)
        menu = _post_menu(shown, controller, self._on_deleted, self.translate) if hasattr(controller, "delete_post") else None
        content.append(_header(user, shown.get("createdAt"), controller, edited=bool(shown.get("edited")), menu=menu))
        if body := _body(shown, controller):
            content.append(body)
        # X's translation, under the original once asked for (⋯ → Translate Post).
        self._translation = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4, visible=False, css_classes=["translation"])
        content.append(self._translation)
        if media := _media(shown.get("media"), controller, shown.get("url")):
            content.append(media)
        for space_id in fmt.space_ids(shown):
            content.append(_space_card(space_id, controller))
        if shown.get("quoted"):
            content.append(_clickable_quote(quoted_card(shown["quoted"], controller), shown["quoted"], controller))
        if detailed:
            content.append(_detail_stats(shown, controller))
        self.actions = ActionBar(shown, controller)
        content.append(self.actions)
        row.append(content)
        outer.append(row)
        self.set_child(outer)

    def translate(self) -> None:
        """Ask X for a translation of this post and show it under the original."""
        if self._translation.get_visible():
            return
        spinner = Adw.Spinner(halign=Gtk.Align.START, width_request=16, height_request=16)
        self._translation.append(spinner)
        self._translation.set_visible(True)

        def done(result, error):
            self._translation.remove(spinner)
            if error:
                self._translation.set_visible(False)
                self._controller.report_error(error, "Couldn't translate the post")
                return
            source = result.get("from") or "another language"
            heading = Gtk.Box(spacing=6)
            heading.append(_label(f"Translated from {source}", "dim-label", "caption", hexpand=True))
            hide = Gtk.Button(label="Hide", css_classes=["flat", "caption"], valign=Gtk.Align.CENTER)
            hide.connect("clicked", lambda *_: self._clear_translation())
            heading.append(hide)
            self._translation.append(heading)
            text = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=True, use_markup=True)
            text.set_markup(fmt.tweet_markup(result.get("text") or ""))
            text.connect("activate-link", lambda _label, uri: self._controller.open_link(uri))
            self._translation.append(text)

        self._controller.sidecar.call("tweet.translate", {"id": self.tweet["id"]}, done)

    def _clear_translation(self) -> None:
        while child := self._translation.get_first_child():
            self._translation.remove(child)
        self._translation.set_visible(False)

    def _on_deleted(self) -> None:
        """The post is gone on X: drop it from this list (the repost wrapper too)."""
        self.set_visible(False)
