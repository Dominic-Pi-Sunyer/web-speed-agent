"""Credential storage using system keychain.

Credentials are stored locally and NEVER sent to Web Speed servers.
Uses platform-native keyring (macOS Keychain, Windows Credential Manager,
Linux secret-tool) via the `keyring` library.
"""

from __future__ import annotations

from .exceptions import CredentialError

_SERVICE_PREFIX = "web-speed-agent"

try:
    import keyring
    from keyring.errors import PasswordDeleteError as _PasswordDeleteError
except ImportError:
    raise ImportError(
        "The 'keyring' package is required for credential storage. "
        "Install it with: pip install keyring"
    )


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
    service = _service(site)
    existing = keyring.get_password(service, username)
    if existing and not overwrite:
        raise CredentialError(
            f"Credential for '{site}' already exists. Pass overwrite=True to replace."
        )
    keyring.set_password(service, username, password)


def get(site: str, username: str) -> str | None:
    """Retrieve password from keychain. Returns None if not found."""
    return keyring.get_password(_service(site), username)


def get_pair(site: str) -> tuple[str, str] | None:
    """Retrieve (username, password) stored under a site identifier."""
    service = _service(site)
    username = keyring.get_password(service, "__username__")
    if not username:
        return None
    password = keyring.get_password(service, username)
    if not password:
        return None
    return username, password


def store_pair(site: str, username: str, password: str, overwrite: bool = False) -> None:
    """Store (username, password) retrievable without knowing the username."""
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
    service = _service(site)
    username = keyring.get_password(service, "__username__")
    if username:
        try:
            keyring.delete_password(service, username)
        except _PasswordDeleteError:
            pass
    try:
        keyring.delete_password(service, "__username__")
    except _PasswordDeleteError:
        pass
