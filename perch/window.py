"""Main window: start-up, sign-in, the tabs (timelines, notifications, messages) and navigation."""

import os

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from perch import APP_ID, APP_NAME, keyring, settings
from perch.compose import ComposeDialog
from perch.edit_history import EditHistoryPage
from perch.people import PeoplePage
from perch.media_viewer import MediaViewer
from perch.messages import ConversationPage, InboxView
from perch.notifications import NotificationsView
from perch.paged_list import PagedListView
from perch.poller import Poller
from perch.sidecar import RpcError, Sidecar
from perch.spaces import PlayerBar, SpaceDialog, SpacePlayer, ask_space_url
from perch import format as fmt
from perch.profile import ProfilePage
from perch.search import SearchPage
from perch.lists import AddToListDialog, BookmarksPage, ListPage, ListsPage
from perch.timeline import TimelineView
from perch.tweet_detail import TweetDetailPage

_KEY_HELP = (
    "Perch reads X using your browser session. Paste a rettiwt <b>API key</b> — "
    "a base64 bundle of your X cookies — generated with the "
    '<a href="https://github.com/Rishikant181/Rettiwt-API#authentication">X Auth Helper</a> '
    "extension. It is stored in your system keyring."
)

TABS = (
    ("following", "Following", "system-users-symbolic"),
    ("foryou", "For You", "starred-symbolic"),
    ("notifications", "Notifications", "preferences-system-notifications-symbolic"),
    ("messages", "Messages", "mail-unread-symbolic"),
)


class PerchWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, sidecar: Sidecar):
        super().__init__(application=app, title=APP_NAME, default_width=600, default_height=880)
        self.set_size_request(360, 400)
        self._sidecar = sidecar
        self._me: dict | None = None
        self._demo = False
        self._opening_post = False  # a post is being fetched to open its page
        self.player = SpacePlayer()
        self.player.connect("failed", lambda _p, message: self.toast(f"Playback stopped: {message}"))
        self._poller = Poller(self)

        self._toasts = Adw.ToastOverlay()
        self._pages = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._toasts.set_child(self._pages)
        self.set_content(self._toasts)

        self._pages.add_named(self._build_starting(), "starting")
        self._pages.add_named(self._build_signin(), "signin")
        self._pages.add_named(self._build_main(), "main")
        self._install_actions()
        self.connect("close-request", self._on_close_request)
        self.connect("notify::visible", lambda *_: self._maybe_mark_inbox_seen())
        self._start()

    # -- pages --------------------------------------------------------------

    def _toolbar(self, content: Gtk.Widget, header: Adw.HeaderBar | None = None) -> Adw.ToolbarView:
        view = Adw.ToolbarView(content=content)
        view.add_top_bar(header or Adw.HeaderBar())
        return view

    def _build_starting(self) -> Gtk.Widget:
        self._start_status = Adw.StatusPage(title="Connecting…")
        self._start_status.set_child(Adw.Spinner(width_request=32, height_request=32))
        return self._toolbar(self._start_status)

    def _build_signin(self) -> Gtk.Widget:
        page = Adw.StatusPage(icon_name=APP_ID, title="Sign in to X")
        page.set_description(_KEY_HELP)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18)
        group = Adw.PreferencesGroup()
        self._key_row = Adw.PasswordEntryRow(title="API key")
        self._key_row.connect("entry-activated", lambda *_: self._submit_key())
        group.add(self._key_row)
        box.append(group)

        self._signin_error = Gtk.Label(wrap=True, visible=False, justify=Gtk.Justification.CENTER)
        self._signin_error.add_css_class("error")
        box.append(self._signin_error)

        self._signin_button = Gtk.Button(label="Sign In", halign=Gtk.Align.CENTER)
        self._signin_button.add_css_class("pill")
        self._signin_button.add_css_class("suggested-action")
        self._signin_button.connect("clicked", lambda *_: self._submit_key())
        box.append(self._signin_button)

        page.set_child(Adw.Clamp(maximum_size=420, child=box))
        return self._toolbar(page)

    def _build_main(self) -> Gtk.Widget:
        self._stack = Adw.ViewStack()
        self._views: dict[str, PagedListView] = {
            "following": TimelineView(self, "following"),
            "foryou": TimelineView(self, "foryou"),
            "notifications": NotificationsView(self),
            "messages": InboxView(self),
        }
        for name, title, icon in TABS:
            view = self._views[name]
            view.connect("load-failed", self._on_load_failed)
            self._stack.add_titled_with_icon(view, name, title, icon)
        self._views["notifications"].connect("notify::visible-child-name", lambda *_: self._maybe_mark_seen())
        self._stack.connect("notify::visible-child-name", lambda *_: self._on_tab_changed())

        header = Adw.HeaderBar()
        # Wide: tabs in the header. Narrow: app title in the header, tabs in the bottom bar.
        self._title_stack = Gtk.Stack()
        self._title_stack.add_named(Adw.ViewSwitcher(stack=self._stack, policy=Adw.ViewSwitcherPolicy.WIDE), "switcher")
        self._title_stack.add_named(Adw.WindowTitle(title=APP_NAME), "title")
        header.set_title_widget(self._title_stack)

        refresh = Gtk.Button(icon_name="view-refresh-symbolic", tooltip_text="Refresh (Ctrl+R)", action_name="win.refresh")
        header.pack_start(refresh)
        new_post = Gtk.Button(icon_name="document-edit-symbolic", tooltip_text="New Post (Ctrl+N)", action_name="win.compose")
        header.pack_start(new_post)
        search = Gtk.Button(icon_name="system-search-symbolic", tooltip_text="Search (Ctrl+F)", action_name="win.search")
        header.pack_start(search)

        self._menu = Gio.Menu()
        self._menu_button = Gtk.MenuButton(icon_name="open-menu-symbolic", tooltip_text="Main Menu", primary=True)
        self._menu_button.set_menu_model(self._menu)
        header.pack_end(self._menu_button)

        bottom = Adw.ViewSwitcherBar(stack=self._stack)
        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(header)
        self._update_banner = Adw.Banner(
            title="A new version of Perch is installed", button_label="Restart", action_name="app.restart-update", revealed=False
        )
        view.add_top_bar(self._update_banner)
        view.add_bottom_bar(PlayerBar(self.player))
        view.add_bottom_bar(bottom)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 680sp"))
        breakpoint.add_setter(bottom, "reveal", True)
        breakpoint.add_setter(self._title_stack, "visible-child-name", "title")
        self.add_breakpoint(breakpoint)

        # Conversations (and anything else that needs a back button) are pushed on top of "home".
        self._nav = Adw.NavigationView()
        self._nav.add(Adw.NavigationPage(child=view, title=APP_NAME, tag="home"))
        return self._nav

    def _rebuild_menu(self) -> None:
        self._menu.remove_all()
        if self._me:
            account = Gio.Menu()
            account.append(f"Signed in as @{self._me.get('userName', '?')}", "win.noop")
            account.append("My Profile", "win.my-profile")
            account.append("Bookmarks", "win.bookmarks")
            account.append("Lists", "win.lists")
            self._menu.append_section(None, account)
        actions = Gio.Menu()
        actions.append("Open Space…", "win.open-space")
        actions.append("Desktop Notifications", "win.desktop-notifications")
        if not self._demo:
            actions.append("Sign Out", "win.sign-out")
        actions.append(f"About {APP_NAME}", "app.about")
        self._menu.append_section(None, actions)
        # Closing the window only hides Perch; this (like the dock's Quit and Ctrl+Q) really exits.
        quit_section = Gio.Menu()
        quit_section.append(f"Quit {APP_NAME}", "app.quit")
        self._menu.append_section(None, quit_section)

    # -- close to dock -------------------------------------------------------

    def _on_close_request(self, _window) -> bool:
        if getattr(self.get_application(), "quitting", True):
            return False  # a real quit: let the window close
        # Hide instead of closing. Videos in the viewer stop; Space audio keeps playing on purpose.
        page = self._nav.get_visible_page()
        if isinstance(page, MediaViewer):
            self._nav.pop()
        self.set_visible(False)
        return True

    def show_update_banner(self) -> None:
        """A newer Perch is installed: a banner on the main view, plus a toast that shows on
        whatever page is open right now."""
        self._update_banner.set_revealed(True)
        toast = Adw.Toast(title="A new version of Perch is installed", button_label="Restart", action_name="app.restart-update", timeout=0)
        self._toasts.add_toast(toast)

    def prepare_quit(self) -> None:
        self._poller.stop()
        self.player.stop()

    # -- actions ------------------------------------------------------------

    def _install_actions(self) -> None:
        for name, handler in (
            ("refresh", self._refresh),
            ("sign-out", self._sign_out),
            ("compose", lambda: self.compose()),
            ("open-space", lambda: self._signed_in() and ask_space_url(self, self)),
            ("my-profile", lambda: self._me and self.open_profile(self._me)),
            ("search", lambda: self.open_search()),
            ("bookmarks", lambda: self.open_bookmarks()),
            ("lists", lambda: self.open_lists()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_, h=handler: h())
            self.add_action(action)
        noop = Gio.SimpleAction.new("noop", None)
        noop.set_enabled(False)
        self.add_action(noop)

        desktop = Gio.SimpleAction.new_stateful(
            "desktop-notifications", None, GLib.Variant.new_boolean(bool(settings.get("desktop_notifications")))
        )
        desktop.connect("change-state", self._on_desktop_notifications_toggled)
        self.add_action(desktop)

        app = self.get_application()
        app.set_accels_for_action("win.refresh", ["<Control>r", "F5"])
        app.set_accels_for_action("win.compose", ["<Control>n"])
        app.set_accels_for_action("win.search", ["<Control>f"])

    def _on_desktop_notifications_toggled(self, action: Gio.SimpleAction, value: GLib.Variant) -> None:
        action.set_state(value)
        settings.set("desktop_notifications", value.get_boolean())

    # -- controller API (used by rows, dialogs, views and the poller) ---------

    @property
    def sidecar(self) -> Sidecar:
        return self._sidecar

    @property
    def me_id(self) -> str | None:
        return (self._me or {}).get("id")

    def _signed_in(self) -> bool:
        return self._pages.get_visible_child_name() == "main"

    def compose(self, reply_to: dict | None = None, quote: dict | None = None) -> None:
        if self._signed_in():
            ComposeDialog(self, reply_to=reply_to, quote=quote).present(self)

    def open_space(self, space_id: str) -> None:
        if self._signed_in():
            SpaceDialog(self, space_id).present(self)

    def open_conversation(self, conversation: dict, users: dict) -> None:
        tag = f"dm-{conversation['id']}"
        if self._nav.find_page(tag):
            self._nav.pop_to_tag(tag)
            return
        self._nav.pop_to_tag("home")
        self._nav.push(ConversationPage(self, conversation, users))

    def open_tweet(self, tweet: dict) -> None:
        """Open a post on its own page (reply chain, the post, its replies)."""
        # A repost has no replies of its own: open the original (e.g. "X liked your repost").
        tweet = tweet.get("retweetedTweet") or tweet
        if not self._signed_in() or not tweet.get("id"):
            return
        tag = f"post-{tweet['id']}"
        if self._nav.get_visible_page().get_tag() == tag:
            return
        self._nav.push(TweetDetailPage(self, tweet))

    def open_profile(self, user_or_handle) -> None:
        """Open a profile page by handle, or from a user dict (shown as a preview while it loads)."""
        user = user_or_handle if isinstance(user_or_handle, dict) else None
        handle = (user.get("userName") if user else str(user_or_handle or "")).lstrip("@")
        if not self._signed_in() or not handle:
            return
        if self._nav.get_visible_page().get_tag() == f"profile-{handle.lower()}":
            return
        self._nav.push(ProfilePage(self, handle, preview=user))

    def open_link(self, uri: str) -> bool:
        """Route X links inside Perch (profiles, posts, hashtags / searches); return False to let
        GTK open the browser."""
        target = fmt.x_link_target(uri)
        if target is None:
            return False
        kind, value = target
        if kind == "profile":
            self.open_profile(value)
        elif kind == "search":
            self.open_search(value, mode="latest" if value.startswith("#") else None)
        else:
            self.open_tweet_by_id(value, fallback_url=uri)
        return True

    def _push_unique(self, tag: str, factory) -> None:
        """Show the page with ``tag`` if it's in the stack, else push a new one."""
        if not self._signed_in():
            return
        if self._nav.find_page(tag) is not None:
            self._nav.pop_to_tag(tag)
        else:
            self._nav.push(factory())

    def open_bookmarks(self) -> None:
        self._push_unique("bookmarks", lambda: BookmarksPage(self))

    def open_lists(self) -> None:
        self._push_unique("lists", lambda: ListsPage(self))

    def open_list(self, lst: dict) -> None:
        self._push_unique(f"list-{lst['id']}", lambda: ListPage(self, lst))

    def open_connections(self, user: dict, kind: str) -> None:
        """A profile's followers (``kind='followers'``) or the accounts it follows (``'following'``)."""
        if not user.get("id"):
            return
        title = "Followers" if kind == "followers" else "Following"
        handle = user.get("userName", "")
        empty = "No followers yet" if kind == "followers" else "Not following anyone yet"
        self._push_unique(
            f"{kind}-{user['id']}",
            lambda: PeoplePage(self, f"{kind}-{user['id']}", title, f"@{handle}", "user.connections", {"id": user["id"], "kind": kind}, empty),
        )

    def open_engagers(self, tweet: dict, kind: str) -> None:
        """Who liked (``kind='likers'``) or reposted (``'retweeters'``) a post."""
        if not tweet.get("id"):
            return
        title = "Liked by" if kind == "likers" else "Reposted by"
        handle = (tweet.get("tweetBy") or {}).get("userName", "")
        empty = "No likes yet" if kind == "likers" else "No reposts yet"
        self._push_unique(
            f"{kind}-{tweet['id']}",
            lambda: PeoplePage(self, f"{kind}-{tweet['id']}", title, f"Post by @{handle}", "tweet.engagers", {"id": tweet["id"], "kind": kind}, empty),
        )

    def open_edit_history(self, tweet: dict) -> None:
        if tweet.get("id"):
            self._push_unique(f"history-{tweet['id']}", lambda: EditHistoryPage(self, tweet))

    def delete_post(self, tweet: dict, on_deleted=None) -> None:
        """Confirm, then delete one of your own posts on X."""
        if not self._signed_in() or not tweet.get("id"):
            return
        dialog = Adw.AlertDialog(heading="Delete post?", body="It will be removed from your profile, the timelines of anyone following you, and search. This can't be undone.")
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def done(_result, error):
            if error:
                self.report_error(error, "Couldn't delete the post")
                return
            if on_deleted:
                on_deleted()
            # Leave the deleted post's own page (and anything opened from it).
            tag = f"post-{tweet['id']}"
            if self._nav.find_page(tag) is not None:
                self._nav.pop_to_tag(tag)
                self._nav.pop()
            self.toast("Post deleted")

        def on_response(_dialog, response):
            if response == "delete":
                self._sidecar.call("tweet.delete", {"id": tweet["id"]}, done)

        dialog.connect("response", on_response)
        dialog.present(self)

    def add_to_list(self, user: dict) -> None:
        if self._signed_in() and user.get("id"):
            AddToListDialog(self, user).present(self)

    def on_lists_changed(self) -> None:
        """A list was created, edited, deleted or had members changed: refresh the Lists page."""
        page = self._nav.find_page("lists")
        if page is not None:
            page.reload()

    def open_search(self, query: str = "", mode: str | None = None) -> None:
        """Open the search page (reusing it if it's already in the navigation stack)."""
        if not self._signed_in():
            return
        page = self._nav.find_page("search")
        if page is not None:
            self._nav.pop_to_tag("search")
        else:
            page = SearchPage(self)
            self._nav.push(page)
        if query:
            page.search(query, mode)

    def open_tweet_by_id(self, tweet_id: str, fallback_url: str | None = None) -> None:
        """Fetch a post by id, then open its page (used where only an id is known, e.g. notifications).
        If X won't return it (deleted, protected), say so and offer the browser instead."""
        if not self._signed_in() or self._opening_post:
            return
        self._opening_post = True
        self.set_cursor(Gdk.Cursor.new_from_name("progress", None))

        def done(tweet, error):
            self._opening_post = False
            self.set_cursor(None)
            if not error:
                self.open_tweet(tweet)
                return
            toast = Adw.Toast(title=GLib.markup_escape_text(self.describe_error(error, "Couldn't open the post")), timeout=6)
            if fallback_url:
                toast.set_button_label("Open in Browser")
                toast.connect("button-clicked", lambda *_: Gtk.UriLauncher(uri=fallback_url).launch(self, None, None, None))
            self._toasts.add_toast(toast)

        self._sidecar.call("tweet.details", {"id": tweet_id}, done)

    def open_media(self, items: list[dict], index: int, post_url: str | None) -> None:
        if not items:
            return
        if self._nav.find_page("media"):
            self._nav.pop_to_tag("media")
            self._nav.pop()
        self._nav.push(MediaViewer(items, index, post_url))

    def show_tab(self, name: str) -> None:
        if not self._signed_in() or name not in self._views:
            return
        self._nav.pop_to_tag("home")
        self._stack.set_visible_child_name(name)
        self.present()

    def update_badges(self) -> None:
        unread = self._views["messages"].unread_count
        page = self._stack.get_page(self._views["messages"])
        page.set_badge_number(unread)
        page.set_needs_attention(unread > 0)

    def on_notifications_polled(self, unread: int, has_new: bool) -> None:
        view = self._views["notifications"]
        visible = self._current_tab() == "notifications" and self._nav.get_visible_page().get_tag() == "home"
        if has_new:
            if visible:
                view.reload()
            else:
                view.loaded_once = False
        if not visible:
            page = self._stack.get_page(view)
            page.set_badge_number(unread)
            page.set_needs_attention(unread > 0)

    def on_inbox_polled(self, result: dict, incoming_ids: list[str]) -> None:
        inbox = self._views["messages"]
        open_page = self._nav.get_visible_page()
        open_id = getattr(open_page, "conversation_id", None)
        if open_id and open_id in incoming_ids:
            open_page.reload()
        if incoming_ids:
            if self._current_tab() == "messages" and open_id is None:
                inbox.reload()
            else:
                inbox.loaded_once = False
        if not inbox.loaded_once:
            inbox.unread_count = sum(bool(c.get("unread")) for c in result.get("conversations") or [])
        if result.get("newestEventId"):
            inbox.newest_event_id = result["newestEventId"]
            inbox.last_seen_event_id = result.get("lastSeenEventId")
        self.update_badges()
        self._maybe_mark_inbox_seen()

    def mark_conversation_read(self, conversation_id: str, event_id: str) -> None:
        """Persist "read" on X (the conversation then shows as read on the web and phone too)."""

        def done(_result, error):
            if error:
                # Local state already shows it read; it will come back unread on the next refresh.
                print(f"[perch] marking {conversation_id} read on X failed: {error}")

        self._sidecar.call("dm.markRead", {"conversationId": conversation_id, "eventId": event_id}, done)

    def on_inbox_loaded(self) -> None:
        self._maybe_mark_inbox_seen()

    def _maybe_mark_inbox_seen(self) -> None:
        """While the Messages tab is actually on screen, clear X's DM badge up to the newest event."""
        inbox = self._views["messages"]
        newest, seen = inbox.newest_event_id, inbox.last_seen_event_id
        if not (self.get_visible() and self._signed_in() and self._current_tab() == "messages" and newest):
            return
        if seen and str(seen).isdigit() and str(newest).isdigit() and int(seen) >= int(newest):
            return

        def done(_result, error):
            if error:
                print(f"[perch] clearing the DM badge on X failed: {error}")
            else:
                inbox.last_seen_event_id = newest

        self._sidecar.call("dm.markSeen", {"eventId": newest}, done)

    def on_dm_sent(self, conversation_id: str, message: dict) -> None:
        self._views["messages"].on_message_sent(conversation_id, message)

    def on_posted(self, tweet_id: str | None, reply_to_id: str | None = None) -> None:
        # A reply posted from a post page shows up in that page's replies.
        page = self._nav.get_visible_page()
        if reply_to_id and isinstance(page, TweetDetailPage) and page.tweet_id == reply_to_id:
            page.on_reply_posted()
        toast = Adw.Toast(title="Posted", timeout=5)
        if tweet_id and self._me:
            url = f"https://x.com/{self._me.get('userName', 'i')}/status/{tweet_id}"
            toast.set_button_label("View")
            toast.connect("button-clicked", lambda *_: Gtk.UriLauncher(uri=url).launch(self, None, None, None))
        self._toasts.add_toast(toast)

    def describe_error(self, error: RpcError, prefix: str) -> str:
        if error.kind == "rate_limit":
            return "X is rate limiting this account — try again in a few minutes"
        if error.kind == "auth":
            return "Your X session is no longer valid"
        return f"{prefix}: {error}" if prefix else str(error)

    def report_error(self, error: RpcError, prefix: str) -> None:
        self.toast(self.describe_error(error, prefix))

    def toast(self, text: str) -> None:
        self._toasts.add_toast(Adw.Toast(title=GLib.markup_escape_text(text), timeout=4))

    # -- tabs ---------------------------------------------------------------

    def _current_tab(self) -> str:
        return self._stack.get_visible_child_name()

    def _on_tab_changed(self) -> None:
        self._ensure_loaded()
        self._maybe_mark_seen()
        self._maybe_mark_inbox_seen()

    def _maybe_mark_seen(self) -> None:
        view = self._views["notifications"]
        if self._current_tab() == "notifications" and view.get_visible_child_name() == "list":
            view.mark_seen()
            page = self._stack.get_page(view)
            page.set_badge_number(0)
            page.set_needs_attention(False)

    def _refresh(self) -> None:
        if not self._signed_in():
            return
        page = self._nav.get_visible_page()
        if isinstance(page, (ConversationPage, TweetDetailPage, ProfilePage, SearchPage, BookmarksPage, ListsPage, ListPage, PeoplePage, EditHistoryPage)):
            page.reload()
            return
        view = self._views[self._current_tab()]
        view.scroll_to_top()
        view.reload()

    def _ensure_loaded(self) -> None:
        if not self._signed_in():
            return
        view = self._views.get(self._current_tab())
        if view and not view.loaded_once:
            view.reload()

    # -- sign in / out ------------------------------------------------------

    def _sign_out(self) -> None:
        self._poller.stop()
        self.player.stop()
        keyring.clear()
        self._me = None
        self._sidecar.sign_out(lambda *_: None)
        self._nav.pop_to_tag("home")
        for view in self._views.values():
            view.loaded_once = False
        self._key_row.set_text("")
        self._show_signin()

    def _start(self) -> None:
        self._pages.set_visible_child_name("starting")
        self._sidecar.call("ping", None, self._on_ping)

    def _on_ping(self, result, error: RpcError | None) -> None:
        if error:
            self._start_status.set_icon_name("dialog-error-symbolic")
            self._start_status.set_title("Couldn't start the X bridge")
            self._start_status.set_description(GLib.markup_escape_text(str(error)))
            self._start_status.set_child(None)
            return
        self._demo = bool(result.get("demo"))
        if self._demo:
            self._sign_in("demo", persist=False)
        elif env_key := os.environ.get("PERCH_API_KEY"):
            self._sign_in(env_key, persist=False)
        else:
            keyring.lookup(lambda key: self._sign_in(key, persist=False) if key else self._show_signin())

    def _show_signin(self, message: str | None = None) -> None:
        self._signin_error.set_label(message or "")
        self._signin_error.set_visible(bool(message))
        self._signin_button.set_sensitive(True)
        self._pages.set_visible_child_name("signin")
        self._key_row.grab_focus()

    def _submit_key(self) -> None:
        key = self._key_row.get_text().strip()
        if not key:
            self._show_signin("Paste your API key first.")
            return
        self._signin_button.set_sensitive(False)
        self._sign_in(key, persist=True)

    def _sign_in(self, key: str, persist: bool) -> None:
        def on_me(user, error: RpcError | None):
            if error:
                self._on_signin_failed(error)
                return
            self._me = user
            if persist:
                keyring.store(key, lambda ok: ok or self.toast("Couldn't save the key to the keyring"))
            self._rebuild_menu()
            self._pages.set_visible_child_name("main")
            self._ensure_loaded()
            self._poller.start()

        def on_init(_result, error: RpcError | None):
            if error:
                self._on_signin_failed(error)
            else:
                self._sidecar.call("me", None, on_me)

        self._sidecar.sign_in(key, on_init)

    def _on_signin_failed(self, error: RpcError) -> None:
        if error.kind == "auth":
            self._show_signin(f"Sign-in failed: {error}")
        elif error.kind == "rate_limit":
            self._show_signin("X is rate limiting this account. Wait a few minutes and try again.")
        else:
            self._show_signin(f"Couldn't reach X: {error}")

    def _on_load_failed(self, _view, error: RpcError) -> None:
        if error.kind == "auth":
            self._sign_out()
            self._show_signin("Your X session expired. Paste a fresh API key.")
        elif error.kind == "rate_limit":
            self.toast("Rate limited by X — try again in a few minutes")
        else:
            self.toast(f"Couldn't load more: {error}")
