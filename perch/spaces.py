"""Spaces: details dialog, GStreamer HLS player and the now-playing bar."""

import os

import gi

gi.require_version("Gst", "1.0")

from gi.repository import Adw, GLib, GObject, Gst, Gtk, Pango  # noqa: E402

from perch import format as fmt  # noqa: E402
from perch import images  # noqa: E402

STATE_LABELS = {"running": "Live", "ended": "Ended", "notstarted": "Scheduled", "canceled": "Canceled"}


def space_state(space: dict) -> str:
    return (space.get("state") or "").lower()


def can_listen(space: dict) -> bool:
    state = space_state(space)
    return bool(space.get("mediaKey")) and (state == "running" or (state == "ended" and space.get("isSpaceAvailableForReplay")))


class SpacePlayer(GObject.Object):
    """Plays a Space's HLS audio with playbin3. ``status`` is idle | loading | playing | paused | error."""

    __gsignals__ = {
        "changed": (GObject.SignalFlags.RUN_FIRST, None, ()),
        "failed": (GObject.SignalFlags.RUN_FIRST, None, (str,)),
    }

    def __init__(self):
        super().__init__()
        self.status = "idle"
        self.space: dict | None = None
        self.is_live = False
        self._pipeline = None

    def _set_status(self, status: str) -> None:
        self.status = status
        self.emit("changed")

    def play(self, space: dict, uri: str) -> None:
        self.stop()
        self.space = space
        self.is_live = space_state(space) == "running"
        pipeline = Gst.ElementFactory.make("playbin3", "space-player") or Gst.ElementFactory.make("playbin", "space-player")
        if pipeline is None:
            self.emit("failed", "GStreamer playbin is not available")
            return
        pipeline.set_property("uri", uri)
        # Audio only: drop any video track some Spaces carry.
        if fakesink := Gst.ElementFactory.make("fakesink", None):
            pipeline.set_property("video-sink", fakesink)
        # Development hook, e.g. PERCH_AUDIO_SINK="fakesink sync=true" to test without sound.
        if sink_description := os.environ.get("PERCH_AUDIO_SINK"):
            pipeline.set_property("audio-sink", Gst.parse_launch(sink_description))
        bus = pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)
        self._pipeline = pipeline
        self._set_status("loading")
        pipeline.set_state(Gst.State.PLAYING)

    def toggle_pause(self) -> None:
        if not self._pipeline:
            return
        if self.status == "playing":
            self._pipeline.set_state(Gst.State.PAUSED)
            self._set_status("paused")
        elif self.status == "paused":
            self._pipeline.set_state(Gst.State.PLAYING)
            self._set_status("loading")

    def stop(self) -> None:
        if self._pipeline:
            self._pipeline.get_bus().remove_signal_watch()
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None
        if self.status != "idle":
            self.space = None
            self._set_status("idle")

    def _on_message(self, _bus, message: Gst.Message) -> None:
        kind = message.type
        if kind == Gst.MessageType.ERROR:
            err, _debug = message.parse_error()
            self.stop()
            self.emit("failed", err.message)
        elif kind == Gst.MessageType.EOS:
            self.stop()
        elif kind == Gst.MessageType.BUFFERING:
            percent = message.parse_buffering()
            if self.status in ("loading", "playing"):
                self._set_status("loading" if percent < 100 else "playing")
        elif kind == Gst.MessageType.STATE_CHANGED and message.src == self._pipeline:
            _old, new, _pending = message.parse_state_changed()
            if new == Gst.State.PLAYING and self.status == "loading":
                self._set_status("playing")


class PlayerBar(Gtk.Revealer):
    def __init__(self, player: SpacePlayer):
        super().__init__(transition_type=Gtk.RevealerTransitionType.SLIDE_UP, reveal_child=False)
        self._player = player
        bar = Gtk.Box(spacing=12, margin_start=12, margin_end=8, margin_top=6, margin_bottom=6)
        bar.add_css_class("player-bar")

        icon = Gtk.Image.new_from_icon_name("audio-input-microphone-symbolic")
        icon.add_css_class("accent")
        bar.append(icon)
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, valign=Gtk.Align.CENTER)
        self._title = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["heading"])
        self._subtitle = Gtk.Label(xalign=0, ellipsize=Pango.EllipsizeMode.END, css_classes=["caption", "dim-label"])
        text.append(self._title)
        text.append(self._subtitle)
        bar.append(text)

        self._spinner = Adw.Spinner(visible=False)
        bar.append(self._spinner)
        self._pause = Gtk.Button(icon_name="media-playback-pause-symbolic", valign=Gtk.Align.CENTER)
        self._pause.add_css_class("circular")
        self._pause.add_css_class("flat")
        self._pause.connect("clicked", lambda *_: player.toggle_pause())
        bar.append(self._pause)
        stop = Gtk.Button(icon_name="media-playback-stop-symbolic", valign=Gtk.Align.CENTER, tooltip_text="Stop")
        stop.add_css_class("circular")
        stop.add_css_class("flat")
        stop.connect("clicked", lambda *_: player.stop())
        bar.append(stop)

        self.set_child(bar)
        player.connect("changed", lambda *_: self._sync())

    def _sync(self) -> None:
        player = self._player
        active = player.status != "idle" and player.space is not None
        self.set_reveal_child(active)
        if not active:
            return
        self._title.set_label(player.space.get("title") or "Untitled Space")
        mode = "Live" if player.is_live else "Replay"
        self._subtitle.set_label({"loading": f"{mode} · Buffering…", "paused": f"{mode} · Paused"}.get(player.status, mode))
        self._spinner.set_visible(player.status == "loading")
        # Pausing a live stream would just fall behind; only replays get pause.
        self._pause.set_visible(not player.is_live and player.status in ("playing", "paused"))
        paused = player.status == "paused"
        self._pause.set_icon_name("media-playback-start-symbolic" if paused else "media-playback-pause-symbolic")
        self._pause.set_tooltip_text("Resume" if paused else "Pause")


def _person_row(person: dict, role: str) -> Adw.ActionRow:
    row = Adw.ActionRow(title=GLib.markup_escape_text(person.get("displayName") or person.get("screenName") or "?"))
    row.set_subtitle(GLib.markup_escape_text(f"@{person.get('screenName', '')}" + (f" · {role}" if role else "")))
    avatar = Adw.Avatar(size=36, text=person.get("displayName") or "", show_initials=True)
    images.load(fmt.avatar_url(person.get("avatarUrl")), lambda tex: tex and avatar.set_custom_image(tex))
    row.add_prefix(avatar)
    return row


class SpaceDialog(Adw.Dialog):
    def __init__(self, controller, space_id: str):
        super().__init__(title="Space", content_width=440, content_height=560)
        self._controller = controller
        self._space_id = space_id
        self._space: dict | None = None

        self._stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self._stack.add_named(Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32), "loading")
        self._error = Adw.StatusPage(icon_name="dialog-error-symbolic", title="Couldn't load this Space")
        self._stack.add_named(self._error, "error")

        view = Adw.ToolbarView(content=self._stack)
        view.add_top_bar(Adw.HeaderBar())
        self.set_child(view)
        controller.sidecar.call("space.details", {"id": space_id}, self._on_details)

    def _on_details(self, space, error) -> None:
        if error:
            self._error.set_description(GLib.markup_escape_text(self._controller.describe_error(error, "")))
            self._stack.set_visible_child_name("error")
            return
        self._space = space
        self._stack.add_named(self._build(space), "details")
        self._stack.set_visible_child_name("details")

    def _build(self, space: dict) -> Gtk.Widget:
        state = space_state(space)
        page = Adw.PreferencesPage()

        head = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8, margin_bottom=6)
        badge = Gtk.Label(label=STATE_LABELS.get(state, state.title() or "Unknown"), halign=Gtk.Align.CENTER)
        badge.add_css_class("space-badge")
        badge.add_css_class("live" if state == "running" else "dim-label")
        head.append(badge)
        title = Gtk.Label(label=space.get("title") or "Untitled Space", wrap=True, justify=Gtk.Justification.CENTER)
        title.add_css_class("title-2")
        head.append(title)

        facts = []
        if state == "running" and space.get("totalLiveListeners") is not None:
            facts.append(f"{fmt.compact_count(space['totalLiveListeners']) or '0'} listening")
        if state == "ended" and space.get("totalReplayWatched"):
            facts.append(f"{fmt.compact_count(space['totalReplayWatched'])} replays")
        when = space.get("startedAt") or space.get("scheduledStart")
        if when:
            facts.append(("Started " if space.get("startedAt") else "Starts ") + fmt.full_time(when))
        if facts:
            head.append(Gtk.Label(label=" · ".join(facts), css_classes=["dim-label"], wrap=True, justify=Gtk.Justification.CENTER))

        self._listen = Gtk.Button(halign=Gtk.Align.CENTER, margin_top=8)
        self._listen.add_css_class("pill")
        self._listen.add_css_class("suggested-action")
        if can_listen(space):
            self._listen.set_label("Listen Live" if state == "running" else "Play Replay")
            self._listen.connect("clicked", lambda *_: self._start())
        else:
            self._listen.set_label({"ended": "Replay Not Available", "notstarted": "Not Started Yet"}.get(state, "Not Available"))
            self._listen.set_sensitive(False)
        head.append(self._listen)

        top = Adw.PreferencesGroup()
        top.add(head)
        page.add(top)

        participants = space.get("participants") or {}
        for key, title_text, role in (("admins", "Hosts", "Host"), ("speakers", "Speakers", "")):
            people = participants.get(key) or []
            if not people:
                continue
            group = Adw.PreferencesGroup(title=title_text)
            for person in people[:30]:
                group.add(_person_row(person, role if key == "admins" else ""))
            page.add(group)
        return page

    def _start(self) -> None:
        self._listen.set_sensitive(False)
        self._listen.set_label("Connecting…")
        space = self._space

        def done(result, error):
            if error:
                self._listen.set_sensitive(True)
                self._listen.set_label("Try Again")
                self._controller.report_error(error, "Couldn't start audio")
                return
            self._controller.player.play(space, result["location"])
            self.close()

        self._controller.sidecar.call("space.stream", {"mediaKey": space.get("mediaKey")}, done)


def ask_space_url(controller, parent: Gtk.Widget) -> None:
    entry = Gtk.Entry(placeholder_text="https://x.com/i/spaces/…", activates_default=True, hexpand=True)
    dialog = Adw.AlertDialog(heading="Open Space", body="Paste a Space link or id.", extra_child=entry)
    dialog.add_response("cancel", "Cancel")
    dialog.add_response("open", "Open")
    dialog.set_response_appearance("open", Adw.ResponseAppearance.SUGGESTED)
    dialog.set_default_response("open")

    def on_response(_dialog, response):
        if response != "open":
            return
        space_id = fmt.space_id_from(entry.get_text())
        if space_id:
            controller.open_space(space_id)
        else:
            controller.toast("That doesn't look like a Space link")

    dialog.connect("response", on_response)
    dialog.present(parent)
    dialog.set_focus(entry)
