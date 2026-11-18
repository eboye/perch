"""Direct messages: inbox list (a tab) and a conversation page pushed on the window's NavigationView."""

import base64
import re
import weakref
from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Graphene, Gtk, Pango

from perch import format as fmt
from perch import images, motion
from perch.focal_picture import FocalPicture
from perch.spelling import MessageBox
from perch.paged_list import PagedListView

# A photo attached to a DM (X serves it only with the session; see the sidecar's dm.media).
DM_PHOTO_URL = re.compile(r"^https://ton\.(?:x|twitter)\.com/(?:i/ton|1\.1/ton)/data/dm/")
# A GIF or video attached to a DM (X serves the MP4 without the session).
DM_VIDEO_URL = re.compile(r"^https://video\.twimg\.com/dm_(?:gif|video)/")
# The quick reactions offered on a message (X's own set).
QUICK_REACTIONS = ("😂", "😲", "😢", "❤️", "🔥", "👍", "👎")
# Image types X accepts in a message.
DM_IMAGE_TYPES = ("image/jpeg", "image/png", "image/webp")
ENCRYPTED_TEXT = "Encrypted message — open X to read it"
UNKNOWN_TEXT = "Message Perch can’t show — open X to see it"
# Conversations with at least this many messages offer "Load Earlier Messages".
OLDER_THRESHOLD = 20


def _set_avatar(avatar: Adw.Avatar, url: str | None) -> None:
    images.load(fmt.avatar_url(url), lambda tex: tex and avatar.set_custom_image(tex))


def conversation_title(conversation: dict, users: dict, me_id: str | None) -> tuple[str, str, dict | None]:
    """(title, subtitle, other user) for a conversation."""
    others = [users.get(uid) or {"id": uid} for uid in conversation.get("participants") or [] if uid != me_id]
    if conversation.get("name"):
        return conversation["name"], f"{len(others) + 1} people", None
    if not others:  # note-to-self
        me = users.get(me_id) or {}
        return me.get("fullName") or "You", f"@{me.get('userName', '')}", me
    if len(others) == 1:
        other = others[0]
        return other.get("fullName") or other.get("id", "?"), f"@{other.get('userName', '')}", other
    names = ", ".join((o.get("fullName") or o.get("userName") or "?") for o in others[:3])
    return names, f"{len(others) + 1} people", None


def message_preview(message: dict | None, me_id: str | None) -> str:
    if not message:
        return ""
    if message.get("isEncrypted") and not message.get("text"):
        text = "🔒 Encrypted message"
    elif not message.get("text") and message.get("mediaUrls"):
        text = "📎 Attachment"
    else:
        text = (message.get("text") or "").replace("\n", " ")
    return f"You: {text}" if message.get("senderId") == me_id else text


class ConversationRow(Gtk.ListBoxRow):
    def __init__(self, conversation: dict, users: dict, me_id: str | None):
        super().__init__(activatable=True)
        self.add_css_class("tweet")
        self.conversation = conversation
        self.users = users
        title, subtitle, other = conversation_title(conversation, users, me_id)

        row = Gtk.Box(spacing=12)
        avatar = Adw.Avatar(size=44, text=title, show_initials=True)
        _set_avatar(avatar, (other or {}).get("profileImage") or conversation.get("avatarUrl"))
        row.append(avatar)

        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, hexpand=True, valign=Gtk.Align.CENTER)
        top = Gtk.Box(spacing=6)
        name = Gtk.Label(label=title, xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"])
        top.append(name)
        handle = Gtk.Label(label=subtitle, xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END, css_classes=["dim-label"])
        top.append(handle)
        last = conversation.get("lastMessage") or {}
        self._when = Gtk.Label(label=fmt.relative_time(last.get("createdAt") or conversation.get("lastActivityAt")), css_classes=["dim-label", "numeric"])
        top.append(self._when)
        content.append(top)

        self._preview = Gtk.Label(label=message_preview(last, me_id), xalign=0, ellipsize=Pango.EllipsizeMode.END)
        content.append(self._preview)
        row.append(content)

        self._dot = Gtk.Box(valign=Gtk.Align.CENTER, width_request=10, height_request=10)
        self._dot.add_css_class("unread-dot")
        row.append(self._dot)
        self.set_unread(bool(conversation.get("unread")))
        self.set_child(row)

    def update_last(self, message: dict, me_id: str | None) -> None:
        self.conversation["lastMessage"] = message
        self.conversation["lastActivityAt"] = message.get("createdAt")
        self._preview.set_label(message_preview(message, me_id))
        self._when.set_label(fmt.relative_time(message.get("createdAt")))

    def set_unread(self, unread: bool) -> None:
        self.conversation["unread"] = unread
        self._dot.set_visible(unread)
        (self._preview.add_css_class if unread else self._preview.remove_css_class)("heading")
        (self._preview.remove_css_class if unread else self._preview.add_css_class)("dim-label")


class InboxView(PagedListView):
    error_title = "Couldn't load messages"
    empty_title = "No messages"
    empty_icon = "mail-unread-symbolic"
    end_label = ""

    def __init__(self, controller):
        super().__init__(controller)
        self.unread_count = 0
        # Inbox-level "seen" state on X (drives X's envelope badge; see window._maybe_mark_inbox_seen).
        self.newest_event_id: str | None = None
        self.last_seen_event_id: str | None = None
        # All / Unread switch; Unread hides read conversations (a filter on the loaded rows).
        self.unread_only = False
        switch = Adw.ToggleGroup(halign=Gtk.Align.CENTER, margin_top=10, margin_bottom=4)
        switch.add_css_class("round")
        switch.add(Adw.Toggle(name="all", label="All"))
        self._unread_toggle = Adw.Toggle(name="unread", label="Unread")
        switch.add(self._unread_toggle)
        switch.set_active_name("all")
        switch.connect("notify::active-name", lambda group, _p: self.set_unread_only(group.get_active_name() == "unread"))
        self.set_header(switch)
        self._list.set_filter_func(lambda row: not self.unread_only or bool(row.conversation.get("unread")))
        placeholder = Gtk.Label(label="No unread conversations", margin_top=24, margin_bottom=24, css_classes=["dim-label"])
        self._list.set_placeholder(placeholder)

    def set_unread_only(self, unread_only: bool) -> None:
        self.unread_only = unread_only
        self._list.invalidate_filter()

    def fetch_page(self, cursor, callback) -> None:
        def adapt(result, error):
            if result is not None:
                result = {**result, "list": result.get("conversations") or [], "next": result.get("cursor") or ""}
            callback(result, error)

        self._controller.sidecar.call("dm.inbox", {"cursor": cursor, "meId": self._controller.me_id}, adapt)

    def make_row(self, item: dict, page: dict) -> ConversationRow:
        return ConversationRow(item, page.get("users") or {}, self._controller.me_id)

    def on_page_loaded(self, page: dict, first: bool) -> None:
        if first:
            self.newest_event_id = page.get("newestEventId")
            self.last_seen_event_id = page.get("lastSeenEventId")
        self._recount()
        self._controller.on_inbox_loaded()

    def on_row_activated(self, row: ConversationRow) -> None:
        if row.conversation.get("unread"):
            row.set_unread(False)
            self._recount()
        self._controller.open_conversation(row.conversation, row.users)

    def on_message_sent(self, conversation_id: str, message: dict) -> None:
        """Reflect a message sent from a conversation page: new preview, row moved to the top."""
        index = 0
        while row := self._list.get_row_at_index(index):
            if row.conversation.get("id") == conversation_id:
                row.update_last(message, self._controller.me_id)
                if index:
                    self._list.remove(row)
                    self._list.prepend(row)
                return
            index += 1

    def remove_conversation(self, conversation_id: str) -> None:
        index = 0
        while row := self._list.get_row_at_index(index):
            if row.conversation.get("id") == conversation_id:
                self._list.remove(row)
                break
            index += 1
        if self._list.get_row_at_index(0) is None:
            self.set_visible_child_name("empty")
        self._recount()

    def _recount(self) -> None:
        count, index = 0, 0
        while row := self._list.get_row_at_index(index):
            count += bool(row.conversation.get("unread"))
            index += 1
        self.unread_count = count
        self._unread_toggle.set_label(f"Unread ({count})" if count else "Unread")
        self._list.invalidate_filter()
        self._controller.update_badges()


def _show_photo_dialog(parent: Gtk.Widget, texture: Gdk.Paintable | None) -> None:
    if texture is None:
        return
    picture = Gtk.Picture(paintable=texture, content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, vexpand=True, hexpand=True)
    view = Adw.ToolbarView(content=picture)
    view.add_top_bar(Adw.HeaderBar())
    width = min(1000, max(320, texture.get_intrinsic_width()))
    height = min(800, max(240, texture.get_intrinsic_height() + 48))
    dialog = Adw.Dialog(title="Photo", child=view, content_width=width, content_height=height)
    dialog.present(parent.get_root())


class _Bubble(Gtk.ListBoxRow):
    """One message. ``actions`` (the conversation page) provides: ``me_id``, ``open_link(uri)``,
    ``load_photo(url, callback)``, ``open_video(url, kind)``, ``reply_to(message)`` and
    ``react(bubble, emoji, remove)``. Without it (tests, previews) the bubble is display-only."""

    def __init__(self, message: dict, mine: bool, sender: dict | None, show_sender: bool, actions=None):
        super().__init__(activatable=False, selectable=False)
        self.add_css_class("bubble-row")
        self.message = message
        self._mine = mine
        # Weak: the page owns its bubbles; a strong reference back would keep a closed conversation
        # in memory (see lifecycle.py).
        self._actions = weakref.proxy(actions) if actions is not None else None
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, halign=Gtk.Align.END if mine else Gtk.Align.START)
        self._column = column
        if show_sender and sender:
            column.append(Gtk.Label(label=sender.get("fullName") or "", xalign=0, css_classes=["caption", "dim-label"]))

        # The message this one replies to, quoted above it.
        if reply := message.get("replyTo"):
            quote = Gtk.Label(label=f"↩ {(reply.get('text') or 'a message').strip()}", xalign=0, ellipsize=Pango.EllipsizeMode.END, max_width_chars=40)
            quote.add_css_class("bubble-reply")
            quote.add_css_class("caption")
            quote.set_halign(Gtk.Align.END if mine else Gtk.Align.START)
            quote.set_tooltip_text(reply.get("text") or "")
            column.append(quote)

        # Photos: one you just sent from Perch (a local file), or ones X has (loaded with the session).
        media = 0
        if local_image := message.get("localImage"):
            try:
                column.append(self._photo(mine, Gdk.Texture.new_from_filename(local_image)))
                media += 1
            except GLib.Error:
                pass
        elif actions is not None:
            for url in [u for u in message.get("mediaUrls") or [] if DM_PHOTO_URL.match(u)][:4]:
                picture = self._photo(mine, None)
                actions.load_photo(url, lambda texture, p=picture: self._show_photo(p, texture))
                column.append(picture)
                media += 1
            # GIFs and videos: X serves the MP4 itself without the session; play it in the viewer.
            for url in [u for u in message.get("mediaUrls") or [] if DM_VIDEO_URL.match(u)][:2]:
                kind = "GIF" if "/dm_gif/" in url else "VIDEO"
                play = Gtk.Button(label=f"▶ {'GIF' if kind == 'GIF' else 'Video'}", halign=Gtk.Align.END if mine else Gtk.Align.START, tooltip_text="Play")
                play.add_css_class("pill")
                play.add_css_class("bubble-video")
                play.connect("clicked", lambda *_, u=url, k=kind: actions.open_video(u, k))
                column.append(play)
                media += 1

        text = message.get("text") or ""
        if text or not media:
            encrypted = message.get("isEncrypted") and not text
            # Something X sent that Perch can't show (no text, media or encryption flag): say so rather
            # than drawing an empty bubble.
            unknown = not encrypted and not text and not message.get("mediaUrls")
            body = Gtk.Label(xalign=0, wrap=True, wrap_mode=Pango.WrapMode.WORD_CHAR, selectable=not (encrypted or unknown), max_width_chars=48)
            if encrypted:
                body.set_label(ENCRYPTED_TEXT)
            elif unknown:
                body.set_label(UNKNOWN_TEXT)
            elif text:
                body.set_markup(fmt.tweet_markup(text))  # links, @mentions and #tags are clickable
                if actions is not None:
                    body.connect("activate-link", lambda _label, uri: actions.open_link(uri))
            else:
                body.set_label("📎 Attachment")
            body.add_css_class("bubble")
            body.add_css_class("mine" if mine else "theirs")
            if encrypted or unknown:
                body.add_css_class("encrypted")
            column.append(body)

        self._reactions = Gtk.Box(spacing=4, halign=Gtk.Align.END if mine else Gtk.Align.START, visible=False)
        column.append(self._reactions)
        self.render_reactions()

        self._meta = Gtk.Label(xalign=1 if mine else 0, css_classes=["caption", "dim-label"])
        self._meta.set_tooltip_text(fmt.full_time(message.get("createdAt")))
        column.append(self._meta)
        self.set_status(fmt.relative_time(message.get("createdAt")))
        self.set_child(column)

        self._menu_enabled = False
        self.enable_menu()

    def enable_menu(self) -> None:
        """Right-click or long-press: react, reply, copy. Only for messages X knows (not one still
        sending, or an encrypted one Perch can't read)."""
        if self._menu_enabled or self._actions is None or not self.can_interact():
            return
        self._menu_enabled = True
        # Capture phase: the message text is selectable, and a selectable label would otherwise
        # take the right-click for its own Copy / Select All menu.
        click = Gtk.GestureClick(button=Gdk.BUTTON_SECONDARY, propagation_phase=Gtk.PropagationPhase.CAPTURE)

        def on_right_click(gesture, _n, x, y):
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)
            self.open_menu(x, y)

        click.connect("pressed", on_right_click)
        self.add_controller(click)
        hold = Gtk.GestureLongPress(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        hold.connect("pressed", lambda _g, x, y: self.open_menu(x, y))
        self.add_controller(hold)
        self.render_reactions()

    def can_interact(self) -> bool:
        message = self.message
        return bool(message.get("id")) and not str(message["id"]).startswith("local-") and not (message.get("isEncrypted") and not message.get("text"))

    # -- reactions ----------------------------------------------------------

    def render_reactions(self) -> None:
        while child := self._reactions.get_first_child():
            self._reactions.remove(child)
        me_id = getattr(self._actions, "me_id", None)
        counts: dict[str, list[str]] = {}
        for reaction in self.message.get("reactions") or []:
            counts.setdefault(reaction["emoji"], []).append(reaction.get("senderId"))
        for emoji, senders in counts.items():
            mine = me_id in senders
            chip = Gtk.Button(label=f"{emoji} {len(senders)}" if len(senders) > 1 else emoji)
            chip.add_css_class("reaction")
            if mine:
                chip.add_css_class("mine")
            chip.set_tooltip_text("Remove your reaction" if mine else f"React {emoji} too")
            chip.set_sensitive(self._actions is not None and self.can_interact())
            chip.connect("clicked", lambda *_, e=emoji, m=mine: self._actions.react(self, e, m))
            self._reactions.append(chip)
        self._reactions.set_visible(bool(counts))

    def open_menu(self, x: float = 0, y: float = 0) -> None:
        popover = Gtk.Popover(has_arrow=True)
        popover.add_css_class("menu")
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        emojis = Gtk.Box(spacing=2, margin_start=4, margin_end=4, margin_top=4, margin_bottom=4)
        me_id = getattr(self._actions, "me_id", None)
        mine = {r["emoji"] for r in self.message.get("reactions") or [] if r.get("senderId") == me_id}
        for emoji in QUICK_REACTIONS:
            button = Gtk.Button(label=emoji, tooltip_text="Remove reaction" if emoji in mine else f"React {emoji}")
            button.add_css_class("flat")
            button.add_css_class("reaction-pick")
            if emoji in mine:
                button.add_css_class("mine")
            button.connect("clicked", lambda *_, e=emoji: (popover.popdown(), self._actions.react(self, e, e in mine)))
            emojis.append(button)
        box.append(emojis)
        for label, callback in (
            ("Reply", lambda: self._actions.reply_to(self.message)),
            ("Copy Text", lambda: Gdk.Display.get_default().get_clipboard().set(self.message.get("text") or "")),
        ):
            if label == "Copy Text" and not self.message.get("text"):
                continue
            button = Gtk.Button(label=label, has_frame=False)
            button.get_child().set_xalign(0)
            button.connect("clicked", lambda *_, c=callback: (popover.popdown(), c()))
            box.append(button)
        popover.set_child(box)
        popover.set_parent(self._column)
        rect = Gdk.Rectangle()
        ok, point = self.compute_point(self._column, Graphene.Point().init(x, y))
        rect.x, rect.y, rect.width, rect.height = (int(point.x), int(point.y), 1, 1) if ok else (0, 0, 1, 1)
        popover.set_pointing_to(rect)
        popover.connect("closed", lambda p: GLib.idle_add(lambda: p.unparent() or False))
        popover.popup()
        self._menu = popover

    # -- photos -------------------------------------------------------------

    @staticmethod
    def _photo(mine: bool, texture: Gdk.Texture | None) -> Gtk.Widget:
        """A photo in a bubble: click to see it larger."""
        # FocalPicture fills a fixed 220×165: a Gtk.Picture asks for height in proportion to the row's
        # whole width, which left a large gap under the photo.
        picture = FocalPicture(width_request=220, height_request=165, overflow=Gtk.Overflow.HIDDEN)
        picture.add_css_class("media")
        picture.add_css_class("bubble-image")
        button = Gtk.Button(child=picture, halign=Gtk.Align.END if mine else Gtk.Align.START, tooltip_text="View Photo", sensitive=texture is not None)
        button.add_css_class("flat")
        button.add_css_class("bubble-photo")
        button.connect("clicked", lambda b: _show_photo_dialog(b, picture.get_paintable()))
        if texture is not None:
            picture.set_paintable(texture)
        return button

    @staticmethod
    def _show_photo(button: Gtk.Button, texture: Gdk.Texture | None) -> None:
        picture = button.get_child()
        if texture is None:
            button.set_child(Gtk.Label(label="📎 Photo X wouldn't send", css_classes=["dim-label", "caption"], margin_top=8, margin_bottom=8))
            return
        picture.set_paintable(texture)
        button.set_sensitive(True)

    def set_status(self, text: str, error: bool = False) -> None:
        self._meta.set_label(text)
        (self._meta.add_css_class if error else self._meta.remove_css_class)("error")


class ConversationPage(Adw.NavigationPage):
    def __init__(self, controller, conversation: dict, users: dict):
        title, subtitle, other = conversation_title(conversation, users, controller.me_id)
        super().__init__(title=title, tag=f"dm-{conversation['id']}")
        self._controller = controller
        self.conversation_id = conversation["id"]
        self._users = dict(users)
        self._last_sender: str | None = None
        self._message_ids: set[str] = set()
        self._is_group = len(conversation.get("participants") or []) > 2
        # Newest event this page has told X about (dm.markRead); starts from the inbox's view.
        self._inbox_newest = conversation.get("newestEventId")
        self._marked_read: str | None = None

        header = Adw.HeaderBar(title_widget=Adw.WindowTitle(title=title, subtitle=subtitle))
        menu = Gio.Menu()
        menu.append("Delete Conversation…", "conversation.delete")
        more = Gtk.MenuButton(icon_name="view-more-symbolic", tooltip_text="More", menu_model=menu)
        actions = Gio.SimpleActionGroup()
        delete = Gio.SimpleAction.new("delete", None)
        delete.connect("activate", lambda *_: controller.delete_conversation(self.conversation_id, title))
        actions.add_action(delete)
        more.insert_action_group("conversation", actions)
        more.releasable = [delete]  # its callback refers to this page (see lifecycle.py)
        header.pack_end(more)
        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh")
        refresh.connect("clicked", lambda *_: controller.sidecar.fresh(self.reload))
        header.pack_end(refresh)
        if other and other.get("userName") and other.get("id") != controller.me_id:
            profile = Gtk.Button(icon_name="avatar-default-symbolic", tooltip_text=f"View @{other['userName']}’s Profile")
            profile.connect("clicked", lambda *_: controller.open_profile(other))
            header.pack_end(profile)

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32), "loading")
        self._error = Adw.StatusPage(icon_name="network-error-symbolic", title="Couldn't load this conversation")
        self._stack.add_named(self._error, "error")

        self._list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
        self._list.add_css_class("bubble-list")
        # Above the messages: load older history (X's first page only covers the newest ~200).
        self._older = Gtk.Stack(hhomogeneous=False, vhomogeneous=False, margin_top=8, visible=False)
        older_button = Gtk.Button(label="Load Earlier Messages", halign=Gtk.Align.CENTER, css_classes=["pill", "small-pill"])
        older_button.connect("clicked", lambda *_: self._load_older())
        self._older.add_named(older_button, "button")
        self._older.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, width_request=24, height_request=24), "loading")
        self._older.add_named(Gtk.Label(label="Start of the conversation", css_classes=["dim-label", "caption"]), "start")
        self._older_before: str | None = None  # id to page back from
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(self._older)
        column.append(self._list)
        clamp = Adw.Clamp(maximum_size=720, child=column)
        self._scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, child=clamp, vexpand=True)
        self._stack.add_named(self._scroller, "messages")

        composer = Gtk.Box(spacing=6, margin_start=12, margin_end=12, margin_top=6, margin_bottom=6)
        attach = Gtk.Button(icon_name="insert-image-symbolic", tooltip_text="Add Image", valign=Gtk.Align.END)
        attach.add_css_class("flat")
        attach.connect("clicked", lambda *_: self._pick_image())
        composer.append(attach)
        # Several lines if you like (Shift+Enter), spell checked; Enter sends.
        self._entry = MessageBox(placeholder="Message")
        self._entry.connect("activate", lambda *_: self._send())
        self._entry.connect("changed", lambda *_: self._sync_send())
        composer.append(self._entry)
        # The picked image (one per message, X's limit), above the message box.
        self._image_path: str | None = None
        self._attachment = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=6, visible=False)
        self._attachment_picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, width_request=56, height_request=56)
        self._attachment_picture.add_css_class("media")
        self._attachment.append(self._attachment_picture)
        self._attachment_name = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.MIDDLE, css_classes=["dim-label"])
        self._attachment.append(self._attachment_name)
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Remove Image", valign=Gtk.Align.CENTER)
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        remove.connect("clicked", lambda *_: self._set_image(None))
        self._attachment.append(remove)
        # "Replying to …" while answering a specific message (right-click a message → Reply).
        self._reply_message: dict | None = None
        self._reply_bar = Gtk.Box(spacing=8, margin_start=12, margin_end=12, margin_top=6, visible=False, css_classes=["schedule-info"])
        self._reply_bar.append(Gtk.Image(icon_name="mail-reply-sender-symbolic"))
        self._reply_label = Gtk.Label(xalign=0, hexpand=True, ellipsize=Pango.EllipsizeMode.END)
        self._reply_bar.append(self._reply_label)
        cancel_reply = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Don't Reply", valign=Gtk.Align.CENTER)
        cancel_reply.add_css_class("flat")
        cancel_reply.add_css_class("circular")
        cancel_reply.connect("clicked", lambda *_: self.reply_to(None))
        self._reply_bar.append(cancel_reply)
        bottom = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bottom.append(self._reply_bar)
        bottom.append(self._attachment)
        bottom.append(composer)
        self._send_button = Gtk.Button(icon_name="mail-send-symbolic", tooltip_text="Send", sensitive=False, valign=Gtk.Align.END)
        self._send_button.add_css_class("suggested-action")
        self._send_button.add_css_class("circular")
        self._send_button.connect("clicked", lambda *_: self._send())
        composer.append(self._send_button)

        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(header)
        view.add_bottom_bar(bottom)
        self.set_child(view)
        self.reload()

    def reload(self) -> None:
        if not self._message_ids:
            self._stack.set_visible_child_name("loading")
        self._controller.sidecar.call("dm.conversation", {"id": self.conversation_id}, self._on_loaded)

    def _on_loaded(self, result, error) -> None:
        if error:
            if not self._message_ids:
                self._error.set_description(GLib.markup_escape_text(self._controller.describe_error(error, "")))
                self._stack.set_visible_child_name("error")
            else:
                self._controller.report_error(error, "Couldn't refresh the conversation")
            return
        self._users.update(result.get("users") or {})
        added = False
        arriving = bool(self._message_ids)  # (on the first load everything is new: no animation)
        for message in result.get("messages") or []:
            if message["id"] not in self._message_ids:
                bubble = self._append(message)
                if arriving:
                    motion.play(bubble, "bubble-in", 320)  # a new message slides up
                added = True
        self._stack.set_visible_child_name("messages")
        if added:
            self._scroll_to_bottom()
        if self._older_before is None and (messages := result.get("messages") or []):
            # Offer older history for a long conversation; a short one is all there is.
            numeric = [m["id"] for m in messages if str(m.get("id", "")).isdigit()]
            self._older_before = min(numeric, key=int) if numeric else messages[0]["id"]
            if len(messages) >= OLDER_THRESHOLD:
                self._older.set_visible(True)
                self._older.set_visible_child_name("button")
        self._entry.grab_focus()
        self._mark_read(result.get("messages") or [])

    def _load_older(self) -> None:
        if not self._older_before:
            return
        self._older.set_visible_child_name("loading")

        def done(result, error):
            if error:
                self._older.set_visible_child_name("button")
                self._controller.report_error(error, "Couldn't load earlier messages")
                return
            self._users.update(result.get("users") or {})
            older = [m for m in result.get("messages") or [] if m["id"] not in self._message_ids]
            self._prepend(older)
            self._older_before = result.get("cursor") or None
            self._older.set_visible_child_name("button" if self._older_before else "start")

        self._controller.sidecar.call("dm.older", {"id": self.conversation_id, "before": self._older_before}, done)

    def _prepend(self, messages: list[dict]) -> None:
        """Insert older messages (oldest first) above the current ones, keeping the view where it is."""
        if not messages:
            return
        adjustment = self._scroller.get_vadjustment()
        from_bottom = adjustment.get_upper() - adjustment.get_value()
        previous = None
        for index, message in enumerate(messages):
            self._message_ids.add(message["id"])
            sender_id = message.get("senderId")
            mine = sender_id == self._controller.me_id
            show_sender = self._is_group and not mine and sender_id != previous
            self._list.insert(_Bubble(message, mine, self._users.get(sender_id), show_sender, actions=self), index)
            previous = sender_id

        def keep_place():
            adjustment.set_value(adjustment.get_upper() - from_bottom)
            return GLib.SOURCE_REMOVE

        GLib.idle_add(keep_place)
        GLib.timeout_add(150, keep_place)  # again after the new bubbles have been measured

    def _mark_read(self, messages: list[dict]) -> None:
        """Tell X the conversation is read up to its newest event, so it stays read everywhere.
        Only snowflake ids qualify (XChat-only events carry other ids), and only forward moves."""
        candidates = [m.get("id") for m in messages] + [self._inbox_newest]
        numeric = [int(c) for c in candidates if c and str(c).isdigit()]
        if not numeric:
            return
        newest = str(max(numeric))
        if self._marked_read is not None and int(self._marked_read) >= int(newest):
            return
        self._marked_read = newest
        self._controller.mark_conversation_read(self.conversation_id, newest)

    def _append(self, message: dict) -> "_Bubble":
        self._message_ids.add(message["id"])
        sender_id = message.get("senderId")
        mine = sender_id == self._controller.me_id
        bubble = _Bubble(message, mine, self._users.get(sender_id), show_sender=self._is_group and not mine and sender_id != self._last_sender, actions=self)
        self._last_sender = sender_id
        self._list.append(bubble)
        return bubble

    # -- actions for the bubbles (see _Bubble) -------------------------------

    @property
    def me_id(self) -> str | None:
        return self._controller.me_id

    def open_link(self, uri: str) -> bool:
        return self._controller.open_link(uri)

    def open_video(self, url: str, kind: str) -> None:
        self._controller.open_media([{"type": kind, "url": url}], 0, None)

    def reply_to(self, message: dict | None) -> None:
        """Answer a specific message: shown above the message box until sent or cancelled."""
        self._reply_message = message
        if message:
            sender = self._users.get(message.get("senderId")) or {}
            who = "yourself" if message.get("senderId") == self.me_id else sender.get("fullName") or "them"
            self._reply_label.set_label(f"Replying to {who}: {(message.get('text') or 'a message').strip()}")
        self._reply_bar.set_visible(message is not None)
        self._entry.grab_focus()

    def react(self, bubble: "_Bubble", emoji: str, remove: bool) -> None:
        """Add or take back a reaction, shown at once and undone if X refuses."""
        message = bubble.message
        before = list(message.get("reactions") or [])
        others = [r for r in before if not (r.get("senderId") == self.me_id and r.get("emoji") == emoji)]
        message["reactions"] = others if remove else others + [{"emoji": emoji, "senderId": self.me_id}]
        bubble.render_reactions()

        def done(_result, error):
            if error:
                message["reactions"] = before
                bubble.render_reactions()
                self._controller.report_error(error, "Couldn't update the reaction")

        params = {"conversationId": self.conversation_id, "messageId": message["id"], "emoji": emoji, "remove": remove}
        self._controller.sidecar.call("dm.react", params, done)

    def load_photo(self, url: str, callback) -> None:
        """A DM photo: from the image cache, else downloaded through the sidecar with the session."""

        def fetch(photo_url: str, done) -> None:
            self._controller.sidecar.call("dm.media", {"url": photo_url}, lambda result, error: done(None if error else base64.b64decode(result["data"])))

        images.load_via(url, fetch, callback)

    def _scroll_to_bottom(self) -> None:
        def go():
            adjustment = self._scroller.get_vadjustment()
            adjustment.set_value(adjustment.get_upper())
            return GLib.SOURCE_REMOVE

        GLib.idle_add(go)
        GLib.timeout_add(150, go)  # again after the bubbles have been measured

    def _sync_send(self) -> None:
        self._send_button.set_sensitive(bool(self._entry.get_text().strip()) or self._image_path is not None)

    def _pick_image(self) -> None:
        image_filter = Gtk.FileFilter(name="Images")
        for mime in DM_IMAGE_TYPES:
            image_filter.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        dialog = Gtk.FileDialog(title="Add Image", filters=filters, default_filter=image_filter)

        def done(source, result):
            try:
                file = source.open_finish(result)
            except GLib.Error:
                return  # cancelled
            if file and file.get_path():
                self._set_image(file.get_path())

        dialog.open(self.get_root(), None, done)

    def _set_image(self, path: str | None) -> None:
        if path:
            try:
                self._attachment_picture.set_paintable(Gdk.Texture.new_from_filename(path))
            except GLib.Error:
                self._controller.toast("Couldn't open that image")
                return
            self._attachment_name.set_label(Path(path).name)
        self._image_path = path
        self._attachment.set_visible(path is not None)
        self._sync_send()
        self._entry.grab_focus()

    def _send(self) -> None:
        text = self._entry.get_text().strip()
        image = self._image_path
        if not text and not image:
            return
        self._entry.set_text("")
        self._set_image(None)
        replying = self._reply_message
        self.reply_to(None)
        local_id = f"local-{GLib.get_monotonic_time()}"
        now = GLib.DateTime.new_now_utc().format_iso8601()
        reply_info = {"id": replying["id"], "text": replying.get("text") or ""} if replying else None
        bubble = self._append({"id": local_id, "senderId": self._controller.me_id, "text": text, "createdAt": now, "localImage": image, "replyTo": reply_info})
        bubble.set_status("Sending…")
        motion.play(bubble, "bubble-in", 320)
        self._stack.set_visible_child_name("messages")
        self._scroll_to_bottom()

        def done(result, error):
            if error:
                bubble.set_status(f"Not sent — {self._controller.describe_error(error, 'error')}", error=True)
                return
            self._message_ids.add(result.get("id") or local_id)
            if result.get("id"):
                bubble.message["id"] = result["id"]  # X's id: the message can now be reacted / replied to
                bubble.enable_menu()
            bubble.set_status(fmt.relative_time(result.get("createdAt")) or "now")
            sent = {"id": result.get("id"), "senderId": self._controller.me_id, "text": text or "📷 Photo", "createdAt": result.get("createdAt") or now}
            self._controller.on_dm_sent(self.conversation_id, sent)

        params = {"conversationId": self.conversation_id, "text": text, "mediaPath": image, "replyTo": replying["id"] if replying else None}
        self._controller.sidecar.call("dm.send", params, done)
