"""Polls notifications and the DM inbox while signed in: tab badges + desktop notifications."""

from gi.repository import Gio, GLib

from perch import APP_ID, settings
from perch.messages import conversation_title, message_preview

POLL_SECONDS = 120
MAX_INDIVIDUAL = 3  # more new items than this collapse into one summary notification


class Poller:
    def __init__(self, window):
        self._window = window
        self._source: int | None = None
        self._known_notifications: set[str] = set()
        self._known_messages: set[str] = set()
        self._baseline = {"notifications": False, "inbox": False}

    def start(self) -> None:
        if self._source is None:
            self._source = GLib.timeout_add_seconds(POLL_SECONDS, self._tick)
            self._tick()

    def stop(self) -> None:
        if self._source is not None:
            GLib.source_remove(self._source)
            self._source = None
        self._known_notifications.clear()
        self._known_messages.clear()
        self._baseline = {"notifications": False, "inbox": False}

    def _tick(self) -> bool:
        sidecar = self._window.sidecar
        sidecar.call("notifications", {}, self._on_notifications)
        sidecar.call("dm.inbox", {"meId": self._window.me_id}, self._on_inbox)
        return GLib.SOURCE_CONTINUE

    # -- notifications ------------------------------------------------------

    def _on_notifications(self, result, error) -> None:
        if error:
            print(f"[perch] notification poll failed: {error}")
            return
        items = result.get("list") or []
        seen_at = settings.get("notifications_seen_at") or ""
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
        if len(fresh) > MAX_INDIVIDUAL:
            self._send("notifications", "X", f"{len(fresh)} new notifications", "notifications")
        else:
            for item in fresh:
                self._send(f"n-{item.get('id')}", "X", item.get("message") or "New notification", "notifications")

    # -- DMs ----------------------------------------------------------------

    def _on_inbox(self, result, error) -> None:
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
