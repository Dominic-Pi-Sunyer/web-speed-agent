"""Example: Monitor product prices on Amazon (no login required).

Demonstrates using agent.map() for public pages — no local browser needed.

Usage:
    WEBSPEED_API_KEY=sk_... python price_monitor.py
"""

import asyncio
import os
from web_speed_agent import Agent

PRODUCTS = [
    "https://www.amazon.com/dp/B0BSHF7WHW",  # Example ASINs
    "https://www.amazon.com/dp/B09V3KXJPB",
]


async def main() -> None:
    api_key = os.getenv("WEBSPEED_API_KEY")
    if not api_key:
        print("Set WEBSPEED_API_KEY env var. Get a free key at: https://getwebspeed.io/signup")
        return

    async with Agent(api_key=api_key) as agent:
        for url in PRODUCTS:
            print(f"\nChecking: {url}")
            try:
                # No local browser needed for public pages
                result = await agent.map(url, js=True)

                product = result.get("product", {})
                if product:
                    print(f"  Name:         {product.get('name', 'N/A')}")
                    print(f"  Price:        {product.get('price', 'N/A')}")
                    print(f"  Availability: {product.get('availability', 'N/A')}")
                    print(f"  Rating:       {product.get('rating', 'N/A')}")
                else:
                    print(f"  Page type: {result.get('page_type')} (not a product page)")

            except Exception as exc:
                print(f"  Error: {exc}")


if __name__ == "__main__":
    asyncio.run(main())
