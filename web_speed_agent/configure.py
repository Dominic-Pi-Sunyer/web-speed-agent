#!/usr/bin/env python3
"""
webspeed-configure — one command to add Web Speed to Claude Desktop.

Hand-editing claude_desktop_config.json is the #1 place people get stuck.
This writes (merges) the Web Speed MCP servers into that file for you, so the
user never touches JSON. It sets up both components:

  • webspeed         — the hosted mapping API (site_map, interpret_page, ...)
  • web-speed-agent  — the local browser agent (login, click, fill, ...)

It is merge-safe: it only touches its own two entries and leaves every other
MCP server in your config untouched. It backs up the existing file first and
writes atomically, so a half-written file can never corrupt your config.

Usage:
    webspeed-configure --key wsp_YOUR_KEY
    python -m web_speed_agent.configure --key wsp_YOUR_KEY
    WEBSPEED_API_KEY=wsp_... webspeed-configure

Common options:
    --key KEY          Web Speed API key (or set WEBSPEED_API_KEY).
    --hosted-only      Configure only the hosted MCP (skip the local agent).
    --agent-only       Configure only the local browser agent.
    --print            Dry run: show what would be written, change nothing.
    --claude-code      Also print the equivalent `claude mcp add` commands.
    --agent-path PATH  Path to agent_mcp_server.py (auto-detected from a clone).
    --python PATH      Interpreter for the local agent (default: this one).
    --config-path PATH Override the Claude Desktop config location.
    --force            Replace a config file that contains invalid JSON
                       (a timestamped backup is always kept).

This file has no third-party imports on purpose, so it can also be downloaded
on its own and run for a hosted-only setup:
    curl -fsSL https://getwebspeed.io/configure.py | python3 - --key wsp_... --hosted-only
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import sys
from datetime import datetime
from pathlib import Path

SSE_URL = "https://api.getwebspeed.io/mcp/sse"
HOSTED_NAME = "webspeed"
AGENT_NAME = "web-speed-agent"


# ── locations ──────────────────────────────────────────────────────────────────

def claude_config_path() -> Path:
    """The Claude Desktop config file for this OS."""
    system = platform.system()
    if system == "Darwin":
        return (Path.home() / "Library" / "Application Support" / "Claude"
                / "claude_desktop_config.json")
    if system == "Windows":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "Claude" / "claude_desktop_config.json"
    # Linux / other
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "Claude" / "claude_desktop_config.json"


def find_agent_server(explicit: str | None) -> Path | None:
    """Locate agent_mcp_server.py from a clone, or use the explicit path."""
    if explicit:
        p = Path(explicit).expanduser().resolve()
        return p if p.is_file() else None
    candidates = [
        # This file lives at <repo>/web_speed_agent/configure.py, so the server
        # sits one directory up when run from a checkout.
        Path(__file__).resolve().parent.parent / "agent_mcp_server.py",
        Path.cwd() / "agent_mcp_server.py",
    ]
    for c in candidates:
        if c.is_file():
            return c.resolve()
    return None


# ── server entries ─────────────────────────────────────────────────────────────

def hosted_entry(key: str) -> dict:
    """Claude Desktop entry for the hosted SSE server (via the mcp-remote bridge)."""
    if platform.system() == "Windows":
        # Claude Desktop on Windows runs commands through cmd.exe, which trips on
        # the space in "C:\Program Files\". A global `npm install -g mcp-remote`
        # puts the binary in %APPDATA%\npm (no spaces), so call it directly.
        return {
            "command": "mcp-remote",
            "args": [SSE_URL, "--header", f"X-Web-Speed-Key: {key}"],
        }
    return {
        "command": "npx",
        "args": ["-y", "mcp-remote@latest", SSE_URL,
                 "--header", f"X-Web-Speed-Key: {key}"],
    }


def agent_entry(key: str, agent_path: Path, python: str) -> dict:
    """Claude Desktop entry for the local browser agent (stdio)."""
    return {
        "command": python,
        "args": [str(agent_path)],
        "env": {"WEBSPEED_API_KEY": key},
    }


# ── config read / write ────────────────────────────────────────────────────────

def load_config(path: Path, force: bool) -> dict:
    """Read existing config. Never destructive; exits on bad JSON unless --force."""
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return {}
    try:
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError("top-level JSON is not an object")
        return data
    except (json.JSONDecodeError, ValueError) as exc:
        if force:
            print(f"!  Existing config isn't valid JSON ({exc}); --force given, "
                  f"starting fresh (a backup is still made).")
            return {}
        print(f"x  {path}\n   exists but isn't valid JSON ({exc}).")
        print("   Refusing to overwrite it. Fix the file, or re-run with --force "
              "to replace it (a backup is made either way).")
        sys.exit(2)


def _lock_down(path: Path) -> None:
    """Make a file owner-only (0600). The config holds the API key in plaintext,
    so other local users must not be able to read it. No-op-ish on Windows (which
    only honors the read-only bit) — harmless there."""
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def backup(path: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    dest = path.with_name(path.name + f".bak-{stamp}")
    shutil.copy2(path, dest)
    _lock_down(dest)  # the backup may contain the API key / other MCP secrets
    return dest


def atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n", encoding="utf-8")
    # Lock the temp file down BEFORE the atomic swap, so the live config is never
    # even briefly world-readable (avoids a perms race, and avoids downgrading an
    # existing 0600 config to the umask default when os.replace swaps inodes).
    _lock_down(tmp)
    os.replace(tmp, path)  # atomic on the same filesystem


# ── claude code helper ─────────────────────────────────────────────────────────

def print_claude_code(key: str, agent_path: Path | None, python: str,
                      do_hosted: bool, do_agent: bool) -> None:
    print("\n# Claude Code equivalents (run these instead, if you use Claude Code):")
    if do_hosted:
        print(f'claude mcp add {HOSTED_NAME} --transport sse \\\n'
              f'  --url "{SSE_URL}" \\\n'
              f'  --header "X-Web-Speed-Key: {key}"')
    if do_agent and agent_path:
        print(f'claude mcp add {AGENT_NAME} '
              f'--env WEBSPEED_API_KEY={key} -- {python} {agent_path}')


# ── main ───────────────────────────────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="webspeed-configure",
        description="Add Web Speed to Claude Desktop's config (merge-safe).")
    ap.add_argument("--key", default=os.environ.get("WEBSPEED_API_KEY", ""),
                    help="Web Speed API key (or set WEBSPEED_API_KEY).")
    ap.add_argument("--hosted-only", action="store_true",
                    help="Configure only the hosted MCP.")
    ap.add_argument("--agent-only", action="store_true",
                    help="Configure only the local browser agent.")
    ap.add_argument("--agent-path", help="Path to agent_mcp_server.py.")
    ap.add_argument("--python", default=sys.executable,
                    help="Interpreter for the local agent (default: this one).")
    ap.add_argument("--config-path", help="Override the Claude Desktop config path.")
    ap.add_argument("--claude-code", action="store_true",
                    help="Also print the equivalent `claude mcp add` commands.")
    ap.add_argument("--print", dest="dry_run", action="store_true",
                    help="Dry run: print what would be written, change nothing.")
    ap.add_argument("--force", action="store_true",
                    help="Replace a config file with invalid JSON (backup kept).")
    args = ap.parse_args(argv)

    if args.hosted_only and args.agent_only:
        print("x  --hosted-only and --agent-only are mutually exclusive.")
        return 2

    key = args.key.strip()
    if not key:
        print("x  No API key. Pass --key wsp_... or set WEBSPEED_API_KEY.")
        print("   Get a key at https://getwebspeed.io")
        return 2
    if not key.startswith("wsp_"):
        print("!  That key doesn't look like a Web Speed key (expected 'wsp_...'). "
              "Continuing anyway.")

    do_hosted = not args.agent_only
    do_agent = not args.hosted_only

    entries: dict[str, dict] = {}
    if do_hosted:
        entries[HOSTED_NAME] = hosted_entry(key)

    agent_path: Path | None = None
    if do_agent:
        agent_path = find_agent_server(args.agent_path)
        if agent_path is None:
            print("!  Couldn't find agent_mcp_server.py, so the local agent will be "
                  "skipped.\n"
                  "   Run this from your web-speed-agent checkout, or pass "
                  "--agent-path /path/to/agent_mcp_server.py.")
            do_agent = False
        else:
            entries[AGENT_NAME] = agent_entry(key, agent_path, args.python)

    if not entries:
        print("x  Nothing to configure.")
        return 2

    cfg_path = (Path(args.config_path).expanduser()
                if args.config_path else claude_config_path())

    # Read-only load. In a dry run, don't abort on bad JSON — just note it.
    config = load_config(cfg_path, force=args.force or args.dry_run)

    mcp = config.setdefault("mcpServers", {})
    if not isinstance(mcp, dict):
        print("x  Your config's 'mcpServers' is not an object; aborting so we "
              "don't corrupt it.")
        return 2

    updated = [n for n in entries if n in mcp]
    mcp.update(entries)

    if args.dry_run:
        print(f"# Dry run — would write to: {cfg_path}\n")
        print(json.dumps(config, indent=2))
        if args.claude_code:
            print_claude_code(key, agent_path, args.python, do_hosted, do_agent)
        return 0

    if cfg_path.exists():
        b = backup(cfg_path)
        print(f"-  Backed up existing config -> {b}")
    atomic_write(cfg_path, config)

    print(f"OK Wrote {cfg_path}")
    for n in entries:
        print(f"   {'Updated' if n in updated else 'Added'}: {n}")

    print("\nNext steps:")
    print("  1. Fully quit Claude Desktop (Cmd/Ctrl+Q) and reopen it.")
    step = 2
    if do_hosted:
        if platform.system() == "Windows":
            print(f"  {step}. (Windows) Install the bridge once:  "
                  f"npm install -g mcp-remote@latest")
        else:
            print(f"  {step}. Make sure Node.js is installed (npx bridges the "
                  f"hosted server).")
        step += 1
    if do_agent and agent_path:
        print(f"  {step}. Make sure Playwright is installed for the agent:  "
              f'"{args.python}" -m playwright install chromium')
        step += 1
    print(f"  {step}. Start a new chat — the Web Speed tools will be available.")

    if do_agent and agent_path:
        print(f"\nLocal agent interpreter: {args.python}")
        print(f"Local agent script:      {agent_path}")

    if not cfg_path.parent.exists():
        print("\n!  Note: the Claude config folder didn't exist — if Claude "
              "Desktop isn't installed yet, install it and this config will be "
              "picked up.")

    if args.claude_code:
        print_claude_code(key, agent_path, args.python, do_hosted, do_agent)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
