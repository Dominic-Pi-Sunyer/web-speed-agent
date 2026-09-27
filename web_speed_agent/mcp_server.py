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
import socket
import sqlite3
import stat
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).parent))

from mcp.server.fastmcp import Context, FastMCP
from pydantic import BaseModel, Field
from web_speed_agent import Agent
from web_speed_agent.credentials import store_pair, get_pair, delete

# ── config ────────────────────────────────────────────────────────────────────

API_KEY     = os.getenv("WEBSPEED_API_KEY", "")
SERVER_URL  = os.getenv("WEBSPEED_SERVER_URL", "https://api.getwebspeed.io")
SESSIONS    = Path("~/.webspeed/sessions").expanduser()
HEADLESS    = os.getenv("WEBSPEED_HEADLESS", "false").lower() != "false"  # legacy default

# ── safety policy ─────────────────────────────────────────────────────────────
# Read-only, site allow/deny lists, human confirmation and the audit log all live
# in safety.py, configured by environment variable and nothing else — an agent
# cannot reach any of it. See that module for why each control is shaped the way
# it is, and which of them are boundaries rather than checkpoints.
from web_speed_agent import safety  # noqa: E402

_WRITE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def _env_ms(name: str, default: int) -> int:
    """A positive millisecond value from the environment, or the default.

    Never raises on a typo: a malformed WEBSPEED_SETTLE_MS should not stop the
    Bridge from starting, it should just fall back to the value that works.
    """
    try:
        v = int(os.getenv(name, "") or default)
        return v if v > 0 else default
    except (TypeError, ValueError):
        return default


# How long a page may keep changing before we stop waiting, and how long it must
# hold still to count as settled. See _settle() for why this replaced networkidle.
_SETTLE_MS       = _env_ms("WEBSPEED_SETTLE_MS", 1500)
_SETTLE_QUIET_MS = _env_ms("WEBSPEED_SETTLE_QUIET_MS", 350)

# Recent blocked requests, newest last. Surfaced in tool results so a blocked
# action reads as "read-only stopped this" instead of an unexplained failure —
# a silent block would just send the agent into a retry loop.
_blocked_writes: list[str] = []
_BLOCKED_KEEP = 20

# ── remote config (control-panel settings) ────────────────────────────────────
# The control panel on api.getwebspeed.io lets a user set agent defaults
# (headless, browser) for their key. We fetch them once per process, lazily, and
# FAIL OPEN: a missing key, a timeout, or any error just falls back to env vars /
# built-in defaults, so the agent never blocks on the network at startup.
# Precedence everywhere: explicit tool arg > env var > server config > default.
# Credentials never travel this path — only behavior flags.
_remote_cfg_cache: dict[str, Any] | None = None


async def _remote_cfg() -> dict[str, Any]:
    """Agent settings from the server, fetched once and cached. Never raises."""
    global _remote_cfg_cache
    if _remote_cfg_cache is not None:
        return _remote_cfg_cache
    cfg: dict[str, Any] = {}
    if API_KEY:
        try:
            import httpx
            async with httpx.AsyncClient(timeout=5.0) as client:
                resp = await client.get(
                    f"{SERVER_URL}/v1/agent/config",
                    headers={"x-web-speed-key": API_KEY},
                )
            if resp.status_code == 200 and isinstance(resp.json(), dict):
                cfg = resp.json()
        except Exception:
            pass  # fail open
    _remote_cfg_cache = cfg
    return cfg


def _env_headless() -> bool | None:
    """WEBSPEED_HEADLESS as a bool, or None if unset (so server config can decide)."""
    raw = os.getenv("WEBSPEED_HEADLESS")
    if raw is None:
        return None
    return raw.strip().lower() not in ("", "false", "0", "no", "off")


async def _resolve_headless(explicit: bool | None) -> bool:
    if explicit is not None:
        return explicit
    env = _env_headless()
    if env is not None:
        return env
    val = (await _remote_cfg()).get("headless")
    return val if isinstance(val, bool) else False


async def _resolve_browser(explicit: str | None) -> str:
    if explicit:
        return explicit
    env = os.getenv("WEBSPEED_BROWSER")
    if env and env.strip():
        return env.strip()
    val = (await _remote_cfg()).get("browser")
    return val.strip() if isinstance(val, str) and val.strip() else "chrome"

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


def _windows_desktop() -> Path:
    """The user's real Desktop folder on Windows.

    `Path.home() / "Desktop"` is wrong on any machine with OneDrive Known Folder
    Move enabled — Desktop is redirected to %OneDrive%\\Desktop and the plain path
    may not exist at all, so writing the launcher raised FileNotFoundError.

    The registry holds the authoritative location (it is what Explorer itself
    reads), so ask it first, then fall back to the OneDrive and home paths, and
    finally to the home directory — always somewhere the user can actually find.
    """
    try:
        import winreg                                    # Windows-only import
        key = r"Software\Microsoft\Windows\CurrentVersion\Explorer\Shell Folders"
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, key) as k:
            path = Path(winreg.QueryValueEx(k, "Desktop")[0])
            if path.is_dir():
                return path
    except Exception:
        pass

    onedrive = os.environ.get("OneDrive") or os.environ.get("OneDriveConsumer")
    for cand in ([Path(onedrive) / "Desktop"] if onedrive else []) + [
        Path.home() / "OneDrive" / "Desktop",
        Path.home() / "Desktop",
    ]:
        if cand.is_dir():
            return cand
    return Path.home()          # never fail the whole setup over a shortcut


def _is_port_open(host: str, port: int) -> bool:
    """Return True if something is accepting TCP connections on host:port.

    Tries 127.0.0.1 explicitly before the given host. Chrome binds its debug port
    to IPv4 loopback only, while "localhost" on Windows commonly resolves to ::1
    first — so a name-based probe can time out against IPv6 before it ever reaches
    the listening IPv4 socket, and the agent reports "Chrome isn't running" while
    Chrome is running perfectly.
    """
    hosts = ["127.0.0.1", host] if host in ("localhost", "127.0.0.1") else [host]
    for h in hosts:
        try:
            with socket.create_connection((h, port), timeout=1.0):
                return True
        except OSError:
            continue
    return False


def _ok(data: dict[str, Any]) -> str:
    return json.dumps({"ok": True, **data}, ensure_ascii=False, indent=2)

def _err(message: str) -> str:
    return json.dumps({"ok": False, "error": message}, ensure_ascii=False, indent=2)

def _require_page():
    if _page is None:
        raise RuntimeError("No browser open. Call open_browser first.")
    return _page


def _readonly_refusal(action: str) -> str | None:
    """An error to return, or None when the action may proceed.

    Used by the handful of tools whose ONLY purpose is to write. The network
    guard below would stop them anyway; refusing here just turns a confusing
    "the page did not change" into a clear reason.
    """
    if not safety.READONLY:
        return None
    return _err(
        f"Read-only mode: {action} is blocked. The Bridge was started with "
        f"WEBSPEED_READONLY set, so it can browse and read but not change "
        f"anything. Unset it in the MCP host config and restart to allow writes."
    )


def _readonly_note() -> dict[str, Any]:
    """Blocked-request detail to merge into a tool result, if there is any."""
    if not _blocked_writes:
        return {}
    return {"read_only_blocked": list(_blocked_writes)}


class _Approve(BaseModel):
    """What the human is asked. One boolean, because a confirmation prompt with
    choices is a prompt people learn to click through."""
    approve: bool = Field(description="Allow this action to run?")


def _client_can_elicit(ctx: Any) -> bool:
    """True only if the connected client declared the elicitation capability."""
    try:
        caps = ctx.session.client_params.capabilities
        return getattr(caps, "elicitation", None) is not None
    except Exception:  # noqa: BLE001
        return False


async def _confirm(ctx: Any, tool: str, summary: str,
                   target: str | None = None) -> str | None:
    """Ask a human before acting. Returns an error to return, or None to proceed.

    FAILS CLOSED. If no human can be reached — no Context, or a client that does
    not implement elicitation — the action is refused rather than allowed. The
    alternative is the worst outcome available: someone configures confirmation,
    their client silently cannot ask, and every action runs unattended while they
    believe each one was approved.
    """
    if not safety.needs_confirmation(tool, target):
        return None

    if ctx is None or not _client_can_elicit(ctx):
        safety.audit(tool, ok=False, detail="blocked: client cannot ask a human")
        return _err(
            f"Confirmation required for {tool}, but this MCP client does not "
            f"support elicitation, so nobody can be asked. Refusing rather than "
            f"acting unattended. Either use a client that supports elicitation, "
            f"or set WEBSPEED_CONFIRM=off (and consider WEBSPEED_READONLY=1 "
            f"instead, which does not depend on the client)."
        )

    try:
        result = await ctx.elicit(message=summary, schema=_Approve)
    except Exception as exc:  # noqa: BLE001
        safety.audit(tool, ok=False, detail=f"blocked: elicitation failed ({exc})")
        return _err(f"Could not ask for confirmation ({exc}). Refusing {tool}.")

    if getattr(result, "action", None) != "accept" or not getattr(
            getattr(result, "data", None), "approve", False):
        safety.audit(tool, ok=False, detail="declined by user")
        return _err(f"Declined: a human did not approve {tool}.")

    safety.audit(tool, ok=True, detail="approved by user")
    return None


async def _element_text(page, selector: str) -> str:
    """The element's visible text, or "" — best effort and quick.

    Used only to decide whether an action looks risky, so a miss must cost
    nothing: a short timeout and any failure means "no label", never an error.
    """
    try:
        txt = await page.locator(selector).first.inner_text(timeout=1500)
        return " ".join((txt or "").split())[:120]
    except Exception:  # noqa: BLE001
        return ""


async def _install_readonly_guard(context) -> None:
    """Abort requests that policy forbids — non-GET in read-only, and any host
    outside the site lists.

    Installed on the CONTEXT, so it covers popups and any tab opened later, not
    just the page that happens to be active now.
    """
    if context is None or not (safety.READONLY or safety.ALLOW_SITES
                               or safety.DENY_SITES):
        return

    async def _guard(route) -> None:
        req = route.request
        try:
            if safety.READONLY and (req.method or "GET").upper() in _WRITE_METHODS:
                _blocked_writes.append(f"{req.method} {req.url[:200]}")
                del _blocked_writes[:-_BLOCKED_KEEP]
                await route.abort("blockedbyclient")
                return
            # Site lists are re-checked HERE, not only before navigate(), because
            # a redirect, an iframe or a script-driven navigation never passes
            # through the tool — a check that only guards the tool is a check an
            # ordinary link can walk around.
            ok, _why = safety.site_allowed(req.url)
            if not ok:
                _blocked_writes.append(f"BLOCKED-SITE {req.url[:200]}")
                del _blocked_writes[:-_BLOCKED_KEEP]
                await route.abort("blockedbyclient")
                return
            await route.continue_()
        except Exception:  # noqa: BLE001
            # A route can die with its page mid-navigation. Failing to continue
            # a request must never take down the guard for every later one.
            pass

    try:
        await context.route("**/*", _guard)
    except Exception as exc:  # noqa: BLE001
        # Fail LOUDLY rather than silently browsing unprotected: someone who
        # asked for read-only must not believe they have it when they do not.
        raise RuntimeError(
            f"Read-only mode was requested but the request guard could not be "
            f"installed ({exc}). Refusing to continue unprotected."
        ) from exc

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
async def setup_browser(browser: str = "chrome") -> str:
    """Set up Chrome or Edge for agent use (macOS and Windows).

    Run this ONCE (with your browser closed). It:
      1. Creates a dedicated agent profile at ~/.webspeed/chrome-debug/ (macOS)
         or %LOCALAPPDATA%\\WebSpeedAgent\\chrome-debug\\ (Windows) —
         a non-default user data directory, which is required by Chrome before
         it will open the remote debugging port.
      2. Copies your existing Chrome cookies into that profile so you are
         already logged into all your sites when the agent opens the browser.
      3. macOS: installs ~/bin/chrome-agent and adds a shell alias in ~/.zshrc.
         Windows: writes chrome-agent.bat to your Desktop.

    After setup:
      - macOS: type 'chrome-agent' in Terminal to open Chrome
      - Windows: double-click chrome-agent.bat on your Desktop
      - Tell the agent: open_browser(browser="chrome", cdp_url="http://localhost:9222")
      - The agent opens a new tab in your real Chrome with all your logins active

    Re-run setup_browser() any time you want to sync fresh cookies from your
    main Chrome profile into the agent profile (close Chrome first).

    Args:
        browser: "chrome" (default) or "edge".
    """
    system = platform.system()
    bname = browser.lower()

    if system == "Windows":
        # ── Windows implementation ────────────────────────────────────────────
        local_app_data = os.environ.get("LOCALAPPDATA", "")
        if not local_app_data:
            return _err("Could not find %LOCALAPPDATA% environment variable.")

        if bname not in ("chrome", "edge"):
            return _err("setup_browser supports 'chrome' or 'edge' only.")

        if bname == "chrome":
            binary    = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
            kill_name = "chrome.exe"
            bat_name  = "chrome-agent.bat"
            real_udd  = Path(local_app_data) / "Google" / "Chrome" / "User Data"
            agent_udd = Path(local_app_data) / "WebSpeedAgent" / "chrome-debug"
        else:
            binary    = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
            kill_name = "msedge.exe"
            bat_name  = "edge-agent.bat"
            real_udd  = Path(local_app_data) / "Microsoft" / "Edge" / "User Data"
            agent_udd = Path(local_app_data) / "WebSpeedAgent" / "edge-debug"

        # Resolve the browser binary. Machine-wide installs land in Program Files,
        # but Chrome installed WITHOUT admin rights goes to %LOCALAPPDATA% instead
        # — very common on managed/corporate laptops. Missing that path made setup
        # fail outright on machines where Chrome was perfectly fine.
        if bname == "chrome":
            candidates = [
                binary,
                binary.replace("Program Files\\", "Program Files (x86)\\"),
                str(Path(local_app_data) / "Google" / "Chrome" / "Application" / "chrome.exe"),
            ]
        else:
            candidates = [
                binary,
                binary.replace("Program Files (x86)\\", "Program Files\\"),
                str(Path(local_app_data) / "Microsoft" / "Edge" / "Application" / "msedge.exe"),
            ]
        found = next((c for c in candidates if Path(c).exists()), None)
        if found is None:
            return _err(
                f"Could not find {browser}. Looked in:\n"
                + "\n".join(f"  {c}" for c in candidates)
                + "\n\nIf it's installed elsewhere, find it with this in PowerShell:\n"
                  "  (Get-Command chrome).Source\n"
                  "…then edit the path inside the generated .bat file."
            )
        binary = found

        # Step 1: create the agent profile directory
        agent_default = agent_udd / "Default"
        agent_default.mkdir(parents=True, exist_ok=True)

        # Step 2: copy essential files from the real profile
        real_default = real_udd / "Default"
        copy_results_win: list[str] = []
        for fname, src, dst in [
            ("Cookies",     real_default / "Cookies",     agent_default / "Cookies"),
            ("Login Data",  real_default / "Login Data",  agent_default / "Login Data"),
            ("Bookmarks",   real_default / "Bookmarks",   agent_default / "Bookmarks"),
            ("Preferences", real_default / "Preferences", agent_default / "Preferences"),
            ("Local State", real_udd / "Local State",     agent_udd / "Local State"),
        ]:
            if src.exists():
                try:
                    shutil.copy2(str(src), str(dst))
                    copy_results_win.append(f"  copied {fname}")
                except Exception as exc:
                    copy_results_win.append(f"  skipped {fname}: {exc}")
            else:
                copy_results_win.append(f"  not found: {fname}")

        # Step 3: write chrome-agent.bat to the Desktop
        desktop = _windows_desktop()
        bat_path = desktop / bat_name
        bat_lines = [
            "@echo off",
            ":: Auto-generated by web-speed-agent — do not edit the --user-data-dir line.",
            ":: Chrome requires a non-default user-data-dir to allow --remote-debugging-port.",
            f"taskkill /F /IM {kill_name} /T 2>nul",
            "timeout /t 2 /nobreak >nul",
            f'start "" "{binary}" ^',
            f'  --user-data-dir="{agent_udd}" ^',
            "  --remote-debugging-port=9222 ^",
            "  --no-first-run ^",
            "  --disable-default-apps",
            "echo Chrome Agent started on port 9222.",
            "",
        ]
        try:
            bat_path.parent.mkdir(parents=True, exist_ok=True)
            bat_path.write_text("\r\n".join(bat_lines))
        except OSError as exc:
            # Last resort: the profile and cookies are already in place, so land
            # the launcher in the home directory rather than losing all of it.
            bat_path = Path.home() / bat_name
            try:
                bat_path.write_text("\r\n".join(bat_lines))
            except OSError:
                return _err(f"Profile created at {agent_udd}, but the launcher "
                            f"could not be written: {exc}")

        return _ok({
            "message": f"{browser.capitalize()} agent profile created at {agent_udd}",
            "profile_files": copy_results_win,
            "bat_file": str(bat_path),
            "next_steps": [
                f"1. Close {browser.capitalize()} completely if it is open",
                f"2. Double-click the launcher: {bat_path}",
                "3. Tell the agent: open_browser(browser='chrome', cdp_url='http://localhost:9222')",
                "   → The agent opens a new tab in your real Chrome window",
                "",
                "Re-run setup_browser() any time to sync fresh cookies from your main Chrome profile.",
                f"Keep Chrome open via '{bat_name}' and the agent can always connect instantly.",
            ],
        })

    elif system != "Darwin":
        return _err(
            "setup_browser supports macOS and Windows only.\n\n"
            "On Linux: launch Chrome manually with --user-data-dir=/path/to/agent-profile\n"
            "and --remote-debugging-port=9222, then open_browser(browser='chrome', cdp_url='http://localhost:9222')."
        )

    if bname not in ("chrome", "edge"):
        return _err("setup_browser supports 'chrome' or 'edge' only.")

    if bname == "chrome":
        binary          = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
        kill_name       = "Google Chrome"
        script_name     = "chrome-agent"
        alias_name      = "chrome-agent"
        real_udd        = Path.home() / "Library" / "Application Support" / "Google" / "Chrome"
        agent_udd       = Path.home() / ".webspeed" / "chrome-debug"
    else:
        binary          = "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge"
        kill_name       = "Microsoft Edge"
        script_name     = "edge-agent"
        alias_name      = "edge-agent"
        real_udd        = Path.home() / "Library" / "Application Support" / "Microsoft Edge"
        agent_udd       = Path.home() / ".webspeed" / "edge-debug"

    if not Path(binary).exists():
        return _err(
            f"Could not find {browser} at '{binary}'.\n"
            "Is it installed? Check /Applications/ for the correct path."
        )

    # ── Step 1: create the agent profile directory ────────────────────────────
    agent_default = agent_udd / "Default"
    agent_default.mkdir(parents=True, exist_ok=True)
    agent_udd.chmod(0o700)

    # ── Step 2: copy essential files from the real Chrome profile ─────────────
    # Chrome only allows --remote-debugging-port on a non-default user data dir.
    # We copy cookies + prefs so the agent profile starts already logged in.
    # Local State lives at the user-data-dir level; everything else in Default/.
    real_default = real_udd / "Default"
    copy_results: list[str] = []

    for fname, src, dst in [
        ("Cookies",     real_default / "Cookies",     agent_default / "Cookies"),
        ("Login Data",  real_default / "Login Data",  agent_default / "Login Data"),
        ("Bookmarks",   real_default / "Bookmarks",   agent_default / "Bookmarks"),
        ("Preferences", real_default / "Preferences", agent_default / "Preferences"),
        ("Local State", real_udd / "Local State",     agent_udd / "Local State"),
    ]:
        if src.exists():
            try:
                shutil.copy2(str(src), str(dst))
                copy_results.append(f"  copied {fname}")
            except Exception as exc:
                copy_results.append(f"  skipped {fname}: {exc}")
        else:
            copy_results.append(f"  not found: {fname}")

    # ── Step 3: write the launch script ───────────────────────────────────────
    bin_dir = Path.home() / "bin"
    bin_dir.mkdir(exist_ok=True)
    script_path = bin_dir / script_name

    script_content = f"""#!/bin/bash
# Auto-generated by web-speed-agent — do not edit the --user-data-dir line.
# Chrome requires a non-default user-data-dir to allow --remote-debugging-port.
pkill -a -i "{kill_name}" 2>/dev/null
sleep 1.5
"{binary}" \\
  --user-data-dir="{agent_udd}" \\
  --remote-debugging-port=9222 \\
  --no-first-run \\
  --disable-default-apps \\
  "$@"
"""
    script_path.write_text(script_content)
    script_path.chmod(0o755)

    # ── Step 4: add shell alias ───────────────────────────────────────────────
    zshrc = Path.home() / ".zshrc"
    alias_line = f'alias {alias_name}="{script_path}"'
    zshrc_text = zshrc.read_text() if zshrc.exists() else ""
    if alias_name not in zshrc_text:
        with zshrc.open("a") as f:
            f.write(f"\n# web-speed-agent\n{alias_line}\n")
        alias_note = f"Added alias to ~/.zshrc — run 'source ~/.zshrc' or open a new Terminal"
    else:
        alias_note = f"Alias '{alias_name}' already in ~/.zshrc"

    return _ok({
        "message": f"{browser.capitalize()} agent profile created at {agent_udd}",
        "profile_files": copy_results,
        "script": str(script_path),
        "alias_status": alias_note,
        "next_steps": [
            "1. Close Chrome completely (Cmd+Q) if it is open",
            f"2. Run 'source ~/.zshrc' in Terminal to activate the alias",
            f"3. Type '{alias_name}' in Terminal — Chrome opens with your logins ready",
            f"4. Tell the agent: open_browser(browser='{bname}', cdp_url='http://localhost:9222')",
            "   → The agent opens a new tab in your real Chrome window",
            "",
            "Re-run setup_browser() any time to sync fresh cookies from your main Chrome profile.",
            "Keep Chrome open via 'chrome-agent' and the agent can always connect instantly.",
        ],
    })


@mcp.tool()
async def open_browser(
    browser: str | None = None,
    session_name: str | None = None,
    headless: bool | None = None,
    cdp_url: str | None = None,
    profile_path: str | None = None,
) -> str:
    """Open a browser for automation.

    **browser** — which browser to use: "chrome" (default), "firefox", or "edge".

    ── Chrome default behaviour ──────────────────────────────────────────────────
    For Chrome, CDP is tried automatically first. If the user has run
    'chrome-agent' (which opens Chrome with --remote-debugging-port=9222),
    the agent connects to that existing window and opens a new tab — the user's
    real Chrome with all their logins, cookies, and extensions.

    If Chrome is not running with the debug port, a helpful message is returned
    explaining how to start it.

    ── Firefox ───────────────────────────────────────────────────────────────────
    Imports cookies from the user's Firefox profile (read-only) into a fresh
    Playwright session. Pass profile_path="auto" to detect the profile.

    ── Manual overrides ──────────────────────────────────────────────────────────
    cdp_url: connect to a specific debug URL (e.g. non-default port).
    profile_path: use an explicit profile directory (Chrome/Firefox/Edge).
    session_name: persist cookies across fresh Playwright sessions.

    Args:
        browser: "chrome" (default), "firefox", or "edge".
        session_name: Cookie-persist name for standard/fresh mode.
        headless: Hide the window in standard/profile mode (default False).
        cdp_url: Override the CDP URL (default for Chrome: http://localhost:9222).
        profile_path: Launch with an existing browser profile ("auto" or full path).
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
        # Resolve browser via precedence: explicit arg > WEBSPEED_BROWSER env >
        # control-panel default > "chrome". (headless is resolved per-mode below.)
        browser = await _resolve_browser(browser)
        bname = browser.lower()
        _browser_name = bname
        _pw = await async_playwright().start()
        engine = _get_browser_type(bname, _pw)

        # ── Chrome auto-CDP ───────────────────────────────────────────────────
        # For Chrome, CDP is the preferred mode. If no explicit mode is set,
        # check whether chrome-agent is already running (port 9222 open) and
        # connect automatically. This means open_browser(browser="chrome") just
        # works when the user has chrome-agent running, with no extra arguments.
        if bname == "chrome" and cdp_url is None and profile_path is None:
            if _is_port_open("localhost", 9222):
                cdp_url = "http://localhost:9222"
            else:
                return _err(
                    "Chrome is not running with the remote debugging port.\n\n"
                    "Start it with:\n"
                    "  chrome-agent\n\n"
                    "If you haven't run setup_browser() yet, do that first — it\n"
                    "creates the chrome-agent shortcut with your existing cookies loaded.\n\n"
                    "Once Chrome is open via chrome-agent, call open_browser() again."
                )

        if cdp_url:
            # ── CDP mode: attach to a running Chrome or Edge ──────────────────
            if bname == "firefox":
                return _err(
                    "Firefox does not support CDP connections in Playwright.\n\n"
                    "To use your existing Firefox session, use profile_path instead:\n"
                    "  open_browser(browser='firefox', profile_path='auto')\n\n"
                    "This imports your Firefox cookies so you are already logged in."
                )

            # Pre-check: verify the debug port is actually listening before
            # attempting to connect. Gives a much clearer error than the generic
            # Playwright exception when the port is closed.
            from urllib.parse import urlparse as _urlparse
            _parsed = _urlparse(cdp_url)
            _host = _parsed.hostname or "localhost"
            _port = _parsed.port or 9222
            if not _is_port_open(_host, _port):
                return _err(
                    f"Port {_port} is not open — {browser} is not running with remote debugging enabled.\n\n"
                    f"Browsers don't expose a control interface by default; the debug port\n"
                    f"must be enabled when the browser starts.\n\n"
                    f"Quickest fix (macOS):\n"
                    f'  pkill -a -i "Google Chrome" 2>/dev/null; sleep 1.5;\n'
                    f"  /Applications/Google\\ Chrome.app/Contents/MacOS/Google\\ Chrome --remote-debugging-port=9222\n\n"
                    f"Or ask me to run setup_browser() once — it installs a wrapper so\n"
                    f"Chrome always starts with the debug port. After that, just open\n"
                    f"Chrome normally and the agent can always connect without any extra steps."
                )

            try:
                _browser = await engine.connect_over_cdp(cdp_url)
            except Exception as exc:
                return _err(
                    f"Port {_port} is open but connection failed: {exc}\n\n"
                    "Open http://localhost:9222 in another browser — you should see a JSON\n"
                    "list of open tabs. If you see an error page, Chrome may have started\n"
                    "with the flag but something blocked the connection."
                )

            contexts = _browser.contexts
            _context = contexts[0] if contexts else await _browser.new_context()
            await _install_readonly_guard(_context)
            _page = await _context.new_page()
            _session_name = None
            _cdp_mode = True
            _persistent_ctx = False

            return _ok({
                "message": f"Connected to your existing {browser}. New tab opened.",
                "browser": browser,
                "cdp": True,
                "tabs_open": len(_context.pages),
                "note": "Using your real browser — already logged in, real fingerprint.",
            })

        elif profile_path:
            run_headless = await _resolve_headless(headless)

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
                await _install_readonly_guard(_context)
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

            elif bname == "chrome":
                return _err(
                    "Chrome only supports CDP mode — profile_path is not used.\n\n"
                    "Start Chrome with 'chrome-agent' in Terminal, then call:\n"
                    "  open_browser(browser='chrome')\n\n"
                    "The agent connects automatically to your running Chrome window."
                )

            elif bname in ("chromium", "edge"):
                # ── Profile mode: Chromium / Edge with real user profile ──────
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
                    await _install_readonly_guard(_context)
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
            # Chrome is not available here — it is handled exclusively via CDP
            # (the auto-CDP block above catches all Chrome calls and either
            # connects or returns an error before reaching this branch).
            if bname == "chrome":
                return _err(
                    "Chrome only supports CDP mode.\n\n"
                    "Start Chrome with 'chrome-agent' in Terminal, then call:\n"
                    "  open_browser(browser='chrome')"
                )

            _cdp_mode = False
            _persistent_ctx = False
            run_headless = await _resolve_headless(headless)

            if bname == "chromium":
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
            await _install_readonly_guard(_context)
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


# ── Google Workspace helpers ─────────────────────────────────────────────────
# Google Docs/Slides/Sheets render on a <canvas> and hold persistent autosave /
# presence / telemetry connections, so the network NEVER goes idle. Waiting on
# "networkidle" therefore burns its full timeout on every action. These helpers
# detect Workspace editors and wait for the editor surface instead.
_WORKSPACE_READY = {
    "docs":   ".kix-appview-editor",
    "slides": ".punch-editor-content, .sketchy-container, .editor-container",
    "sheets": "#waffle-grid-container, .grid-container",
}


def _workspace_kind(url: str) -> str | None:
    """Return 'docs' | 'slides' | 'sheets' if the URL is a Google Workspace editor."""
    if not url:
        return None
    if "docs.google.com/document" in url:
        return "docs"
    if "docs.google.com/presentation" in url:
        return "slides"
    if "docs.google.com/spreadsheets" in url:
        return "sheets"
    return None


async def _settle(page) -> None:
    """Wait for the page to be interactive, without betting on networkidle.

    networkidle is the wrong signal for most modern sites and the cost is not
    subtle: anything holding a websocket, a poll or a telemetry beacon NEVER goes
    idle, so the wait is not "until ready", it is "the full timeout, every time".
    Measured on Google Calendar, that was ~6.4 s per navigation — 13 navigations
    in one session spent about 80 s waiting for a condition that could not occur.

    So we watch the DOM instead. `_wait_dom_settled` returns the moment mutations
    stop, which on a static page is faster than networkidle would have been, and
    on a chatty one returns in the quiet window rather than at the cap. Workspace
    editors keep their own readiness selector — a real signal beats both.

    Tunable if a site needs it:
      WEBSPEED_SETTLE_MS        hard cap for the settle wait (default 1500)
      WEBSPEED_SETTLE_QUIET_MS  how long "quiet" must last  (default 350)
    """
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=10_000)
    except Exception:
        pass
    if _workspace_kind(page.url):
        try:
            await page.wait_for_selector(_WORKSPACE_READY[_workspace_kind(page.url)], timeout=15_000)
        except Exception:
            pass
        return  # a real readiness selector — nothing generic beats it

    # Return on whichever signal arrives FIRST, capped. Neither alone is right,
    # and picking one just moves which pages are slow:
    #   • networkidle never fires on a site holding a socket (Calendar) — 6s, always.
    #   • DOM-quiet never fires on a page with a ticking clock, a carousel or a
    #     running animation, even though its network went idle immediately.
    # Racing them means a page waits the full cap only when BOTH are genuinely
    # unsettled, which is the case where waiting is the right thing to do anyway.
    import asyncio as _asyncio

    async def _network_idle() -> bool:
        try:
            await page.wait_for_load_state("networkidle", timeout=_SETTLE_MS)
            return True
        except Exception:  # noqa: BLE001
            return False

    tasks = {
        _asyncio.create_task(_network_idle()),
        _asyncio.create_task(_wait_dom_settled(
            page, quiet_ms=_SETTLE_QUIET_MS, timeout_ms=_SETTLE_MS)),
    }
    try:
        _done, pending = await _asyncio.wait(
            tasks, timeout=_SETTLE_MS / 1000,
            return_when=_asyncio.FIRST_COMPLETED)
        for t in pending:
            t.cancel()   # the DOM watcher carries its own cap, so nothing leaks
    except Exception:  # noqa: BLE001
        for t in tasks:
            t.cancel()


# ── post-action waits ────────────────────────────────────────────────────────
# Waiting is cheap; asking the model whether to wait is not. A wait that needs
# its own tool call costs a full round-trip — inference plus IPC plus JSON-RPC —
# which dominates the wall-clock time of a multi-step task and dwarfs the wait
# itself. So these are PARAMETERS on the action tools: click-then-wait is one
# call, not two.
#
# They also exist to kill `wait_ms`. A fixed sleep is a guess, and both ways of
# being wrong are expensive: too short is flaky, too long burns seconds on every
# single action. A condition returns the moment it is true.

_WAIT_UNTIL = ("none", "load", "domcontentloaded", "networkidle", "settle", "dom_settled")


async def _wait_dom_settled(page, quiet_ms: int = 400, timeout_ms: int = 5_000) -> bool:
    """Resolve once the DOM has stopped changing for `quiet_ms`.

    This is the honest replacement for `wait_ms: 2000`, and it catches what
    `networkidle` cannot: CSS animations, hydration, and client-side rendering
    that finish without issuing another network request.

    The page-side promise carries its own hard cap, so a page that mutates
    forever — a ticking clock, a carousel, a live feed — always resolves and
    always disconnects its observer. Without that cap this would hang on exactly
    the pages that need it most, and leak a MutationObserver every time.

    Returns True if the DOM went quiet, False if the cap was hit first. Never
    raises: a wait that fails is worth reporting, not worth losing the page over.
    """
    # finish() reports WHY it resolved. Resolving true unconditionally would
    # make a page that never stops moving indistinguishable from one that went
    # quiet immediately — so the "still changing" warning could never fire and
    # the caller would be told the page was ready when it wasn't.
    js = """
    ([quiet, cap]) => new Promise(resolve => {
        let timer = null;
        const hard = setTimeout(() => finish(false), cap);
        const observer = new MutationObserver(() => {
            if (timer) clearTimeout(timer);
            timer = setTimeout(() => finish(true), quiet);
        });
        function finish(settled) {
            if (timer) clearTimeout(timer);
            clearTimeout(hard);
            observer.disconnect();
            resolve(settled);
        }
        observer.observe(document.documentElement, {
            childList: true, subtree: true, attributes: true, characterData: true,
        });
        timer = setTimeout(() => finish(true), quiet);
    })
    """
    quiet = max(50, int(quiet_ms))
    cap = max(int(timeout_ms), quiet + 250)
    try:
        return bool(await page.evaluate(js, [quiet, cap]))
    except Exception:
        # Navigation during the wait destroys the promise's execution context.
        # That is a completed navigation, not a failure worth escalating.
        return False


async def _apply_waits(
    page,
    *,
    wait_for: str | None = None,
    wait_for_predicate: str | None = None,
    wait_until: str = "none",
    wait_ms: int = 0,
    timeout_ms: int = 10_000,
) -> list[str]:
    """Run the requested post-action waits, in order, and report what did not happen.

    Returns a list of human-readable notes for waits that timed out. Callers
    surface these as `warnings` rather than raising: the action itself already
    succeeded, and the agent needs to SEE that the modal never opened. Swallowing
    it silently is what makes an agent re-explore a page it already acted on.
    """
    notes: list[str] = []

    if wait_for:
        try:
            await page.wait_for_selector(wait_for, timeout=timeout_ms)
        except Exception:
            notes.append(f"wait_for: selector {wait_for!r} never appeared within {timeout_ms}ms")

    if wait_for_predicate:
        try:
            await page.wait_for_function(wait_for_predicate, timeout=timeout_ms, polling=100)
        except Exception:
            notes.append(f"wait_for_predicate: never became truthy within {timeout_ms}ms")

    if wait_until == "settle":
        await _settle(page)
    elif wait_until == "dom_settled":
        if not await _wait_dom_settled(page, timeout_ms=timeout_ms):
            notes.append(f"wait_until=dom_settled: DOM still changing after {timeout_ms}ms")
    elif wait_until in ("load", "domcontentloaded", "networkidle"):
        try:
            await page.wait_for_load_state(wait_until, timeout=timeout_ms)
        except Exception:
            notes.append(f"wait_until={wait_until}: not reached within {timeout_ms}ms")

    if wait_ms > 0:
        import asyncio as _asyncio
        await _asyncio.sleep(min(wait_ms, 10_000) / 1000)

    return notes


async def _dismiss_workspace_sidebars(page) -> None:
    """Best-effort close of side panels (Gemini, Help, Explore) that overlay the
    editor and swallow clicks/keystrokes. Safe: only clicks visible close buttons."""
    for sel in (
        "div[role='button'][aria-label^='Close'][aria-label*='ide']",   # 'Close side panel'
        "button[aria-label^='Close'][aria-label*='Gemini']",
        "div[aria-label='Close'][role='button']",
    ):
        try:
            btn = page.locator(sel).first
            if await btn.count() and await btn.is_visible():
                await btn.click(timeout=1_500)
        except Exception:
            pass


async def _docs_visible_text(page) -> str:
    """Best-effort read-back of Google Docs text for verification. Canvas Docs keep
    an accessibility mirror in .kix-* nodes; returns '' if unavailable."""
    try:
        return await page.eval_on_selector_all(
            ".kix-paragraphrenderer, .kix-lineview",
            "els => els.map(e => e.innerText).join('\\n')",
        )
    except Exception:
        return ""


# The proven Google Docs focus sequence, discovered empirically:
#  1. Blank docs pop a Gemini onboarding overlay that swallows keystrokes — remove it.
#  2. The real input is a hidden iframe positioned offscreen, so Playwright clicks miss
#     it. Pull it full-screen and make it the only pointer-events target so a click
#     lands squarely on it and moves browser focus into the editor.
# After that, raw keyboard events (page.keyboard.type) drive the canvas editor.
_DOCS_PREP_JS = r"""() => {
  let removed = 0;
  ['.kixWizBarkickWrapper', '[class*="WizBar"]', '.docs-gm-promo', '.kix-wizbar'].forEach(sel => {
    document.querySelectorAll(sel).forEach(el => { el.remove(); removed++; });
  });
  const iframe = document.querySelector('iframe.docs-texteventtarget-iframe');
  if (!iframe) return { ok: false, removed };
  if (!iframe.hasAttribute('data-ws-prev-style'))
    iframe.setAttribute('data-ws-prev-style', iframe.getAttribute('style') || '');
  iframe.style.cssText = 'position:fixed;z-index:2147483647;top:0;left:0;width:100vw;height:100vh;opacity:0;';
  if (!document.getElementById('ws-pe-override')) {
    const s = document.createElement('style');
    s.id = 'ws-pe-override';
    s.textContent = 'body *{pointer-events:none !important;} iframe.docs-texteventtarget-iframe{pointer-events:auto !important;}';
    document.head.appendChild(s);
  }
  return { ok: true, removed };
}"""

_DOCS_CLEANUP_JS = r"""() => {
  const iframe = document.querySelector('iframe.docs-texteventtarget-iframe');
  if (iframe && iframe.hasAttribute('data-ws-prev-style')) {
    iframe.setAttribute('style', iframe.getAttribute('data-ws-prev-style'));
    iframe.removeAttribute('data-ws-prev-style');
  }
  const s = document.getElementById('ws-pe-override');
  if (s) s.remove();
}"""


async def _prepare_docs_input(page) -> bool:
    """Remove the Gemini overlay, pull the hidden input iframe full-screen, and click
    it to move browser focus into the Docs editor. Returns True if the iframe was
    found and focused; False (with a best-effort editor-surface click) otherwise."""
    try:
        prepped = await page.evaluate(_DOCS_PREP_JS)
    except Exception:
        prepped = None
    if not (isinstance(prepped, dict) and prepped.get("ok")):
        try:
            await page.click(_WORKSPACE_READY["docs"], timeout=5_000)
        except Exception:
            pass
        return False
    try:
        await page.click("iframe.docs-texteventtarget-iframe", timeout=5_000)
    except Exception:
        pass
    return True


async def _restore_docs_input(page) -> None:
    """Undo the full-screen-iframe / pointer-events overrides so the document is
    usable again for screenshots and subsequent actions. Generic: also clears the
    Slides pointer-events lock (same #ws-pe-override style id)."""
    try:
        await page.evaluate(_DOCS_CLEANUP_JS)
    except Exception:
        pass


# The proven Google Slides sequence, discovered empirically:
#  1. New decks pop a .goog-modalpopup onboarding dialog (+ scrim) — remove both.
#  2. Placeholder IDs are generated per-slide, so find them by the 'editor-' prefix.
#  3. SVG <g> placeholders have no .click(); compute the box centre and dispatch a
#     synthetic double-click there to enter text-edit mode and focus the input.
#  4. Invisible SVG/div overlays steal clicks — lock pointer-events to the input
#     iframe so nothing intercepts focus while typing.
# `idx` selects which placeholder (0 = first, usually the title).
_SLIDES_PREP_JS = r"""(idx) => {
  ['.goog-modalpopup', '.goog-modalpopup-bg'].forEach(sel =>
    document.querySelectorAll(sel).forEach(el => el.remove()));
  const all = Array.from(document.querySelectorAll('[id^="editor-"]'))
    .filter(el => { const r = el.getBoundingClientRect(); return r.width > 4 && r.height > 4; });
  if (!all.length) return { ok: false, count: 0 };
  const i = Math.max(0, Math.min(idx | 0, all.length - 1));
  const el = all[i];
  const r = el.getBoundingClientRect();
  const x = r.left + r.width / 2, y = r.top + r.height / 2;
  const fire = (t) => el.dispatchEvent(new MouseEvent(t,
    { bubbles: true, cancelable: true, view: window, clientX: x, clientY: y }));
  fire('mousedown'); fire('mouseup'); fire('click');
  fire('mousedown'); fire('mouseup'); fire('click'); fire('dblclick');
  if (!document.getElementById('ws-pe-override')) {
    const s = document.createElement('style');
    s.id = 'ws-pe-override';
    s.textContent = '*{pointer-events:none !important;} iframe.docs-texteventtarget-iframe{pointer-events:auto !important;}';
    document.head.appendChild(s);
  }
  return { ok: true, count: all.length, index: i };
}"""


async def _prepare_slides_input(page, placeholder: int = 0) -> bool:
    """Remove the Slides onboarding modal, find the Nth text placeholder (IDs are
    dynamic, so query [id^="editor-"]), synthesise a double-click at its centre to
    enter edit mode, and lock pointer-events to the input iframe. Returns True if a
    placeholder was found and activated."""
    try:
        res = await page.evaluate(_SLIDES_PREP_JS, placeholder)
    except Exception:
        res = None
    return bool(isinstance(res, dict) and res.get("ok"))


@mcp.tool()
async def navigate(url: str, expect_url_contains: str | None = None,
                   ctx: Context = None) -> str:
    """Navigate to a URL and return the page title and final URL.

    Always call this before interacting with a new page.

    Args:
        url: The URL to navigate to.
        expect_url_contains: Optional substring the final URL should contain.
                             If the page redirected elsewhere (common on SPAs),
                             a 'spa_redirect' warning is included in the result
                             so the agent knows to adjust its approach.
    """
    allowed, why = safety.site_allowed(url)
    if not allowed:
        safety.audit("navigate", url=url, ok=False, detail="blocked by site policy")
        return _err(why)
    if (stop := await _confirm(ctx, "navigate", f"Open {url} ?", url)) is not None:
        return stop
    page = _require_page()
    try:
        await page.goto(url, wait_until="domcontentloaded", timeout=30_000)
    except Exception:
        pass  # goto can throw on slow/streaming pages; _settle handles readiness
    await _settle(page)
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
    ctx: Context = None,
) -> str:
    """Fill a login form and submit it.

    Credentials: provide either `site` (to load from keychain) OR
    `username` + `password` directly.

    Selectors: if omitted, common patterns are tried automatically
    (input[type=email], input[name=username], etc.).

    Use navigate() to go to the login page first.
    """
    # Refused rather than attempted: a sign-in POST would be blocked by the guard
    # anyway, and a half-typed password sitting in a form is a worse outcome than
    # a clear refusal. Sign in first with the mode off — the session persists, so
    # read-only runs afterwards still reach logged-in pages.
    if (stop := _readonly_refusal("signing in")) is not None:
        return stop
    safety.audit("login", url=_page.url if _page else None)
    if (stop := await _confirm(ctx, "login", f"Sign in on {_page.url if _page else 'this page'}?")) is not None:
        return stop
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
        await _settle(page)
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
    wait_for_predicate: str | None = None,
    wait_until: str | None = None,
    ctx: Context = None,
) -> str:
    """Click an element by CSS selector.

    The wait arguments all run inside THIS call. Reach for them instead of
    following a click with a separate wait tool — the extra round-trip costs far
    more than the wait. Prefer `wait_for` / `wait_for_predicate` / `wait_until`
    over `wait_ms`: a fixed sleep is either too short (flaky) or too long (slow),
    while a condition returns the moment it is satisfied.

    Args:
        selector: CSS selector for the element to click.
        wait_for_navigation: Wait for a page load after clicking (default True).
                             Set to False for clicks that trigger in-page UI changes
                             like modals, dropdowns, or expanding sections.
        wait_for: CSS selector to wait for AFTER clicking — use this when the click
                  opens a modal or triggers async UI rendering. The tool waits up to
                  5 s for the element to appear before returning.
        wait_ms: Fixed sleep after the click. Discouraged — use `wait_until` or
                 `wait_for_predicate`, which finish as soon as the page is ready.
        wait_for_predicate: JS expression polled (up to 5 s) until it returns
                 truthy, e.g. "!document.querySelector('.spinner')".
        wait_until: Readiness signal — 'dom_settled' (wait for the DOM to stop
                 changing; the right replacement for wait_ms), 'networkidle',
                 'load', 'domcontentloaded', 'settle', or 'none'. Defaults to the
                 historical behaviour: settle unless `wait_for` was given.

    Waits that time out do not fail the click — the click already happened. They
    come back in a `warnings` list so you can see, for instance, that the modal
    you expected never opened.
    """
    page = _require_page()
    if wait_until is not None and wait_until not in _WAIT_UNTIL:
        return _err(f"Invalid wait_until '{wait_until}'. Choose from: {', '.join(_WAIT_UNTIL)}")

    # Match on the element's own LABEL, not just the selector. "Delete account"
    # is the signal; `button.btn-primary` carries none.
    #
    # Only read it when something will actually use it. In the default
    # configuration — no confirmation, no audit — this is skipped entirely, so an
    # ordinary click costs exactly what it did before. Reading it unconditionally
    # added a DOM round-trip to every click, and a full timeout to every click
    # whose selector did not match.
    label = ""
    if safety.CONFIRM != "off" or safety.AUDIT_PATH is not None:
        label = await _element_text(page, selector)
    safety.audit("click", url=page.url, selector=selector, label=label or None)
    if (stop := await _confirm(ctx, "click", f"Click {label or selector!r} on {page.url}?",
                               f"{selector} {label}")) is not None:
        return stop
    try:
        await page.click(selector, timeout=10_000)

        # An explicit wait_until wins. Otherwise keep the original contract:
        # waiting for a specific post-click element takes priority over the
        # generic navigation settle — the element appearing IS the signal.
        effective = wait_until
        if effective is None:
            effective = "none" if wait_for else ("settle" if wait_for_navigation else "none")

        notes = await _apply_waits(
            page, wait_for=wait_for, wait_for_predicate=wait_for_predicate,
            wait_until=effective, wait_ms=wait_ms, timeout_ms=5_000,
        )
        summary = await _page_summary(page)
        out: dict[str, Any] = {"message": f"Clicked '{selector}'", **summary,
                               **_readonly_note()}
        if notes:
            out["warnings"] = notes
        return _ok(out)
    except Exception as exc:
        return _err(f"Could not click '{selector}': {exc}")


@mcp.tool()
async def fill_field(
    selector: str,
    value: str,
    press_tab: bool = False,
    use_keyboard: bool = False,
    delay_ms: int = 0,
    ctx: Context = None,
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
        value: Text to type. Never include \\n — it will be stripped.
               A trailing \\n is treated as Tab (advance to next field).
        press_tab: Press Tab after filling to move focus to the next field.
        use_keyboard: Simulate real keystrokes instead of direct fill.
        delay_ms: Milliseconds between keystrokes in keyboard mode.
                  0 = fast (default). Use 30–80 for sites that check
                  typing cadence.
    """
    page = _require_page()
    safety.audit("fill_field", url=page.url, selector=selector, value=value)
    if (stop := await _confirm(ctx, "fill_field",
                               f"Type into {selector!r} on {page.url}?",
                               selector)) is not None:
        return stop

    # Strip literal \n from values — they must never appear in form inputs.
    # A trailing \n is interpreted as "advance to next field" (Tab press).
    if "\n" in value:
        clean = value.rstrip("\n")
        if len(clean) < len(value):
            press_tab = True        # trailing \n → advance focus via Tab
        value = clean.replace("\n", " ")   # mid-string \n → space

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
async def hover(
    selector: str,
    wait_for: str | None = None,
    wait_for_predicate: str | None = None,
    wait_until: str = "none",
) -> str:
    """Move the pointer over an element, without clicking.

    Whole menus exist only on hover — nav dropdowns, tooltips, the row of icons
    that appears on a table row. A synthetic mouseover event dispatched from
    JavaScript often will not open them, because the site listens for a trusted
    pointer or checks `:hover` in CSS. This moves the real pointer.

    Args:
        selector: CSS selector for the element to hover.
        wait_for: CSS selector to wait for afterwards (the menu that should open).
        wait_for_predicate: JS expression polled until truthy afterwards.
        wait_until: Readiness signal afterwards — 'none' (default), 'dom_settled',
                    'networkidle', 'load', 'domcontentloaded', 'settle'.
    """
    page = _require_page()
    if wait_until not in _WAIT_UNTIL:
        return _err(f"Invalid wait_until '{wait_until}'. Choose from: {', '.join(_WAIT_UNTIL)}")
    try:
        await page.hover(selector, timeout=10_000)
        notes = await _apply_waits(page, wait_for=wait_for,
                                   wait_for_predicate=wait_for_predicate,
                                   wait_until=wait_until, timeout_ms=5_000)
        out: dict[str, Any] = {"message": f"Hovered '{selector}'",
                               **await _page_summary(page)}
        if notes:
            out["warnings"] = notes
        return _ok(out)
    except Exception as exc:
        return _err(f"Could not hover '{selector}': {exc}")


@mcp.tool()
async def scroll(
    to: str = "down",
    selector: str | None = None,
    amount_px: int = 600,
    wait_until: str = "dom_settled",
) -> str:
    """Scroll the page, or bring an element into view.

    Needed for more than reading: infinite feeds only load the next batch once
    you reach the bottom, and lazy images never request until they approach the
    viewport. An element below the fold can also be genuinely unclickable.

    Args:
        to: 'down', 'up', 'top', 'bottom', or 'element' (with `selector`).
        selector: Element to scroll into view. Implies to='element'.
        amount_px: Pixels to scroll for 'down'/'up' (default 600).
        wait_until: Readiness signal afterwards. Defaults to 'dom_settled', which
                    is what makes this useful on infinite scroll — it returns once
                    the newly loaded rows stop arriving.
    """
    page = _require_page()
    if wait_until not in _WAIT_UNTIL:
        return _err(f"Invalid wait_until '{wait_until}'. Choose from: {', '.join(_WAIT_UNTIL)}")
    if selector:
        to = "element"
    if to not in ("down", "up", "top", "bottom", "element"):
        return _err("Invalid `to`. Use 'down', 'up', 'top', 'bottom', or 'element'.")
    if to == "element" and not selector:
        return _err("to='element' needs a `selector`.")

    try:
        if to == "element":
            await page.locator(selector).first.scroll_into_view_if_needed(timeout=10_000)
            did = f"Scrolled '{selector}' into view"
        elif to in ("top", "bottom"):
            await page.evaluate(
                "(bottom) => window.scrollTo(0, bottom ? document.body.scrollHeight : 0)",
                to == "bottom")
            did = f"Scrolled to {to}"
        else:
            px = max(1, min(int(amount_px), 20_000))
            await page.mouse.wheel(0, px if to == "down" else -px)
            did = f"Scrolled {to} {px}px"

        notes = await _apply_waits(page, wait_until=wait_until, timeout_ms=5_000)
        pos = await page.evaluate(
            "() => ({y: Math.round(window.scrollY),"
            " height: document.body ? document.body.scrollHeight : 0,"
            " atBottom: (window.innerHeight + window.scrollY) >="
            "           ((document.body && document.body.scrollHeight) || 0) - 2})")
        out: dict[str, Any] = {"message": did, "scroll": pos, **await _page_summary(page)}
        if notes:
            out["warnings"] = notes
        return _ok(out)
    except Exception as exc:
        return _err(f"Could not scroll: {exc}")


@mcp.tool()
async def select_option(
    selector: str,
    value: str | None = None,
    label: str | None = None,
    index: int | None = None,
    ctx: Context = None,
) -> str:
    """Choose an option in a <select> dropdown.

    `fill_field` cannot do this — typing into a <select> does nothing. Pick ONE
    of value / label / index:

        value  matches the option's `value` attribute (most reliable)
        label  matches the visible text
        index  zero-based position

    Args:
        selector: CSS selector for the <select> element.
        value: The option's value attribute.
        label: The option's visible text.
        index: Zero-based option position.
    """
    page = _require_page()
    given = [x for x in (value, label, index) if x is not None]
    if len(given) != 1:
        return _err("Pass exactly one of value, label, or index.")
    safety.audit("select_option", url=page.url, selector=selector,
                 value=value, label=label, index=index)
    if (stop := await _confirm(ctx, "select_option",
                               f"Change the dropdown {selector!r} on {page.url}?",
                               f"{selector} {label or value or ''}")) is not None:
        return stop

    # Read the options BEFORE selecting. Playwright does not report a bad option
    # as a miss — it retries for the whole timeout and then throws its call log,
    # so a single wrong value costs 8 seconds and returns something unreadable.
    # Checking first turns that into an instant answer that also says what the
    # valid choices were, which is the difference between a caller recovering on
    # the next call and a caller guessing again.
    try:
        options = await page.eval_on_selector(
            selector,
            "el => Array.from(el.options || []).map("
            "  (o, i) => ({index: i, value: o.value, label: (o.label || o.text || '').trim()}))")
    except Exception as exc:
        return _err(f"Could not read options from '{selector}': {exc}. "
                    f"Is it a <select> element?")
    if not options:
        return _err(f"'{selector}' has no <option> elements — it may not be a "
                    f"<select>, or the options load dynamically (wait for them first).")

    if value is not None:
        match = next((o for o in options if o["value"] == value), None)
        wanted = f"value={value!r}"
    elif label is not None:
        match = next((o for o in options if o["label"] == label), None)
        wanted = f"label={label!r}"
    else:
        i = int(index)
        match = next((o for o in options if o["index"] == i), None)
        wanted = f"index={i}"

    if match is None:
        return _err(
            f"No option with {wanted} in '{selector}'. Available: "
            + json.dumps(options, ensure_ascii=False))

    try:
        # Select by index: it is the one criterion that cannot be ambiguous, and
        # we have already resolved the caller's criterion to a specific option.
        chosen = await page.select_option(selector, index=match["index"], timeout=8_000)
        return _ok({"message": f"Selected {match['label'] or match['value']!r} in '{selector}'",
                    "selected": chosen, "option": match, **await _page_summary(page)})
    except Exception as exc:
        return _err(f"Could not select in '{selector}': {exc}")


@mcp.tool()
async def go_back(steps: int = 1, wait_until: str = "settle") -> str:
    """Go back in browser history.

    The honest way out of a wrong turn. Re-navigating to a remembered URL is not
    the same thing: it drops scroll position, in-page state and any POST result,
    and on a SPA the URL you remember may not rebuild the view you were on.

    Args:
        steps: How many entries to go back (1–20).
        wait_until: Readiness signal after the last step. Defaults to 'settle'.
    """
    page = _require_page()
    if wait_until not in _WAIT_UNTIL:
        return _err(f"Invalid wait_until '{wait_until}'. Choose from: {', '.join(_WAIT_UNTIL)}")
    steps = max(1, min(int(steps), 20))
    try:
        moved = 0
        for _ in range(steps):
            # None means there was nothing further back — stop and say how far we
            # actually got, rather than reporting a move that did not happen.
            if await page.go_back(timeout=15_000) is None:
                break
            moved += 1
        if moved == 0:
            return _err("Nothing to go back to — this is the first page in history.")
        notes = await _apply_waits(page, wait_until=wait_until, timeout_ms=5_000)
        out: dict[str, Any] = {
            "message": f"Went back {moved} page(s)" + (
                f" (asked for {steps}, history ran out)" if moved < steps else ""),
            **await _page_summary(page)}
        if notes:
            out["warnings"] = notes
        return _ok(out)
    except Exception as exc:
        return _err(f"Could not go back: {exc}")


@mcp.tool()
async def press_keys(
    text: str | None = None,
    keys: list[str] | None = None,
    selector: str | None = None,
    delay_ms: int = 0,
    repeat: int = 1,
    wait_for: str | None = None,
    wait_for_predicate: str | None = None,
    wait_until: str = "none",
    ctx: Context = None,
) -> str:
    """Send real keystrokes to the page, rather than into a form field.

    `fill_field` writes into an `<input>`. This types at whatever holds keyboard
    focus, which is what word games, canvas editors (Figma, Excalidraw),
    terminal emulators, and keyboard-shortcut UIs actually listen to: they bind
    `keydown`/`keyup` on `window` or `document`, so setting an input's value
    never reaches them and there is often no input to target in the first place.

    Examples:
        press_keys(text="crane", keys=["Enter"])       # type a word, submit it
        press_keys(keys=["ArrowDown"], repeat=3)       # move a selection
        press_keys(keys=["Control+a", "Backspace"])    # select all, clear
        press_keys(text="hello", selector="#chat")     # focus first, then type

    Args:
        text: Literal text to type one character at a time, firing the full
              keydown → keypress → input → keyup sequence per character. Typed
              BEFORE `keys`, so text="crane", keys=["Enter"] does the right thing.
        keys: Key names pressed in order — ["Enter"], ["Escape"], ["ArrowLeft"] —
              or chords like ["Control+a"], ["Shift+Tab"]. Playwright names:
              Enter, Tab, Escape, Backspace, Delete, ArrowUp/Down/Left/Right,
              Home, End, PageUp, PageDown, F1–F12, and single characters.
        selector: CSS selector to focus first. Omit to send keys to whatever
                  already has focus — the usual case for games and canvas apps.
        delay_ms: Milliseconds per keystroke (0–1000). 0 is fastest; use 20–80
                  on sites that check typing cadence.
        repeat: Press the `keys` sequence this many times (1–50). Does not
                repeat `text`.
        wait_for: CSS selector to wait for afterwards, in this same call.
        wait_for_predicate: JS expression polled until truthy afterwards.
        wait_until: Readiness signal afterwards — 'none' (default), 'dom_settled'
                    (wait for the DOM to stop changing; prefer this over a fixed
                    sleep), 'networkidle', 'load', 'domcontentloaded', 'settle'.
    """
    page = _require_page()
    if not text and not keys:
        return _err("Nothing to send — pass `text`, `keys`, or both.")
    _what = (text or "") + " " + " ".join(keys or [])
    safety.audit("press_keys", url=page.url, keys=keys, text=text)
    if (stop := await _confirm(ctx, "press_keys",
                               f"Send keystrokes to {page.url}?", _what)) is not None:
        return stop
    if wait_until not in _WAIT_UNTIL:
        return _err(f"Invalid wait_until '{wait_until}'. Choose from: {', '.join(_WAIT_UNTIL)}")

    repeat = max(1, min(int(repeat), 50))
    delay = max(0, min(int(delay_ms), 1_000))

    try:
        if selector:
            try:
                await page.focus(selector, timeout=8_000)
            except Exception as exc:
                return _err(f"Could not focus '{selector}': {exc}")

        did: list[str] = []
        if text:
            await page.keyboard.type(text, delay=delay)
            did.append(f"typed {len(text)} character(s)")
        if keys:
            for _ in range(repeat):
                for key in keys:
                    try:
                        await page.keyboard.press(key, delay=delay)
                    except Exception as exc:
                        # Name the offending key: a typo'd key name is by far the
                        # likeliest failure here, and "press failed" alone leaves
                        # the caller guessing which of five keys was wrong.
                        return _err(
                            f"Could not press '{key}': {exc}. Use Playwright key "
                            f"names — Enter, Tab, Escape, ArrowUp, Control+a, etc."
                        )
            did.append(f"pressed {', '.join(keys)}" + (f" ×{repeat}" if repeat > 1 else ""))

        notes = await _apply_waits(
            page, wait_for=wait_for, wait_for_predicate=wait_for_predicate,
            wait_until=wait_until, timeout_ms=5_000,
        )
        summary = await _page_summary(page)
        out: dict[str, Any] = {"message": "; ".join(did), **summary,
                               **_readonly_note()}
        if notes:
            out["warnings"] = notes
        return _ok(out)
    except Exception as exc:
        return _err(f"Key dispatch failed: {exc}")


@mcp.tool()
async def workspace_write(text: str, target: str = "auto", verify: bool = True,
                          placeholder: int = 0, ctx: Context = None) -> str:
    """Type text into a Google Workspace editor (Docs or Slides) reliably.

    Google Docs/Slides render on <canvas>, so `fill_field`/`click` can't place
    text and `execCommand` is deprecated/flaky. This tool dismisses blocking side
    panels, focuses the editor, and types with real keystrokes (the events the
    canvas editor actually listens for), then verifies.

    Docs:   types at the current cursor (auto-removes the Gemini overlay and
            focuses the hidden input iframe).
    Slides: pass placeholder=N to pick the text box (0 = first, usually the
            title). Removes the onboarding modal, double-clicks the placeholder to
            enter edit mode, then types. Use workspace_new_slide() to add slides.

    Newlines in `text` are sent as real Enter presses.

    Args:
        text:        Text to type.
        target:      'auto' (detect from URL), or force 'docs' / 'slides'.
        verify:      Read the text back to confirm it landed (Docs only, best-effort).
        placeholder: Slides only — which text placeholder to edit (0-based, in
                     document order; 0 is typically the title).
    """
    if (stop := _readonly_refusal("writing to a Google Workspace document")) is not None:
        return stop
    safety.audit("workspace_write", url=_page.url if _page else None)
    if (stop := await _confirm(ctx, "workspace_write", f"Type into the open Google Workspace document?")) is not None:
        return stop
    import asyncio as _asyncio
    page = _require_page()
    kind = target if target in ("docs", "slides") else _workspace_kind(page.url)
    if kind not in ("docs", "slides"):
        return _err(
            "Not on a Google Docs or Slides editor. Navigate to the document first, "
            "or pass target='docs'|'slides'."
        )
    try:
        await _dismiss_workspace_sidebars(page)

        # Focus the editor with the app-specific proven sequence. Docs: remove the
        # Gemini overlay + full-screen the hidden input iframe. Slides: remove the
        # onboarding modal + synthetic double-click the target placeholder.
        docs_prepped = slides_prepped = False
        if kind == "docs":
            docs_prepped = await _prepare_docs_input(page)
        elif kind == "slides":
            slides_prepped = await _prepare_slides_input(page, placeholder)

        # Real keystrokes — canvas editors ignore programmatic value changes but
        # honour genuine key events. Split on newlines to send Enter presses.
        lines = text.split("\n")
        for i, line in enumerate(lines):
            if line:
                await page.keyboard.type(line, delay=12)
            if i < len(lines) - 1:
                await page.keyboard.press("Enter")

        # Restore the page (undo the iframe overlay / pointer-events lock) before
        # anything else touches it, so screenshots and later clicks work normally.
        if docs_prepped or slides_prepped:
            await _restore_docs_input(page)

        result: dict = {"message": f"Typed {len(text)} chars into Google {kind.title()}"}

        if verify and kind == "docs":
            await _asyncio.sleep(0.5)  # let the editor commit the edit
            body = await _docs_visible_text(page)
            probe = text.strip().split("\n")[0][:40]
            if probe and body:
                result["verified"] = probe in body
                if not result["verified"]:
                    result["warning"] = (
                        "Couldn't confirm the text landed. The editor may not have had "
                        "focus — click into the document body and retry."
                    )
            else:
                result["verified"] = None  # couldn't read the canvas a11y mirror
        return _ok(result)
    except Exception as exc:
        return _err(f"workspace_write failed: {exc}")


@mcp.tool()
async def workspace_new_slide(ctx: Context = None) -> str:
    """Add a new slide in Google Slides (equivalent to Ctrl+M).

    Drops the pointer-events lock first so the toolbar is clickable, clicks the
    'New slide' button, and settles. Then call
    workspace_write(target='slides', placeholder=N) to fill the new slide.
    """
    if (stop := _readonly_refusal("adding a slide")) is not None:
        return stop
    safety.audit("workspace_new_slide", url=_page.url if _page else None)
    if (stop := await _confirm(ctx, "workspace_new_slide", f"Add a new slide to the open presentation?")) is not None:
        return stop
    page = _require_page()
    if _workspace_kind(page.url) != "slides":
        return _err("Not on a Google Slides editor.")
    try:
        await _restore_docs_input(page)  # clear any pointer-events lock first
        clicked = False
        try:
            await page.click("[aria-label^='New slide']", timeout=5_000)
            clicked = True
        except Exception:
            try:
                await page.keyboard.press("Control+m")
                clicked = True
            except Exception:
                pass
        await _settle(page)
        if not clicked:
            return _err("Could not find the 'New slide' button (aria-label^='New slide').")
        return _ok({"message": "Added a new slide"})
    except Exception as exc:
        return _err(f"workspace_new_slide failed: {exc}")


@mcp.tool()
async def submit_form(selector: str | None = None, ctx: Context = None) -> str:
    """Submit a form by clicking a submit button or pressing Enter.

    Args:
        selector: CSS selector of the submit button or form. If omitted,
                  presses Enter on the focused element.
    """
    if (stop := _readonly_refusal("submitting a form")) is not None:
        return stop
    safety.audit("submit_form", url=_page.url if _page else None)
    if (stop := await _confirm(ctx, "submit_form", f"Submit the form on {_page.url if _page else 'this page'}?")) is not None:
        return stop
    page = _require_page()
    try:
        if selector:
            await page.click(selector, timeout=8_000)
        else:
            await page.keyboard.press("Enter")
        await _settle(page)
        summary = await _page_summary(page)
        return _ok({"message": f"Form submitted — now on: {summary['url']}",
                    **summary, **_readonly_note()})
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
async def wait_for_predicate(js: str, timeout_ms: int = 10000, poll_ms: int = 100) -> str:
    """Wait until a JavaScript expression returns a truthy value.

    The general-purpose wait, for when readiness is not "an element appeared":
    a list reached a length, a spinner class was removed, a global got
    populated, an animation finished.

        wait_for_predicate("document.querySelectorAll('.row').length > 10")
        wait_for_predicate("!document.querySelector('.spinner')")
        wait_for_predicate("window.__APP_READY === true")

    Always prefer this to a fixed sleep — it returns the moment the condition
    holds, instead of costing the full delay every time. If you only need to
    wait after a click or a keypress, pass `wait_for_predicate` to that tool
    instead and save the extra round-trip.

    Args:
        js: JavaScript expression (or zero-argument function) re-evaluated in
            page context until it returns truthy.
        timeout_ms: Maximum time to wait (default 10 000).
        poll_ms: How often to re-evaluate, in milliseconds (default 100).
    """
    page = _require_page()
    try:
        await page.wait_for_function(js, timeout=timeout_ms, polling=max(10, int(poll_ms)))
        summary = await _page_summary(page)
        return _ok({"message": "Predicate is now truthy", **summary})
    except Exception as exc:
        summary = await _page_summary(page)
        return _err(
            f"Predicate did not become truthy within {timeout_ms}ms: {exc}. "
            f"Current URL: {summary['url']}"
        )


@mcp.tool()
async def evaluate(js: str, ctx: Context = None) -> str:
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
    safety.audit("evaluate", url=page.url, js=js[:200])
    if (stop := await _confirm(ctx, "evaluate",
                               f"Run JavaScript on {page.url}?", js)) is not None:
        return stop
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
async def safety_status() -> str:
    """What this Bridge is currently allowed to do.

    Call it first when an action is refused, or before planning anything that
    changes data. The limits are set outside the agent and cannot be changed from
    here — knowing them up front beats discovering them one failure at a time.

    Reports read-only mode, the confirmation level, any site allow/deny lists,
    and where the audit log is written.
    """
    policy = safety.describe()
    if not safety.any_enabled():
        return _ok({
            "policy": policy,
            "summary": "No restrictions. Every tool is available, nothing is "
                       "logged, and no action will ask for approval.",
        })

    limits: list[str] = []
    if policy["read_only"]:
        limits.append("Read-only: nothing that changes data will reach the server "
                      "(every non-GET request is blocked).")
    if policy["confirm"] != "off":
        limits.append(f"Confirmation ({policy['confirm']}): a human is asked before "
                      f"these actions, and they are refused if nobody can be reached.")
    if policy["allow_sites"]:
        limits.append("Only these hosts are reachable: "
                      + ", ".join(policy["allow_sites"]))
    if policy["deny_sites"]:
        limits.append("These hosts are blocked: " + ", ".join(policy["deny_sites"]))
    if policy["audit_log"]:
        limits.append(f"Every action is recorded to {policy['audit_log']}.")
    return _ok({"policy": policy, "limits": limits})


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

def main() -> None:
    """Console-script entry point (webspeed-agent).

    stdio transport: the MCP host launches this as a subprocess and talks over
    pipes. Nothing is ever bound to a port — this agent drives a browser holding
    the user's own logins, so it must not be reachable over a network.
    """
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
