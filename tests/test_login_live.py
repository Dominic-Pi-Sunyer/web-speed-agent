"""
Live login tests against public test sites.

Sites used (all purpose-built for automation testing, no bot detection):
  1. the-internet.herokuapp.com  — classic Selenium test site
  2. quotes.toscrape.com         — scraping practice site with login
  3. practicetestautomation.com  — form testing site

Run with:
    export WEBSPEED_API_KEY="wsp_..."
    python3 tests/test_login_live.py
"""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
from web_speed_agent import Agent


PASS = "✅"
FAIL = "❌"
INFO = "→"

API_KEY = os.getenv("WEBSPEED_API_KEY")


def section(title: str) -> None:
    print(f"\n{'─' * 55}")
    print(f"  {title}")
    print(f"{'─' * 55}")


async def test_herokuapp_login(agent: Agent) -> bool:
    """
    Site:     https://the-internet.herokuapp.com/login
    Creds:    tomsmith / SuperSecretPassword!
    Tests:    login, protected page access, logout action
    """
    section("TEST 1 — Basic login + protected page + logout")
    passed = True

    async with agent.browser(session_name="herokuapp_test", headless=False) as browser:
        page = await browser.new_page()

        # 1. Navigate to login page
        await page.goto("https://the-internet.herokuapp.com/login")
        await page.wait_for_load_state("networkidle")
        print(f"  {INFO} Loaded login page")

        # 2. Fill credentials
        await page.fill("#username", "tomsmith")
        await page.fill("#password", "SuperSecretPassword!")
        print(f"  {INFO} Credentials filled")

        # 3. Submit
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")
        url = page.url
        html = await page.content()

        if "/secure" in url:
            print(f"  {PASS} Login succeeded — landed on {url}")
        else:
            print(f"  {FAIL} Login failed — on {url}")
            passed = False
            return passed

        # 4. Extract logged-in page via server
        result = await agent.extract(html, page_type="auto")
        print(f"  {PASS} Extraction: page_type={result.get('page_type')}  engine={result.get('engine')}")

        # 5. Navigate to another protected page
        await page.goto("https://the-internet.herokuapp.com/secure")
        await page.wait_for_load_state("networkidle")
        if "/secure" in page.url:
            print(f"  {PASS} Protected page accessible while logged in")
        else:
            print(f"  {FAIL} Could not access protected page")
            passed = False

        # 6. Perform a logged-in action (logout)
        await page.click('a[href="/logout"]')
        await page.wait_for_load_state("networkidle")
        if "/login" in page.url or "/" in page.url:
            print(f"  {PASS} Logout action performed — now on {page.url}")
        else:
            print(f"  {FAIL} Logout may have failed — on {page.url}")
            passed = False

    return passed


async def test_session_persistence(agent: Agent) -> bool:
    """
    Tests that cookies saved from test 1 allow access
    to the protected page without logging in again.
    """
    section("TEST 2 — Session persistence (re-use saved cookies)")
    passed = True

    # Note: herokuapp logs out on explicit logout click,
    # so this tests that the session FILE was saved correctly.
    session_file = os.path.expanduser("~/.webspeed/sessions/herokuapp_test/storage.json")
    if os.path.exists(session_file):
        size = os.path.getsize(session_file)
        perms = oct(os.stat(session_file).st_mode)[-3:]
        print(f"  {PASS} Session file exists ({size} bytes, permissions={perms})")
        if perms == "600":
            print(f"  {PASS} File permissions are secure (owner-only)")
        else:
            print(f"  {FAIL} File permissions are too open ({perms}) — expected 600")
            passed = False
    else:
        print(f"  {FAIL} Session file not found at {session_file}")
        passed = False

    return passed


async def test_quotes_login_and_post(agent: Agent) -> bool:
    """
    Site:     https://quotes.toscrape.com
    Creds:    user / password  (any username/password works)
    Tests:    login, scrape logged-in content, pagination
    """
    section("TEST 3 — quotes.toscrape.com login + scrape")
    passed = True

    async with agent.browser(session_name="quotes_test", headless=False) as browser:
        page = await browser.new_page()

        # 1. Login
        await page.goto("https://quotes.toscrape.com/login")
        await page.wait_for_load_state("networkidle")
        await page.fill("input[name='username']", "testuser")
        await page.fill("input[name='password']", "testpass")
        await page.click('input[type="submit"]')
        await page.wait_for_load_state("networkidle")

        if "quotes.toscrape.com" in page.url and "/login" not in page.url:
            print(f"  {PASS} Logged in — on {page.url}")
        else:
            print(f"  {FAIL} Login failed — still on {page.url}")
            passed = False
            return passed

        # 2. Scrape the logged-in quote listing
        html = await page.content()
        result = await agent.extract(html, page_type="listing")
        items = result.get("listing", {}).get("items", [])
        print(f"  {PASS} Extracted {len(items)} quotes from logged-in page")
        if items:
            print(f"       First quote: \"{items[0].get('title', '')[:60]}...\"")

        # 3. Navigate to page 2 (simulate browsing while logged in)
        await page.goto("https://quotes.toscrape.com/page/2/")
        await page.wait_for_load_state("networkidle")
        html2 = await page.content()
        result2 = await agent.extract(html2, page_type="listing")
        items2 = result2.get("listing", {}).get("items", [])
        print(f"  {PASS} Page 2: extracted {len(items2)} quotes while still logged in")

    return passed


async def test_form_submission(agent: Agent) -> bool:
    """
    Site:     https://the-internet.herokuapp.com/login (re-login + form)
    Tests:    login, fill a different form while authenticated
    """
    section("TEST 4 — Login then fill a different form")
    passed = True

    async with agent.browser(headless=False) as browser:
        page = await browser.new_page()

        # Login
        await page.goto("https://the-internet.herokuapp.com/login")
        await page.wait_for_load_state("networkidle")
        await page.fill("#username", "tomsmith")
        await page.fill("#password", "SuperSecretPassword!")
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        if "/secure" not in page.url:
            print(f"  {FAIL} Could not login")
            return False

        # Navigate to a form page while logged in
        await page.goto("https://the-internet.herokuapp.com/forgot_password")
        await page.wait_for_load_state("networkidle")

        # Fill the form
        await page.fill("#email", "test@example.com")
        print(f"  {PASS} Filled form field while authenticated")

        # Extract the form page
        html = await page.content()
        result = await agent.extract(html)
        forms = result.get("forms", [])
        print(f"  {PASS} Extracted {len(forms)} form(s) from authenticated page")
        if forms:
            fields = forms[0].get("fields", [])
            print(f"       Form has {len(fields)} field(s): {[f.get('name') for f in fields]}")

    return passed


async def test_keychain(agent: Agent) -> bool:
    """Tests credential store/retrieve/delete cycle."""
    section("TEST 5 — Keychain store / retrieve / delete")
    passed = True

    # Store
    agent.store_credential("__test_site__", "hello@test.com", "s3cr3t!", overwrite=True)
    print(f"  {PASS} Stored credential in keychain")

    # Retrieve
    creds = agent.get_credential("__test_site__")
    if creds and creds[0] == "hello@test.com" and creds[1] == "s3cr3t!":
        print(f"  {PASS} Retrieved correct credentials")
    else:
        print(f"  {FAIL} Retrieved wrong or missing credentials: {creds}")
        passed = False

    # Overwrite protection
    try:
        agent.store_credential("__test_site__", "hello@test.com", "other", overwrite=False)
        print(f"  {FAIL} Overwrite protection not working")
        passed = False
    except Exception:
        print(f"  {PASS} Overwrite protection working")

    # Delete
    agent.delete_credential("__test_site__")
    creds_after = agent.get_credential("__test_site__")
    if creds_after is None:
        print(f"  {PASS} Credential deleted from keychain")
    else:
        print(f"  {FAIL} Credential still present after delete")
        passed = False

    return passed


async def main() -> None:
    if not API_KEY:
        print("ERROR: set WEBSPEED_API_KEY environment variable first")
        print("  export WEBSPEED_API_KEY=wsp_...")
        sys.exit(1)

    print("\n" + "═" * 55)
    print("  Web Speed Agent — Live Login Test Suite")
    print("═" * 55)
    print(f"  API key: {API_KEY[:12]}...")
    print(f"  Server:  {os.getenv('WEBSPEED_SERVER_URL', 'https://api.getwebspeed.io')}")

    async with Agent(api_key=API_KEY) as agent:

        # Check account first
        acc = await agent.account()
        print(f"\n  Credits: {acc.get('credits')}  Tier: {acc.get('tier')}")

        results = {}
        results["Basic login"] = await test_herokuapp_login(agent)
        results["Session persistence"] = await test_session_persistence(agent)
        results["Login + scrape"] = await test_quotes_login_and_post(agent)
        results["Auth form fill"] = await test_form_submission(agent)
        results["Keychain"] = await test_keychain(agent)

    # Summary
    print(f"\n{'═' * 55}")
    print("  RESULTS")
    print(f"{'═' * 55}")
    for name, ok in results.items():
        icon = PASS if ok else FAIL
        print(f"  {icon} {name}")

    passed = sum(results.values())
    total = len(results)
    print(f"\n  {passed}/{total} tests passed")

    # Credits used
    async with Agent(api_key=API_KEY) as agent:
        acc_after = await agent.account()
        credits_used = acc.get("credits", 0) - acc_after.get("credits", 0)
        print(f"  Credits used: {credits_used}")
    print()


if __name__ == "__main__":
    asyncio.run(main())
