"""Compose dialog for new posts, replies and quotes (text + up to 4 images)."""

from pathlib import Path

from gi.repository import Adw, Gdk, Gio, GLib, Gtk, Pango

from perch import format as fmt
from perch.tweet_row import quoted_card

MAX_CHARS = 280
MAX_IMAGES = 4
MAX_ALT_CHARS = 1000  # X's limit for an image description


class ComposeDialog(Adw.Dialog):
    def __init__(self, controller, reply_to: dict | None = None, quote: dict | None = None):
        title = "Reply" if reply_to else "Quote" if quote else "New Post"
        super().__init__(title=title, content_width=520, content_height=440)
        self._controller = controller
        self._reply_to = reply_to
        self._quote = quote
        self._images: list[str] = []
        self._alts: dict[str, str] = {}  # image path -> description (alt text)
        # A thread that stopped partway: the last post that went out, and the thread's first post.
        self._thread_parent: str | None = None
        self._thread_first: str | None = None
        self._thread_total = 1

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self._post = Gtk.Button(label="Reply" if reply_to else "Post", sensitive=False)
        self._post.add_css_class("suggested-action")
        self._post.connect("clicked", lambda *_: self._submit())
        header.pack_end(self._post)
        self._spinner = Adw.Spinner(visible=False)
        header.pack_end(self._spinner)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, margin_top=12, margin_bottom=12, margin_start=16, margin_end=16)

        if reply_to:
            handle = (reply_to.get("tweetBy") or {}).get("userName", "")
            context = Gtk.Label(xalign=0, wrap=True, ellipsize=Pango.EllipsizeMode.END, lines=2)
            context.add_css_class("dim-label")
            context.set_markup(
                f"Replying to <b>@{GLib.markup_escape_text(handle)}</b>: "
                f"{GLib.markup_escape_text((reply_to.get('fullText') or '')[:140])}"
            )
            body.append(context)

        self._error = Adw.Banner(revealed=False, use_markup=False)
        body.append(self._error)

        self._text = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, vexpand=True)
        self._text.add_css_class("compose-text")
        self._text.get_buffer().connect("changed", lambda *_: self._update_state())
        placeholder = Gtk.Label(
            label="Add your reply" if reply_to else "Add a comment" if quote else "What's happening?",
            xalign=0, yalign=0, can_target=False,
        )
        placeholder.add_css_class("dim-label")
        placeholder.add_css_class("compose-placeholder")
        self._placeholder = placeholder
        overlay = Gtk.Overlay(child=self._text)
        overlay.add_overlay(placeholder)
        scroller = Gtk.ScrolledWindow(child=overlay, vexpand=True, min_content_height=120, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("card")
        # The first post, its quote / images, then any further posts of a thread, all scrolling together.
        posts = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        posts.append(scroller)

        if quote:
            posts.append(quoted_card(quote))

        self._thumbs = Gtk.Box(spacing=8)
        posts.append(self._thumbs)
        self._extras: list[dict] = []  # further posts of a thread: {"view", "counter", "box"}
        self._extras_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        posts.append(self._extras_box)
        self._posts_scroller = Gtk.ScrolledWindow(child=posts, vexpand=True, hscrollbar_policy=Gtk.PolicyType.NEVER)
        body.append(self._posts_scroller)

        # "Will post tomorrow at 09:00" with a button to go back to posting now.
        self._schedule_info = Gtk.Box(spacing=6, visible=False, css_classes=["schedule-info"])
        self._schedule_info.append(Gtk.Image(icon_name="alarm-symbolic"))
        self._schedule_label = Gtk.Label(xalign=0, hexpand=True)
        self._schedule_info.append(self._schedule_label)
        unschedule = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="Post Now Instead")
        unschedule.add_css_class("flat")
        unschedule.add_css_class("circular")
        unschedule.connect("clicked", lambda *_: self._set_schedule(None))
        self._schedule_info.append(unschedule)
        body.append(self._schedule_info)

        footer = Gtk.Box(spacing=8)
        self._add_image = Gtk.Button(icon_name="insert-image-symbolic", tooltip_text="Add Images")
        self._add_image.add_css_class("flat")
        self._add_image.connect("clicked", lambda *_: self._pick_images())
        footer.append(self._add_image)
        # X can schedule new posts only, not replies or quotes.
        self._schedule_at: int | None = None  # unix seconds
        if not reply_to and not quote:
            self._schedule_button = Gtk.MenuButton(icon_name="alarm-symbolic", tooltip_text="Schedule", popover=self._build_schedule_popover())
            self._schedule_button.add_css_class("flat")
            footer.append(self._schedule_button)
        self._add_post = Gtk.Button(icon_name="list-add-symbolic", tooltip_text="Add Another Post (Thread)")
        self._add_post.add_css_class("flat")
        self._add_post.connect("clicked", lambda *_: self._add_extra())
        footer.append(self._add_post)
        footer.append(Gtk.Box(hexpand=True))
        self._counter = Gtk.Label(css_classes=["numeric", "dim-label"])
        footer.append(self._counter)
        body.append(footer)

        view = Adw.ToolbarView(content=body)
        view.add_top_bar(header)
        self.set_child(view)

        # Capture phase: the text view would otherwise take Ctrl+Return before it bubbles up here.
        send = Gtk.ShortcutController(propagation_phase=Gtk.PropagationPhase.CAPTURE)
        send.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string("<Control>Return"), Gtk.CallbackAction.new(lambda *_: self._submit() or True)))
        self.add_controller(send)
        self.set_focus(self._text)
        self._update_state()

    # -- state --------------------------------------------------------------

    def _get_text(self) -> str:
        buffer = self._text.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _update_state(self) -> None:
        text = self._get_text()
        used = fmt.weighted_length(text)
        self._placeholder.set_visible(not text)
        self._counter.set_label(f"{used}/{MAX_CHARS}")
        (self._counter.add_css_class if used > MAX_CHARS else self._counter.remove_css_class)("error")
        has_content = bool(text.strip()) or bool(self._images)
        # Every further post of a thread needs text, within the limit.
        extras_ok = True
        for extra in self._extras:
            extra_text = self._extra_text(extra)
            extra_used = fmt.weighted_length(extra_text)
            extra["counter"].set_label(f"{extra_used}/{MAX_CHARS}")
            (extra["counter"].add_css_class if extra_used > MAX_CHARS else extra["counter"].remove_css_class)("error")
            extra["placeholder"].set_visible(not extra_text)
            extras_ok = extras_ok and bool(extra_text.strip()) and extra_used <= MAX_CHARS
        busy = self._spinner.get_visible()
        self._post.set_sensitive(has_content and used <= MAX_CHARS and extras_ok and not busy)
        self._add_image.set_sensitive(len(self._images) < MAX_IMAGES)
        # A thread can't be scheduled (X schedules single posts), so the two exclude each other.
        self._add_post.set_sensitive(self._schedule_at is None and not busy)
        if hasattr(self, "_schedule_button"):
            self._schedule_button.set_sensitive(not self._extras and not busy)
        if self._schedule_at is None:
            if not busy:
                self._post.set_label("Post the Rest" if self._thread_parent else "Post All" if self._extras else "Reply" if self._reply_to else "Post")

    # -- thread -------------------------------------------------------------

    @staticmethod
    def _extra_text(extra: dict) -> str:
        buffer = extra["view"].get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _add_extra(self, text: str = "") -> None:
        """Another post below, posted as a reply to the one above it (a thread)."""
        view = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False)
        view.add_css_class("compose-text")
        view.get_buffer().set_text(text)
        view.get_buffer().connect("changed", lambda *_: self._update_state())
        placeholder = Gtk.Label(label="Add another post", xalign=0, yalign=0, can_target=False, css_classes=["dim-label", "compose-placeholder"])
        overlay = Gtk.Overlay(child=view)
        overlay.add_overlay(placeholder)
        scroller = Gtk.ScrolledWindow(child=overlay, min_content_height=80, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("card")
        counter = Gtk.Label(css_classes=["numeric", "dim-label", "caption"], hexpand=True, xalign=1)
        remove = Gtk.Button(icon_name="user-trash-symbolic", tooltip_text="Remove This Post")
        remove.add_css_class("flat")
        remove.add_css_class("circular")
        row = Gtk.Box(spacing=6)
        row.append(counter)
        row.append(remove)
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(scroller)
        box.append(row)
        extra = {"view": view, "counter": counter, "box": box, "placeholder": placeholder}
        remove.connect("clicked", lambda *_: self._remove_extra(extra))
        self._extras.append(extra)
        self._extras_box.append(box)
        self._update_state()
        view.grab_focus()

        def reveal():
            adjustment = self._posts_scroller.get_vadjustment()
            adjustment.set_value(adjustment.get_upper())
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(100, reveal)

    def _remove_extra(self, extra: dict) -> None:
        self._extras.remove(extra)
        self._extras_box.remove(extra["box"])
        self._update_state()

    # -- images -------------------------------------------------------------

    def _pick_images(self) -> None:
        image_filter = Gtk.FileFilter(name="Images")
        for mime in ("image/png", "image/jpeg", "image/gif", "image/webp"):
            image_filter.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        dialog = Gtk.FileDialog(title="Add Images", filters=filters, default_filter=image_filter)

        def done(source, result):
            try:
                files = source.open_multiple_finish(result)
            except GLib.Error:
                return  # cancelled
            for index in range(files.get_n_items()):
                path = files.get_item(index).get_path()
                if path and len(self._images) < MAX_IMAGES and path not in self._images:
                    self._images.append(path)
            self._render_thumbs()

        dialog.open_multiple(self.get_root(), None, done)

    def _render_thumbs(self) -> None:
        while child := self._thumbs.get_first_child():
            self._thumbs.remove(child)
        for path in self._images:
            picture = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, width_request=88, height_request=88, overflow=Gtk.Overflow.HIDDEN)
            picture.add_css_class("media")
            try:
                picture.set_paintable(Gdk.Texture.new_from_filename(path))
            except GLib.Error:
                picture.set_tooltip_text(Path(path).name)
            remove = Gtk.Button(icon_name="window-close-symbolic", halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=4, margin_end=4, tooltip_text="Remove")
            remove.add_css_class("osd")
            remove.add_css_class("circular")
            remove.connect("clicked", lambda *_, p=path: (self._images.remove(p), self._alts.pop(p, None), self._render_thumbs()))
            alt = Gtk.Button(label="ALT", halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=4, margin_bottom=4)
            alt.set_tooltip_text("Edit description" if self._alts.get(path) else "Add a description (alt text)")
            for css in ("osd", "alt-badge"):
                alt.add_css_class(css)
            if self._alts.get(path):
                alt.add_css_class("has-alt")
            alt.connect("clicked", lambda *_, p=path: self._edit_alt(p))
            overlay = Gtk.Overlay(child=picture)
            overlay.add_overlay(remove)
            overlay.add_overlay(alt)
            self._thumbs.append(overlay)
        self._update_state()

    def _edit_alt(self, path: str) -> None:
        """Describe an image for people who use screen readers (X's "alt text")."""
        buffer = Gtk.TextBuffer(text=self._alts.get(path, ""))
        text = Gtk.TextView(buffer=buffer, wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=8, bottom_margin=8, left_margin=8, right_margin=8)
        text.add_css_class("compose-text")
        scroller = Gtk.ScrolledWindow(child=text, min_content_height=120, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("card")
        counter = Gtk.Label(xalign=1, css_classes=["numeric", "dim-label", "caption"])
        extra = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        extra.append(scroller)
        extra.append(counter)

        dialog = Adw.AlertDialog(heading="Image Description", body=f"Describe {Path(path).name} for people who can't see it.", extra_child=extra)
        dialog.add_response("cancel", "Cancel")
        dialog.add_response("save", "Save")
        dialog.set_response_appearance("save", Adw.ResponseAppearance.SUGGESTED)
        dialog.set_default_response("save")
        dialog.set_close_response("cancel")

        def current() -> str:
            return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

        def sync(*_):
            used = len(current())
            counter.set_label(f"{used}/{MAX_ALT_CHARS}")
            (counter.add_css_class if used > MAX_ALT_CHARS else counter.remove_css_class)("error")
            dialog.set_response_enabled("save", used <= MAX_ALT_CHARS)

        def on_response(_dialog, response):
            if response != "save":
                return
            value = current().strip()
            if value:
                self._alts[path] = value
            else:
                self._alts.pop(path, None)
            self._render_thumbs()

        buffer.connect("changed", sync)
        sync()
        dialog.connect("response", on_response)
        dialog.present(self)
        text.grab_focus()

    # -- scheduling ---------------------------------------------------------

    def _build_schedule_popover(self) -> Gtk.Popover:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10, margin_top=6, margin_bottom=6, margin_start=6, margin_end=6)
        box.append(Gtk.Label(label="Schedule Post", css_classes=["heading"]))
        self._calendar = Gtk.Calendar()
        box.append(self._calendar)

        time_row = Gtk.Box(spacing=4, halign=Gtk.Align.CENTER)
        time_row.append(Gtk.Label(label="Time", margin_end=8, css_classes=["dim-label"]))
        self._hour = self._time_spin(23, "Hour")
        self._minute = self._time_spin(59, "Minute")
        time_row.append(self._hour)
        time_row.append(Gtk.Label(label=":"))
        time_row.append(self._minute)
        box.append(time_row)

        self._schedule_hint = Gtk.Label(wrap=True, max_width_chars=28, css_classes=["caption", "dim-label"])
        box.append(self._schedule_hint)
        confirm = Gtk.Button(label="Set Time", css_classes=["suggested-action"])
        confirm.connect("clicked", lambda *_: self._confirm_schedule())
        box.append(confirm)
        self._schedule_confirm = confirm

        popover = Gtk.Popover(child=box)
        popover.connect("show", lambda *_: self._prefill_schedule())
        for widget, signal in ((self._calendar, "day-selected"), (self._hour, "value-changed"), (self._minute, "value-changed")):
            widget.connect(signal, lambda *_: self._sync_schedule_hint())
        self._schedule_popover = popover
        return popover

    @staticmethod
    def _time_spin(upper: int, name: str) -> Gtk.SpinButton:
        spin = Gtk.SpinButton(adjustment=Gtk.Adjustment(lower=0, upper=upper, step_increment=1, page_increment=10), numeric=True, wrap=True, width_chars=2, orientation=Gtk.Orientation.VERTICAL, tooltip_text=name)

        def two_digits(button):
            button.set_text(f"{int(button.get_value()):02d}")
            return True

        spin.connect("output", two_digits)
        return spin

    def _picked_time(self) -> int:
        day = self._calendar.get_date()
        stamp = GLib.DateTime.new_local(day.get_year(), day.get_month(), day.get_day_of_month(), int(self._hour.get_value()), int(self._minute.get_value()), 0)
        return stamp.to_unix()

    def _prefill_schedule(self) -> None:
        # The chosen time, or an hour from now rounded up to the next 5 minutes.
        if self._schedule_at:
            stamp = GLib.DateTime.new_from_unix_local(self._schedule_at)
        else:
            now = GLib.DateTime.new_now_local().add_minutes(60)
            stamp = now.add_minutes((5 - now.get_minute() % 5) % 5)
        self._calendar.select_day(stamp)
        self._hour.set_value(stamp.get_hour())
        self._minute.set_value(stamp.get_minute())
        self._sync_schedule_hint()

    def _schedule_problem(self, at: int) -> str | None:
        if at < GLib.DateTime.new_now_local().to_unix() + 120:
            return "Pick a time at least a few minutes from now."
        return None

    def _sync_schedule_hint(self) -> None:
        at = self._picked_time()
        problem = self._schedule_problem(at)
        self._schedule_hint.set_label(problem or f"Posts {fmt.schedule_phrase(at)}")
        (self._schedule_hint.add_css_class if problem else self._schedule_hint.remove_css_class)("error")
        self._schedule_confirm.set_sensitive(problem is None)

    def _confirm_schedule(self) -> None:
        at = self._picked_time()
        if self._schedule_problem(at) is None:
            self._set_schedule(at)
            self._schedule_popover.popdown()

    def _set_schedule(self, at: int | None) -> None:
        self._schedule_at = at
        self._schedule_info.set_visible(at is not None)
        if at is not None:
            self._schedule_label.set_label(f"Will post {fmt.schedule_phrase(at)}")
            self._schedule_button.add_css_class("accent")
        elif hasattr(self, "_schedule_button"):
            self._schedule_button.remove_css_class("accent")
        self._post.set_label("Schedule" if at is not None else "Post")
        self._update_state()

    # -- submit -------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._spinner.set_visible(busy)
        self._text.set_editable(not busy)
        self._update_state()

    def _submit(self) -> None:
        if not self._post.get_sensitive():
            return
        self._error.set_revealed(False)
        if self._schedule_at is not None:
            self._submit_scheduled()
            return
        self._set_busy(True)
        if not self._thread_parent:
            self._thread_total = 1 + len(self._extras)
        params = {
            "text": self._get_text().strip(),
            "replyTo": (self._reply_to or {}).get("id"),
            "quote": (self._quote or {}).get("id"),
            "media": [{"path": path, "alt": self._alts.get(path, "")} for path in self._images],
        }

        if self._thread_parent:
            # Resuming a thread that stopped partway: the first post is already on X.
            self._post_rest(self._thread_parent, self._thread_first)
            return
        total = 1 + len(self._extras)
        if total > 1:
            self._post.set_label(f"Posting 1/{total}…")

        def done(result, error):
            if error:
                self._set_busy(False)
                self._error.set_title(self._controller.describe_error(error, "Couldn't post"))
                self._error.set_revealed(True)
                return
            first_id = result.get("id")
            if not self._extras:
                self._set_busy(False)
                self.close()
                self._controller.on_posted(first_id, (self._reply_to or {}).get("id"))
                return
            # The first post is out: it can't be edited any more, so lock it and its images.
            self._thread_first = first_id
            self._text.set_sensitive(False)
            self._add_image.set_visible(False)
            self._thumbs.set_sensitive(False)
            self._post_rest(first_id, first_id)

        self._controller.sidecar.call("tweet.post", params, done)

    def _post_rest(self, parent_id: str, first_id: str) -> None:
        """Post the thread's further posts in order, each replying to the one before."""
        if not self._extras:
            self._set_busy(False)
            self.close()
            self._controller.on_posted(first_id, (self._reply_to or {}).get("id"))
            return
        extra = self._extras[0]
        done_count = self._thread_total - len(self._extras)
        self._post.set_label(f"Posting {done_count + 1}/{self._thread_total}…")

        def done(result, error):
            if error:
                self._thread_parent = parent_id
                self._set_busy(False)
                self._post.set_label("Post the Rest")
                message = self._controller.describe_error(error, "")
                self._error.set_title(f"Posted {done_count} of {self._thread_total}. The rest didn't go out: {message}")
                self._error.set_revealed(True)
                return
            self._remove_extra(extra)
            self._set_busy(True)
            self._post_rest(result.get("id"), first_id)

        self._controller.sidecar.call("tweet.post", {"text": self._extra_text(extra).strip(), "replyTo": parent_id}, done)

    def _submit_scheduled(self) -> None:
        at = self._schedule_at
        if problem := self._schedule_problem(at):
            self._error.set_title(problem)
            self._error.set_revealed(True)
            return
        self._set_busy(True)
        params = {
            "text": self._get_text().strip(),
            "media": [{"path": path, "alt": self._alts.get(path, "")} for path in self._images],
            "at": at,
        }

        def done(_result, error):
            self._set_busy(False)
            if error:
                self._error.set_title(self._controller.describe_error(error, "Couldn't schedule the post"))
                self._error.set_revealed(True)
                return
            self.close()
            self._controller.on_scheduled(at)

        self._controller.sidecar.call("tweet.schedule", params, done)
