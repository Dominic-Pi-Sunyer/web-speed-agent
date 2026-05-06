"""Credential storage using system keychain.

Credentials are stored locally and NEVER sent to Web Speed servers.
Uses platform-native keyring (macOS Keychain, Windows Credential Manager,
Linux secret-tool) via the `keyring` library.
"""

from __future__ import annotations

import json
from pathlib import Path

from .exceptions import CredentialError

_SERVICE_PREFIX = "web-speed-agent"


def _service(site: str) -> str:
    return f"{_SERVICE_PREFIX}:{site}"


def store(site: str, username: str, password: str, overwrite: bool = False) -> None:
    """Store credentials in system keychain.

    Args:
        site: Identifier (e.g. "united", "amazon").
        username: Email or username.
        password: Password. Never sent to Web Speed servers.
        overwrite: If False, raises CredentialError if credential already exists.
    """
    try:
        import keyring
    except ImportError:
        raise CredentialError("keyring package not installed. Run: pip install keyring")

    service = _service(site)
    existing = keyring.get_password(service, username)
    if existing and not overwrite:
        raise CredentialError(
            f"Credential for '{site}' already exists. Pass overwrite=True to replace."
        )
    keyring.set_password(service, username, password)


def get(site: str, username: str) -> str | None:
    """Retrieve password from keychain. Returns None if not found."""
    try:
        import keyring
    except ImportError:
        raise CredentialError("keyring package not installed. Run: pip install keyring")

    return keyring.get_password(_service(site), username)


def get_pair(site: str) -> tuple[str, str] | None:
    """Retrieve (username, password) stored under a site identifier.

    Stores the username separately as metadata so we can retrieve it
    without knowing it in advance.
    """
    try:
        import keyring
    except ImportError:
        raise CredentialError("keyring package not installed. Run: pip install keyring")

    # Username stored under a meta-key
    username = keyring.get_password(_service(site), "__username__")
    if not username:
        return None
    password = keyring.get_password(_service(site), username)
    if not password:
        return None
    return username, password


def store_pair(site: str, username: str, password: str, overwrite: bool = False) -> None:
    """Store (username, password) retrievable without knowing the username."""
    try:
        import keyring
    except ImportError:
        raise CredentialError("keyring package not installed. Run: pip install keyring")

    service = _service(site)
    existing = keyring.get_password(service, "__username__")
    if existing and not overwrite:
        raise CredentialError(
            f"Credential for '{site}' already exists. Pass overwrite=True to replace."
        )
    keyring.set_password(service, "__username__", username)
    keyring.set_password(service, username, password)


def delete(site: str) -> None:
    """Remove credentials for a site from keychain."""
    try:
        import keyring
        from keyring.errors import PasswordDeleteError
    except ImportError:
        raise CredentialError("keyring package not installed. Run: pip install keyring")

    service = _service(site)
    username = keyring.get_password(service, "__username__")
    if username:
        try:
            keyring.delete_password(service, username)
        except Exception:
            pass
    try:
        keyring.delete_password(service, "__username__")
    except Exception:
        pass
