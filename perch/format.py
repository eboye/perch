"""Pure text helpers: tweet markup, counts and timestamps."""

import html
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, quote, unquote, urlparse

from gi.repository import GLib

_TOKEN = re.compile(r"(https?://[^\s]+)|(?<![\w@])@(\w{1,15})|(?<![\w&#])#(\w+)")
_TRAILING_TCO = re.compile(r"\s*https://t\.co/\w+\s*$")


def _attr(value: str) -> str:
    return GLib.markup_escape_text(value).replace('"', "&quot;")


def tweet_markup(text: str, strip_trailing_link: bool = False) -> str:
    """Pango markup for a tweet body with links for URLs, @mentions and #hashtags."""
    text = html.unescape(text or "")
    # Tidy the layout: no spaces at line ends, and at most one blank line between paragraphs.
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
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


# X's "lang" values that aren't a language: undetermined, no text, media / mentions / hashtags /
# cashtags only, emoji only.
_NOT_A_LANGUAGE = {"und", "zxx", "qme", "qam", "qht", "qct", "qst", "art"}


def user_language() -> str:
    """The desktop's language as a two-letter code (``en`` when it can't be told)."""
    for name in GLib.get_language_names():
        code = name.split("_")[0].split(".")[0].split("@")[0].lower()
        if code and code not in ("c", "posix"):
            return code
    return "en"


def needs_translation(lang: str | None, mine: str | None = None) -> bool:
    """Whether a post in ``lang`` (X's code) is in a language other than the reader's."""
    if not lang or lang in _NOT_A_LANGUAGE:
        return False
    return lang.split("-")[0].lower() != (mine or user_language())


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


def schedule_time(unix: float | None, now: datetime | None = None) -> str:
    """When a scheduled post goes out, in local time: "Today at 18:30", "Tomorrow at 09:00",
    "Tue 18 Nov at 09:00", or with the year when it isn't this year."""
    if unix is None:
        return ""
    stamp = datetime.fromtimestamp(float(unix)).astimezone()
    now = (now or datetime.now(timezone.utc)).astimezone()
    days = (stamp.date() - now.date()).days
    clock = f"{stamp:%H:%M}"
    if days == 0:
        return f"Today at {clock}"
    if days == 1:
        return f"Tomorrow at {clock}"
    if stamp.year == now.year:
        return f"{stamp:%a} {stamp.day} {stamp:%b} at {clock}"
    return f"{stamp:%a} {stamp.day} {stamp:%b} {stamp.year} at {clock}"


def schedule_phrase(unix: float | None, now: datetime | None = None) -> str:
    """``schedule_time`` worded to follow "Posts …": "today at 18:30", "on Tue 18 Nov at 09:00"."""
    text = schedule_time(unix, now)
    if text.startswith(("Today", "Tomorrow")):
        return text[0].lower() + text[1:]
    return f"on {text}" if text else ""


def avatar_url(url: str | None) -> str | None:
    # X serves 48px "_normal" avatars by default; "_bigger" (73px) stays sharp at 2x scale.
    return url.replace("_normal.", "_bigger.") if url else None


_X_HOST = re.compile(r"^https?://(?:www\.|mobile\.)?(?:x|twitter)\.com(/[^?#]*)?", re.I)
_PROFILE_PATH = re.compile(r"^/([A-Za-z0-9_]{1,15})/?$")
_SPACE_PATH = re.compile(r"^/i/spaces/([A-Za-z0-9]+)")
_POST_PATH = re.compile(r"^/(?:[A-Za-z0-9_]{1,15}|i(?:/web)?)/status(?:es)?/(\d+)")
# First path segments that are X pages, not usernames.
_RESERVED = {
    "home", "explore", "search", "notifications", "messages", "settings", "i", "hashtag", "compose",
    "login", "logout", "signup", "tos", "privacy", "about", "intent", "share", "lists", "bookmarks",
}


def x_link_target(uri: str) -> tuple[str, str] | None:
    """Where an X link points, if Perch can show it: ("profile", handle), ("post", id),
    ("space", id) or ("search", query) for hashtag and search links."""
    match = _X_HOST.match(uri or "")
    if not match:
        return None
    path = match.group(1) or "/"
    if post := _POST_PATH.match(path):
        return "post", post.group(1)
    if space := _SPACE_PATH.match(path):
        return "space", space.group(1)
    if hashtag := re.match(r"^/hashtag/([^/]+)/?$", path):
        return "search", "#" + unquote(hashtag.group(1))
    if path.rstrip("/") == "/search":
        query = parse_qs(urlparse(uri).query).get("q", [""])[0].strip()
        return ("search", query) if query else None
    if (profile := _PROFILE_PATH.match(path)) and profile.group(1).lower() not in _RESERVED:
        return "profile", profile.group(1)
    return None


def joined(raw: str | None) -> str:
    stamp = parse_time(raw)
    return f"Joined {stamp:%B %Y}" if stamp else ""


def profile_banner_url(url: str | None) -> str | None:
    return f"{url}/1500x500" if url else None


def profile_avatar_url(url: str | None) -> str | None:
    return url.replace("_normal.", "_400x400.") if url else None


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
