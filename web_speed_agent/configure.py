#!/usr/bin/env python3
"""
webspeed-configure — one command to add Web Speed to your MCP hosts.

Hand-editing claude_desktop_config.json is the #1 place people get stuck.
This writes (merges) the Web Speed MCP servers into that file for you, so the
user never touches JSON. It sets up both components:

  • webspeed         — the hosted mapping API (site_map, interpret_page, ...)
  • web-speed-agent  — the local browser agent (login, click, fill, ...)

It is merge-safe: it only touches its own two entries and leaves every other
MCP server in your config untouched. It backs up the existing file first and
writes atomically, so a half-written file can never corrupt your config.

Hosts
─────
  Claude Desktop   config file       (default)
  Claude Code      `claude mcp add`  --claude-code
  Antigravity CLI  `agy mcp add`     --antigravity
  ChatGPT          instructions only --chatgpt

The CLI-driven hosts go through their own `mcp add` command rather than a
hand-written file, because those tools own their config format and location —
writing the JSON ourselves would be guessing at a private detail.

ChatGPT prints steps instead of configuring anything, and cannot be automated:
it never launches a local process (connectors are remote HTTPS endpoints), so
the local agent is only reachable through OpenAI's Secure MCP Tunnel, which
needs a tunnel id and an API key created in OpenAI's own UI.

Usage:
    webspeed-configure --key wsp_YOUR_KEY
    webspeed-configure --key wsp_YOUR_KEY --all --apply
    python -m web_speed_agent.configure --key wsp_YOUR_KEY
    WEBSPEED_API_KEY=wsp_... webspeed-configure

Common options:
    --key KEY          Web Speed API key (or set WEBSPEED_API_KEY).
    --all              Claude Desktop + Claude Code + Antigravity, and print
                       the ChatGPT steps.
    --apply            With --claude-code / --antigravity, RUN the commands
                       instead of printing them.
    --hosted-only      Configure only the hosted MCP (skip the local agent).
    --agent-only       Configure only the local browser agent.
    --print            Dry run: show what would be written, change nothing.
    --claude-code      Also configure Claude Code (`claude mcp add`).
    --antigravity      Also configure the Antigravity CLI (`agy mcp add`).
    --chatgpt          Print ChatGPT connector / Secure MCP Tunnel steps.
    --no-claude-desktop  Skip the Claude Desktop config file.
    --agent-path PATH  Path to agent_mcp_server.py (auto-detected from a clone).
    --python PATH      Interpreter for the local agent (default: this one).
    --config-path PATH Override the Claude Desktop config location.
    --force            Replace a config file that contains invalid JSON
                       (a timestamped backup is always kept).

This file has no third-party imports on purpose, so it can also be downloaded
on its own and run for a hosted-only setup:
    curl -fsSL https://api.getwebspeed.io/configure.py | python3 - --key wsp_... --hosted-only
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

SSE_URL = "https://api.getwebspeed.io/mcp/sse"
# Streamable HTTP. Used by clients that speak MCP over HTTP natively (Antigravity)
# instead of being bridged through the Node mcp-remote proxy. SSE is the legacy
# transport and stays above only for hosts that still require the bridge.
MCP_URL = "https://api.getwebspeed.io/mcp"
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


def find_console_script() -> str | None:
    """Absolute path to the installed `webspeed-agent` executable, if present.

    Absolute, never the bare name: MCP hosts are GUI apps, and a GUI process on
    Windows inherits a minimal PATH that usually excludes the Python Scripts
    directory. A bare command works from a terminal and then fails silently when
    the desktop app launches it — the worst kind of bug to support.

    Checks the running interpreter's own script directory first so a venv
    install resolves to that venv rather than whatever is on PATH.
    """
    exe = "webspeed-agent.exe" if os.name == "nt" else "webspeed-agent"
    here = Path(sys.executable).parent          # venv/bin or venv\Scripts
    cand = here / exe
    if cand.exists():
        return str(cand)
    found = shutil.which("webspeed-agent")
    return found or None


def module_importable(python: str) -> bool:
    """Can `python -m web_speed_agent.mcp_server` actually run?

    Tested in a subprocess with a NEUTRAL cwd, which is the whole point. An
    in-process `import web_speed_agent` succeeds whenever this file is run from a
    checkout, because cwd is on sys.path — so the naive check passes, we write a
    `-m` entry, and then the MCP host (a GUI app starting in some other
    directory) dies with ModuleNotFoundError. The failure surfaces later, in a
    different program, with no obvious link back to the installer.
    """
    try:
        r = subprocess.run(
            [python, "-c", "import web_speed_agent.mcp_server"],
            capture_output=True, timeout=30, cwd=tempfile.gettempdir())
        return r.returncode == 0
    except Exception:  # noqa: BLE001 — a missing//broken interpreter is just "no"
        return False


def agent_argv(python: str, agent_path: Path | None, use_module: bool) -> list[str]:
    """The command+args that launch the local agent, as one list.

    Single source of truth: the Claude Desktop entry, the `claude mcp add` and
    `agy mcp add` commands, and the ChatGPT tunnel command must all launch the
    agent identically, or the host that got the odd one out breaks in a way
    nobody reproduces.
    """
    if use_module:
        return [python, "-m", "web_speed_agent.mcp_server"]
    # Checkout with no install: the shim inserts its own directory on sys.path,
    # so it runs from any cwd, which is exactly where `-m` fails.
    return [python, str(agent_path)]


def agent_entry(key: str, agent_path: Path | None, python: str,
                use_module: bool = True) -> dict:
    """Claude Desktop entry for the local browser agent (stdio).

    Prefers the installed console script — one absolute path, no separate
    interpreter, and it survives the package moving on disk. Falls back to
    interpreter + module path for checkout-only installs (`git clone` with no
    `pip install`), which is how the agent ran before it was packaged properly.
    """
    # Launch via `python -m`, NOT the console script, even though the script
    # exists. Two Windows failures come from pointing an MCP host at a
    # package-owned .exe:
    #
    #   1. Upgrades break. Claude Desktop holds webspeed-agent.exe open, so pip
    #      hits "WinError 32: file in use", aborts mid-upgrade, and leaves the
    #      package renamed to ~eb_speed_agent — importable by nothing. The user
    #      is left with a working shim pointing at a package that is gone.
    #   2. The shim can drift from the install. A stale .exe on PATH resolves to
    #      a Python whose site-packages no longer has the module.
    #
    # sys.executable is by definition the interpreter running this configurator,
    # so it is guaranteed to be the one that can import the package — and pip
    # never needs to replace python.exe, so upgrades stop fighting the MCP host.
    argv = agent_argv(python, agent_path, use_module)
    return {
        "command": argv[0],
        "args": argv[1:],
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

def claude_code_commands(key: str, python: str, do_hosted: bool, do_agent: bool,
                         agent_path: Path | None = None,
                         use_module: bool = True) -> list[list[str]]:
    """The `claude mcp add` invocations, as argv lists so they can be run as well
    as printed."""
    cmds: list[list[str]] = []
    if do_hosted:
        cmds.append(["claude", "mcp", "add", HOSTED_NAME, "--transport", "sse",
                     "--url", SSE_URL, "--header", f"X-Web-Speed-Key: {key}"])
    if do_agent:
        # Launch form comes from agent_argv(), matching agent_entry() exactly — NOT
        # a hardcoded path to agent_mcp_server.py. Two bugs lived in the old form:
        # a pip or uv install has no such file, so the command it printed could not
        # run; and it was emitted only when a checkout happened to be found, so the
        # very users this is aimed at got no agent command at all.
        cmds.append(["claude", "mcp", "add", AGENT_NAME,
                     "--env", f"WEBSPEED_API_KEY={key}",
                     "--", *agent_argv(python, agent_path, use_module)])
    return cmds


def print_claude_code(key: str, agent_path: Path | None, python: str,
                      do_hosted: bool, do_agent: bool,
                      use_module: bool = True) -> None:
    print("\n# Claude Code equivalents (run these instead, if you use Claude Code):")
    for cmd in claude_code_commands(key, python, do_hosted, do_agent,
                                    agent_path, use_module):
        print(shlex.join(cmd))


def apply_claude_code(key: str, python: str, do_hosted: bool, do_agent: bool,
                      agent_path: Path | None = None,
                      use_module: bool = True) -> bool:
    """Run the `claude mcp add` commands. Returns False if the CLI isn't present.

    Falls back to printing rather than failing: someone piping install.sh may only
    have Claude Desktop, and a missing `claude` binary is not worth exiting on.
    """
    if shutil.which("claude") is None:
        return False
    print("\nConfiguring Claude Code:")
    for cmd in claude_code_commands(key, python, do_hosted, do_agent,
                                    agent_path, use_module):
        name = cmd[3]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as exc:  # noqa: BLE001
            print(f"   x  {name}: {exc}")
            continue
        if r.returncode == 0:
            print(f"   OK {name}")
            continue
        detail = (r.stderr or r.stdout or "").strip().splitlines()
        first = detail[0] if detail else f"exit {r.returncode}"
        # `claude mcp add` refuses a name that is already registered. On a re-run
        # that is the expected outcome, not something to alarm anyone about.
        if "already exists" in first.lower():
            print(f"   -  {name} already configured (left alone)")
        else:
            print(f"   x  {name}: {first[:120]}")
    return True


# ── antigravity cli helper ─────────────────────────────────────────────────────

def find_agy() -> str | None:
    """Absolute path to the Antigravity CLI, if installed.

    Checked before PATH because the installer drops it in ~/.local/bin, which is
    frequently not on PATH — shutil.which alone reports "not installed" on a
    machine that plainly has it.
    """
    local = Path.home() / ".local" / "bin" / ("agy.exe" if os.name == "nt" else "agy")
    if local.exists():
        return str(local)
    return shutil.which("agy")


def antigravity_commands(agy: str, key: str, python: str,
                         do_hosted: bool, do_agent: bool,
                         agent_path: Path | None = None,
                         use_module: bool = True) -> list[tuple[str, list[str]]]:
    """(name, argv) pairs for `agy mcp add`, so they can be run or printed.

    Flags must precede the name (agy rejects them after), and `--` precedes the
    stdio command so an interpreter path starting with '-' can never be parsed
    as a flag.
    """
    cmds: list[tuple[str, list[str]]] = []
    if do_hosted:
        # Native HTTP — no npx/mcp-remote bridge. Antigravity speaks MCP over HTTP
        # directly, so routing it through a Node proxy would add a dependency and a
        # failure mode for nothing.
        cmds.append((HOSTED_NAME,
                     [agy, "mcp", "add", "--header", f"X-Web-Speed-Key: {key}",
                      HOSTED_NAME, MCP_URL]))
    if do_agent:
        cmds.append((AGENT_NAME,
                     [agy, "mcp", "add", "--env", f"WEBSPEED_API_KEY={key}",
                      AGENT_NAME, "--",
                      *agent_argv(python, agent_path, use_module)]))
    return cmds


def print_antigravity(agy: str | None, key: str, python: str,
                      do_hosted: bool, do_agent: bool,
                      agent_path: Path | None = None,
                      use_module: bool = True) -> None:
    print("\n# Antigravity CLI equivalents:")
    for _name, cmd in antigravity_commands(agy or "agy", key, python,
                                           do_hosted, do_agent,
                                           agent_path, use_module):
        print(shlex.join(cmd))


def apply_antigravity(key: str, python: str, do_hosted: bool, do_agent: bool,
                      agent_path: Path | None = None,
                      use_module: bool = True) -> bool:
    """Run the `agy mcp add` commands. Returns False if the CLI isn't installed.

    Unlike `claude mcp add`, agy's own help says "Add or update", so re-running
    REPLACES a stale entry rather than refusing. That is the behaviour we want:
    the common case here is an old checkout path left behind by a previous
    install, and silently leaving it in place is how someone ends up running a
    months-old agent and reporting missing tools as bugs.
    """
    agy = find_agy()
    if agy is None:
        return False
    print("\nConfiguring Antigravity CLI:")
    for name, cmd in antigravity_commands(agy, key, python, do_hosted, do_agent,
                                          agent_path, use_module):
        try:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except Exception as exc:  # noqa: BLE001
            print(f"   x  {name}: {exc}")
            continue
        if r.returncode == 0:
            print(f"   OK {name}")
        else:
            detail = (r.stderr or r.stdout or "").strip().splitlines()
            print(f"   x  {name}: {detail[0][:120] if detail else r.returncode}")
    return True


# ── chatgpt helper ─────────────────────────────────────────────────────────────

def print_chatgpt(key: str, python: str, do_hosted: bool, do_agent: bool,
                  agent_path: Path | None = None,
                  use_module: bool = True) -> None:
    """Instructions, not automation — and deliberately so.

    ChatGPT is the one target here that cannot be configured by writing a file.
    It never launches a local process: connectors are remote HTTPS endpoints, so
    a stdio server is unreachable to it by design. The two routes below are the
    only ones that exist, and both need steps taken in OpenAI's own UI (a tunnel
    id, an API key) that no installer can perform on your behalf.
    """
    print("\n" + "─" * 70)
    print("ChatGPT (desktop and web)")
    print("─" * 70)
    print("ChatGPT cannot start a local MCP server — its connectors are remote")
    print("HTTPS endpoints only. Requires a paid plan (Plus/Pro/Business/Enterprise).")

    if do_hosted:
        print("\n1. Hosted mapping tools — add as a custom connector:")
        print("     Settings -> Connectors -> Advanced -> Developer mode, then Create")
        print(f"     URL:  {MCP_URL}?key={key}")
        print("   The key travels in the URL because ChatGPT connectors do not send")
        print("   custom headers. Treat that URL as a password, and rotate the key if")
        print("   it leaks. (Not yet verified end to end against ChatGPT.)")

    if do_agent:
        print("\n2. Local browser agent — needs OpenAI's Secure MCP Tunnel.")
        print("   The tunnel runs the stdio server on your machine and proxies it")
        print("   outbound-only, so nothing is exposed to the internet:")
        print("     a. platform.openai.com -> Tunnels -> create one, copy its tunnel_id")
        print("     b. Download `tunnel-client` from that same page")
        print("     c. Run:")
        print(f"          export WEBSPEED_API_KEY={key}")
        print(f"          tunnel-client init --tunnel-id <TUNNEL_ID> \\")
        print(f"              --mcp-command \"{shlex.join(agent_argv(python, agent_path, use_module))}\"")
        print(f"          tunnel-client run --profile default")
        print("   Needs an OpenAI API key with Tunnels Read + Use. The tunnel must")
        print("   stay running for the tools to be reachable.")


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
                    help="Also emit the equivalent `claude mcp add` commands.")
    ap.add_argument("--antigravity", action="store_true",
                    help="Also configure the Antigravity CLI (`agy mcp add`).")
    ap.add_argument("--chatgpt", action="store_true",
                    help="Print ChatGPT connector / Secure MCP Tunnel setup steps.")
    ap.add_argument("--all", dest="all_targets", action="store_true",
                    help="Claude Desktop + Claude Code + Antigravity, and print "
                         "the ChatGPT steps.")
    ap.add_argument("--no-claude-desktop", dest="no_desktop", action="store_true",
                    help="Skip writing the Claude Desktop config (use when you "
                         "only want the other targets).")
    ap.add_argument("--apply", action="store_true",
                    help="With --claude-code / --antigravity, run those commands "
                         "instead of printing them.")
    ap.add_argument("--print", dest="dry_run", action="store_true",
                    help="Dry run: print what would be written, change nothing.")
    ap.add_argument("--force", action="store_true",
                    help="Replace a config file with invalid JSON (backup kept).")
    args = ap.parse_args(argv)

    if args.all_targets:
        args.claude_code = args.antigravity = args.chatgpt = True

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
    use_module = True
    if do_agent:
        # Does `--python -m web_speed_agent.mcp_server` actually resolve from an
        # arbitrary directory? Asked of the interpreter that will really run it,
        # from a neutral cwd — see module_importable(). The old in-process import
        # answered "yes" for any checkout and wrote a config the MCP host could
        # not launch.
        use_module = module_importable(args.python)
        agent_path = find_agent_server(args.agent_path)
        if not use_module and agent_path is None:
            print("!  Local agent skipped — it isn't installed into "
                  f"{args.python} and no checkout was found.\n"
                  "   Fix with:  pip install web-speed-agent\n"
                  "   Or run this from a checkout / pass --agent-path "
                  "/path/to/agent_mcp_server.py.")
            do_agent = False
        else:
            if not use_module:
                # Works, but it is the weaker of the two entries: it depends on a
                # checkout staying where it is. Say so at the moment it is chosen.
                print(f"!  {args.python} cannot import web_speed_agent, so the "
                      f"agent will be launched via the checkout shim:\n"
                      f"     {agent_path}\n"
                      f"   That works, but moving or deleting that folder breaks "
                      f"it. `pip install web-speed-agent` gives a sturdier setup.")
            entries[AGENT_NAME] = agent_entry(key, agent_path, args.python,
                                              use_module=use_module)

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
            print_claude_code(key, agent_path, args.python, do_hosted, do_agent, use_module)
        if args.antigravity:
            print_antigravity(find_agy(), key, args.python, do_hosted, do_agent,
                              agent_path, use_module)
        if args.chatgpt:
            print_chatgpt(key, args.python, do_hosted, do_agent, agent_path, use_module)
        return 0

    if not args.no_desktop:
        if cfg_path.exists():
            b = backup(cfg_path)
            print(f"-  Backed up existing config -> {b}")
        atomic_write(cfg_path, config)

        print(f"OK Wrote {cfg_path}")
        for n in entries:
            print(f"   {'Updated' if n in updated else 'Added'}: {n}")

    print("\nNext steps:")
    step = 1
    if not args.no_desktop:
        # Restarting matters more than it looks: an MCP server is a long-lived
        # process started when the host launched, so a config change (or a package
        # upgrade) is invisible until the host relaunches it.
        print(f"  {step}. Fully quit Claude Desktop (Cmd/Ctrl+Q) and reopen it.")
        step += 1
    if do_hosted and not args.no_desktop:
        if platform.system() == "Windows":
            print(f"  {step}. (Windows) Install the bridge once:  "
                  f"npm install -g mcp-remote@latest")
        else:
            print(f"  {step}. Make sure Node.js is installed (npx bridges the "
                  f"hosted server).")
        step += 1
    if do_agent:
        # Deliberately NOT "run playwright install chromium". Chrome is driven over
        # CDP by attaching to your own browser — open_browser never launches it —
        # so the ~150MB browser download the old text demanded is not needed for
        # the path almost everyone uses. Only the firefox/chromium/edge launch
        # modes need it, and setup_browser says so at the point it matters.
        print(f"  {step}. Ask Claude to \"set up my browser\" the first time you "
              f"need a logged-in site.")
        step += 1
    print(f"  {step}. Start a new chat — the Web Speed tools will be available.")

    if do_agent:
        print(f"\nLocal agent interpreter: {args.python}")
        print(f"Local agent entry:       -m web_speed_agent.mcp_server")

    if not cfg_path.parent.exists():
        print("\n!  Note: the Claude config folder didn't exist — if Claude "
              "Desktop isn't installed yet, install it and this config will be "
              "picked up.")

    # A missing host CLI means different things depending on how we got here. If
    # it was named explicitly, print the commands — the user wants that host and
    # may be setting it up elsewhere. Under --all we are sweeping whatever happens
    # to be installed, so a host that isn't here earns one line, not a wall of
    # commands for a tool the user does not run.
    sweeping = args.all_targets and args.apply

    if args.claude_code:
        # --apply runs them; without it, or when the CLI isn't installed, fall back
        # to printing so the user still has something to copy.
        if not (args.apply and apply_claude_code(key, args.python, do_hosted, do_agent,
                                                 agent_path, use_module)):
            if sweeping:
                print("\n-  Claude Code not installed — skipped.")
            else:
                if args.apply:
                    print("\n!  `claude` CLI not found — printing the commands instead.")
                print_claude_code(key, agent_path, args.python, do_hosted, do_agent,
                                  use_module)

    if args.antigravity:
        if not (args.apply and apply_antigravity(key, args.python, do_hosted, do_agent,
                                                 agent_path, use_module)):
            if sweeping:
                print("-  Antigravity CLI not installed — skipped.")
            else:
                if args.apply:
                    print("\n!  `agy` not found (looked in ~/.local/bin and on PATH) — "
                          "printing the commands instead.")
                print_antigravity(find_agy(), key, args.python, do_hosted, do_agent,
                                  agent_path, use_module)

    if args.chatgpt:
        print_chatgpt(key, args.python, do_hosted, do_agent, agent_path, use_module)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
