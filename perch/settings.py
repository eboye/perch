"""Tiny JSON settings store in the user config dir (~/.var/app/<id>/config/perch inside Flatpak)."""

import json
from pathlib import Path

from gi.repository import GLib

_PATH = Path(GLib.get_user_config_dir()) / "perch" / "settings.json"
_DEFAULTS = {
    "desktop_notifications": True,
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
    try:
        _PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = _PATH.with_suffix(".tmp")
        tmp.write_text(json.dumps(_values, indent=2))
        tmp.replace(_PATH)
    except OSError as err:
        print(f"[perch] could not save settings: {err}")
