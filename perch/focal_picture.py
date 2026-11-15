"""A picture that fills its frame (like Gtk.ContentFit.COVER) but doesn't cut off faces.

It starts from the usual centre crop and moves it only as far as needed to keep real faces in
view. Faces come from X's face boxes (see the sidecar's addFocusHints), which also fire on text,
food and fabric, so only real-sized faces in the upper part of the photo count; with none, the
crop is exactly the old centre crop — it can only fix a crop, never make one worse.
"""

import gi

gi.require_version("Gdk", "4.0")
gi.require_version("Gtk", "4.0")
gi.require_version("Graphene", "1.0")

from gi.repository import Gdk, Graphene, Gtk  # noqa: E402


# X's face detector also fires on text, food, UI… — those hits are tiny. A real face in a photo
# worth cropping around (selfie, portrait, group shot) covers at least ~1% of the image.
MIN_FACE_AREA = 0.01
# Next to the biggest face, keep only faces of comparable size (drops leftover noise).
MIN_FACE_RATIO = 0.4


def real_faces(media: dict) -> list[dict]:
    """Real-sized faces centred in the upper part of the photo. The position rule runs before the
    "comparable size" rule, so a big false hit lower down can't push a real face out."""
    faces = [
        f
        for f in media.get("faces") or []
        if f["w"] * f["h"] >= MIN_FACE_AREA and f["y"] + f["h"] / 2 <= MAX_FACE_CENTER_Y
    ]
    if not faces:
        return []
    largest = max(f["w"] * f["h"] for f in faces)
    return [f for f in faces if f["w"] * f["h"] >= largest * MIN_FACE_RATIO]


# Real photos of people get their heads cut off at the *top*; X's false hits that survive the size
# filter (fabric, illustrations) tend to sit low. Only faces centred above this line count.
MAX_FACE_CENTER_Y = 0.55
FACE_MARGIN = 0.04  # breathing room kept around the faces (fraction of the image)


def face_region(media: dict) -> tuple[float, float, float, float] | None:
    """(left, top, right, bottom) in 0..1 covering the faces worth keeping in view, or None."""
    faces = real_faces(media)
    if not faces:
        return None
    return (
        max(0.0, min(f["x"] for f in faces) - FACE_MARGIN),
        max(0.0, min(f["y"] for f in faces) - FACE_MARGIN),
        min(1.0, max(f["x"] + f["w"] for f in faces) + FACE_MARGIN),
        min(1.0, max(f["y"] + f["h"] for f in faces) + FACE_MARGIN),
    )


def crop_offset(frame: float, scaled: float, span: tuple[float, float] | None) -> float:
    """Offset (<= 0) of the scaled image inside the frame along one axis.

    Starts from the centre crop and moves it only as far as needed to keep ``span`` (start, end in
    0..1 of the image) in view; if the span can't fit, its start (the top of the faces) wins.
    Never exposes the image edges. With no span this is exactly the old centre crop."""
    if scaled <= frame:
        return (frame - scaled) / 2
    window = frame / scaled  # visible fraction of the image
    start = 0.5 - window / 2
    if span is not None:
        a, b = span
        if b - a > window or a < start:
            start = a
        elif b > start + window:
            start = b - window
    start = min(max(start, 0.0), 1.0 - window)
    return -start * scaled


class FocalPicture(Gtk.Widget):
    __gtype_name__ = "PerchFocalPicture"

    def __init__(self, media: dict | None = None, **props):
        super().__init__(**props)
        self._media = media or {}
        self._paintable: Gdk.Paintable | None = None

    def set_paintable(self, paintable: Gdk.Paintable | None) -> None:
        self._paintable = paintable
        self.queue_draw()

    def get_paintable(self) -> Gdk.Paintable | None:
        return self._paintable

    def do_measure(self, orientation, for_size):
        # Fill whatever the layout gives us; height comes from set_size_request().
        return 0, 0, -1, -1

    def focal_offsets(self, width: float, height: float) -> tuple[float, float, float, float]:
        """(x offset, y offset, scaled width, scaled height) for drawing — exposed for tests."""
        image_w = self._paintable.get_intrinsic_width()
        image_h = self._paintable.get_intrinsic_height()
        scale = max(width / image_w, height / image_h)
        scaled_w, scaled_h = image_w * scale, image_h * scale
        region = face_region(self._media)
        x_span = (region[0], region[2]) if region else None
        y_span = (region[1], region[3]) if region else None
        return crop_offset(width, scaled_w, x_span), crop_offset(height, scaled_h, y_span), scaled_w, scaled_h

    def do_snapshot(self, snapshot: Gtk.Snapshot) -> None:
        width, height = self.get_width(), self.get_height()
        paintable = self._paintable
        if paintable is None or width <= 0 or height <= 0:
            return
        if paintable.get_intrinsic_width() <= 0 or paintable.get_intrinsic_height() <= 0:
            return
        x, y, scaled_w, scaled_h = self.focal_offsets(width, height)
        snapshot.push_clip(Graphene.Rect().init(0, 0, width, height))
        snapshot.save()
        snapshot.translate(Graphene.Point().init(x, y))
        paintable.snapshot(snapshot, scaled_w, scaled_h)
        snapshot.restore()
        snapshot.pop()
