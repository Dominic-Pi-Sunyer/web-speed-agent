"""The Web Speed API key, kept in the operating system's keychain.

Why this exists: until 0.7.0 the key lived in plaintext in every MCP client's
config file (Claude Desktop, Claude Code, Antigravity). Those files are 0600, so
other *users* could not read them — but the attacker that matters is code
running as you: an npm postinstall script, an editor extension, a compromised
dependency. A `wsp_` string in a predictable JSON path is exactly what that kind
of payload greps for, and the same files are swept into Time Machine / iCloud /
Dropbox backups, pasted into support tickets, and committed with dotfiles.

The keychain (macOS Keychain, Windows Credential Manager, the Secret Service on
Linux desktops) keeps the key out of all of those. It is not a perfect boundary
— once your session is unlocked, code running as you can still ask for it — but
it takes the key out of every file that gets copied around.

Resolution order, everywhere the key is read:

    1. WEBSPEED_API_KEY environment variable   (CI, Docker, explicit override)
    2. the keychain                            (the normal desktop case)

The environment wins so headless systems with no keychain keep working, and so
an explicit override is never silently ignored.
"""
from __future__ import annotations

import os
import re

SERVICE = "web-speed"
ACCOUNT = "api-key"
ENV_VAR = "WEBSPEED_API_KEY"

# Matches a Web Speed key anywhere in text. Used to find plaintext copies left
# in config files; never used to validate a key's contents beyond its shape.
KEY_RE = re.compile(r"wsp_[A-Za-z0-9_\-]{16,}")


class KeystoreUnavailable(RuntimeError):
    """No usable OS keychain on this machine (typically a headless Linux box)."""


def _keyring():
    try:
        import keyring
        from keyring.errors import KeyringError
    except ImportError as exc:  # keyring is a hard dependency; be explicit anyway
        raise KeystoreUnavailable("the 'keyring' package is not installed") from exc
    return keyring, KeyringError


def available() -> bool:
    """True when a real keychain backend is present.

    keyring falls back to a 'fail' backend that raises on every call when no
    keychain exists, and a priority-0 chainer when nothing usable is installed.
    Both mean "no keychain", and asking up front lets callers fall back cleanly
    instead of discovering it halfway through writing configs.
    """
    try:
        keyring, _ = _keyring()
        backend = keyring.get_keyring()
    except Exception:  # noqa: BLE001 — any failure to load a backend is "no"
        return False
    mod = type(backend).__module__
    if mod.startswith("keyring.backends.fail") or mod.startswith("keyrings.alt"):
        # keyrings.alt's file backends store the secret in a file in your home
        # directory — the very thing this module exists to avoid.
        return False
    try:
        return float(getattr(backend, "priority", 1)) > 0
    except Exception:  # noqa: BLE001
        return True


def get() -> str | None:
    """The key stored in the keychain, or None. Never raises."""
    try:
        keyring, KeyringError = _keyring()
        val = keyring.get_password(SERVICE, ACCOUNT)
    except Exception:  # noqa: BLE001 — a locked or absent keychain is just "none"
        return None
    return val.strip() if val else None


def set(key: str) -> None:  # noqa: A001 — mirrors keyring's own verb
    key = (key or "").strip()
    if not key.startswith("wsp_"):
        raise ValueError("Web Speed keys start with 'wsp_'.")
    if not available():
        raise KeystoreUnavailable(
            "No system keychain is available on this machine.")
    keyring, KeyringError = _keyring()
    try:
        keyring.set_password(SERVICE, ACCOUNT, key)
    except KeyringError as exc:
        raise KeystoreUnavailable(str(exc)) from exc


def delete() -> bool:
    """Remove the stored key. Returns True if one was removed."""
    try:
        keyring, KeyringError = _keyring()
        if keyring.get_password(SERVICE, ACCOUNT) is None:
            return False
        keyring.delete_password(SERVICE, ACCOUNT)
        return True
    except Exception:  # noqa: BLE001
        return False


def resolve() -> tuple[str | None, str]:
    """(key, source) where source is 'env', 'keychain' or 'none'."""
    env = (os.environ.get(ENV_VAR) or "").strip()
    if env:
        return env, "env"
    kc = get()
    if kc:
        return kc, "keychain"
    return None, "none"


def mask(key: str | None) -> str:
    """Enough to tell two keys apart, useless to anyone who reads it."""
    if not key:
        return "(none)"
    return f"wsp_…{key[-4:]}" if len(key) > 12 else "wsp_…"


MISSING_HELP = (
    "No Web Speed API key on this machine. Run `webspeed-key set` and paste your "
    "key when prompted (get one at https://api.getwebspeed.io/account), then "
    "restart your AI app."
)
