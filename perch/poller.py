"""Polls notifications, the DM inbox and the Following timeline while signed in: tab badges,
desktop notifications and the "new posts" pill."""

from gi.repository import Gio, GLib

from perch import APP_ID, settings
from perch.messages import conversation_title, message_preview

POLL_SECONDS = 120
# notification_icon -> the Preferences switch that decides whether it gets a desktop notification.
_ALERT_KINDS = {"heart_icon": "notify_likes", "retweet_icon": "notify_reposts", "person_icon": "notify_follows"}


def alert_setting(icon: str | None) -> str:
    return _ALERT_KINDS.get(icon or "", "notify_other")

# When X rate-limits us or the network is down, the gap doubles up to this, and goes back to the
# normal interval after the first good round (hammering X while it says "slow down" prolongs it).
MAX_BACKOFF_SECONDS = 16 * 60
MAX_INDIVIDUAL = 3  # more new items than this collapse into one summary notification


class Poller:
    def __init__(self, window):
        self._window = window
        self._source: int | None = None
        self._known_notifications: set[str] = set()
        self._known_messages: set[str] = set()
        self._baseline = {"notifications": False, "inbox": False}
        self._ticks = 0
        self.interval = max(60, int(settings.get("poll_minutes") or 2) * 60)  # the normal gap (Preferences)
        self._delay = self.interval  # the gap until the next round (longer while backing off)
        self._round_failed = False

    def start(self) -> None:
        if self._source is None:
            self._delay = self.interval
            self._tick()
            self._schedule()

    def set_interval(self, seconds: int) -> None:
        """A new normal gap; takes effect from the next round."""
        self.interval = max(30, int(seconds))
        if self._source is not None and self._delay < self.interval * 2:
            GLib.source_remove(self._source)
            self._delay = self.interval
            self._schedule()

    def _schedule(self) -> None:
        self._source = GLib.timeout_add_seconds(self._delay, self._fire)

    def _fire(self) -> bool:
        self._source = None
        self._tick()
        self._schedule()
        return GLib.SOURCE_REMOVE

    def _round_result(self, result, error) -> None:
        """Back off while X says slow down (or a saved copy had to stand in), recover after."""
        throttled = (error is not None and error.kind in ("rate_limit", "network")) or (isinstance(result, dict) and result.get("stale"))
        if throttled and not self._round_failed:
            self._round_failed = True
            self._delay = min(max(self._delay, self.interval) * 2, MAX_BACKOFF_SECONDS)
            print(f"[perch] X is throttling or unreachable: next check in {self._delay // 60} min")
            if self._source is not None:  # the next round is already scheduled: push it back
                GLib.source_remove(self._source)
                self._schedule()
        elif not throttled and error is None and not self._round_failed and self._delay > self.interval:
            # X answers again: bring the already scheduled (long) wait back to the normal gap.
            self._delay = self.interval
            if self._source is not None:
                GLib.source_remove(self._source)
                self._schedule()

    def stop(self) -> None:
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None
        self._known_notifications.clear()
        self._known_messages.clear()
        self._baseline = {"notifications": False, "inbox": False}
        self._ticks = 0
        self._delay = self.interval

    def _tick(self) -> bool:
        if self._ticks and not self._round_failed:
            self._delay = self.interval  # the last round went through: back to the normal gap
        self._round_failed = False
        sidecar = self._window.sidecar
        sidecar.call("notifications", {"fresh": True}, self._on_notifications)
        sidecar.call("dm.inbox", {"meId": self._window.me_id, "fresh": True}, self._on_inbox)
        # New posts in Following (only once that timeline has been opened). The result is cached, so
        # showing the new posts afterwards costs no second request.
        # Not on the first round: at sign-in the timeline has only just been loaded.
        if self._ticks and self._window.timeline_loaded("following"):
            sidecar.call("timeline", {"kind": "following", "fresh": True}, self._on_timeline)
        self._ticks += 1
        return GLib.SOURCE_CONTINUE

    def _on_timeline(self, result, error) -> None:
        self._round_result(result, error)
        if error:
            print(f"[perch] timeline poll failed: {error}")
            return
        self._window.on_timeline_polled("following", result.get("list") or [])

    # -- notifications ------------------------------------------------------

    def _on_notifications(self, result, error) -> None:
        self._round_result(result, error)
        if error:
            print(f"[perch] notification poll failed: {error}")
            return
        items = result.get("list") or []
        seen_at = settings.account_get("notifications_seen_at") or ""
        unread = [i for i in items if seen_at and (i.get("receivedAt") or "") > seen_at]
        fresh = [i for i in items if i.get("id") not in self._known_notifications]
        self._known_notifications.update(i.get("id") for i in items)

        first = not self._baseline["notifications"]
        self._baseline["notifications"] = True
        self._window.on_notifications_polled(len(unread), bool(fresh) and not first)
        if first or not fresh:
            return
        # Only alert for things that are actually unread (not just newly paged in).
        fresh = [i for i in fresh if not seen_at or (i.get("receivedAt") or "") > seen_at]
        # ... and only for the kinds chosen in Preferences.
        fresh = [i for i in fresh if settings.get(alert_setting(i.get("icon")))]
        if len(fresh) > MAX_INDIVIDUAL:
            self._send("notifications", "X", f"{len(fresh)} new notifications", "notifications")
        else:
            for item in fresh:
                self._send(f"n-{item.get('id')}", "X", item.get("message") or "New notification", "notifications")

    # -- DMs ----------------------------------------------------------------

    def _on_inbox(self, result, error) -> None:
        self._round_result(result, error)
        if error:
            print(f"[perch] inbox poll failed: {error}")
            return
        me_id = self._window.me_id
        users = result.get("users") or {}
        conversations = result.get("conversations") or []
        first = not self._baseline["inbox"]
        self._baseline["inbox"] = True

        incoming = []
        for conversation in conversations:
            last = conversation.get("lastMessage") or {}
            key = f"{conversation.get('id')}:{last.get('id')}"
            if key in self._known_messages:
                continue
            self._known_messages.add(key)
            if not first and conversation.get("unread") and last.get("senderId") != me_id:
                incoming.append(conversation)

        self._window.on_inbox_polled(result, [c.get("id") for c in incoming])
        if not settings.get("notify_messages"):
            return
        if len(incoming) > MAX_INDIVIDUAL:
            self._send("dm", "Messages", f"{len(incoming)} new messages", "messages")
            return
        for conversation in incoming:
            title, _subtitle, _other = conversation_title(conversation, users, me_id)
            body = message_preview(conversation.get("lastMessage"), me_id) or "New message"
            self._send(f"dm-{conversation.get('id')}", title, body, "messages")

    # -- desktop ------------------------------------------------------------

    def _send(self, key: str, title: str, body: str, tab: str) -> None:
        if not settings.get("desktop_notifications"):
            return
        notification = Gio.Notification.new(title)
        notification.set_body(body[:280])
        notification.set_icon(Gio.ThemedIcon.new(APP_ID))
        notification.set_default_action_and_target_value("app.show-tab", GLib.Variant.new_string(tab))
        self._window.get_application().send_notification(f"perch-{key}", notification)
