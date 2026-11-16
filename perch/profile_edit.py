"""Edit Profile dialog: name, bio, location, website, profile photo and header image."""

import base64
import html

import gi

gi.require_version("GdkPixbuf", "2.0")

from gi.repository import Adw, Gdk, GdkPixbuf, Gio, GLib, Gtk  # noqa: E402

from perch import format as fmt  # noqa: E402
from perch import images  # noqa: E402

# X's limits for each field.
LIMITS = {"name": 50, "description": 160, "location": 30, "url": 100}
# Pictures are cropped to X's shapes and scaled down before upload (X rejects large files).
PICTURE_SIZES = {"image": (400, 400), "banner": (1500, 500)}


def prepare_picture(path: str, kind: str) -> tuple[str, Gdk.Texture]:
    """Centre-crop ``path`` to the shape X uses for ``kind`` (square photo, 3:1 header), scale it to
    X's recommended size, and return (base64 JPEG, preview texture)."""
    width, height = PICTURE_SIZES[kind]
    source = GdkPixbuf.Pixbuf.new_from_file(path)
    source = source.apply_embedded_orientation() or source
    src_w, src_h = source.get_width(), source.get_height()
    target_ratio = width / height
    if src_w / src_h > target_ratio:  # too wide: trim the sides
        crop_w, crop_h = round(src_h * target_ratio), src_h
    else:  # too tall: trim top and bottom
        crop_w, crop_h = src_w, round(src_w / target_ratio)
    cropped = source.new_subpixbuf((src_w - crop_w) // 2, (src_h - crop_h) // 2, crop_w, crop_h)
    scale = min(1.0, width / crop_w)
    out_w, out_h = max(1, round(crop_w * scale)), max(1, round(crop_h * scale))
    scaled = cropped.scale_simple(out_w, out_h, GdkPixbuf.InterpType.BILINEAR)
    if scaled.get_has_alpha():  # JPEG has no alpha: flatten onto white
        flat = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, out_w, out_h)
        flat.fill(0xFFFFFFFF)
        scaled.composite(flat, 0, 0, out_w, out_h, 0, 0, 1, 1, GdkPixbuf.InterpType.NEAREST, 255)
        scaled = flat
    ok, data = scaled.save_to_bufferv("jpeg", ["quality"], ["90"])
    if not ok:
        raise GLib.Error("Couldn't encode the image")
    return base64.b64encode(data).decode("ascii"), Gdk.Texture.new_for_pixbuf(scaled)


class ProfileEditDialog(Adw.Dialog):
    def __init__(self, controller, user: dict):
        super().__init__(title="Edit Profile", content_width=520, content_height=640)
        self._controller = controller
        self._user = dict(user)  # updated as fields save, so a retry only resends what failed
        self._pictures: dict[str, str] = {}  # kind -> base64 JPEG waiting to upload

        header = Adw.HeaderBar(show_end_title_buttons=False, show_start_title_buttons=False)
        cancel = Gtk.Button(label="Cancel")
        cancel.connect("clicked", lambda *_: self.close())
        header.pack_start(cancel)
        self._save = Gtk.Button(label="Save", css_classes=["suggested-action"])
        self._save.connect("clicked", lambda *_: self._submit())
        header.pack_end(self._save)
        self._spinner = Adw.Spinner(visible=False)
        header.pack_end(self._spinner)

        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=18, margin_bottom=18, margin_start=16, margin_end=16, margin_top=12)
        self._error = Adw.Banner(revealed=False, use_markup=False)
        body.append(self._error)
        body.append(self._build_pictures())

        group = Adw.PreferencesGroup()
        self._name = self._entry(group, "Name", user.get("fullName") or "", "name")
        self._location = self._entry(group, "Location", user.get("location") or "", "location")
        self._url = self._entry(group, "Website", user.get("website") or "", "url")
        body.append(group)

        bio_group = Adw.PreferencesGroup(title="Bio")
        self._bio = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR, accepts_tab=False, top_margin=10, bottom_margin=10, left_margin=12, right_margin=12)
        self._bio.add_css_class("compose-text")
        self._bio.get_buffer().set_text(html.unescape(user.get("description") or ""))  # X sends the bio HTML-escaped
        self._bio.get_buffer().connect("changed", lambda *_: self._validate())
        scroller = Gtk.ScrolledWindow(child=self._bio, min_content_height=96, hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.add_css_class("card")
        bio_group.add(scroller)
        self._bio_counter = Gtk.Label(xalign=1, margin_top=4, css_classes=["numeric", "dim-label", "caption"])
        bio_group.add(self._bio_counter)
        body.append(bio_group)

        view = Adw.ToolbarView(content=Gtk.ScrolledWindow(child=body, hscrollbar_policy=Gtk.PolicyType.NEVER, propagate_natural_height=True))
        view.add_top_bar(header)
        self.set_child(view)
        self._validate()

    # -- building -----------------------------------------------------------

    def _build_pictures(self) -> Gtk.Widget:
        self._banner = Gtk.Picture(content_fit=Gtk.ContentFit.COVER, can_shrink=True, height_request=130, hexpand=True, overflow=Gtk.Overflow.HIDDEN)
        self._banner.add_css_class("profile-banner")
        self._banner.add_css_class("edit-banner")
        images.load(fmt.profile_banner_url(self._user.get("profileBanner")), lambda tex: tex and self._banner.set_paintable(tex), cache=False)
        banner_stage = Gtk.Overlay(child=self._banner)
        banner_stage.add_overlay(self._picture_button("banner", "Change Header Image", Gtk.Align.CENTER, Gtk.Align.CENTER))

        self._avatar = Adw.Avatar(size=88, text=self._user.get("fullName") or "", show_initials=True)
        images.load(fmt.profile_avatar_url(self._user.get("profileImage")), lambda tex: tex and self._avatar.set_custom_image(tex), cache=False)
        avatar_stage = Gtk.Overlay(child=self._avatar, halign=Gtk.Align.START, valign=Gtk.Align.END, margin_start=16)
        avatar_stage.add_css_class("profile-avatar")
        avatar_stage.add_overlay(self._picture_button("image", "Change Profile Photo", Gtk.Align.CENTER, Gtk.Align.CENTER))

        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        column.append(banner_stage)
        column.append(Gtk.Box(height_request=44))  # room for the avatar hanging off the banner
        stage = Gtk.Overlay(child=column)
        stage.add_overlay(avatar_stage)

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        box.append(stage)
        box.append(Gtk.Label(label="New pictures are cropped to fit: square for the photo, 3:1 for the header.", xalign=0, wrap=True, css_classes=["caption", "dim-label"]))
        return box

    def _picture_button(self, kind: str, tooltip: str, halign: Gtk.Align, valign: Gtk.Align) -> Gtk.Button:
        button = Gtk.Button(icon_name="camera-photo-symbolic", tooltip_text=tooltip, halign=halign, valign=valign)
        button.add_css_class("osd")
        button.add_css_class("circular")
        button.update_property([Gtk.AccessibleProperty.LABEL], [tooltip])
        button.connect("clicked", lambda *_: self._pick_picture(kind))
        return button

    def _entry(self, group: Adw.PreferencesGroup, title: str, value: str, key: str) -> Adw.EntryRow:
        row = Adw.EntryRow(title=title, text=value)
        row.limit = LIMITS[key]
        row.connect("changed", lambda *_: self._validate())
        group.add(row)
        return row

    # -- state --------------------------------------------------------------

    def _bio_text(self) -> str:
        buffer = self._bio.get_buffer()
        return buffer.get_text(buffer.get_start_iter(), buffer.get_end_iter(), False)

    def _validate(self) -> bool:
        problems = []
        for row in (self._name, self._location, self._url):
            too_long = len(row.get_text().strip()) > row.limit
            (row.add_css_class if too_long else row.remove_css_class)("error")
            if too_long:
                problems.append(f"{row.get_title()} can be at most {row.limit} characters")
        if not self._name.get_text().strip():
            self._name.add_css_class("error")
            problems.append("Name can't be empty")
        bio_used = len(self._bio_text().strip())
        self._bio_counter.set_label(f"{bio_used}/{LIMITS['description']}")
        (self._bio_counter.add_css_class if bio_used > LIMITS["description"] else self._bio_counter.remove_css_class)("error")
        if bio_used > LIMITS["description"]:
            problems.append(f"Bio can be at most {LIMITS['description']} characters")
        self._save.set_tooltip_text(problems[0] if problems else None)
        self._save.set_sensitive(not problems and not self._spinner.get_visible())
        return not problems

    def _changed_fields(self) -> dict:
        current = {
            "name": self._name.get_text().strip(),
            "location": self._location.get_text().strip(),
            "url": self._url.get_text().strip(),
            "description": self._bio_text().strip(),
        }
        original = {
            "name": (self._user.get("fullName") or "").strip(),
            "location": (self._user.get("location") or "").strip(),
            "url": (self._user.get("website") or "").strip(),
            "description": html.unescape(self._user.get("description") or "").strip(),
        }
        return {key: value for key, value in current.items() if value != original[key]}

    # -- pictures -----------------------------------------------------------

    def _pick_picture(self, kind: str) -> None:
        image_filter = Gtk.FileFilter(name="Images")
        for mime in ("image/png", "image/jpeg", "image/webp", "image/gif"):
            image_filter.add_mime_type(mime)
        filters = Gio.ListStore.new(Gtk.FileFilter)
        filters.append(image_filter)
        title = "Choose a Profile Photo" if kind == "image" else "Choose a Header Image"
        dialog = Gtk.FileDialog(title=title, filters=filters, default_filter=image_filter)

        def done(source, result):
            try:
                path = source.open_finish(result).get_path()
            except GLib.Error:
                return  # cancelled
            self.set_picture(kind, path)

        dialog.open(self.get_root(), None, done)

    def set_picture(self, kind: str, path: str) -> None:
        try:
            data, texture = prepare_picture(path, kind)
        except GLib.Error as err:
            self._show_error(f"Couldn't use that image: {err.message}")
            return
        self._pictures[kind] = data
        if kind == "image":
            self._avatar.set_custom_image(texture)
        else:
            self._banner.set_paintable(texture)

    # -- saving -------------------------------------------------------------

    def _show_error(self, text: str) -> None:
        self._error.set_title(text)
        self._error.set_revealed(True)

    def _set_busy(self, busy: bool) -> None:
        self._spinner.set_visible(busy)
        for row in (self._name, self._location, self._url):
            row.set_sensitive(not busy)
        self._bio.set_editable(not busy)
        self._validate()

    def _submit(self) -> None:
        if not self._validate():
            return
        steps = []
        if fields := self._changed_fields():
            steps.append(("profile.update", fields, "Couldn't update your profile"))
        for kind in ("image", "banner"):
            if kind in self._pictures:
                failure = "Couldn't change your profile photo" if kind == "image" else "Couldn't change your header image"
                steps.append(("profile.picture", {"kind": kind, "data": self._pictures[kind]}, failure))
        if not steps:
            self.close()
            return
        self._error.set_revealed(False)
        self._set_busy(True)
        self._run(steps, saved_any=False)

    def _run(self, steps: list, saved_any: bool) -> None:
        if not steps:
            self._set_busy(False)
            self.close()
            self._controller.on_profile_updated()
            return
        method, params, failure = steps[0]

        def done(_result, error):
            if error:
                self._set_busy(False)
                self._show_error(self._controller.describe_error(error, failure))
                if saved_any:
                    self._controller.on_profile_updated(quiet=True)
                return
            if method == "profile.picture":
                self._pictures.pop(params["kind"], None)
            elif method == "profile.update":
                self._mark_saved(params)
            self._run(steps[1:], saved_any=True)

        self._controller.sidecar.call(method, params, done)

    def _mark_saved(self, fields: dict) -> None:
        """Remember what's on X now, so a retry after a later failure doesn't resend it."""
        keys = {"name": "fullName", "location": "location", "url": "website", "description": "description"}
        for key, value in fields.items():
            self._user[keys[key]] = value
