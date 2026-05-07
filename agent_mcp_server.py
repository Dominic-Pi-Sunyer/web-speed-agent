#!/usr/bin/env python3.11
"""Web Speed Agent — local MCP server.

Runs on your machine so the browser, credentials, and cookies never leave.
Claude calls these tools via MCP to log into sites and take actions.

Start:
    WEBSPEED_API_KEY="wsp_..." python3.11 agent_mcp_server.py

Add to Claude Desktop (~/.claude/claude_desktop_config.json):
    {
      "mcpServers": {
        "web-speed-agent": {
          "command": "python3.11",
          "args": ["/path/to/agent_mcp_server.py"],
          "env": { "WEBSPEED_API_KEY": "wsp_..." }
        }
      }
    }

Add to Claude Code:
    claude mcp add web-speed-agent python3.11 /path/to/agent_mcp_server.py
"""

from __future__ import annotations

import json
import os
import re
import stat
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import FastMCP
from web_speed_agent import Agent
from web_speed_agent.credentials import store_pair, get_pair, delete

# ── config ────────────────────────────────────────────────────────────────────

API_KEY     = os.getenv("WEBSPEED_API_KEY", "")
SERVER_URL  = os.getenv("WEBSPEED_SERVER_URL", "https://api.getwebspeed.io")
SESSIONS    = Path("~/.webspeed/sessions").expanduser()
HEADLESS    = os.getenv("WEBSPEED_HEADLESS", "false").lower() != "false"

# ── global browser state (persists across tool calls in this process) ─────────

_pw       = None   # playwright instance
_browser  = None   # Browser
_context  = None   # BrowserContext
_page     = None   # Page  (the active tab)
_session_name: str | None = None

mcp = FastMCP("web-speed-agent")

# ── helpers ───────────────────────────────────────────────────────────────────

def _ok(data: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False, indent=2)

def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2)

def _require_page():
    if _page is None:
        raise RuntimeError("No browser open. Call open_browser first.")
    return _page

def _secure_mkdir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    path.chmod(0o700)

async def _page_summary(page) -> dict:
    """Title + URL of the current page."""
    try:
        title = await page.title()
    except Exception:
        title = ""
    return {"url": page.url, "title": title}

# ── tools ─────────────────────────────────────────────────────────────────────

@mcp.tool()
async def store_credential(site: str, username: str, password: str) -> str:
    """Save login credentials to the system keychain (macOS/Windows/Linux).

    Credentials are stored locally and NEVER sent to any server.
    Use site as a short identifier, e.g. "indiehackers", "twitter", "gmail".
    """
    try:
        store_pair(site, username, password, overwrite=True)
        return _ok({"site": site, "username": username,
                    "message": f"Saved credentials for '{site}' to system keychain."})
    except Exception as exc:
        return _err(str(exc))


@mcp.tool()
async def open_browser(
    session_name: str | None = None,
    headless: bool | None = None,
) -> str:
    """Open a local Playwright browser.

    Args:
        session_name: Optional name to persist cookies between runs
                      (e.g. "indiehackers"). Omit for a fresh session.
        headless: Run browser without a visible window. Default: False (visible).
    """
    global _pw, _browser, _context, _page, _session_name

    # Close any existing browser cleanly
    if _page or _context or _browser:
        await close_browser()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return _err("Playwright not installed. Run: pip install playwright && playwright install chromium")

    try:
        _pw = await async_playwright().start()
        run_headless = headless if headless is not None else HEADLESS
        _browser = await _pw.chromium.launch(headless=run_headless)

        ctx_opts: dict[str, Any] = {}
        _session_name = session_name

        if session_name:
            _validate_session_name(session_name)
            session_dir = SESSIONS / session_name
            _secure_mkdir(session_dir)
            storage_file = session_dir / "storage.json"
            if storage_file.exists():
                mode = storage_file.stat().st_mode
                if mode & (stat.S_IRGRP | stat.S_IROTH):
                    import warnings
                    warnings.warn(f"Session file {storage_file} is readable by others.")
                ctx_opts["storage_state"] = str(storage_file)

        _context = await _browser.new_context(**ctx_opts)
        _page = await _context.new_page()

        msg = f"Browser opened"
        if session_name:
            loaded = "storage.json" in str(ctx_opts.get("storage_state", ""))
            msg += f" with session '{session_name}'"
            msg += " (existing cookies loaded)" if loaded else " (fresh session)"
        return _ok({"message": msg, "headless": run_headless, "session": session_name})

    except Exception as exc:
        return _err(f"Could not launch browser: {exc}. Try: playwright install chromium")


@mcp.tool()
async def navigate(url: str) -> str:
    """Navigate to a URL and return the page title and final URL.

    Always call this before interacting with a new page.
    """
    page = _require_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass  # networkidle can time out on busy pages — that's fine
    summary = await _page_summary(page)
    return _ok({"message": f"Navigated to {summary['url']}", **summary})


@mcp.tool()
async def login(
    site: str | None = None,
    username: str | None = None,
    password: str | None = None,
    username_selector: str | None = None,
    password_selector: str | None = None,
    submit_selector: str | None = None,
) -> str:
    """Fill a login form and submit it.

    Credentials: provide either `site` (to load from keychain) OR
    `username` + `password` directly.

    Selectors: if omitted, common patterns are tried automatically
    (input[type=email], input[name=username], etc.).

    Use navigate() to go to the login page first.
    """
    page = _require_page()

    # Resolve credentials
    _user, _pass = None, None
    if site:
        creds = get_pair(site)
        if not creds:
            return _err(f"No credentials stored for '{site}'. Call store_credential first.")
        _user, _pass = creds
    if username:
        _user = username
    if password:
        _pass = password
    if not _user or not _pass:
        return _err("Provide either site (keychain lookup) or username + password.")

    # Auto-detect selectors if not provided
    u_sel = username_selector or await _find_username_field(page)
    p_sel = password_selector or "input[type='password']"
    s_sel = submit_selector or await _find_submit_button(page)

    if not u_sel:
        return _err("Could not find a username/email field. Provide username_selector.")
    if not s_sel:
        return _err("Could not find a submit button. Provide submit_selector.")

    try:
        await page.fill(u_sel, _user)
        await page.fill(p_sel, _pass)
        await page.click(s_sel)
        await page.wait_for_load_state("domcontentloaded", timeout=15_000)
        try:
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        summary = await _page_summary(page)
        return _ok({"message": f"Login submitted — now on: {summary['url']}", **summary})
    except Exception as exc:
        return _err(f"Login failed: {exc}")


@mcp.tool()
async def read_page(page_type: str = "auto") -> str:
    """Extract structured data from the current page via the Web Speed API.

    Returns type-aware structured JSON:
      article  → title, author, sections, links
      product  → name, price, availability, specs
      listing  → items with title, url, price, snippet
      other    → headings, navigation, forms, text_blocks

    Costs 1 Web Speed credit. Requires WEBSPEED_API_KEY.
    """
    page = _require_page()
    if not API_KEY:
        return _err("WEBSPEED_API_KEY not set. Set it as an env var or pass to the server.")

    try:
        html = await page.content()
        async with Agent(api_key=API_KEY, server_url=SERVER_URL) as agent:
            result = await agent.extract(html, page_type=page_type)
        summary = await _page_summary(page)
        return _ok({"current_url": summary["url"], "current_title": summary["title"],
                    "extraction": result})
    except Exception as exc:
        return _err(f"Extraction failed: {exc}")


@mcp.tool()
async def click(selector: str, wait_for_navigation: bool = True) -> str:
    """Click an element by CSS selector.

    Args:
        selector: CSS selector for the element to click.
        wait_for_navigation: Wait for the page to load after clicking (default True).
    """
    page = _require_page()
    try:
        await page.click(selector, timeout=10_000)
        if wait_for_navigation:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                await page.wait_for_load_state("networkidle", timeout=6_000)
            except Exception:
                pass
        summary = await _page_summary(page)
        return _ok({"message": f"Clicked '{selector}'", **summary})
    except Exception as exc:
        return _err(f"Could not click '{selector}': {exc}")


@mcp.tool()
async def fill_field(selector: str, value: str, press_tab: bool = False) -> str:
    """Type a value into a form field.

    Args:
        selector: CSS selector for the input field.
        value: Text to type.
        press_tab: Press Tab after filling (useful to trigger field validation).
    """
    page = _require_page()
    try:
        await page.fill(selector, value, timeout=8_000)
        if press_tab:
            await page.keyboard.press("Tab")
        return _ok({"message": f"Filled '{selector}' with value"})
    except Exception as exc:
        return _err(f"Could not fill '{selector}': {exc}")


@mcp.tool()
async def submit_form(selector: str | None = None) -> str:
    """Submit a form by clicking a submit button or pressing Enter.

    Args:
        selector: CSS selector of the submit button or form. If omitted,
                  presses Enter on the focused element.
    """
    page = _require_page()
    try:
        if selector:
            await page.click(selector, timeout=8_000)
        else:
            await page.keyboard.press("Enter")
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15_000)
            await page.wait_for_load_state("networkidle", timeout=8_000)
        except Exception:
            pass
        summary = await _page_summary(page)
        return _ok({"message": f"Form submitted — now on: {summary['url']}", **summary})
    except Exception as exc:
        return _err(f"Submit failed: {exc}")


@mcp.tool()
async def get_page_info() -> str:
    """Return the current page URL, title, and visible text snippet.

    Useful for orientation — call this to confirm where the browser is.
    """
    page = _require_page()
    summary = await _page_summary(page)
    try:
        body_text = await page.inner_text("body")
        snippet = body_text[:800].strip()
    except Exception:
        snippet = ""
    return _ok({**summary, "text_snippet": snippet})


@mcp.tool()
async def wait_for_element(selector: str, timeout_ms: int = 10000) -> str:
    """Wait for an element to appear on the page.

    Useful after an action that triggers async loading (e.g. clicking a button
    that loads a new section). Returns ok once the element is visible.
    """
    page = _require_page()
    try:
        await page.wait_for_selector(selector, timeout=timeout_ms)
        return _ok({"message": f"Element '{selector}' appeared on page"})
    except Exception as exc:
        return _err(f"Element '{selector}' did not appear within {timeout_ms}ms: {exc}")


@mcp.tool()
async def close_browser() -> str:
    """Save the browser session (cookies) and close the browser.

    Always call this when you are done to persist the session for next time.
    """
    global _pw, _browser, _context, _page, _session_name

    saved = False
    if _context and _session_name:
        try:
            session_dir = SESSIONS / _session_name
            _secure_mkdir(session_dir)
            storage_file = session_dir / "storage.json"
            await _context.storage_state(path=str(storage_file))
            storage_file.chmod(0o600)
            saved = True
        except Exception:
            pass

    try:
        if _context:
            await _context.close()
        if _browser:
            await _browser.close()
        if _pw:
            await _pw.stop()
    except Exception:
        pass

    _page = _context = _browser = _pw = None
    msg = "Browser closed"
    if saved:
        msg += f" and session '{_session_name}' saved"
    _session_name = None
    return _ok({"message": msg, "session_saved": saved})


@mcp.tool()
async def account_info() -> str:
    """Check your Web Speed API credit balance and account status."""
    if not API_KEY:
        return _err("WEBSPEED_API_KEY not set.")
    try:
        async with Agent(api_key=API_KEY, server_url=SERVER_URL) as agent:
            info = await agent.account()
        return _ok(info)
    except Exception as exc:
        return _err(str(exc))


# ── private helpers ───────────────────────────────────────────────────────────

_SESSION_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

def _validate_session_name(name: str) -> None:
    if not _SESSION_NAME_RE.match(name):
        raise ValueError(f"Invalid session name {name!r}. Use letters, digits, hyphens, underscores.")


async def _find_username_field(page) -> str | None:
    """Try common username/email selector patterns."""
    candidates = [
        "input[type='email']",
        "input[name='email']",
        "input[name='username']",
        "input[name='user']",
        "input[name='login']",
        "input[id='email']",
        "input[id='username']",
        "input[id='user']",
        "input[placeholder*='email' i]",
        "input[placeholder*='username' i]",
        "input[autocomplete='email']",
        "input[autocomplete='username']",
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                return sel
        except Exception:
            continue
    return None


async def _find_submit_button(page) -> str | None:
    """Try common submit button selector patterns."""
    candidates = [
        "button[type='submit']",
        "input[type='submit']",
        "button:has-text('Log in')",
        "button:has-text('Sign in')",
        "button:has-text('Login')",
        "button:has-text('Continue')",
        "[data-testid*='login']",
        "[data-testid*='signin']",
        "form button",
    ]
    for sel in candidates:
        try:
            el = page.locator(sel).first
            if await el.count() > 0:
                return sel
        except Exception:
            continue
    return None


# ── entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    mcp.run(transport="stdio")
