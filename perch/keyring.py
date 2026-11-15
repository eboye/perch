"""Stores the rettiwt API key (a base64 bundle of X session cookies) in the Secret Service / portal."""

from typing import Callable

import gi

try:
    gi.require_version("Secret", "1")
    from gi.repository import Secret
except (ImportError, ValueError):  # libsecret typelib missing: keys are not persisted
    Secret = None

from gi.repository import GLib

from perch import APP_ID

_ATTRS = {"account": "default"}
_schema = (
    Secret.Schema.new(APP_ID, Secret.SchemaFlags.NONE, {"account": Secret.SchemaAttributeType.STRING})
    if Secret
    else None
)


def available() -> bool:
    return Secret is not None


def lookup(callback: Callable[[str | None], None]) -> None:
    if not Secret:
        GLib.idle_add(lambda: callback(None))
        return

    def done(_source, result):
        try:
            callback(Secret.password_lookup_finish(result))
        except GLib.Error as err:
            print(f"[perch] keyring lookup failed: {err.message}")
            callback(None)

    Secret.password_lookup(_schema, _ATTRS, None, done)


def store(api_key: str, callback: Callable[[bool], None] | None = None) -> None:
    if not Secret:
        if callback:
            callback(False)
        return

    def done(_source, result):
        try:
            ok = Secret.password_store_finish(result)
        except GLib.Error as err:
            print(f"[perch] keyring store failed: {err.message}")
            ok = False
        if callback:
            callback(ok)

    Secret.password_store(_schema, _ATTRS, Secret.COLLECTION_DEFAULT, "Perch X API key", api_key, None, done)


def clear() -> None:
    if not Secret:
        return

    def done(_source, result):
        try:
            Secret.password_clear_finish(result)
        except GLib.Error as err:
            print(f"[perch] keyring clear failed: {err.message}")

    Secret.password_clear(_schema, _ATTRS, None, done)
