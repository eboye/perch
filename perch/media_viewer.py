"""Full-window media viewer: swipeable gallery of a post's photos, videos and GIFs."""

import gi

gi.require_version("Gst", "1.0")

from gi.repository import Adw, Gdk, GLib, Gst, Gtk  # noqa: E402

from perch import format as fmt  # noqa: E402
from perch import images  # noqa: E402


def full_size_url(media: dict) -> str | None:
    url = media.get("url") or ""
    if media.get("type") == "PHOTO" and "pbs.twimg.com/media/" in url:
        return f"{url}?name=large"  # up to 2048px; "orig" can be huge to decode
    return url or None


class _PhotoPage(Gtk.Overlay):
    def __init__(self, media: dict):
        super().__init__(hexpand=True, vexpand=True)
        self.is_video = False
        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.set_child(self._picture)
        self._spinner = Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32)
        self.add_overlay(self._spinner)

        # Show the cached thumbnail at once, then swap in the large version.
        images.load(fmt.media_thumb(media), lambda tex: tex and not self._picture.get_paintable() and self._picture.set_paintable(tex))
        images.load(full_size_url(media), self._on_full, cache=False)

    def _on_full(self, texture) -> None:
        self._spinner.set_visible(False)
        if texture is not None:
            self._picture.set_paintable(texture)

    def pause(self) -> None:
        pass

    def resume(self) -> None:
        pass

    def toggle(self) -> None:
        pass


def _clock(ns: int) -> str:
    seconds = max(0, ns // Gst.SECOND)
    return f"{seconds // 60}:{seconds % 60:02d}"


class _VideoPage(Gtk.Overlay):
    """playbin3 -> gtk4paintablesink. (Gtk.Video can't open https URLs inside the sandbox: its GIO
    source needs gvfs.) GIFs loop silently without controls; videos get play/seek/mute."""

    def __init__(self, media: dict):
        super().__init__(hexpand=True, vexpand=True)
        self.is_video = True
        self.gif = media.get("type") == "GIF"
        self._duration = 0
        self._playing = False
        self._pipeline_started = False  # set before images.load: a cached thumbnail calls back at once

        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self.set_child(self._picture)
        images.load(fmt.media_thumb(media), lambda tex: tex and not self._pipeline_started and self._picture.set_paintable(tex))
        self._spinner = Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32, visible=False)
        self.add_overlay(self._spinner)
        self._error = Gtk.Label(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, wrap=True, visible=False, css_classes=["media-error"])
        self.add_overlay(self._error)

        self._pipeline = Gst.ElementFactory.make("playbin3", None) or Gst.ElementFactory.make("playbin", None)
        video_sink = self._make_video_sink() if self._pipeline else None
        if video_sink is None:
            self._show_error("Video playback is not available (missing GStreamer plugins)")
            self._pipeline = None
            return
        self._pipeline.set_property("video-sink", video_sink)
        self._pipeline.set_property("uri", media["url"])
        if self.gif:
            self._pipeline.set_property("mute", True)
        bus = self._pipeline.get_bus()
        bus.add_signal_watch()
        bus.connect("message", self._on_message)

        if not self.gif:
            self.add_overlay(self._build_controls())
            self._ticker = GLib.timeout_add(250, self._update_position)
        else:
            self._ticker = None

    def _make_video_sink(self) -> Gst.Element | None:
        """gtk4paintablesink, set up the way its docs recommend: inside glsinkbin when GTK has a GL
        context (GPU upload/colour conversion); otherwise behind videoconvert -> RGBA, because
        without GL some YUV frames are shown with swapped colours."""
        sink = Gst.ElementFactory.make("gtk4paintablesink", None)
        if sink is None:
            return None
        self._sink_paintable = sink.get_property("paintable")
        if self._sink_paintable.get_property("gl-context") is not None:
            if glsinkbin := Gst.ElementFactory.make("glsinkbin", None):
                glsinkbin.set_property("sink", sink)
                return glsinkbin
        convert = Gst.ElementFactory.make("videoconvert", None)
        caps = Gst.ElementFactory.make("capsfilter", None)
        if convert is None or caps is None:
            return sink
        caps.set_property("caps", Gst.Caps.from_string("video/x-raw,format=RGBA"))
        sink_bin = Gst.Bin.new("perch-video-sink")
        for element in (convert, caps, sink):
            sink_bin.add(element)
        convert.link(caps)
        caps.link(sink)
        sink_bin.add_pad(Gst.GhostPad.new("sink", convert.get_static_pad("sink")))
        return sink_bin

    # -- controls -------------------------------------------------------------

    def _build_controls(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=8, valign=Gtk.Align.END, margin_start=12, margin_end=12, margin_bottom=12)
        bar.add_css_class("video-controls")
        self._play_button = Gtk.Button(icon_name="media-playback-start-symbolic", tooltip_text="Play (Space)")
        self._play_button.add_css_class("flat")
        self._play_button.connect("clicked", lambda *_: self.toggle())
        bar.append(self._play_button)
        self._time = Gtk.Label(label="0:00", css_classes=["numeric", "caption"])
        bar.append(self._time)
        self._scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, hexpand=True, draw_value=False)
        self._scale.set_range(0, 1)
        self._scale.connect("change-value", self._on_seek)
        bar.append(self._scale)
        self._length = Gtk.Label(label="0:00", css_classes=["numeric", "caption"])
        bar.append(self._length)
        self._mute = Gtk.ToggleButton(icon_name="audio-volume-high-symbolic", tooltip_text="Mute")
        self._mute.add_css_class("flat")
        self._mute.connect("toggled", self._on_mute)
        bar.append(self._mute)
        return bar

    def _on_mute(self, button: Gtk.ToggleButton) -> None:
        muted = button.get_active()
        button.set_icon_name("audio-volume-muted-symbolic" if muted else "audio-volume-high-symbolic")
        if self._pipeline:
            self._pipeline.set_property("mute", muted)

    def _on_seek(self, _scale, _scroll, value: float) -> bool:
        if self._pipeline and self._duration:
            target = int(max(0.0, min(1.0, value)) * self._duration)
            self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT, target)
        return False

    def _update_position(self) -> bool:
        if not self._pipeline:
            return GLib.SOURCE_REMOVE
        ok_d, duration = self._pipeline.query_duration(Gst.Format.TIME)
        ok_p, position = self._pipeline.query_position(Gst.Format.TIME)
        if ok_d and duration > 0:
            self._duration = duration
            self._length.set_label(_clock(duration))
        if ok_p:
            self._time.set_label(_clock(position))
            if self._duration:
                self._scale.set_value(position / self._duration)
        return GLib.SOURCE_CONTINUE

    def _sync_button(self) -> None:
        if not self.gif:
            self._play_button.set_icon_name("media-playback-pause-symbolic" if self._playing else "media-playback-start-symbolic")
            self._play_button.set_tooltip_text("Pause (Space)" if self._playing else "Play (Space)")

    # -- playback ---------------------------------------------------------------

    def _show_error(self, text: str) -> None:
        self._spinner.set_visible(False)
        self._error.set_label(text)
        self._error.set_visible(True)

    def _on_message(self, _bus, message: Gst.Message) -> None:
        kind = message.type
        if kind == Gst.MessageType.ERROR:
            err, _debug = message.parse_error()
            print(f"[perch] video error: {err.message}")
            self._show_error(f"Couldn't play this video: {err.message}")
            self.pause()
        elif kind == Gst.MessageType.EOS:
            if self.gif:
                self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
            else:
                self._pipeline.set_state(Gst.State.PAUSED)
                self._pipeline.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
                self._playing = False
                self._sync_button()
        elif kind == Gst.MessageType.BUFFERING:
            self._spinner.set_visible(message.parse_buffering() < 100 and self._playing)
        elif kind == Gst.MessageType.STATE_CHANGED and message.src == self._pipeline:
            _old, new, _pending = message.parse_state_changed()
            if new == Gst.State.PLAYING:
                self._spinner.set_visible(False)

    def resume(self) -> None:
        if not self._pipeline:
            return
        if not self._pipeline_started:
            self._pipeline_started = True
            self._picture.set_paintable(self._sink_paintable)
            self._spinner.set_visible(True)
        self._pipeline.set_state(Gst.State.PLAYING)
        self._playing = True
        self._sync_button()

    def pause(self) -> None:
        if self._pipeline and self._playing:
            self._pipeline.set_state(Gst.State.PAUSED)
        self._playing = False
        self._sync_button()

    def toggle(self) -> None:
        self.pause() if self._playing else self.resume()

    def status(self) -> str:
        """Debug summary: state, position and duration."""
        if not self._pipeline:
            return "no pipeline"
        _ret, state, _pending = self._pipeline.get_state(0)
        ok_p, position = self._pipeline.query_position(Gst.Format.TIME)
        ok_d, duration = self._pipeline.query_duration(Gst.Format.TIME)
        return f"state={state.value_nick} pos={position / Gst.SECOND if ok_p else 0:.1f}s dur={duration / Gst.SECOND if ok_d else 0:.1f}s"

    def release(self) -> None:
        if self._ticker:
            GLib.source_remove(self._ticker)
            self._ticker = None
        if self._pipeline:
            self._pipeline.get_bus().remove_signal_watch()
            self._pipeline.set_state(Gst.State.NULL)
            self._pipeline = None


class MediaViewer(Adw.NavigationPage):
    def __init__(self, items: list[dict], index: int, post_url: str | None):
        super().__init__(title="Media", tag="media")
        self.add_css_class("media-viewer")
        self._items = items

        self._carousel = Adw.Carousel(hexpand=True, vexpand=True, allow_scroll_wheel=False)
        self._pages = [(_VideoPage if m.get("type") in ("VIDEO", "GIF") else _PhotoPage)(m) for m in items]
        for page in self._pages:
            self._carousel.append(page)
        self._carousel.connect("page-changed", lambda _c, i: self._on_page_changed(i))

        self._title = Adw.WindowTitle(title="Media")
        header = Adw.HeaderBar(title_widget=self._title)
        header.add_css_class("osd-header")
        if post_url:
            open_post = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open Post in Browser")
            open_post.connect("clicked", lambda *_: Gtk.UriLauncher(uri=post_url).launch(self.get_root(), None, None, None))
            header.pack_end(open_post)

        # Previous/next float over the image edges (as in GNOME's image viewer) so the header keeps
        # a single back arrow — the page's own — instead of a second, identical-looking one.
        stage = Gtk.Overlay(child=self._carousel)
        self._prev = self._edge_button("go-previous-symbolic", "Previous (Left)", Gtk.Align.START, -1)
        self._next = self._edge_button("go-next-symbolic", "Next (Right)", Gtk.Align.END, 1)
        if len(items) > 1:
            stage.add_overlay(self._prev)
            stage.add_overlay(self._next)

        view = Adw.ToolbarView(content=stage, extend_content_to_top_edge=False)
        view.add_top_bar(header)
        if len(items) > 1:
            view.add_bottom_bar(Adw.CarouselIndicatorDots(carousel=self._carousel, margin_bottom=6))
        self.set_child(view)

        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("hidden", lambda *_: self._release())
        self.connect("shown", lambda *_: self._carousel.grab_focus())

        # Adw.Carousel ignores scroll_to() until it has been allocated, and the page is built before it
        # is pushed, so jump to the clicked item on the first frame where the carousel has a width.
        self._initial_index = index
        self._on_page_changed(index)
        self._carousel.add_tick_callback(self._scroll_to_initial)

    def _edge_button(self, icon: str, tooltip: str, side: Gtk.Align, delta: int) -> Gtk.Button:
        button = Gtk.Button(icon_name=icon, tooltip_text=tooltip, halign=side, valign=Gtk.Align.CENTER, margin_start=12, margin_end=12)
        button.add_css_class("osd")
        button.add_css_class("circular")
        button.add_css_class("media-nav")
        button.connect("clicked", lambda *_: self._step(delta))
        return button

    def _scroll_to_initial(self, carousel: Adw.Carousel, _clock) -> bool:
        if carousel.get_width() <= 0 or carousel.get_n_pages() <= self._initial_index:
            return GLib.SOURCE_CONTINUE
        carousel.scroll_to(self._pages[self._initial_index], False)
        self._on_page_changed(self._initial_index)
        return GLib.SOURCE_REMOVE

    def _step(self, delta: int) -> None:
        position = round(self._carousel.get_position()) + delta
        if 0 <= position < len(self._pages):
            self._carousel.scroll_to(self._pages[position], True)

    def _on_page_changed(self, index: int) -> bool:
        for i, page in enumerate(self._pages):
            if i != index:
                page.pause()
        current = self._pages[index]
        if current.is_video:
            current.resume()
        kind = {"VIDEO": "Video", "GIF": "GIF"}.get(self._items[index].get("type"), "Photo")
        self._title.set_title(kind)
        self._title.set_subtitle(f"{index + 1} of {len(self._pages)}" if len(self._pages) > 1 else "")
        self._prev.set_visible(index > 0)
        self._next.set_visible(index < len(self._pages) - 1)
        return GLib.SOURCE_REMOVE

    def _on_key(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self._step(-1)
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self._step(1)
            return True
        if keyval == Gdk.KEY_space:
            self._pages[round(self._carousel.get_position())].toggle()
            return True
        if keyval == Gdk.KEY_Escape:
            if nav := self.get_ancestor(Adw.NavigationView):
                nav.pop()
            return True
        return False

    def _release(self) -> None:
        for page in self._pages:
            if page.is_video:
                page.release()
