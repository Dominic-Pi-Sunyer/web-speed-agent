"""Web Speed Agent SDK.

Authenticate locally with Playwright, extract structured data via Web Speed API.

Quick start::

    import asyncio
    from web_speed_agent import Agent

    async def main():
        agent = Agent(api_key="sk_...")

        async with agent.browser(session_name="my_site") as browser:
            page = await browser.new_page()
            await page.goto("https://example.com")
            html = await page.content()
            result = await agent.extract(html, page_type="article")
            print(result["article"]["sections"])

    asyncio.run(main())
"""

from .agent import Agent
from .exceptions import (
    APIError,
    AuthenticationError,
    BrowserError,
    CredentialError,
    InsufficientCreditsError,
    NetworkError,
    PlaywrightNotInstalledError,
    RateLimitError,
    WebSpeedError,
)

__all__ = [
    "Agent",
    "WebSpeedError",
    "AuthenticationError",
    "InsufficientCreditsError",
    "APIError",
    "RateLimitError",
    "CredentialError",
    "BrowserError",
    "PlaywrightNotInstalledError",
    "NetworkError",
]

__version__ = "0.1.0"
