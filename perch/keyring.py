"""Stores rettiwt API keys (base64 bundles of X session cookies) in the Secret Service / portal, one
per X account (keyed by the account's user id).

Perch 0.3.6 and older stored a single key under the account name "default"; window.py moves it to
the account's id after the first sign-in (see ``LEGACY``).

``PERCH_KEYRING=memory`` keeps keys in memory only (UI tests: never touches the real keyring).
"""

import os
from typing import Callable

import gi

try:
    gi.require_version("Secret", "1")
    from gi.repository import Secret
except (ImportError, ValueError):  # libsecret typelib missing: keys are not persisted
    Secret = None

from gi.repository import GLib

from perch import APP_ID

LEGACY = "default"  # the account name the single key of Perch ≤ 0.3.6 was stored under

_memory: dict[str, str] | None = {} if os.environ.get("PERCH_KEYRING") == "memory" else None
_schema = (
    Secret.Schema.new(APP_ID, Secret.SchemaFlags.NONE, {"account": Secret.SchemaAttributeType.STRING})
    if Secret
    else None
)


def available() -> bool:
    return _memory is not None or Secret is not None


def lookup(account: str, callback: Callable[[str | None], None]) -> None:
    if _memory is not None or not Secret:
        value = (_memory or {}).get(account)
        GLib.idle_add(lambda: callback(value) or False)
        return

    def done(_source, result):
        try:
            callback(Secret.password_lookup_finish(result))
        except GLib.Error as err:
            print(f"[perch] keyring lookup failed: {err.message}")
            callback(None)

    Secret.password_lookup(_schema, {"account": account}, None, done)


def store(account: str, api_key: str, label: str, callback: Callable[[bool], None] | None = None) -> None:
    if _memory is not None or not Secret:
        if _memory is not None:
            _memory[account] = api_key
        if callback:
            GLib.idle_add(lambda: callback(_memory is not None) or False)
        return

    def done(_source, result):
        try:
            ok = Secret.password_store_finish(result)
        except GLib.Error as err:
            print(f"[perch] keyring store failed: {err.message}")
            ok = False
        if callback:
            callback(ok)

    Secret.password_store(_schema, {"account": account}, Secret.COLLECTION_DEFAULT, label, api_key, None, done)


def clear(account: str) -> None:
    if _memory is not None or not Secret:
        if _memory is not None:
            _memory.pop(account, None)
        return

    def done(_source, result):
        try:
            Secret.password_clear_finish(result)
        except GLib.Error as err:
            print(f"[perch] keyring clear failed: {err.message}")

    Secret.password_clear(_schema, {"account": account}, None, done)
