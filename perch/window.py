"""Main window: start-up, sign-in, the tabs (timelines, notifications, messages) and navigation."""

import os

from gi.repository import Adw, Gdk, Gio, GLib, Gtk

from perch import APP_ID, APP_NAME, crash, keyring, settings, snapshot
from perch.compose import ComposeDialog
from perch.edit_history import EditHistoryPage
from perch.people import PeoplePage
from perch.media_viewer import MediaViewer
from perch.messages import ConversationPage, InboxView
from perch.muted_words import MutedWordsPage, compile_words, matches
from perch.notifications import NotificationsView
from perch.paged_list import PagedListView
from perch.poller import Poller
from perch.sidecar import RpcError, Sidecar
from perch.spaces import PlayerBar, SpaceDialog, SpacePlayer, ask_space_url
from perch import format as fmt
from perch.profile import ProfilePage
from perch.profile_edit import ProfileEditDialog
from perch.scheduled import ScheduledPage
from perch.search import SearchPage
from perch.launcher import LauncherBadge
from perch.lifecycle import release, release_dialog
from perch.list_tab import ListTabView
from perch.preferences import PreferencesDialog
from perch.quotes import QuotesPage
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


def _page_kind(page) -> str:
    """A page's kind for crash reports (never its title: that can be a name)."""
    return "home" if page is not None and page.get_tag() == "home" else type(page).__name__


def _tab_kind(name: str | None) -> str:
    return "list" if name and name.startswith("list-") else str(name)


def _when(timestamp: float) -> str:
    """'at 14:05', 'yesterday at 14:05' or 'on 12 Nov' for the offline banner."""
    then = GLib.DateTime.new_from_unix_local(int(timestamp))
    now = GLib.DateTime.new_now_local()
    days = now.get_day_of_year() - then.get_day_of_year() if now.get_year() == then.get_year() else 99
    if days == 0:
        return then.format("at %H:%M")
    if days == 1:
        return then.format("yesterday at %H:%M")
    return then.format("on %-d %b")


class PerchWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application, sidecar: Sidecar):
        # The size (and maximized state) it had last time.
        width = max(360, int(settings.get("window_width") or 600))
        height = max(400, int(settings.get("window_height") or 880))
        super().__init__(application=app, title=APP_NAME, default_width=width, default_height=height)
        self.set_size_request(360, 400)
        if settings.get("window_maximized"):
            self.maximize()
        self._size_save = 0
        for prop in ("default-width", "default-height", "maximized"):
            self.connect(f"notify::{prop}", lambda *_: self._queue_size_save())
        self._sidecar = sidecar
        self._stale_toast_at = -60_000_000
        self._muted_patterns: list = []  # compiled muted words (see on_muted_words)
        self._restoring_tabs = False  # rebuilding tabs for an account (see on_account_ready)
        self._pending_link: str | None = None  # a link handed to Perch before it was signed in
        self._adding_from: str | None = None  # account id while "Add Account…" is showing the sign-in page
        self._signin_attempted = False  # a key was tried while adding (the sidecar must be re-signed)
        sidecar.on_stale = self._on_stale
        self._me: dict | None = None
        # Showing the saved copy of an account (see snapshot.py) until X answers, or while it can't.
        self._from_saved = False
        self._live = False  # X answered for the signed-in account (it's not only the saved copy)
        self._retry_me = None  # signs in again once X can be reached (see _on_offline)
        self._retry_source = 0
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
        # Minimized to the dock and back: the window stays "visible"; X11 reports it in the surface
        # state, GNOME on Wayland as "suspended".
        self.connect("realize", lambda *_: self.get_surface().connect("notify::state", self._on_surface_state))
        self.connect("notify::suspended", self._on_surface_state)
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
        # While adding another account: back to the one that's signed in.
        self._signin_cancel = Gtk.Button(label="Cancel", halign=Gtk.Align.CENTER, visible=False)
        self._signin_cancel.add_css_class("flat")
        self._signin_cancel.connect("clicked", lambda *_: self._cancel_add_account())
        box.append(self._signin_cancel)

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
        # The dock icon's count follows the Notifications and Messages tab badges.
        self._badge = LauncherBadge(self.get_application())
        for name in ("notifications", "messages"):
            self._stack.get_page(self._views[name]).connect("notify::badge-number", lambda *_: self._sync_dock_badge())
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
        # X can't be reached: what's shown is Perch's saved copy (see _on_offline).
        self._offline_banner = Adw.Banner(button_label="Try Again", revealed=False)
        self._offline_banner.connect("button-clicked", lambda *_: self._try_again())
        view.add_top_bar(self._offline_banner)
        view.add_bottom_bar(PlayerBar(self.player))
        view.add_bottom_bar(bottom)

        breakpoint = Adw.Breakpoint.new(Adw.BreakpointCondition.parse("max-width: 680sp"))
        breakpoint.add_setter(bottom, "reveal", True)
        breakpoint.add_setter(self._title_stack, "visible-child-name", "title")
        self.add_breakpoint(breakpoint)

        # Conversations (and anything else that needs a back button) are pushed on top of "home".
        self._nav = Adw.NavigationView()
        # A page that's been gone back from is never shown again (pages are made fresh): free it.
        self._nav.connect("popped", lambda _nav, page: release(page))
        self._nav.connect("notify::visible-page", lambda nav, _p: crash.note(f"page {_page_kind(nav.get_visible_page())}"))
        self._nav.add(Adw.NavigationPage(child=view, title=APP_NAME, tag="home"))
        return self._nav

    def _rebuild_menu(self) -> None:
        self._menu.remove_all()
        if self._me:
            # Accounts: who you are, the others to switch to, add one, sign out of this one.
            accounts = Gio.Menu()
            accounts.append(f"Signed in as @{self._me.get('userName', '?')}", "win.noop")
            if self._accounts_enabled():
                for other in self._accounts():
                    if other.get("id") != self.me_id:
                        item = Gio.MenuItem.new(f"Switch to @{other.get('handle') or other.get('id')}", None)
                        item.set_action_and_target_value("win.switch-account", GLib.Variant.new_string(other["id"]))
                        accounts.append_item(item)
                accounts.append("Add Account…", "win.add-account")
                accounts.append("Sign Out", "win.sign-out")
            self._menu.append_section(None, accounts)
            account = Gio.Menu()
            account.append("My Profile", "win.my-profile")
            account.append("Bookmarks", "win.bookmarks")
            account.append("Lists", "win.lists")
            account.append("Scheduled Posts", "win.scheduled")
            self._menu.append_section(None, account)
            people = Gio.Menu()
            people.append("Who to Follow", "win.suggestions")
            people.append("Muted Accounts", "win.muted")
            people.append("Blocked Accounts", "win.blocked")
            people.append("Muted Words", "win.muted-words")
            self._menu.append_section(None, people)
        actions = Gio.Menu()
        actions.append("Open X Link…", "win.open-link")
        actions.append("Open Space…", "win.open-space")
        actions.append("Preferences", "win.preferences")
        actions.append("Keyboard Shortcuts", "win.shortcuts")
        actions.append(f"About {APP_NAME}", "app.about")
        self._menu.append_section(None, actions)
        # Closing the window only minimizes Perch; this (like the dock's Quit and Ctrl+Q) really exits.
        quit_section = Gio.Menu()
        quit_section.append(f"Quit {APP_NAME}", "app.quit")
        self._menu.append_section(None, quit_section)

    # -- close to dock -------------------------------------------------------

    def _on_close_request(self, _window) -> bool:
        if getattr(self.get_application(), "quitting", True):
            return False  # a real quit: let the window close
        # Minimize instead of closing, so Perch stays in the dock (a hidden window drops out of it)
        # and a click on its icon brings it back. Videos in the viewer stop; Space audio keeps
        # playing on purpose.
        page = self._nav.get_visible_page()
        if isinstance(page, MediaViewer):
            self._nav.pop()
        crash.note("to the dock")
        self._save_timelines_now()  # Perch may not come back from the dock in one piece
        self.minimize()
        return True

    def _queue_size_save(self) -> None:
        """Remember the window size a moment after resizing stops (not on every step of a drag)."""
        if self._size_save:
            GLib.source_remove(self._size_save)
        self._size_save = GLib.timeout_add(500, self._save_size)

    def _save_size(self) -> bool:
        self._size_save = 0
        maximized = self.is_maximized()
        settings.set("window_maximized", maximized)
        if not maximized:  # keep the un-maximized size to come back to
            width, height = self.get_default_size()
            if width > 0 and height > 0:
                settings.set("window_width", width)
                settings.set("window_height", height)
        return GLib.SOURCE_REMOVE

    def is_on_screen(self) -> bool:
        """Shown and not minimized: only then has the user seen what's in the window. On Wayland GTK
        isn't told about minimizing; GNOME marks a minimized window *suspended* instead."""
        if not self.get_visible() or self.is_suspended():
            return False
        surface = self.get_surface()
        return not (surface is not None and surface.get_state() & Gdk.ToplevelState.MINIMIZED)

    def _on_surface_state(self, *_args) -> None:
        # Back from the dock: what's on screen now counts as seen.
        self._maybe_mark_inbox_seen()
        self._maybe_mark_seen()

    def show_update_banner(self) -> None:
        """A newer Perch is installed: a banner on the main view, plus a toast that shows on
        whatever page is open right now."""
        self._update_banner.set_revealed(True)
        toast = Adw.Toast(title="A new version of Perch is installed", button_label="Restart", action_name="app.restart-update", timeout=0)
        self._toasts.add_toast(toast)

    def prepare_quit(self) -> None:
        self._save_timelines_now()
        self._poller.stop()
        self.player.stop()
        self._badge.set_count(0)  # don't leave a count on the dock icon after quitting

    def _sync_dock_badge(self) -> None:
        total = sum(self._stack.get_page(self._views[name]).get_badge_number() for name in ("notifications", "messages"))
        self._badge.set_count(total if settings.get("dock_badge") else 0)

    # -- preferences ------------------------------------------------------------

    def on_poll_interval_changed(self) -> None:
        self._poller.set_interval(int(settings.get("poll_minutes")) * 60)

    def on_saved_data_cleared(self) -> None:
        """The sidecar forgot its saved copies: reload what's on screen straight from X."""
        snapshot.forget_all()
        self._refresh()
        self.toast("Cleared — loading fresh from X")

    def on_dock_badge_changed(self) -> None:
        self._sync_dock_badge()

    def on_desktop_notifications_changed(self, on: bool) -> None:
        action = self.lookup_action("desktop-notifications")
        if action is not None:
            action.set_state(GLib.Variant.new_boolean(on))

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
            ("scheduled", lambda: self.open_scheduled()),
            ("suggestions", lambda: self.open_suggestions()),
            ("muted", lambda: self.open_muted()),
            ("blocked", lambda: self.open_blocked()),
            ("muted-words", lambda: self.open_muted_words()),
            ("add-account", lambda: self.add_account()),
            ("shortcuts", lambda: self._show_shortcuts()),
            ("preferences", lambda: self._present_releasing(PreferencesDialog(self))),
            ("open-link", lambda: self.open_x_link_dialog()),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda *_, h=handler, n=name: (crash.note(f"action {n}"), h()))
            self.add_action(action)
        switch = Gio.SimpleAction.new("switch-account", GLib.VariantType.new("s"))
        switch.connect("activate", lambda _a, target: self.switch_account(target.get_string()))
        self.add_action(switch)
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
        app.set_accels_for_action("win.shortcuts", ["<Control>question"])
        app.set_accels_for_action("win.preferences", ["<Control>comma"])
        app.set_accels_for_action("win.open-link", ["<Control>l"])
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)

    # -- keyboard navigation ------------------------------------------------

    def _on_key(self, _controller, keyval: int, _keycode: int, state: Gdk.ModifierType) -> bool:
        """j / k: next / previous item, l: like, r: reply. Enter (on the focused item) is the list's
        own activation and Esc (back) the navigation view's. Ignored while typing or with a dialog open."""
        if state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.ALT_MASK | Gdk.ModifierType.SUPER_MASK):
            return False
        if not self._signed_in() or self.get_visible_dialog() is not None:
            return False
        if isinstance(self.get_focus(), (Gtk.Editable, Gtk.TextView)):
            return False
        key = Gdk.keyval_name(keyval)
        if key not in ("j", "k", "l", "r"):
            return False
        view = self._current_list_view()
        if view is None:
            return False
        rows = view.visible_rows()
        if not rows:
            return False
        current = self._focused_row(view)
        if key in ("j", "k"):
            if current is None:
                target = view.first_row_on_screen() or rows[0]
            else:
                index = rows.index(current) + (1 if key == "j" else -1)
                if index >= len(rows):
                    view.load_more()
                    return True
                target = rows[max(0, index)]
            target.grab_focus()
            view.scroll_to_row(target)
            return True
        row = current or view.first_row_on_screen()
        actions = getattr(row, "actions", None)
        if actions is None:
            return False
        if key == "l":
            actions.toggle_like()
        else:
            self.compose(reply_to=row.tweet)
        return True

    def _current_list_view(self) -> PagedListView | None:
        page = self._nav.get_visible_page()
        if page.get_tag() == "home":
            return self._views.get(self._current_tab())
        stack = [page]
        while stack:
            widget = stack.pop(0)
            if isinstance(widget, PagedListView) and widget.get_mapped():
                return widget
            child = widget.get_first_child()
            while child is not None:
                stack.append(child)
                child = child.get_next_sibling()
        return None

    def _focused_row(self, view: PagedListView) -> Gtk.ListBoxRow | None:
        widget = self.get_focus()
        while widget is not None and not (isinstance(widget, Gtk.ListBoxRow) and widget.get_parent() is view.list_box):
            widget = widget.get_parent()
        return widget

    def _show_shortcuts(self) -> None:
        dialog = Adw.ShortcutsDialog()
        lists = Adw.ShortcutsSection(title="Posts and Lists")
        for title, accel in (
            ("Next item", "j"),
            ("Previous item", "k"),
            ("Open the selected item", "Return"),
            ("Like or unlike the selected post", "l"),
            ("Reply to the selected post", "r"),
            ("Go back", "Escape"),
        ):
            lists.add(Adw.ShortcutsItem(title=title, accelerator=accel))
        general = Adw.ShortcutsSection(title="General")
        for title, accel in (
            ("New post", "<Control>n"),
            ("Search", "<Control>f"),
            ("Open an X link", "<Control>l"),
            ("Preferences", "<Control>comma"),
            ("Refresh", "<Control>r"),
            ("Post (in the compose box)", "<Control>Return"),
            ("Keyboard shortcuts", "<Control>question"),
            ("Quit", "<Control>q"),
        ):
            general.add(Adw.ShortcutsItem(title=title, accelerator=accel))
        dialog.add(lists)
        dialog.add(general)
        dialog.present(self)

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
            self._present_releasing(ComposeDialog(self, reply_to=reply_to, quote=quote))

    def _present_releasing(self, dialog: Adw.Dialog) -> None:
        """Show a dialog and free it once closed (after its own 'closed' handlers have run)."""
        crash.note(f"dialog {type(dialog).__name__}")
        dialog.connect("closed", lambda d: crash.note(f"closed {type(d).__name__}"))
        dialog.connect("closed", lambda d: GLib.idle_add(lambda: release_dialog(d) or GLib.SOURCE_REMOVE))
        dialog.present(self)

    def open_space(self, space_id: str) -> None:
        if self._signed_in():
            self._present_releasing(SpaceDialog(self, space_id))

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
        elif kind == "space":
            self.open_space(value)
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

    def open_scheduled(self) -> None:
        self._push_unique("scheduled", lambda: ScheduledPage(self))

    def on_scheduled(self, at: int) -> None:
        """A post was scheduled from the compose dialog."""
        page = self._nav.find_page("scheduled")
        if page is not None:
            page.reload()
        toast = Adw.Toast(title=GLib.markup_escape_text(f"Scheduled to post {fmt.schedule_phrase(at)}"), timeout=5)
        toast.set_button_label("View")
        toast.connect("button-clicked", lambda *_: self.open_scheduled())
        self._toasts.add_toast(toast)

    def edit_profile(self, user: dict) -> None:
        if self._signed_in() and user.get("id") == self.me_id:
            self._present_releasing(ProfileEditDialog(self, user))

    def on_profile_updated(self, quiet: bool = False) -> None:
        """Your profile changed on X: refresh the signed-in account and any open copy of your profile."""

        def on_me(user, error):
            if not error and user:
                self._me = user
                self._rebuild_menu()

        self._sidecar.call("me", None, on_me)
        handle = (self._me or {}).get("userName", "")
        page = self._nav.find_page(f"profile-{handle.lower()}") if handle else None
        if page is not None:
            page.reload()
        if not quiet:
            self.toast("Profile updated")

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

    def open_suggestions(self) -> None:
        self._push_unique(
            "suggestions",
            lambda: PeoplePage(self, "suggestions", "Who to Follow", "Suggested by X", "user.suggestions", {}, "No suggestions right now"),
        )

    def open_muted(self) -> None:
        self._push_unique(
            "muted",
            lambda: PeoplePage(self, "muted", "Muted Accounts", "Their posts are hidden from your timelines", "user.muted", {}, "You haven’t muted anyone"),
        )

    def set_muted(self, user: dict, mute: bool, undoable: bool = True) -> None:
        """Mute or unmute someone on X. Muting hides their posts already loaded in the timelines;
        either way a toast offers to undo it."""
        if not self._signed_in() or not user.get("id"):
            return
        handle = user.get("userName", "")

        def done(_result, error):
            if error:
                self.report_error(error, f"Couldn't {'mute' if mute else 'unmute'} @{handle}")
                return
            user["muted"] = mute
            if mute:
                for name in ("following", "foryou"):
                    self._views[name].hide_rows(lambda row: user["id"] in getattr(row, "author_ids", ()))
            for tag in ("muted", f"profile-{handle.lower()}"):
                if (page := self._nav.find_page(tag)) is not None:
                    page.reload()
            toast = Adw.Toast(title=GLib.markup_escape_text(f"Muted @{handle}" if mute else f"Unmuted @{handle}"), timeout=5)
            if undoable:
                toast.set_button_label("Undo")
                toast.connect("button-clicked", lambda *_: self.set_muted(user, not mute, undoable=False))
            self._toasts.add_toast(toast)

        self._sidecar.call("user.mute", {"id": user["id"], "mute": mute}, done)

    # -- pinned lists (extra tabs) --------------------------------------------

    def _pinned_lists(self) -> list[dict]:
        return list(settings.account_get("pinned_lists") or [])

    def is_list_pinned(self, list_id: str) -> bool:
        return any(item.get("id") == list_id for item in self._pinned_lists())

    def set_list_pinned(self, lst: dict, pinned: bool) -> None:
        lists = [item for item in self._pinned_lists() if item.get("id") != lst["id"]]
        if pinned:
            lists.append({"id": lst["id"], "name": lst.get("name") or "List"})
        settings.account_set("pinned_lists", lists)
        self._rebuild_list_tabs()
        self.toast(f"Pinned “{lst.get('name') or 'List'}” as a tab" if pinned else f"Unpinned “{lst.get('name') or 'List'}”")

    def _rebuild_list_tabs(self) -> None:
        """One tab per pinned list of the signed-in account, after Following / For You / … ."""
        current = self._current_tab()
        for name in [n for n in self._views if n.startswith("list-")]:
            view = self._views.pop(name)
            self._stack.remove(view)
            release(view)
        for lst in self._pinned_lists():
            name = f"list-{lst['id']}"
            view = ListTabView(self, lst)
            view.connect("load-failed", self._on_load_failed)
            self._views[name] = view
            self._stack.add_titled_with_icon(view, name, lst.get("name") or "List", "view-list-bullet-symbolic")
        if current in self._views:
            self._stack.set_visible_child_name(current)

    def on_account_ready(self) -> None:
        """Signed in (or switched account): that account's pinned lists become tabs, and the tab it
        was last on is shown again."""
        start = settings.get("start_tab") or "last"
        last = settings.account_get("last_tab") if start == "last" else start
        self._restoring_tabs = True
        try:
            self._rebuild_list_tabs()
            if last in self._views:
                self._stack.set_visible_child_name(last)
        finally:
            self._restoring_tabs = False

    def open_muted_words(self) -> None:
        self._push_unique("muted-words", lambda: MutedWordsPage(self))

    def load_muted_words(self) -> None:
        def done(result, error):
            if not error:
                self.on_muted_words([w["keyword"] for w in result.get("list") or []])

        self._sidecar.call("mutedWords.list", {}, done)

    def on_muted_words(self, words: list[str]) -> None:
        """Your muted words changed (or loaded): re-check every loaded post that honours them."""
        self._muted_patterns = compile_words(words)
        views = list(self._views.values())
        pages = self._nav.get_navigation_stack()
        for index in range(pages.get_n_items()):
            stack = [pages.get_item(index)]
            while stack:
                widget = stack.pop()
                if isinstance(widget, PagedListView) and widget not in views:
                    views.append(widget)
                child = widget.get_first_child()
                while child is not None:
                    stack.append(child)
                    child = child.get_next_sibling()
        for view in views:
            if view.hide_muted_words:
                view.refilter()

    def hides_by_muted_word(self, tweet: dict) -> bool:
        return matches(self._muted_patterns, tweet)

    def open_blocked(self) -> None:
        self._push_unique(
            "blocked",
            lambda: PeoplePage(self, "blocked", "Blocked Accounts", "They can't follow you or see your posts", "user.blocked", {}, "You haven’t blocked anyone"),
        )

    def set_blocked(self, user: dict, block: bool) -> None:
        """Block (after confirming) or unblock someone on X."""
        if not self._signed_in() or not user.get("id"):
            return
        handle = user.get("userName", "")

        def run():
            def done(_result, error):
                if error:
                    self.report_error(error, f"Couldn't {'block' if block else 'unblock'} @{handle}")
                    return
                user["blocked"] = block
                if block:
                    user["youFollow"] = user["followsYou"] = False
                    for name in ("following", "foryou", "notifications"):
                        self._views[name].hide_rows(lambda row: user["id"] in getattr(row, "author_ids", ()))
                for tag in ("blocked", f"profile-{handle.lower()}"):
                    if (page := self._nav.find_page(tag)) is not None:
                        page.reload()
                self.toast(f"Blocked @{handle}" if block else f"Unblocked @{handle}")

            self._sidecar.call("user.block", {"id": user["id"], "block": block}, done)

        if not block:
            run()
            return
        dialog = Adw.AlertDialog(
            heading=f"Block @{handle}?",
            body="They won't be able to follow you, see your posts or message you, and you won't see theirs. "
            "Any follows between you are removed (unblocking doesn't restore them). X doesn't notify them.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("block", "Block")
        dialog.set_response_appearance("block", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")
        dialog.connect("response", lambda _d, response: response == "block" and run())
        dialog.present(self)

    def delete_conversation(self, conversation_id: str, title: str) -> None:
        """Confirm, then delete a DM conversation from your inbox."""
        if not self._signed_in() or not conversation_id:
            return
        dialog = Adw.AlertDialog(
            heading="Delete conversation?",
            body=f"Your conversation with {title} will be removed from your inbox. The other people in it keep their copy.",
        )
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("delete", "Delete")
        dialog.set_response_appearance("delete", Adw.ResponseAppearance.DESTRUCTIVE)
        dialog.set_default_response("cancel")
        dialog.set_close_response("cancel")

        def done(_result, error):
            if error:
                self.report_error(error, "Couldn't delete the conversation")
                return
            self._views["messages"].remove_conversation(conversation_id)
            if self._nav.find_page(f"dm-{conversation_id}") is not None:
                self._nav.pop_to_tag("home")
            self.toast("Conversation deleted")

        def on_response(_dialog, response):
            if response == "delete":
                self._sidecar.call("dm.deleteConversation", {"id": conversation_id}, done)

        dialog.connect("response", on_response)
        dialog.present(self)

    # -- links from outside Perch ----------------------------------------------

    def open_external_link(self, uri: str) -> None:
        """A link handed to Perch (command line, twitter://): open it once signed in."""
        if not self._signed_in():
            self._pending_link = uri  # opened by the sign-in (see _sign_in)
            return
        if not self.open_link(uri):
            self.toast("That isn't a link Perch can open")

    def open_x_link_dialog(self) -> None:
        """Paste an x.com / twitter.com link to open it in Perch (prefilled from the clipboard)."""
        if not self._signed_in():
            return
        entry = Gtk.Entry(placeholder_text="https://x.com/…", activates_default=True, hexpand=True)
        dialog = Adw.AlertDialog(heading="Open X Link", body="A post, profile, Space or search link from x.com or twitter.com.", extra_child=entry)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("open", "Open")
        dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("open")
        dialog.set_close_response("cancel")

        def sync(*_):
            dialog.set_response_enabled("open", fmt.x_link_target(entry.get_text().strip()) is not None)

        def on_response(_dialog, response):
            if response == "open":
                self.open_link(entry.get_text().strip())

        def pasted(clipboard, result):
            try:
                text = (clipboard.read_text_finish(result) or "").strip()
            except GLib.Error:
                return
            if fmt.x_link_target(text) and not entry.get_text():
                entry.set_text(text)
                entry.select_region(0, -1)

        entry.connect("changed", sync)
        sync()
        self.get_display().get_clipboard().read_text_async(None, pasted)
        dialog.connect("response", on_response)
        self._present_releasing(dialog)
        entry.grab_focus()

    def open_quotes(self, tweet: dict) -> None:
        """The posts quoting ``tweet``."""
        if tweet.get("id"):
            self._push_unique(f"quotes-{tweet['id']}", lambda: QuotesPage(self, tweet))

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
            self._present_releasing(AddToListDialog(self, user))

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
        if not (self.is_on_screen() and self._signed_in() and self._current_tab() == "messages" and newest):
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
        crash.note(f"tab {_tab_kind(self._current_tab())}")
        if self._restoring_tabs:
            return  # tabs are being rebuilt for an account; the final one loads once they're done
        if self._signed_in():
            settings.account_set("last_tab", self._current_tab())
        self._ensure_loaded()
        self._maybe_mark_seen()
        self._maybe_mark_inbox_seen()

    def timeline_loaded(self, kind: str) -> bool:
        return self._signed_in() and self._views[kind].loaded_once

    def on_timeline_polled(self, kind: str, items: list[dict]) -> None:
        """The poller fetched the timeline's first page: offer posts newer than what's shown."""
        view = self._views[kind]
        if view.loaded_once:
            view.offer_new_posts(view.count_new(items))

    def on_notifications_loaded(self) -> None:
        """The All list loaded: if it's on screen, its newest notification counts as seen."""
        self._maybe_mark_seen()

    def _maybe_mark_seen(self) -> None:
        view = self._views["notifications"]
        if self.is_on_screen() and self._current_tab() == "notifications" and view.get_visible_child_name() == "list":
            view.mark_seen()
            page = self._stack.get_page(view)
            page.set_badge_number(0)
            page.set_needs_attention(False)

    def _refresh(self) -> None:
        if not self._signed_in():
            return
        page = self._nav.get_visible_page()
        # A refresh always asks X (the sidecar otherwise reuses results for a short while).
        if isinstance(page, (ConversationPage, TweetDetailPage, ProfilePage, SearchPage, BookmarksPage, ListsPage, ListPage, PeoplePage, EditHistoryPage, ScheduledPage, MutedWordsPage, QuotesPage)):
            self._sidecar.fresh(page.reload)
            return
        view = self._views[self._current_tab()]
        view.scroll_to_top()
        self._sidecar.fresh(view.reload)

    def _on_stale(self, age: int) -> None:
        """X failed (rate limit, offline) and the sidecar answered with a saved copy."""
        now = GLib.get_monotonic_time()
        if now - self._stale_toast_at < 60_000_000:
            return
        self._stale_toast_at = now
        minutes = max(1, round(age / 60))
        self.toast(f"Couldn't reach X — showing what Perch loaded {minutes} min ago")

    def _ensure_loaded(self) -> None:
        if not self._signed_in():
            return
        view = self._views.get(self._current_tab())
        if view and not view.loaded_once:
            saved = snapshot.load_timeline(self.me_id, view.kind) if isinstance(view, TimelineView) else None
            if saved:
                # Where you left off; anything newer waits behind the new-posts pill.
                view.restore(saved)
                view.restored_pending = not self._live  # checked once X answers (see _sign_in)
                if self._live:
                    self._check_newer(view)
            else:
                view.reload()

    # -- accounts ------------------------------------------------------------
    #
    # Each X account's key is in the keyring under its user id; settings["accounts"] lists them
    # ({id, handle, name}) and settings["active_account"] is the one to start with. The sidecar talks
    # to one account at a time: switching re-initialises it (which also empties its cache).

    def _accounts_enabled(self) -> bool:
        """Saved accounts are off in plain demo mode (no keys); on with a real account, or in demo
        mode with PERCH_KEYRING=memory (UI tests)."""
        return not self._demo or os.environ.get("PERCH_KEYRING") == "memory"

    def _accounts(self) -> list[dict]:
        return list(settings.get("accounts") or [])

    def _remember_account(self, user: dict) -> None:
        accounts = [a for a in self._accounts() if a.get("id") != user["id"]]
        accounts.append({"id": user["id"], "handle": user.get("userName") or "", "name": user.get("fullName") or ""})
        settings.set("accounts", accounts)
        settings.set("active_account", user["id"])

    def switch_account(self, account_id: str) -> None:
        """Sign in to another saved account (its key is in the keyring)."""
        if account_id == self.me_id:
            return
        account = next((a for a in self._accounts() if a.get("id") == account_id), None)
        handle = (account or {}).get("handle", "")

        def found(key):
            if not key:
                self.toast(f"No saved key for @{handle} — add the account again")
                return
            self._reset_session()
            self._pages.set_visible_child_name("starting")
            self._sign_in(key, persist=False, account_id=account_id)

        keyring.lookup(account_id, found)

    def add_account(self) -> None:
        """The sign-in page, with a way back to the current account."""
        self._adding_from = self.me_id
        self._key_row.set_text("")
        self._show_signin()

    def _cancel_add_account(self) -> None:
        previous, self._adding_from = self._adding_from, None
        attempted, self._signin_attempted = self._signin_attempted, False
        if previous and not attempted and self._me and self._me.get("id") == previous:
            self._pages.set_visible_child_name("main")  # no key was tried: still signed in as before
        elif previous:
            # A key was tried (and failed): the sidecar has to be signed back in to the previous account.
            self._pages.set_visible_child_name("starting")
            self.switch_account_after_failure(previous)
        else:
            self._show_signin()

    def switch_account_after_failure(self, account_id: str) -> None:
        """A sign-in to another account failed: go back to the one that was signed in."""
        self._me = None
        keyring.lookup(account_id, lambda key: self._sign_in(key, persist=False, account_id=account_id) if key else self._show_signin())

    def _reset_session(self) -> None:
        """Before signing in as someone else: stop polling and audio, drop pages and loaded lists."""
        self._save_timelines_now()
        self._from_saved = self._live = False
        self._stop_retrying()
        self._offline_banner.set_revealed(False)
        self._poller.stop()
        self.player.stop()
        self._nav.pop_to_tag("home")
        for view in self._views.values():
            view.loaded_once = False
        inbox = self._views["messages"]
        inbox.unread_count = 0
        inbox.newest_event_id = inbox.last_seen_event_id = None
        for name in ("notifications", "messages"):
            page = self._stack.get_page(self._views[name])
            page.set_badge_number(0)
            page.set_needs_attention(False)
        self._muted_patterns = []

    # -- sign in / out ------------------------------------------------------

    def _sign_out(self) -> None:
        """Sign out of the current account: forget its key; go to another saved account if any."""
        current = self.me_id
        if current:
            keyring.clear(current)
            snapshot.forget(current)
            settings.set("accounts", [a for a in self._accounts() if a.get("id") != current])
        self._reset_session()
        self._me = None
        settings.set_account(None)
        self._sidecar.sign_out(lambda *_: None)
        self._key_row.set_text("")
        remaining = self._accounts()
        if remaining and self._accounts_enabled():
            settings.set("active_account", remaining[0]["id"])
            self._pages.set_visible_child_name("starting")
            self.switch_account_after_failure(remaining[0]["id"])
            return
        settings.set("active_account", None)
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
        if self._demo and not self._accounts_enabled():
            self._sign_in("demo", persist=False)
        elif env_key := os.environ.get("PERCH_API_KEY"):
            self._sign_in(env_key, persist=False)
        elif active := settings.get("active_account"):
            keyring.lookup(active, lambda key: self._sign_in(key, persist=False, account_id=active) if key else self._start_legacy())
        else:
            self._start_legacy()

    def _start_legacy(self) -> None:
        """No saved account: an older Perch's single key, if there is one (moved to its account after sign-in)."""
        keyring.lookup(keyring.LEGACY, lambda key: self._sign_in(key, persist=True, legacy=True) if key else self._show_signin())

    def _show_signin(self, message: str | None = None) -> None:
        self._signin_error.set_label(message or "")
        self._signin_error.set_visible(bool(message))
        self._signin_button.set_sensitive(True)
        self._signin_cancel.set_visible(bool(self._adding_from))
        self._pages.set_visible_child_name("signin")
        self._key_row.grab_focus()

    def _submit_key(self) -> None:
        key = self._key_row.get_text().strip()
        if not key:
            self._show_signin("Paste your API key first.")
            return
        self._signin_button.set_sensitive(False)
        if self._adding_from:
            self._reset_session()
            self._signin_attempted = True
        self._sign_in(key, persist=True)

    def _sign_in(self, key: str, persist: bool, account_id: str | None = None, legacy: bool = False) -> None:
        def on_me(user, error: RpcError | None):
            if error:
                self._on_signin_failed(error)
                return
            if account_id and user.get("id") != account_id:
                # The saved key belongs to a different account than the one it was saved for.
                self._on_signin_failed(RpcError("That key signs in to a different account", "auth"))
                return
            was_saved = self._from_saved and (self._me or {}).get("id") == user.get("id")
            self._me = user
            self._from_saved = False
            self._live = True
            self._stop_retrying()
            self._offline_banner.set_revealed(False)
            crash.note("signed in")
            self._adding_from = None
            self._signin_attempted = False
            settings.set_account(user["id"])
            if self._accounts_enabled():
                if legacy:
                    settings.adopt_legacy(user["id"])
                self._remember_account(user)
                if persist:
                    label = f"Perch X API key (@{user.get('userName', '')})"

                    def stored(ok):
                        if not ok:
                            self.toast("Couldn't save the key to the keyring")
                        elif legacy:
                            keyring.clear(keyring.LEGACY)

                    keyring.store(user["id"], key, label, stored)
            snapshot.save_me(user["id"], user)
            self._rebuild_menu()
            if not was_saved:  # (already set up from the saved copy otherwise)
                self.on_account_ready()  # this account's pinned tabs and last tab, before anything loads
            self._pages.set_visible_child_name("main")
            self._ensure_loaded()
            for view in self._views.values():
                if isinstance(view, TimelineView) and view.restored_pending:
                    self._check_newer(view)
            self.load_muted_words()
            self._poller.start()
            if self._pending_link:
                link, self._pending_link = self._pending_link, None
                self.open_external_link(link)

        saved_key = not persist  # (a key from the keyring, not one just typed in)

        def on_me_or_offline(user, error: RpcError | None):
            if error and saved_key and error.kind != "auth":
                # X can't be reached (or is rate limiting): the key is fine, so don't ask for it.
                self._on_offline(error, retry=lambda: self._sidecar.call("me", None, on_me_or_offline))
                return
            if error and self._from_saved:
                self._reset_session()  # the saved copy was shown, but this key no longer works
                self._me = None
            on_me(user, error)

        def on_init(_result, error: RpcError | None):
            if error:
                if self._from_saved:
                    self._reset_session()  # the saved copy was shown, but this key no longer works
                    self._me = None
                self._on_signin_failed(error)
            else:
                self._sidecar.call("me", None, on_me_or_offline)

        self._sidecar.sign_in(key, on_init)  # (sent first: anything asked of X below goes after it)
        if account_id and self._me is None:
            self._show_saved(account_id)

    def _show_saved(self, account_id: str) -> None:
        """Open on the account's saved copy straight away, while X is asked (see snapshot.py)."""
        user = snapshot.load_me(account_id)
        if not user or snapshot.saved_at(account_id) is None:
            return
        crash.note("opened on the saved copy")
        self._me = user
        self._from_saved = True
        settings.set_account(user["id"])
        self._rebuild_menu()
        self.on_account_ready()
        self._pages.set_visible_child_name("main")
        self._ensure_loaded()

    def _on_offline(self, error: RpcError, retry) -> None:
        """Signing in with a saved key failed because X can't be reached (or is rate limiting):
        keep showing the saved copy (or say so on the start page), and try again by itself."""
        crash.note(f"offline ({error.kind})")
        self._retry_me = retry
        what = "X is limiting requests right now" if error.kind == "rate_limit" else "Can't reach X"
        if self._from_saved:
            when = snapshot.saved_at(self.me_id)
            self._offline_banner.set_title(f"{what} — showing posts saved {_when(when)}" if when else what)
            self._offline_banner.set_revealed(True)
        else:
            self._start_status.set_icon_name("network-offline-symbolic")
            self._start_status.set_title(what)
            self._start_status.set_description("Perch will keep trying. Check your connection, or try again now.")
            retry_button = Gtk.Button(label="Try Again", halign=Gtk.Align.CENTER, css_classes=["pill", "suggested-action"])
            retry_button.connect("clicked", lambda *_: self._try_again())
            self._start_status.set_child(retry_button)
            self._pages.set_visible_child_name("starting")
        self._start_retrying()

    def _start_retrying(self) -> None:
        """Every minute, and as soon as the computer says it's back online."""
        if not self._retry_source:
            self._retry_source = GLib.timeout_add_seconds(60, lambda: self._try_again() or GLib.SOURCE_CONTINUE)
        if not getattr(self, "_network_handler", 0):
            monitor = Gio.NetworkMonitor.get_default()
            self._network_handler = monitor.connect("network-changed", lambda _m, up: up and self._retry_me and GLib.timeout_add_seconds(2, lambda: self._try_again() or GLib.SOURCE_REMOVE))

    def _stop_retrying(self) -> None:
        self._retry_me = None
        if self._retry_source:
            GLib.source_remove(self._retry_source)
            self._retry_source = 0

    def _try_again(self) -> None:
        if self._retry_me:
            crash.note("trying again")
            if not self._from_saved:
                self._start_status.set_child(Adw.Spinner(width_request=32, height_request=32))
                self._start_status.set_icon_name(None)
                self._start_status.set_title("Connecting…")
                self._start_status.set_description(None)
            self._sidecar.fresh(self._retry_me)
        elif self._live:
            # Signed in, but loading failed: ask X again for what's on screen.
            self._offline_banner.set_revealed(False)
            self._refresh()

    def on_unreachable(self, error: RpcError | None = None) -> None:
        """Signed in, but X stopped answering (a load or a background check failed that way)."""
        if self._live and not self._offline_banner.get_revealed():
            crash.note("x unreachable")
            self._offline_banner.set_title("X is limiting requests right now" if error and error.kind == "rate_limit" else "Can't reach X — what's shown may be out of date")
            self._offline_banner.set_revealed(True)

    def on_reachable(self) -> None:
        """X answered again (a background check worked)."""
        if self._live and self._offline_banner.get_revealed():
            crash.note("x reachable")
            self._offline_banner.set_revealed(False)

    def _check_newer(self, view: TimelineView) -> None:
        """A timeline opened on its saved copy: ask X for its newest posts and offer the ones that
        are newer than the copy with the new-posts pill (instead of moving you to the top)."""
        view.restored_pending = False

        def done(result, error):
            if error:
                if error.kind in ("network", "rate_limit"):
                    self.on_unreachable(error)
                return
            if not result.get("stale"):
                self.on_reachable()
            items = result.get("list") or []
            view.offer_new_posts(view.count_new(items))

        self._sidecar.fresh(self._sidecar.call, "timeline", {"kind": view.kind, "cursor": None}, done)

    def save_timeline(self, view: TimelineView) -> None:
        """Save a timeline's posts and place (called a moment after they change)."""
        if not self._me or not view.loaded_once or self._pages.get_visible_child_name() != "main":
            return
        items, cursor, anchor = view.saved_state(snapshot.MAX_ITEMS)
        if items:
            snapshot.save_timeline(self.me_id, view.kind, items, cursor, anchor)

    def _save_timelines_now(self) -> None:
        for view in self._views.values():
            if isinstance(view, TimelineView):
                view.save_pending()

    def offer_crash_report(self, report: str) -> None:
        """The last run crashed: say so, show the report, and offer to send it (nothing is sent
        unless you do)."""
        text = Gtk.TextView(editable=False, monospace=True, wrap_mode=Gtk.WrapMode.WORD_CHAR, cursor_visible=False, top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        text.get_buffer().set_text(report)
        text.update_property([Gtk.AccessibleProperty.LABEL], ["Crash report"])
        scroller = Gtk.ScrolledWindow(child=text, min_content_height=220, max_content_height=320, propagate_natural_height=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("card")
        dialog = Adw.AlertDialog(
            heading="Perch Closed Unexpectedly",
            body="Last time, Perch stopped while it was running. This report says what it was doing — no posts, names or messages. Sending it helps fix the problem.",
            extra_child=scroller,
        )
        dialog.add_response("close", "Close")
        dialog.add_response("copy", "Copy Report")
        dialog.add_response("report", "Report on GitHub")
        dialog.set_response_appearance("report", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_close_response("close")

        def on_response(_dialog, response):
            crash.dismiss()
            if response == "copy":
                self.get_display().get_clipboard().set(report)
                self.toast("Report copied")
            elif response == "report":
                Gtk.UriLauncher(uri=crash.issue_url(report)).launch(self, None, None, None)

        dialog.connect("response", on_response)
        self._present_releasing(dialog)

    def _on_signin_failed(self, error: RpcError) -> None:
        if error.kind == "auth":
            self._show_signin(f"Sign-in failed: {error}")
        elif error.kind == "rate_limit":
            self._show_signin("X is rate limiting this account. Wait a few minutes and try again.")
        else:
            self._show_signin(f"Couldn't reach X: {error}")

    def _on_load_failed(self, _view, error: RpcError) -> None:
        if error.kind == "auth":
            handle = (self._me or {}).get("userName", "")
            self._sign_out()  # forgets this account's key; another saved account takes over if there is one
            message = f"The X session for @{handle} expired. Paste a fresh API key." if handle else "Your X session expired. Paste a fresh API key."
            if self._pages.get_visible_child_name() == "signin":
                self._show_signin(message)
            else:
                self.toast(message)
        elif error.kind == "rate_limit":
            self.on_unreachable(error)
            self.toast("Rate limited by X — try again in a few minutes")
        elif error.kind == "network":
            self.on_unreachable(error)
        else:
            self.toast(f"Couldn't load more: {error}")
