"""Pure text helpers: tweet markup, counts and timestamps."""

import html
import re
from datetime import datetime, timezone
from urllib.parse import quote

from gi.repository import GLib

_TOKEN = re.compile(r"(https?://[^\s]+)|(?<![\w@])@(\w{1,15})|(?<![\w&#])#(\w+)")
_TRAILING_TCO = re.compile(r"\s*https://t\.co/\w+\s*$")


def _attr(value: str) -> str:
    return GLib.markup_escape_text(value).replace('"', "&quot;")


def tweet_markup(text: str, strip_trailing_link: bool = False) -> str:
    """Pango markup for a tweet body with links for URLs, @mentions and #hashtags."""
    text = html.unescape(text or "")
    if strip_trailing_link:
        text = _TRAILING_TCO.sub("", text)
    out, pos = [], 0
    for match in _TOKEN.finditer(text):
        out.append(GLib.markup_escape_text(text[pos : match.start()]))
        url, mention, tag = match.groups()
        if url:
            shown = re.sub(r"^https?://", "", url)
            shown = shown if len(shown) <= 40 else shown[:39] + "…"
            out.append(f'<a href="{_attr(url)}">{GLib.markup_escape_text(shown)}</a>')
        elif mention:
            out.append(f'<a href="https://x.com/{_attr(mention)}">@{GLib.markup_escape_text(mention)}</a>')
        else:
            out.append(f'<a href="https://x.com/hashtag/{_attr(quote(tag))}">#{GLib.markup_escape_text(tag)}</a>')
        pos = match.end()
    out.append(GLib.markup_escape_text(text[pos:]))
    return "".join(out).strip()


def compact_count(value) -> str:
    if not value:
        return ""
    value = int(value)
    for limit, suffix in ((1_000_000_000, "B"), (1_000_000, "M"), (1_000, "K")):
        if value >= limit:
            shown = f"{value / limit:.1f}".rstrip("0").rstrip(".")
            return f"{shown}{suffix}"
    return str(value)


def parse_time(raw: str | None) -> datetime | None:
    if not raw:
        return None
    for fmt in ("%a %b %d %H:%M:%S %z %Y", "%Y-%m-%dT%H:%M:%S.%f%z", "%Y-%m-%dT%H:%M:%S%z"):
        try:
            return datetime.strptime(raw.replace("Z", "+0000"), fmt)
        except ValueError:
            continue
    return None


def relative_time(raw: str | None, now: datetime | None = None) -> str:
    stamp = parse_time(raw)
    if not stamp:
        return ""
    now = now or datetime.now(timezone.utc)
    seconds = (now - stamp).total_seconds()
    if seconds < 60:
        return "now"
    if seconds < 3600:
        return f"{int(seconds // 60)}m"
    if seconds < 86400:
        return f"{int(seconds // 3600)}h"
    local = stamp.astimezone()
    if local.year == now.astimezone().year:
        return f"{local:%b} {local.day}"
    return f"{local:%b} {local.day}, {local.year}"


def full_time(raw: str | None) -> str:
    stamp = parse_time(raw)
    return f"{stamp.astimezone():%H:%M · %a %d %b %Y}" if stamp else ""


def avatar_url(url: str | None) -> str | None:
    # X serves 48px "_normal" avatars by default; "_bigger" (73px) stays sharp at 2x scale.
    return url.replace("_normal.", "_bigger.") if url else None


_SPACE_URL = re.compile(r"(?:x|twitter)\.com/i/spaces/(\w+)")


def space_id_from(text: str) -> str | None:
    """Space id from a Space URL, or the text itself if it already looks like an id."""
    text = (text or "").strip()
    if match := _SPACE_URL.search(text):
        return match.group(1)
    return text if re.fullmatch(r"1\w{10,20}", text) else None


def space_ids(tweet: dict) -> list[str]:
    found: list[str] = []
    for url in (tweet.get("entities") or {}).get("urls") or []:
        if (match := _SPACE_URL.search(url or "")) and match.group(1) not in found:
            found.append(match.group(1))
    return found


def weighted_length(text: str) -> int:
    """Approximate X's character count: URLs count as 23, wide (CJK/emoji) characters as 2."""
    text = re.sub(r"https?://\S+", "x" * 23, text or "")
    return sum(1 if ord(ch) < 0x1100 else 2 for ch in text)


def media_thumb(media: dict) -> str | None:
    if media.get("type") == "PHOTO":
        url = media.get("url") or ""
        return f"{url}?name=small" if "pbs.twimg.com/media/" in url else url or None
    return media.get("thumbnailUrl") or None
