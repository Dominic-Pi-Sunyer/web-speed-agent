"""Playwright browser context with session persistence."""

from __future__ import annotations

import json
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Any

from .exceptions import BrowserError, PlaywrightNotInstalledError

if TYPE_CHECKING:
    from playwright.async_api import Browser, BrowserContext, Page


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
            session_dir.mkdir(parents=True, exist_ok=True)
            storage_file = session_dir / "storage.json"
            if storage_file.exists():
                try:
                    ctx_opts["storage_state"] = str(storage_file)
                except Exception:
                    pass

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
            session_dir.mkdir(parents=True, exist_ok=True)
            storage_file = session_dir / "storage.json"
            try:
                await self._context.storage_state(path=str(storage_file))
            except Exception:
                pass

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
