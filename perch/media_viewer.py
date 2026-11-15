"""Full-window media viewer: swipeable gallery of a post's photos, videos and GIFs."""

import gi

gi.require_version("Gst", "1.0")

from gi.repository import Adw, Gdk, GLib, GObject, Gst, Gtk  # noqa: E402

from perch import format as fmt  # noqa: E402
from perch import images  # noqa: E402


def full_size_url(media: dict) -> str | None:
    url = media.get("url") or ""
    if media.get("type") == "PHOTO" and "pbs.twimg.com/media/" in url:
        return f"{url}?name=large"  # up to 2048px; "orig" can be huge to decode
    return url or None


MAX_ZOOM = 8.0  # relative to fit-to-window
ZOOM_STEP = 1.25


class _PhotoPage(Gtk.Overlay):
    """A photo that fits the window and can be zoomed (Ctrl+scroll, pinch, double-click, keys) and
    panned (drag / scroll) while zoomed. ``zoom`` is relative to fit-to-window: 1.0 means fitted."""

    __gsignals__ = {"zoom-changed": (GObject.SignalFlags.RUN_FIRST, None, ())}

    def __init__(self, media: dict):
        super().__init__(hexpand=True, vexpand=True)
        self.is_video = False
        self.zoom = 1.0
        self._pointer = None  # last pointer position in scroller coordinates
        self._pending_scroll = None

        self._picture = Gtk.Picture(content_fit=Gtk.ContentFit.CONTAIN, can_shrink=True, hexpand=True, vexpand=True)
        self._scroller = Gtk.ScrolledWindow(child=self._picture, hexpand=True, vexpand=True)
        self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
        self.set_child(self._scroller)
        self._spinner = Adw.Spinner(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, width_request=32, height_request=32)
        self.add_overlay(self._spinner)
        self._install_gestures()

        # Show the cached thumbnail at once, then swap in the large version.
        images.load(fmt.media_thumb(media), lambda tex: tex and not self._picture.get_paintable() and self._picture.set_paintable(tex))
        images.load(full_size_url(media), self._on_full, cache=False)

    def _on_full(self, texture) -> None:
        self._spinner.set_visible(False)
        if texture is not None:
            zoom, self.zoom = self.zoom, 1.0
            self._picture.set_paintable(texture)
            if zoom > 1.0:  # the thumbnail was zoomed: keep the same zoom on the sharper image
                self.set_zoom(zoom)

    # -- gestures -------------------------------------------------------------

    def _install_gestures(self) -> None:
        motion = Gtk.EventControllerMotion()
        motion.connect("motion", lambda _c, x, y: setattr(self, "_pointer", (x, y)))
        motion.connect("leave", lambda _c: setattr(self, "_pointer", None))
        self._scroller.add_controller(motion)

        # Capture phase so Ctrl+scroll zooms before the ScrolledWindow scrolls.
        wheel = Gtk.EventControllerScroll(flags=Gtk.EventControllerScrollFlags.VERTICAL)
        wheel.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        wheel.connect("scroll", self._on_scroll)
        self._scroller.add_controller(wheel)

        double = Gtk.GestureClick(button=Gdk.BUTTON_PRIMARY)
        double.connect("pressed", self._on_pressed)
        self._scroller.add_controller(double)

        pinch = Gtk.GestureZoom()
        pinch.connect("begin", self._on_pinch_begin)
        pinch.connect("scale-changed", self._on_pinch)
        self._scroller.add_controller(pinch)

        drag = Gtk.GestureDrag(button=Gdk.BUTTON_PRIMARY)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", lambda *_: self._set_cursor("grab" if self.zoom > 1.0 else None))
        self._scroller.add_controller(drag)

    def _on_scroll(self, controller, _dx: float, dy: float) -> bool:
        if not controller.get_current_event_state() & Gdk.ModifierType.CONTROL_MASK:
            return False  # plain scroll pans while zoomed (handled by the ScrolledWindow)
        self.set_zoom(self.zoom * (ZOOM_STEP ** -dy), self._pointer)
        return True

    def _on_pressed(self, _gesture, n_press: int, x: float, y: float) -> None:
        if n_press != 2:
            return
        if self.zoom > 1.0:
            self.set_zoom(1.0)
        else:
            # Double-click a fitted photo: show it at 1:1 pixels (at least 2x if it's small).
            fit = self._fit_scale()
            self.set_zoom(max(2.0, 1.0 / fit) if fit else 2.0, (x, y))

    def _on_pinch_begin(self, gesture, _sequence) -> None:
        self._pinch_start = self.zoom
        ok, x, y = gesture.get_bounding_box_center()
        self._pinch_center = (x, y) if ok else None

    def _on_pinch(self, _gesture, scale: float) -> None:
        self.set_zoom(self._pinch_start * scale, self._pinch_center)

    def _on_drag_begin(self, gesture, _x, _y) -> None:
        if self.zoom <= 1.0:
            gesture.set_state(Gtk.EventSequenceState.DENIED)  # let the carousel swipe
            return
        self._drag_origin = (self._scroller.get_hadjustment().get_value(), self._scroller.get_vadjustment().get_value())
        self._set_cursor("grabbing")

    def _on_drag_update(self, _gesture, dx: float, dy: float) -> None:
        if self.zoom > 1.0:
            start_h, start_v = self._drag_origin
            self._scroller.get_hadjustment().set_value(start_h - dx)
            self._scroller.get_vadjustment().set_value(start_v - dy)

    def _set_cursor(self, name: str | None) -> None:
        self._scroller.set_cursor(Gdk.Cursor.new_from_name(name, None) if name else None)

    # -- zoom -----------------------------------------------------------------

    def _image_size(self) -> tuple[int, int]:
        paintable = self._picture.get_paintable()
        if paintable is None:
            return 0, 0
        return paintable.get_intrinsic_width(), paintable.get_intrinsic_height()

    def _fit_scale(self) -> float:
        width, height = self._image_size()
        view_w, view_h = self._scroller.get_width(), self._scroller.get_height()
        if not (width and height and view_w and view_h):
            return 0.0
        return min(view_w / width, view_h / height)

    @property
    def scale_percent(self) -> int:
        """Zoom as a percentage of the image's real pixels (what a viewer usually shows)."""
        return round(self._fit_scale() * self.zoom * 100)

    def zoom_by(self, factor: float) -> None:
        self.set_zoom(self.zoom * factor, self._pointer)

    def reset_zoom(self) -> None:
        self.set_zoom(1.0)

    def set_zoom(self, zoom: float, anchor: tuple[float, float] | None = None) -> None:
        zoom = max(1.0, min(MAX_ZOOM, zoom))
        fit = self._fit_scale()
        if not fit or abs(zoom - self.zoom) < 1e-3:
            return
        width, height = self._image_size()
        view_w, view_h = self._scroller.get_width(), self._scroller.get_height()
        hadj, vadj = self._scroller.get_hadjustment(), self._scroller.get_vadjustment()
        ax, ay = anchor or (view_w / 2, view_h / 2)

        # Image point under the anchor, in image pixels, before the change.
        old_scale = fit * self.zoom
        old_ox = max(0.0, (view_w - width * old_scale) / 2) if self.zoom > 1.0 else (view_w - width * old_scale) / 2
        old_oy = max(0.0, (view_h - height * old_scale) / 2) if self.zoom > 1.0 else (view_h - height * old_scale) / 2
        scroll_x = hadj.get_value() if self.zoom > 1.0 else 0.0
        scroll_y = vadj.get_value() if self.zoom > 1.0 else 0.0
        px = (scroll_x + ax - old_ox) / old_scale
        py = (scroll_y + ay - old_oy) / old_scale

        self.zoom = zoom
        if zoom <= 1.0:
            self._pending_scroll = None
            self._picture.set_size_request(-1, -1)
            self._picture.set_content_fit(Gtk.ContentFit.CONTAIN)
            self._picture.set_can_shrink(True)
            self._picture.set_halign(Gtk.Align.FILL)
            self._picture.set_valign(Gtk.Align.FILL)
            self._scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.NEVER)
            self._set_cursor(None)
        else:
            scale = fit * zoom
            new_w, new_h = round(width * scale), round(height * scale)
            self._picture.set_content_fit(Gtk.ContentFit.FILL)
            self._picture.set_size_request(new_w, new_h)
            self._picture.set_halign(Gtk.Align.CENTER)
            self._picture.set_valign(Gtk.Align.CENTER)
            self._scroller.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
            self._set_cursor("grab")
            # Keep the anchored image point under the anchor once the new size has been laid out.
            new_ox = max(0.0, (view_w - new_w) / 2)
            new_oy = max(0.0, (view_h - new_h) / 2)
            self._pending_scroll = (px * scale + new_ox - ax, py * scale + new_oy - ay, new_w, new_h)
            self._scroller.add_tick_callback(self._apply_pending_scroll)
        self.emit("zoom-changed")

    def _apply_pending_scroll(self, _widget, _clock) -> bool:
        if self._pending_scroll is None:
            return GLib.SOURCE_REMOVE
        target_x, target_y, new_w, new_h = self._pending_scroll
        hadj, vadj = self._scroller.get_hadjustment(), self._scroller.get_vadjustment()
        # Content extent becomes max(image, viewport) once laid out at the new size; until then a
        # set_value() would be clamped to the old range.
        if hadj.get_upper() < new_w - 1 or vadj.get_upper() < new_h - 1:
            return GLib.SOURCE_CONTINUE
        hadj.set_value(target_x)
        vadj.set_value(target_y)
        self._pending_scroll = None
        return GLib.SOURCE_REMOVE

    # -- page interface -------------------------------------------------------

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
            if not page.is_video:
                page.connect("zoom-changed", lambda p: self._on_zoom_changed(p))
        self._carousel.connect("page-changed", lambda _c, i: self._on_page_changed(i))

        self._title = Adw.WindowTitle(title="Media")
        header = Adw.HeaderBar(title_widget=self._title)
        header.add_css_class("osd-header")
        if post_url:
            open_post = Gtk.Button(icon_name="web-browser-symbolic", tooltip_text="Open Post in Browser")
            open_post.connect("clicked", lambda *_: Gtk.UriLauncher(uri=post_url).launch(self.get_root(), None, None, None))
            header.pack_end(open_post)

        # Zoom controls (photos only); pack_end adds right-to-left, so this reads [−] [fit] [+].
        self._zoom_box = Gtk.Box(spacing=2)
        self._zoom_out = Gtk.Button(icon_name="zoom-out-symbolic", tooltip_text="Zoom Out (−)")
        self._zoom_out.connect("clicked", lambda *_: self._current_photo() and self._current_photo().zoom_by(1 / ZOOM_STEP))
        self._zoom_fit = Gtk.Button(icon_name="zoom-fit-best-symbolic", tooltip_text="Fit to Window (0)")
        self._zoom_fit.connect("clicked", lambda *_: self._current_photo() and self._current_photo().reset_zoom())
        self._zoom_in = Gtk.Button(icon_name="zoom-in-symbolic", tooltip_text="Zoom In (+)")
        self._zoom_in.connect("clicked", lambda *_: self._current_photo() and self._current_photo().zoom_by(ZOOM_STEP))
        for button in (self._zoom_out, self._zoom_fit, self._zoom_in):
            self._zoom_box.append(button)
        header.pack_end(self._zoom_box)

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

        # The page itself holds keyboard focus: the carousel stops being focusable while zoomed
        # (interactive=False), and focus would otherwise drop out of the viewer, taking keys with it.
        self.set_focusable(True)
        keys = Gtk.EventControllerKey()
        keys.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        keys.connect("key-pressed", self._on_key)
        self.add_controller(keys)
        self.connect("hidden", lambda *_: self._release())
        self.connect("shown", lambda *_: self.grab_focus())

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

    def _current_index(self) -> int:
        return max(0, min(len(self._pages) - 1, round(self._carousel.get_position())))

    def _current_photo(self) -> "_PhotoPage | None":
        page = self._pages[self._current_index()]
        return None if page.is_video else page

    def _on_page_changed(self, index: int) -> bool:
        for i, page in enumerate(self._pages):
            if i != index:
                page.pause()
                if not page.is_video:
                    page.reset_zoom()  # leaving a page drops its zoom, like other viewers
        current = self._pages[index]
        if current.is_video:
            current.resume()
        kind = {"VIDEO": "Video", "GIF": "GIF"}.get(self._items[index].get("type"), "Photo")
        self._title.set_title(kind)
        self._prev.set_visible(index > 0)
        self._next.set_visible(index < len(self._pages) - 1)
        self._zoom_box.set_visible(not current.is_video)
        self._sync_zoom_ui(index)
        return GLib.SOURCE_REMOVE

    def _on_zoom_changed(self, page: "_PhotoPage") -> None:
        if page is self._pages[self._current_index()]:
            self._sync_zoom_ui(self._current_index())

    def _sync_zoom_ui(self, index: int) -> None:
        page = self._pages[index]
        zoomed = not page.is_video and page.zoom > 1.0
        # While zoomed, dragging pans the photo, so the carousel must not swipe.
        self._carousel.set_interactive(not zoomed)
        root = self.get_root()
        if root is not None and root.get_focus() is None:
            self.grab_focus()
        parts = [f"{index + 1} of {len(self._pages)}"] if len(self._pages) > 1 else []
        if zoomed:
            parts.append(f"{page.scale_percent}%")
        self._title.set_subtitle(" · ".join(parts))
        if not page.is_video:
            self._zoom_out.set_sensitive(zoomed)
            self._zoom_fit.set_sensitive(zoomed)
            self._zoom_in.set_sensitive(page.zoom < MAX_ZOOM - 1e-3)

    def _on_key(self, _controller, keyval, _keycode, _state) -> bool:
        if keyval in (Gdk.KEY_Left, Gdk.KEY_KP_Left):
            self._step(-1)
            return True
        if keyval in (Gdk.KEY_Right, Gdk.KEY_KP_Right):
            self._step(1)
            return True
        if keyval == Gdk.KEY_space:
            self._pages[self._current_index()].toggle()
            return True
        if photo := self._current_photo():
            if keyval in (Gdk.KEY_plus, Gdk.KEY_equal, Gdk.KEY_KP_Add):
                photo.zoom_by(ZOOM_STEP)
                return True
            if keyval in (Gdk.KEY_minus, Gdk.KEY_KP_Subtract):
                photo.zoom_by(1 / ZOOM_STEP)
                return True
            if keyval in (Gdk.KEY_0, Gdk.KEY_KP_0):
                photo.reset_zoom()
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
