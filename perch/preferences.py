"""Preferences (Main menu → Preferences, Ctrl+,)."""

from gi.repository import Adw, GLib, Gtk

from perch import images, settings

POLL_CHOICES = ((1, "Every minute"), (2, "Every 2 minutes"), (5, "Every 5 minutes"), (10, "Every 10 minutes"), (15, "Every 15 minutes"))
START_CHOICES = (("last", "The last tab you used"), ("following", "Following"), ("foryou", "For You"), ("notifications", "Notifications"), ("messages", "Messages"))
CACHE_CHOICES = ((100, "100 MB"), (300, "300 MB"), (1000, "1 GB"))
ALERT_SWITCHES = (
    ("notify_messages", "Messages", "New direct messages"),
    ("notify_likes", "Likes", "Someone liked your post"),
    ("notify_reposts", "Reposts", "Someone reposted your post"),
    ("notify_follows", "New followers", None),
    ("notify_other", "Everything else", "Mentions, replies and other notifications"),
)


def _combo(title: str, subtitle: str | None, choices, current) -> Adw.ComboRow:
    row = Adw.ComboRow(title=title, model=Gtk.StringList.new([label for _value, label in choices]))
    if subtitle:
        row.set_subtitle(subtitle)
    values = [value for value, _label in choices]
    row.set_selected(values.index(current) if current in values else 0)
    row.values = values
    return row


def _size(num_bytes: int) -> str:
    return GLib.format_size(num_bytes)


class PreferencesDialog(Adw.PreferencesDialog):
    def __init__(self, controller):
        super().__init__(title="Preferences", search_enabled=False)
        self._controller = controller
        page = Adw.PreferencesPage(title="General", icon_name="preferences-system-symbolic")

        checking = Adw.PreferencesGroup(title="Checking for New Posts and Messages")
        poll = _combo("How often", "Slows down by itself while X is rate limiting", POLL_CHOICES, settings.get("poll_minutes"))
        poll.connect("notify::selected", lambda row, _p: self._set("poll_minutes", row.values[row.get_selected()], controller.on_poll_interval_changed))
        checking.add(poll)
        self.poll_row = poll
        page.add(checking)

        alerts = Adw.PreferencesGroup(title="Desktop Notifications", description="While Perch is running (also in the dock)")
        master = Adw.SwitchRow(title="Show desktop notifications", active=bool(settings.get("desktop_notifications")))
        alerts.add(master)
        self._kind_rows = []
        for key, title, subtitle in ALERT_SWITCHES:
            row = Adw.SwitchRow(title=title, active=bool(settings.get(key)))
            if subtitle:
                row.set_subtitle(subtitle)
            row.connect("notify::active", lambda r, _p, k=key: self._set(k, r.get_active()))
            alerts.add(row)
            self._kind_rows.append(row)
        master.connect("notify::active", lambda r, _p: self._set_master(r.get_active()))
        self.master_row = master
        self._sync_kinds(master.get_active())
        page.add(alerts)

        look = Adw.PreferencesGroup(title="Window and Dock")
        start = _combo("Start on", None, START_CHOICES, settings.get("start_tab"))
        start.connect("notify::selected", lambda row, _p: self._set("start_tab", row.values[row.get_selected()]))
        look.add(start)
        self.start_row = start
        badge = Adw.SwitchRow(title="Unread count on the dock icon", subtitle="On docks that show counts, such as Dash to Dock", active=bool(settings.get("dock_badge")))
        badge.connect("notify::active", lambda r, _p: self._set("dock_badge", r.get_active(), controller.on_dock_badge_changed))
        look.add(badge)
        self.badge_row = badge
        page.add(look)

        storage = Adw.PreferencesGroup(
            title="Cache",
            description="Answers from X are reused for a few minutes (and shown if X can't be reached); images stay on disk so they show at once after a restart.",
        )
        # Saved copies of X's answers (kept a few minutes; also shown when X can't be reached).
        self._saved = Adw.ActionRow(title="Posts, profiles and messages", subtitle="…")
        clear_saved = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER, tooltip_text="Forget the saved copies and load everything fresh from X")
        clear_saved.connect("clicked", lambda *_: self._clear_saved())
        self._saved.add_suffix(clear_saved)
        storage.add(self._saved)
        self._refresh_saved()
        cache = _combo("Image cache size", None, CACHE_CHOICES, settings.get("image_cache_mb"))
        cache.connect("notify::selected", lambda row, _p: self._set("image_cache_mb", row.values[row.get_selected()], lambda: images.set_disk_limit(settings.get("image_cache_mb"))))
        storage.add(cache)
        self._usage = Adw.ActionRow(title="Images in use")
        clear = Gtk.Button(label="Clear", valign=Gtk.Align.CENTER)
        clear.connect("clicked", lambda *_: self._clear_cache())
        self._usage.add_suffix(clear)
        storage.add(self._usage)
        self._refresh_usage()
        page.add(storage)
        self.add(page)

    def _set(self, key: str, value, then=None) -> None:
        settings.set(key, value)
        if then:
            then()

    def _set_master(self, on: bool) -> None:
        self._set("desktop_notifications", on, lambda: self._controller.on_desktop_notifications_changed(on))
        self._sync_kinds(on)

    def _sync_kinds(self, on: bool) -> None:
        for row in self._kind_rows:
            row.set_sensitive(on)

    def _refresh_saved(self) -> None:
        def done(result, error):
            if error:
                self._saved.set_subtitle("Couldn't read")
                return
            count = int(result.get("entries") or 0)
            if not count:
                self._saved.set_subtitle("Nothing saved")
                return
            minutes = max(1, round((result.get("oldestSeconds") or 0) / 60))
            self._saved.set_subtitle(f"{count} saved {'answer' if count == 1 else 'answers'} from X, the oldest from {minutes} min ago")

        self._controller.sidecar.call("cache.stats", {}, done)

    def _clear_saved(self) -> None:
        def done(_result, error):
            if error:
                self._controller.report_error(error, "Couldn't clear the cache")
                return
            self._refresh_saved()
            self._controller.on_saved_data_cleared()

        self._controller.sidecar.call("cache.clear", {}, done)

    def _refresh_usage(self) -> None:
        self._usage.set_subtitle(_size(images.disk_usage()))

    def _clear_cache(self) -> None:
        images.clear_disk()
        self._refresh_usage()
        self._controller.toast("Image cache cleared")
