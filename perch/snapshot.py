"""What the timelines last showed, saved per account, so Perch opens instantly and where you left
off — even when X can't be reached.

Files live in the cache folder (~/.var/app/<id>/cache/perch/snapshots/<account id>/ inside Flatpak):
``me.json`` (your account, for the header and menu) and ``<timeline>.json``:
{"saved_at", "items" (the posts loaded, newest first), "next" (cursor after them, or null),
"anchor": {"id", "offset"} (the post at the top of the screen and how far it was scrolled)}.
Signing out of an account deletes its folder.
"""

import json
import shutil
import time
from pathlib import Path

from gi.repository import GLib

MAX_ITEMS = 150  # posts kept per timeline (plus whatever it takes to reach the one you were reading)
MAX_AGE = 7 * 24 * 3600  # older copies aren't worth opening on

_root = Path(GLib.get_user_cache_dir()) / "perch" / "snapshots"


def _folder(account_id: str) -> Path:
    # Account ids are X user ids (digits); keep anything else out of the path.
    return _root / "".join(ch for ch in str(account_id) if ch.isalnum())


def _write(path: Path, data: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")))
        tmp.replace(path)
    except OSError as err:
        print(f"[perch] could not save {path.name}: {err}")


def _read(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or time.time() - float(data.get("saved_at") or 0) > MAX_AGE:
        return None
    return data


def save_me(account_id: str, user: dict) -> None:
    _write(_folder(account_id) / "me.json", {"saved_at": time.time(), "user": user})


def load_me(account_id: str) -> dict | None:
    data = _read(_folder(account_id) / "me.json")
    user = (data or {}).get("user")
    return user if isinstance(user, dict) and user.get("id") == str(account_id) else None


def save_timeline(account_id: str, kind: str, items: list[dict], next_cursor: str | None, anchor: dict | None) -> None:
    _write(_folder(account_id) / f"{kind}.json", {"saved_at": time.time(), "items": items, "next": next_cursor, "anchor": anchor})


def load_timeline(account_id: str, kind: str) -> dict | None:
    data = _read(_folder(account_id) / f"{kind}.json")
    if not data or not isinstance(data.get("items"), list) or not data["items"]:
        return None
    return data


def saved_at(account_id: str) -> float | None:
    """When the account's newest timeline copy was saved."""
    times = [float(data["saved_at"]) for kind in ("following", "foryou") if (data := load_timeline(account_id, kind))]
    return max(times) if times else None


def forget(account_id: str) -> None:
    shutil.rmtree(_folder(account_id), ignore_errors=True)


def forget_all() -> None:
    shutil.rmtree(_root, ignore_errors=True)
