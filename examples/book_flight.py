"""Example: Find flights using authenticated browsing + Web Speed extraction.

This example shows how to:
1. Store login credentials locally (never sent to server)
2. Authenticate to a site via local Playwright
3. Send page HTML to Web Speed API for structured extraction

Usage:
    # First time: store credentials
    python book_flight.py --setup

    # Find flights
    python book_flight.py --from SFO --to JFK --date 2026-06-01
"""

import asyncio
import argparse
import os
from web_speed_agent import Agent


async def setup_credentials(agent: Agent) -> None:
    """One-time setup: store credentials in system keychain."""
    email = input("United email: ")
    password = input("United password: ")
    agent.store_credential("united", email, password)
    print("✓ Credentials saved to system keychain (never sent to any server)")


async def find_flights(agent: Agent, origin: str, dest: str, date: str) -> None:
    """Log in to United and extract available flights."""
    creds = agent.get_credential("united")
    if not creds:
        print("No credentials stored. Run with --setup first.")
        return

    username, password = creds

    # Credentials stay on your machine — Playwright runs locally
    async with agent.browser(session_name="united_account") as browser:
        page = await browser.new_page()

        print("Navigating to United...")
        await page.goto("https://www.united.com")

        # Login locally
        try:
            await page.click('button:has-text("Sign In")', timeout=5000)
            await page.fill('[name="email"]', username)
            await page.fill('[name="password"]', password)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle", timeout=15000)
            print("✓ Logged in")
        except Exception:
            print("Could not find login form — may already be logged in")

        # Search for flights
        print(f"Searching {origin} → {dest} on {date}...")
        await page.goto(
            f"https://www.united.com/en/us/fsr/choose-flights"
            f"?f={origin}&t={dest}&d={date}&tt=1&at=1"
        )
        await page.wait_for_load_state("networkidle", timeout=20000)

        # Get page HTML and send to Web Speed API for extraction
        html = await page.content()

    # Extract structured data via API — this is where Web Speed earns its keep
    print("Extracting flight data...")
    result = await agent.extract(html, page_type="listing")

    if result.get("js_required"):
        print("Page requires JavaScript rendering. Try agent.map(url, js=True) instead.")
        return

    items = result.get("listing", {}).get("items", [])
    if not items:
        print("No flights found in page output.")
        return

    print(f"\n✓ Found {len(items)} flights:\n")
    for i, flight in enumerate(items[:10], 1):
        title = flight.get("title", "Unknown flight")
        price = flight.get("price", "")
        snippet = flight.get("snippet", "")
        print(f"  {i}. {title}")
        if price:
            print(f"     Price: {price}")
        if snippet:
            print(f"     {snippet}")
        print()


async def main() -> None:
    parser = argparse.ArgumentParser(description="Find flights via United.com")
    parser.add_argument("--setup", action="store_true", help="Store credentials")
    parser.add_argument("--from", dest="origin", default="SFO")
    parser.add_argument("--to", dest="dest", default="JFK")
    parser.add_argument("--date", default="2026-06-15")
    args = parser.parse_args()

    api_key = os.getenv("WEBSPEED_API_KEY")
    if not api_key:
        print("Set WEBSPEED_API_KEY env var. Get a free key at: https://getwebspeed.io/signup")
        return

    async with Agent(api_key=api_key) as agent:
        if args.setup:
            await setup_credentials(agent)
        else:
            await find_flights(agent, args.origin, args.dest, args.date)


if __name__ == "__main__":
    asyncio.run(main())
