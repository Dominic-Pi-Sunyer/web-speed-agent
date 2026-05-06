"""Playwright browser context with session persistence."""

from __future__ import annotations

import re
import stat
import warnings
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .exceptions import BrowserError, PlaywrightNotInstalledError

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page

# Only alphanumeric, hyphens, underscores — prevents path traversal
_SESSION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")


def _validate_session_name(name: str) -> None:
    if not _SESSION_NAME_RE.match(name):
        raise ValueError(
            f"Invalid session_name {name!r}. Use only letters, digits, hyphens, underscores (max 64 chars)."
        )


def _secure_mkdir(path: Path) -> None:
    """Create directory with owner-only permissions (0o700)."""
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)


class ManagedBrowser:
    """Async context manager that wraps a Playwright browser with session persistence.

    Usage:
        async with ManagedBrowser(sessions_dir, session_name, headless) as browser:
            page = await browser.new_page()
            await page.goto("https://example.com")
    """

    def __init__(
        self,
        sessions_dir: Path,
        session_name: str | None,
        headless: bool,
        proxy: str | None = None,
    ) -> None:
        if session_name is not None:
            _validate_session_name(session_name)
        self._sessions_dir = sessions_dir
        self._session_name = session_name
        self._headless = headless
        self._proxy = proxy
        self._pw = None
        self._browser: Browser | None = None
        self._context: BrowserContext | None = None

    async def __aenter__(self) -> "ManagedBrowser":
        try:
            from playwright.async_api import async_playwright
        except ImportError:
            raise PlaywrightNotInstalledError(
                "Playwright not installed. Run: pip install playwright && playwright install chromium"
            )

        self._pw = await async_playwright().start()

        launch_opts: dict[str, Any] = {"headless": self._headless}
        if self._proxy:
            launch_opts["proxy"] = {"server": self._proxy}

        try:
            self._browser = await self._pw.chromium.launch(**launch_opts)
        except Exception as exc:
            raise PlaywrightNotInstalledError(
                f"Could not launch browser: {exc}. Run: playwright install chromium"
            ) from exc

        ctx_opts: dict[str, Any] = {}

        # Load persisted session if available
        if self._session_name:
            session_dir = self._sessions_dir / self._session_name
            _secure_mkdir(session_dir)
            storage_file = session_dir / "storage.json"
            if storage_file.exists():
                # Warn if file is world-readable (might have been created with wrong perms)
                mode = storage_file.stat().st_mode
                if mode & (stat.S_IRGRP | stat.S_IROTH):
                    warnings.warn(
                        f"Session file {storage_file} is readable by others. "
                        "Consider deleting and re-creating it.",
                        UserWarning,
                        stacklevel=3,
                    )
                ctx_opts["storage_state"] = str(storage_file)

        self._context = await self._browser.new_context(**ctx_opts)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        # Save session state before closing
        if self._context and self._session_name:
            session_dir = self._sessions_dir / self._session_name
            _secure_mkdir(session_dir)
            storage_file = session_dir / "storage.json"
            try:
                await self._context.storage_state(path=str(storage_file))
                # Lock down the file after writing (contains auth cookies)
                storage_file.chmod(0o600)
            except (OSError, IOError) as exc:
                warnings.warn(f"Failed to save session state: {exc}", UserWarning, stacklevel=2)

        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._pw:
            await self._pw.stop()

    async def new_page(self) -> "Page":
        if not self._context:
            raise BrowserError("Browser context not initialized. Use as async context manager.")
        return await self._context.new_page()

    async def clear_session(self) -> None:
        """Delete the saved session state for this session name."""
        if not self._session_name:
            return
        storage_file = self._sessions_dir / self._session_name / "storage.json"
        if storage_file.exists():
            storage_file.unlink()
