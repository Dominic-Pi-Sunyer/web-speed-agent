"""Main Agent class — primary interface for the Web Speed SDK."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from .browser import ManagedBrowser
from .config import Config
from .credentials import delete as _cred_delete
from .credentials import get_pair as _cred_get
from .credentials import store_pair as _cred_store
from .exceptions import AuthenticationError
from .http_client import APIClient


class Agent:
    """Browser automation + Web Speed API integration.

    Handles Playwright browser sessions locally (credentials never leave your
    machine) and calls the Web Speed API for advanced data extraction.

    Args:
        api_key: Web Speed API key. Falls back to WEBSPEED_API_KEY env var,
                 then ~/.webspeed/config.yaml.
        server_url: Override the API server (default: https://api.getwebspeed.io).
        config_dir: Where to store config, sessions, and logs.
        headless: Run browser headlessly (default True).

    Example::

        import asyncio
        from web_speed_agent import Agent

        async def main():
            agent = Agent(api_key="sk_...")

            async with agent.browser(session_name="amazon") as browser:
                page = await browser.new_page()
                await page.goto("https://amazon.com")
                html = await page.content()
                result = await agent.extract(html, page_type="listing")
                print(result["listing"]["items"])

        asyncio.run(main())
    """

    def __init__(
        self,
        api_key: str | None = None,
        server_url: str | None = None,
        config_dir: str = "~/.webspeed",
        headless: bool = True,
    ) -> None:
        self._config = Config(Path(config_dir).expanduser())
        self._headless = headless

        resolved_key = api_key or self._config.api_key
        resolved_url = server_url or self._config.server_url

        self._api_key = resolved_key
        self._client: APIClient | None = None

        if resolved_key:
            self._client = APIClient(
                api_key=resolved_key,
                server_url=resolved_url,
                timeout=self._config.timeout,
            )

    # ── Browser ───────────────────────────────────────────────────────────────

    def browser(
        self,
        session_name: str | None = None,
        headless: bool | None = None,
        proxy: str | None = None,
    ) -> ManagedBrowser:
        """Return an async context manager for a local Playwright browser.

        Args:
            session_name: Name for persistent session (cookies saved to
                          ~/.webspeed/sessions/<name>/). None = no persistence.
            headless: Override instance headless setting for this session.
            proxy: Optional proxy URL (e.g. "socks5://localhost:1080").

        Returns:
            ManagedBrowser context manager yielding a browser with .new_page().

        Example::

            async with agent.browser(session_name="united") as browser:
                page = await browser.new_page()
                await page.goto("https://united.com")
                await page.fill('[name="email"]', email)
                await page.fill('[name="password"]', password)
                await page.click('button[type="submit"]')
                html = await page.content()
        """
        return ManagedBrowser(
            sessions_dir=self._config.sessions_dir,
            session_name=session_name,
            headless=headless if headless is not None else self._headless,
            proxy=proxy,
        )

    # ── Extraction ────────────────────────────────────────────────────────────

    async def extract(self, html: str, page_type: str = "auto") -> dict[str, Any]:
        """Send HTML to the Web Speed API for advanced structured extraction.

        60–85% more token-efficient than raw HTML. Returns page-type-aware
        structured data (article, product, listing, or generic).

        Args:
            html: Raw HTML string (e.g. from ``page.content()``).
            page_type: "article", "product", "listing", or "auto" (server detects).

        Returns:
            Structured extraction result with ``engine: "advanced"`` marker.

        Raises:
            AuthenticationError: No API key set.
            InsufficientCreditsError: Account out of credits.
            RateLimitError: Rate limit exceeded.
            APIError: Other API error.

        Example::

            result = await agent.extract(html, page_type="product")
            print(result["product"]["price"])
        """
        self._require_key()
        return await self._client.extract(html, page_type)

    async def map(self, url: str, js: bool = False) -> dict[str, Any]:
        """Extract a public URL directly via the Web Speed API (no local browser).

        For public pages that don't require login. Uses advanced extraction
        engine automatically.

        Args:
            url: Full URL to fetch and extract.
            js: If True, renders JavaScript before extracting.

        Returns:
            Structured extraction result.

        Example::

            result = await agent.map("https://techcrunch.com/2026/05/06/article/")
            print(result["article"]["sections"])
        """
        self._require_key()
        return await self._client.map_url(url, js)

    async def account(self) -> dict[str, Any]:
        """Fetch account info: credits remaining, tier, and usage stats.

        Returns:
            dict with keys: credits, tier, status, lifetime (total/hits/misses).
        """
        self._require_key()
        return await self._client.account()

    # ── Credentials ───────────────────────────────────────────────────────────

    def store_credential(
        self,
        site: str,
        username: str,
        password: str,
        overwrite: bool = False,
    ) -> None:
        """Store login credentials in the system keychain.

        Credentials are stored locally and NEVER sent to Web Speed servers.
        Uses macOS Keychain, Windows Credential Manager, or Linux secret-tool.

        Args:
            site: Identifier for the site (e.g. "united", "amazon").
            username: Email or username.
            password: Password.
            overwrite: Replace existing credential if present.

        Example::

            agent.store_credential("united", "me@example.com", "hunter2")
        """
        _cred_store(site, username, password, overwrite=overwrite)

    def get_credential(self, site: str) -> tuple[str, str] | None:
        """Retrieve stored credentials from keychain.

        Args:
            site: Identifier used when storing.

        Returns:
            (username, password) tuple, or None if not found.

        Example::

            creds = agent.get_credential("united")
            if creds:
                username, password = creds
        """
        return _cred_get(site)

    def delete_credential(self, site: str) -> None:
        """Remove stored credentials from keychain.

        Args:
            site: Identifier used when storing.
        """
        _cred_delete(site)

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def close(self) -> None:
        """Close the HTTP client. Call when done if not using as context manager."""
        if self._client:
            await self._client.close()

    async def __aenter__(self) -> "Agent":
        return self

    async def __aexit__(self, *_: Any) -> None:
        await self.close()

    # ── Private ───────────────────────────────────────────────────────────────

    def _require_key(self) -> None:
        if not self._api_key or not self._client:
            raise AuthenticationError(
                "No API key set. Pass api_key=... or set WEBSPEED_API_KEY env var. "
                "Get a free key at: https://getwebspeed.io/signup"
            )
