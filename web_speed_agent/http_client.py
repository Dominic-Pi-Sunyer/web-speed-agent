"""HTTP client for Web Speed API calls."""

from __future__ import annotations

from urllib.parse import urlparse

import httpx

from .exceptions import (
    APIError,
    AuthenticationError,
    InsufficientCreditsError,
    NetworkError,
    RateLimitError,
)

_USER_AGENT = "web-speed-agent/0.1.0"


def _validate_url(url: str) -> None:
    """Reject non-HTTP(S) URLs to prevent misuse."""
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(f"Only http:// and https:// URLs are supported (got {url!r})")
    if not parsed.netloc:
        raise ValueError(f"URL has no host: {url!r}")


class APIClient:
    def __init__(self, api_key: str, server_url: str, timeout: int = 30) -> None:
        self._key = api_key
        self._base = server_url.rstrip("/")
        self._timeout = timeout
        self._client = httpx.AsyncClient(
            headers={
                "x-web-speed-key": api_key,
                "user-agent": _USER_AGENT,
            },
            timeout=timeout,
            verify=True,  # Explicit: always verify TLS certificates
        )

    async def extract(self, html: str, page_type: str = "auto") -> dict:
        """Send HTML to the advanced extraction endpoint."""
        try:
            resp = await self._client.post(
                f"{self._base}/v1/extract",
                json={"html": html, "page_type": page_type},
            )
        except httpx.TimeoutException as exc:
            raise NetworkError(f"Request timed out after {self._timeout}s") from exc
        except httpx.NetworkError as exc:
            raise NetworkError(f"Network error: {exc}") from exc

        return self._raise_for_status(resp)

    async def map_url(self, url: str, js: bool = False) -> dict:
        """Call /v1/map directly (no local browser needed)."""
        _validate_url(url)
        try:
            resp = await self._client.get(
                f"{self._base}/v1/map",
                params={"url": url, "js": str(js).lower()},
            )
        except httpx.TimeoutException as exc:
            raise NetworkError(f"Request timed out after {self._timeout}s") from exc
        except httpx.NetworkError as exc:
            raise NetworkError(f"Network error: {exc}") from exc

        return self._raise_for_status(resp)

    async def account(self) -> dict:
        """Fetch account info (credits, tier, usage)."""
        try:
            resp = await self._client.get(f"{self._base}/v1/account")
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise NetworkError(str(exc)) from exc
        return self._raise_for_status(resp)

    async def close(self) -> None:
        await self._client.aclose()

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> dict:
        if resp.status_code == 401:
            raise AuthenticationError("Invalid or missing API key")
        if resp.status_code == 403:
            try:
                msg = resp.json().get("message", "Insufficient credits or suspended key")
            except (ValueError, KeyError):
                msg = "Insufficient credits or suspended key"
            if "credit" in msg.lower():
                raise InsufficientCreditsError(msg)
            raise AuthenticationError(msg)
        if resp.status_code == 429:
            raise RateLimitError(429, "Rate limit exceeded. Upgrade your plan.")
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("message", resp.text)
            except (ValueError, KeyError):
                msg = resp.text
            raise APIError(resp.status_code, msg)
        return resp.json()
