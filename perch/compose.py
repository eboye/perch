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

        self._error = Adw.Banner(revealed=False)
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
        body.append(scroller)

        if quote:
            body.append(quoted_card(quote))

        self._thumbs = Gtk.Box(spacing=8)
        body.append(self._thumbs)

        footer = Gtk.Box(spacing=8)
        self._add_image = Gtk.Button(icon_name="insert-image-symbolic", tooltip_text="Add Images")
        self._add_image.add_css_class("flat")
        self._add_image.connect("clicked", lambda *_: self._pick_images())
        footer.append(self._add_image)
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
        self._post.set_sensitive(has_content and used <= MAX_CHARS and not self._spinner.get_visible())
        self._add_image.set_sensitive(len(self._images) < MAX_IMAGES)

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

    # -- submit -------------------------------------------------------------

    def _set_busy(self, busy: bool) -> None:
        self._spinner.set_visible(busy)
        self._text.set_editable(not busy)
        self._update_state()

    def _submit(self) -> None:
        if not self._post.get_sensitive():
            return
        self._error.set_revealed(False)
        self._set_busy(True)
        params = {
            "text": self._get_text().strip(),
            "replyTo": (self._reply_to or {}).get("id"),
            "quote": (self._quote or {}).get("id"),
            "media": [{"path": path, "alt": self._alts.get(path, "")} for path in self._images],
        }

        def done(result, error):
            self._set_busy(False)
            if error:
                self._error.set_title(GLib.markup_escape_text(self._controller.describe_error(error, "Couldn't post")))
                self._error.set_revealed(True)
                return
            self.close()
            self._controller.on_posted(result.get("id"), (self._reply_to or {}).get("id"))

        self._controller.sidecar.call("tweet.post", params, done)
