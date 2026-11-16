"""Tiny JSON settings store in the user config dir (~/.var/app/<id>/config/perch inside Flatpak)."""

import json
from pathlib import Path

from gi.repository import GLib

_PATH = Path(GLib.get_user_config_dir()) / "perch" / "settings.json"
_DEFAULTS = {
    "desktop_notifications": True,
    # Which kinds get a desktop notification (Preferences).
    "notify_messages": True,
    "notify_likes": True,
    "notify_reposts": True,
    "notify_follows": True,
    "notify_other": True,
    "poll_minutes": 2,
    "dock_badge": True,
    "start_tab": "last",  # or a tab name: following, foryou, notifications, messages
    "image_cache_mb": 300,
    "notifications_seen_at": "",  # ISO time of the newest notification the user has looked at
}


def _load() -> dict:
    try:
        return {**_DEFAULTS, **json.loads(_PATH.read_text())}
    except (OSError, ValueError):
        return dict(_DEFAULTS)


_values = _load()


def get(key: str):
    return _values.get(key, _DEFAULTS.get(key))


def set(key: str, value) -> None:  # noqa: A001 - mirrors get()
    _values[key] = value
    _save()


# -- per-account values (what's been seen, drafts, pinned lists) -------------------------------

_account: str | None = None  # the signed-in X account's user id
# Values that used to be global (Perch ≤ 0.3.6, one account) and now belong to an account.
_PER_ACCOUNT = ("notifications_seen_at", "drafts")


def set_account(account_id: str | None) -> None:
    global _account
    _account = account_id


def account_get(key: str, default=None):
    if _account is None:
        return _values.get(key, _DEFAULTS.get(key, default))
    return (_values.get("by_account") or {}).get(_account, {}).get(key, default)


def account_set(key: str, value) -> None:
    if _account is None:
        set(key, value)
        return
    by_account = dict(_values.get("by_account") or {})
    by_account[_account] = {**by_account.get(_account, {}), key: value}
    _values["by_account"] = by_account
    _save()


def adopt_legacy(account_id: str) -> None:
    """Give the one account of an older Perch its global values (seen marker, drafts)."""
    moved = {key: _values.pop(key) for key in _PER_ACCOUNT if _values.get(key)}
    if moved:
        by_account = dict(_values.get("by_account") or {})
        by_account[account_id] = {**moved, **by_account.get(account_id, {})}
        _values["by_account"] = by_account
        _save()


def _save() -> None:
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_values, indent=2))
        tmp.replace(_PATH)
    except OSError as err:
        print(f"[perch] could not save settings: {err}")
