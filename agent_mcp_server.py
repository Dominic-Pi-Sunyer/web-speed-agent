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

import configparser
import json
import os
import platform
import re
import shutil
import sqlite3
import stat
import sys
import tempfile
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

# ── stealth browser config ────────────────────────────────────────────────────

_UA_CHROME = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_UA_FIREFOX = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:133.0) "
    "Gecko/20100101 Firefox/133.0"
)

_CHROMIUM_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-extensions",
    "--disable-plugins",
    "--disable-background-networking",
    "--no-first-run",
    "--disable-dev-shm-usage",
]

# Masks headless browser signals before any page script runs.
# Works on both Chromium and Firefox (pure JS, no browser-specific APIs).
_STEALTH_INIT = """\
Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
window.chrome = {runtime: {}, loadTimes: function(){}, csi: function(){}, app: {}};
Object.defineProperty(navigator, 'plugins', {get: () => [
  {name: 'Chrome PDF Plugin', filename: 'internal-pdf-viewer', description: 'Portable Document Format'},
  {name: 'Chrome PDF Viewer', filename: 'mhjfbmdgcfjbbpaeojofohoefgiehjai', description: ''},
  {name: 'Native Client', filename: 'internal-nacl-plugin', description: ''}
]});
Object.defineProperty(navigator, 'languages', {get: () => ['en-US', 'en']});
"""

# ── global browser state (persists across tool calls in this process) ─────────

_pw             = None   # playwright instance
_browser        = None   # Browser (None in persistent-context mode)
_context        = None   # BrowserContext
_page           = None   # Page  (the active tab)
_session_name:  str | None = None
_cdp_mode:      bool = False   # True when connected to an existing browser via CDP
_persistent_ctx: bool = False  # True when using launch_persistent_context (Firefox profile)
_browser_name:  str = "chrome"  # "chrome" | "firefox" | "edge"

mcp = FastMCP("web-speed-agent")

# ── helpers ───────────────────────────────────────────────────────────────────

def _resolve_firefox_profile(path: str) -> tuple[Path | None, str]:
    """Return (profile_dir, description) for a Firefox profile.

    Pass "auto" to find the standard (non-Nightly) default profile from
    profiles.ini, or pass an explicit path string.
    Returns (None, error_message) if not found.
    """
    if path != "auto":
        p = Path(path).expanduser()
        if p.exists():
            return p, str(p)
        return None, f"path does not exist: {path}"

    system = platform.system()
    if system == "Darwin":
        base = Path.home() / "Library" / "Application Support" / "Firefox"
    elif system == "Windows":
        base = Path(os.environ.get("APPDATA", "")) / "Mozilla" / "Firefox"
    else:
        base = Path.home() / ".mozilla" / "firefox"

    ini = base / "profiles.ini"
    if not ini.exists():
        return None, f"profiles.ini not found at {ini}"

    cfg = configparser.ConfigParser()
    cfg.read(ini)

    def _is_special(raw: str) -> bool:
        low = raw.lower()
        return any(k in low for k in ("nightly", "dev-edition", "developer"))

    def _try(section: str) -> Path | None:
        raw = cfg.get(section, "Path", fallback="")
        if not raw:
            return None
        relative = cfg.get(section, "IsRelative", fallback="1") == "1"
        p = (base / raw) if relative else Path(raw)
        return p if p.exists() else None

    # Pass 1: Default=1 sections that are NOT Nightly / Dev Edition
    for section in cfg.sections():
        if cfg.get(section, "Default", fallback="") == "1":
            raw = cfg.get(section, "Path", fallback="")
            if raw and not _is_special(raw):
                p = _try(section)
                if p:
                    return p, str(p)

    # Pass 2: any Default=1 section (user may only have Nightly)
    for section in cfg.sections():
        if cfg.get(section, "Default", fallback="") == "1":
            p = _try(section)
            if p:
                return p, str(p)

    # Pass 3: first non-Nightly profile on disk
    for section in cfg.sections():
        raw = cfg.get(section, "Path", fallback="")
        if raw and not _is_special(raw):
            p = _try(section)
            if p:
                return p, str(p)

    # Pass 4: any profile
    for section in cfg.sections():
        p = _try(section)
        if p:
            return p, str(p)

    return None, f"no valid profile found in {ini}"


def _find_chrome_profile(browser: str) -> Path | None:
    """Find the Chrome or Edge user-data directory on this machine."""
    system = platform.system()
    b = browser.lower()
    if b in ("chrome", "chromium"):
        paths = {
            "Darwin":  Path.home() / "Library" / "Application Support" / "Google" / "Chrome",
            "Windows": Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data",
            "Linux":   Path.home() / ".config" / "google-chrome",
        }
    elif b == "edge":
        paths = {
            "Darwin":  Path.home() / "Library" / "Application Support" / "Microsoft Edge",
            "Windows": Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data",
            "Linux":   Path.home() / ".config" / "microsoft-edge",
        }
    else:
        return None
    p = paths.get(system)
    return p if p and p.exists() else None


def _get_browser_type(browser: str, pw: Any):
    """Return the Playwright browser-type object for the requested browser."""
    b = browser.lower()
    if b in ("chrome", "chromium", "edge"):
        return pw.chromium
    if b in ("firefox", "ff"):
        return pw.firefox
    raise ValueError(
        f"Unknown browser '{browser}'. "
        "Choose: chrome, firefox, or edge"
    )


def _read_firefox_cookies(profile_dir: Path) -> list[dict]:
    """Read cookies from Firefox's cookies.sqlite as a read-only snapshot.

    SECURITY: cookies stay on-device. They are injected into the local
    Playwright browser context and only ever transmitted to the target
    websites as normal HTTP request headers — identical to regular browsing.
    They are NEVER sent to the Web Speed API or any third party.
    read_page() only sends page HTML to the API, not cookies.

    Works whether or not Firefox is running — we copy the file to a temp
    location first so we never touch or lock the real database.
    Firefox cookies are not encrypted (unlike Chrome), so values are
    directly readable from the SQLite file.
    """
    cookies_db = profile_dir / "cookies.sqlite"
    if not cookies_db.exists():
        return []

    tmp = Path(tempfile.mktemp(suffix="_ff_cookies.sqlite"))
    try:
        shutil.copy2(str(cookies_db), str(tmp))
        conn = sqlite3.connect(str(tmp))
        try:
            cursor = conn.execute(
                "SELECT name, value, host, path, expiry, isSecure, isHttpOnly, sameSite "
                "FROM moz_cookies"
            )
            same_site_map = {0: "None", 1: "Lax", 2: "Strict"}
            cookies = []
            for name, value, host, path, expiry, is_secure, is_http_only, same_site in cursor:
                if not host or name is None:
                    continue
                cookies.append({
                    "name": name,
                    "value": value or "",
                    "domain": host,
                    "path": path or "/",
                    "expires": int(expiry) if expiry and expiry > 0 else -1,
                    "secure": bool(is_secure),
                    "httpOnly": bool(is_http_only),
                    "sameSite": same_site_map.get(same_site, "None"),
                })
            return cookies
        finally:
            conn.close()
    except Exception:
        return []
    finally:
        try:
            tmp.unlink(missing_ok=True)
        except Exception:
            pass


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
    browser: str = "chrome",
    session_name: str | None = None,
    headless: bool | None = None,
    cdp_url: str | None = None,
    profile_path: str | None = None,
) -> str:
    """Open a browser for automation.

    **browser** — which browser to use: "chrome" (default), "firefox", or "edge".

    ── Standard mode ────────────────────────────────────────────────────────────
    Launches a fresh Playwright browser. Use session_name to save cookies and
    reuse them on the next run.

    ── Profile mode (recommended) ───────────────────────────────────────────────
    Opens the browser using your real installed browser binary and existing
    profile — all your logins and cookies are already there. The browser must
    be fully closed first (profile directory is locked while it's running).

      open_browser(browser="chrome",   profile_path="auto")
      open_browser(browser="firefox",  profile_path="auto")
      open_browser(browser="edge",     profile_path="auto")

    "auto" finds the profile directory automatically. Pass an explicit path to
    override. Note: Playwright ships a Firefox Nightly build, so Firefox will
    always show "Firefox Nightly" in the title bar — this is expected.

    ── CDP mode — Chrome / Edge only ────────────────────────────────────────────
    Alternative for Chrome/Edge: attach to a browser you launched manually with
    --remote-debugging-port=9222. More fragile than profile mode (ghost processes
    can prevent the port from opening). Use profile mode when possible.

    Args:
        browser: "chrome" (default), "firefox", or "edge".
        session_name: Cookie-persist name for standard mode (ignored in other modes).
        headless: Hide the window in standard/profile mode (default False).
        cdp_url: Chrome/Edge only — attach to an already-running browser.
                 Typically "http://localhost:9222".
        profile_path: Launch with an existing browser profile. Pass "auto" to
                      detect the profile automatically, or a full directory path.
                      Supported for chrome, firefox, and edge.
    """
    global _pw, _browser, _context, _page, _session_name, _cdp_mode, _persistent_ctx, _browser_name

    # Close any existing browser cleanly
    if _page or _context or _browser:
        await close_browser()

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return _err("Playwright not installed. Run: pip install playwright && playwright install chromium")

    try:
        bname = browser.lower()
        _browser_name = bname
        _pw = await async_playwright().start()
        engine = _get_browser_type(bname, _pw)

        if cdp_url:
            # ── CDP mode: attach to a running Chrome or Edge ──────────────────
            if bname == "firefox":
                return _err(
                    "Firefox does not support CDP connections in Playwright.\n\n"
                    "To use your existing Firefox session, use profile_path instead:\n"
                    "  open_browser(browser='firefox', profile_path='auto')\n\n"
                    "This launches Firefox with your existing profile so all logins "
                    "and cookies are preserved. Firefox must be fully closed first."
                )

            try:
                _browser = await engine.connect_over_cdp(cdp_url)
            except Exception as exc:
                kill_tips = {
                    "chrome": (
                        "Most likely cause: an existing Chrome process was running when you launched with\n"
                        "--remote-debugging-port, so Chrome silently ignored the flag and the port never\n"
                        "opened (single-instance enforcement + background helper processes).\n\n"
                        "Kill ALL Chrome processes first, then relaunch:\n\n"
                        "macOS:\n"
                        '  pkill -a -i "Google Chrome" 2>/dev/null; sleep 1.5;\n'
                        "  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222\n\n"
                        "Windows:\n"
                        "  taskkill /F /IM chrome.exe /T\n"
                        '  "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe" --remote-debugging-port=9222'
                    ),
                    "edge": (
                        "Most likely cause: an existing Edge process was running when you launched with\n"
                        "--remote-debugging-port, so Edge silently ignored the flag and the port never\n"
                        "opened (single-instance enforcement + background helper processes).\n\n"
                        "Kill ALL Edge processes first, then relaunch:\n\n"
                        "Windows:\n"
                        "  taskkill /F /IM msedge.exe /T\n"
                        '  "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe" --remote-debugging-port=9222\n\n'
                        "macOS:\n"
                        '  pkill -a -i "Microsoft Edge" 2>/dev/null; sleep 1.5;\n'
                        "  /Applications/Microsoft\\ Edge.app/Contents/MacOS/Microsoft\\ Edge --remote-debugging-port=9222"
                    ),
                }.get(bname, f"Make sure {browser} is fully closed, then relaunch with --remote-debugging-port=9222.")
                return _err(
                    f"Could not connect to {browser} at {cdp_url}: {exc}\n\n"
                    + kill_tips
                    + "\n\nVerify the port opened: open http://localhost:9222 in another browser — "
                    "you should see a JSON list of open tabs, not 'Connection Refused'."
                )

            contexts = _browser.contexts
            _context = contexts[0] if contexts else await _browser.new_context()
            _page = await _context.new_page()
            _session_name = None
            _cdp_mode = True
            _persistent_ctx = False

            return _ok({
                "message": f"Connected to existing {browser} at {cdp_url}. New tab opened.",
                "browser": browser,
                "cdp": True,
                "tabs_open": len(_context.pages),
                "note": "Using your real browser — already logged in, real fingerprint.",
            })

        elif profile_path:
            run_headless = headless if headless is not None else HEADLESS

            if bname == "firefox":
                # ── Firefox cookie-import mode ────────────────────────────────
                # We do NOT open the profile directly with launch_persistent_context.
                # Playwright's Firefox build is older than Firefox Nightly, so opening
                # a Nightly profile triggers Firefox's downgrade-protection dialog and
                # can corrupt bookmarks/history. Instead we:
                #   1. Read cookies.sqlite from the profile (read-only copy — safe)
                #   2. Launch a fresh Playwright Firefox context
                #   3. Inject the cookies so all existing sessions are active
                # Firefox cookies are not encrypted, so the values are directly usable.
                resolved, resolve_desc = _resolve_firefox_profile(profile_path)
                if resolved is None:
                    hint = (
                        "macOS:   ~/Library/Application Support/Firefox/Profiles/<name>\n"
                        "Windows: %APPDATA%\\Mozilla\\Firefox\\Profiles\\<name>"
                    )
                    return _err(
                        f"Could not find Firefox profile at '{profile_path}' ({resolve_desc}).\n\n"
                        "Pass profile_path='auto' to detect it automatically, or find "
                        "the full path to your profile folder:\n" + hint
                    )

                ff_cookies = _read_firefox_cookies(resolved)

                try:
                    _browser = await engine.launch(headless=run_headless)
                except Exception as exc:
                    return _err(f"Could not launch Firefox: {exc}")

                _context = await _browser.new_context(
                    user_agent=_UA_FIREFOX,
                    viewport={"width": 1920, "height": 1080},
                    extra_http_headers={"Accept-Language": "en-US,en;q=0.9"},
                )
                await _context.add_init_script(_STEALTH_INIT)

                # Import cookies — try bulk first, then one-by-one to skip bad entries
                imported = 0
                if ff_cookies:
                    try:
                        await _context.add_cookies(ff_cookies)
                        imported = len(ff_cookies)
                    except Exception:
                        for cookie in ff_cookies:
                            try:
                                await _context.add_cookies([cookie])
                                imported += 1
                            except Exception:
                                pass

                _page = await _context.new_page()
                _session_name = None
                _cdp_mode = False
                _persistent_ctx = False  # regular browser launch, not persistent context

                return _ok({
                    "message": f"Firefox ready — {imported} cookies imported from profile '{resolved.name}'.",
                    "browser": "firefox",
                    "profile": str(resolved),
                    "cookies_imported": imported,
                    "note": (
                        "Your Firefox login cookies are loaded (profile is read-only — never modified). "
                        "Playwright uses its own Firefox build which appears as 'Firefox Nightly' — expected."
                    ),
                })

            elif bname in ("chrome", "chromium", "edge"):
                # ── Profile mode: Chrome / Edge with real user profile ────────
                # We intentionally do NOT pass channel="chrome"/"msedge" here.
                # System Chrome refuses remote debugging on its default user data
                # directory ("DevTools remote debugging requires a non-default data
                # directory"). Playwright's bundled Chromium has no such restriction
                # and reads the same profile format. On macOS it uses the system
                # Keychain, so encrypted cookies are decrypted the same way Chrome
                # would — you stay logged in to your existing sessions.
                if profile_path == "auto":
                    resolved = _find_chrome_profile(bname)
                    if resolved is None:
                        hint = {
                            "chrome": (
                                "macOS:   ~/Library/Application Support/Google/Chrome\n"
                                "Windows: %LOCALAPPDATA%\\Google\\Chrome\\User Data"
                            ),
                            "edge": (
                                "Windows: %LOCALAPPDATA%\\Microsoft\\Edge\\User Data\n"
                                "macOS:   ~/Library/Application Support/Microsoft Edge"
                            ),
                        }.get(bname, "")
                        return _err(
                            f"Could not find {browser} profile directory automatically.\n\n"
                            "Pass the path explicitly:\n" + hint
                        )
                else:
                    resolved = Path(profile_path).expanduser()
                    if not resolved.exists():
                        return _err(f"Profile path not found: {resolved}")

                # Remove Chrome's singleton lock files so we can open the profile
                # even if Chrome crashed or didn't exit cleanly.
                for lock in ("SingletonLock", "SingletonCookie", "SingletonSocket"):
                    try:
                        (resolved / lock).unlink(missing_ok=True)
                    except Exception:
                        pass

                try:
                    _context = await engine.launch_persistent_context(
                        str(resolved),
                        headless=run_headless,
                        viewport={"width": 1920, "height": 1080},
                        args=_CHROMIUM_ARGS,
                    )
                except Exception as exc:
                    return _err(
                        f"Could not open {browser} with profile '{resolved}': {exc}\n\n"
                        f"Make sure {browser} is fully closed before calling this — "
                        "the profile directory is locked while the browser is running."
                    )

                await _context.add_init_script(_STEALTH_INIT)
                _page = await _context.new_page()
                _browser = None
                _session_name = None
                _cdp_mode = False
                _persistent_ctx = True

                return _ok({
                    "message": f"Launched with your {browser.capitalize()} profile.",
                    "browser": browser,
                    "profile": str(resolved),
                    "note": (
                        "Your existing logins and cookies are loaded. "
                        "The window uses Playwright's Chromium binary (not your system Chrome) "
                        "to avoid Chrome's remote-debugging restriction on the default profile."
                    ),
                })

            else:
                return _err(
                    f"profile_path is not supported for browser '{browser}'. "
                    "Choose: chrome, firefox, or edge."
                )

        else:
            # ── Standard mode: launch a fresh browser ─────────────────────────
            _cdp_mode = False
            _persistent_ctx = False
            run_headless = headless if headless is not None else HEADLESS

            if bname in ("chrome", "chromium"):
                _browser = await engine.launch(
                    headless=run_headless, args=_CHROMIUM_ARGS
                )
                user_agent = _UA_CHROME
                extra_headers: dict[str, str] = {
                    "Accept-Language": "en-US,en;q=0.9",
                    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                }
            elif bname == "edge":
                _browser = await engine.launch(
                    headless=run_headless, channel="msedge", args=_CHROMIUM_ARGS
                )
                user_agent = _UA_CHROME.replace("Chrome/131", "Edg/131")
                extra_headers = {
                    "Accept-Language": "en-US,en;q=0.9",
                    "sec-ch-ua": '"Microsoft Edge";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
                    "sec-ch-ua-mobile": "?0",
                    "sec-ch-ua-platform": '"Windows"',
                }
            else:  # firefox
                _browser = await engine.launch(headless=run_headless)
                user_agent = _UA_FIREFOX
                extra_headers = {"Accept-Language": "en-US,en;q=0.9"}

            ctx_opts: dict[str, Any] = {
                "user_agent": user_agent,
                "viewport": {"width": 1920, "height": 1080},
                "extra_http_headers": extra_headers,
            }
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
            await _context.add_init_script(_STEALTH_INIT)
            _page = await _context.new_page()

            msg = f"{browser.capitalize()} opened"
            if session_name:
                loaded = "storage.json" in str(ctx_opts.get("storage_state", ""))
                msg += f" with session '{session_name}'"
                msg += " (existing cookies loaded)" if loaded else " (fresh session)"
            return _ok({
                "message": msg,
                "browser": browser,
                "headless": run_headless,
                "session": session_name,
            })

    except ValueError as exc:
        return _err(str(exc))
    except Exception as exc:
        return _err(f"Could not open {browser}: {exc}. Try: playwright install chromium firefox")


@mcp.tool()
async def navigate(url: str, expect_url_contains: str | None = None) -> str:
    """Navigate to a URL and return the page title and final URL.

    Always call this before interacting with a new page.

    Args:
        url: The URL to navigate to.
        expect_url_contains: Optional substring the final URL should contain.
                             If the page redirected elsewhere (common on SPAs),
                             a 'spa_redirect' warning is included in the result
                             so the agent knows to adjust its approach.
    """
    page = _require_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
        await page.wait_for_load_state("networkidle", timeout=10_000)
    except Exception:
        pass  # networkidle can time out on busy pages — that's fine
    summary = await _page_summary(page)
    result: dict = {"message": f"Navigated to {summary['url']}", **summary}
    if expect_url_contains and expect_url_contains not in summary["url"]:
        result["spa_redirect"] = (
            f"Expected URL to contain '{expect_url_contains}' but landed on "
            f"'{summary['url']}'. The SPA may have redirected — try navigating "
            "from the homepage or interacting with the UI instead of deep-linking."
        )
    return _ok(result)


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
async def click(
    selector: str,
    wait_for_navigation: bool = True,
    wait_for: str | None = None,
    wait_ms: int = 0,
) -> str:
    """Click an element by CSS selector.

    Args:
        selector: CSS selector for the element to click.
        wait_for_navigation: Wait for a page load after clicking (default True).
                             Set to False for clicks that trigger in-page UI changes
                             like modals, dropdowns, or expanding sections.
        wait_for: CSS selector to wait for AFTER clicking — use this when the click
                  opens a modal or triggers async UI rendering. The tool waits up to
                  5 s for the element to appear before returning.
        wait_ms: Extra milliseconds to wait after the click before reading the page.
                 Useful for SPAs where JS hydration takes a moment (e.g. 500–2000).
    """
    page = _require_page()
    try:
        await page.click(selector, timeout=10_000)

        if wait_for:
            # Waiting for a specific post-click element takes priority over
            # generic navigation waits — the element appearing IS the signal.
            try:
                await page.wait_for_selector(wait_for, timeout=5_000)
            except Exception:
                pass  # Report what we can; caller will see what's on the page
        elif wait_for_navigation:
            try:
                await page.wait_for_load_state("domcontentloaded", timeout=10_000)
                await page.wait_for_load_state("networkidle", timeout=6_000)
            except Exception:
                pass

        if wait_ms > 0:
            import asyncio as _asyncio
            await _asyncio.sleep(min(wait_ms, 10_000) / 1000)

        summary = await _page_summary(page)
        return _ok({"message": f"Clicked '{selector}'", **summary})
    except Exception as exc:
        return _err(f"Could not click '{selector}': {exc}")


@mcp.tool()
async def fill_field(
    selector: str,
    value: str,
    press_tab: bool = False,
    use_keyboard: bool = False,
    delay_ms: int = 0,
) -> str:
    """Type a value into a form field.

    **Standard mode** (`use_keyboard=False`, default): sets the field value
    directly. Works for plain `<input>` and `<textarea>` elements.

    **Keyboard mode** (`use_keyboard=True`): simulates real keystrokes
    (keydown → keypress → input → keyup per character). Use this for:
    - `contenteditable` divs (X/Twitter post box, Notion, Slack, etc.)
    - React / Vue inputs that ignore programmatic `.value` changes
    - Sites that check for "trusted" input events to prevent botting

    For X (Twitter): click the "What's happening?" box first, then call
    `fill_field` with `use_keyboard=True`. This fires the React-compatible
    events that enable the Post button.

    Args:
        selector: CSS selector for the input or contenteditable element.
        value: Text to type.
        press_tab: Press Tab after filling (triggers field validation).
        use_keyboard: Simulate real keystrokes instead of direct fill.
        delay_ms: Milliseconds between keystrokes in keyboard mode.
                  0 = fast (default). Use 30–80 for sites that check
                  typing cadence.
    """
    page = _require_page()
    try:
        if use_keyboard:
            locator = page.locator(selector).first
            await locator.press_sequentially(value, delay=delay_ms)
        else:
            await page.fill(selector, value, timeout=8_000)
        if press_tab:
            await page.keyboard.press("Tab")
        mode = "keyboard" if use_keyboard else "fill"
        return _ok({"message": f"Filled '{selector}' ({mode} mode)"})
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
async def wait_for_element(
    selector: str,
    timeout_ms: int = 10000,
    state: str = "visible",
) -> str:
    """Wait for an element to reach a given state on the page.

    Useful after an action that triggers async loading, modal opening, or
    element removal. Returns ok once the condition is met.

    Args:
        selector: CSS selector for the element to watch.
        timeout_ms: Maximum time to wait in milliseconds (default 10 000).
        state: One of:
               'visible'  — element exists and is visible (default)
               'hidden'   — element exists but is hidden, or does not exist
               'attached' — element is in the DOM (may be hidden)
               'detached' — element has been removed from the DOM
    """
    page = _require_page()
    valid_states = ("visible", "hidden", "attached", "detached")
    if state not in valid_states:
        return _err(f"Invalid state '{state}'. Choose from: {', '.join(valid_states)}")
    try:
        await page.wait_for_selector(selector, state=state, timeout=timeout_ms)
        return _ok({"message": f"Element '{selector}' is now {state}"})
    except Exception as exc:
        return _err(f"Element '{selector}' did not reach state '{state}' within {timeout_ms}ms: {exc}")


@mcp.tool()
async def wait_for_url(url_contains: str, timeout_ms: int = 10000) -> str:
    """Wait for the page URL to contain a given substring.

    Use this after clicking a SPA navigation link where the URL changes
    client-side without a full page reload. Returns once the URL matches
    or the timeout expires.

    Args:
        url_contains: Substring the URL must contain (e.g. '/dashboard', '?tab=posts').
        timeout_ms: Maximum time to wait in milliseconds (default 10 000).
    """
    page = _require_page()
    try:
        await page.wait_for_url(f"**{url_contains}**", timeout=timeout_ms)
        summary = await _page_summary(page)
        return _ok({"message": f"URL now contains '{url_contains}'", **summary})
    except Exception as exc:
        summary = await _page_summary(page)
        return _err(
            f"URL did not contain '{url_contains}' within {timeout_ms}ms. "
            f"Current URL: {summary['url']}"
        )


@mcp.tool()
async def evaluate(js: str) -> str:
    """Run JavaScript in the page context and return the result.

    Use this to handle situations standard selectors can't reach:
    - Shadow DOM:  document.querySelector('my-el').shadowRoot.querySelector('input')
    - Iframes:     document.querySelector('iframe').contentDocument.querySelector('p')
    - Hidden data: window.__INITIAL_DATA__ or JSON.parse(document.getElementById('__NEXT_DATA__').textContent)
    - Visibility checks: document.querySelector('.modal')?.getBoundingClientRect()
    - Triggering events: document.querySelector('input').dispatchEvent(new Event('focus'))

    Args:
        js: JavaScript expression to evaluate. The return value is JSON-serialised
            and included in the response. Keep expressions simple — complex logic
            is better split across multiple calls.
    """
    page = _require_page()
    try:
        result = await page.evaluate(js)
        summary = await _page_summary(page)
        return _ok({"result": result, **summary})
    except Exception as exc:
        return _err(f"JavaScript evaluation failed: {exc}")


@mcp.tool()
async def close_browser() -> str:
    """Close the tab and disconnect from the browser.

    In CDP mode (connected to your existing Chrome): closes the tab the agent
    opened and disconnects. Chrome itself stays running with all your other tabs.

    In standard mode: saves the session (if named) and closes the browser.
    """
    global _pw, _browser, _context, _page, _session_name, _cdp_mode, _persistent_ctx, _browser_name

    was_cdp        = _cdp_mode
    was_persistent = _persistent_ctx
    saved = False

    if was_cdp:
        # CDP mode — close the tab we opened; leave the browser running
        try:
            if _page:
                await _page.close()
        except Exception:
            pass
        try:
            if _browser:
                await _browser.close()  # disconnects from CDP, does NOT kill the browser
        except Exception:
            pass
        try:
            if _pw:
                await _pw.stop()
        except Exception:
            pass

    elif was_persistent:
        # Profile mode — the context owns the browser; just close the context
        try:
            if _context:
                await _context.close()
        except Exception:
            pass
        try:
            if _pw:
                await _pw.stop()
        except Exception:
            pass

    else:
        # Standard mode — optionally save session, then shut down the browser
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
    _cdp_mode = False
    _persistent_ctx = False
    _browser_name = "chrome"

    if was_cdp:
        msg = "Tab closed and disconnected (browser is still running)"
    elif was_persistent:
        msg = "Firefox closed"
    else:
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
