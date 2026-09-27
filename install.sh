#!/bin/sh
# Web Speed Bridge — one-command install.
#
#   curl -fsSL https://api.getwebspeed.io/install.sh | sh -s -- --key wsp_YOUR_KEY
#
# Replaces the documented sequence of: install a newer Python, create a venv,
# activate it, pip install, then hand-edit claude_desktop_config.json with an
# absolute interpreter path. Every one of those steps is a place people stop.
#
# `uv tool install` is what removes them. It resolves and pins its own isolated
# interpreter, so the package can never land in a different Python from the one
# the client launches — which is the single most common failure mode, and the one
# that surfaces to the user as a blank "Couldn't start" with the real
# ModuleNotFoundError buried in a log file.
#
# POSIX sh on purpose: this is piped into whatever /bin/sh is, which on Debian is
# dash, not bash.

set -eu

KEY="${WEBSPEED_API_KEY:-}"

while [ $# -gt 0 ]; do
    case "$1" in
        --key)
            KEY="${2:-}"
            shift
            if [ $# -gt 0 ]; then shift; fi
            ;;
        --key=*) KEY="${1#*=}"; shift ;;
        -h|--help)
            echo "usage: install.sh --key wsp_YOUR_KEY"
            echo "       WEBSPEED_API_KEY=wsp_... install.sh"
            exit 0 ;;
        *) echo "install.sh: unknown option '$1'" >&2; exit 2 ;;
    esac
done

say()  { printf '%s\n' "$*"; }
fail() { printf '\nx  %s\n' "$*" >&2; exit 1; }

if [ -z "$KEY" ]; then
    fail "No API key.

   curl -fsSL https://api.getwebspeed.io/install.sh | sh -s -- --key wsp_YOUR_KEY

   Don't have one? A free key with 100 maps: https://getwebspeed.io/free"
fi

case "$KEY" in
    wsp_*) ;;
    *) fail "That key doesn't look right — Web Speed keys start with 'wsp_'." ;;
esac

# ── 1. uv ────────────────────────────────────────────────────────────────────
if command -v uv >/dev/null 2>&1; then
    say "-  uv already installed ($(uv --version 2>/dev/null || echo 'version unknown'))"
else
    say "-  Installing uv (manages an isolated Python for the Bridge)…"
    curl -LsSf https://astral.sh/uv/install.sh | sh >/dev/null 2>&1 \
        || fail "Could not install uv. Install it yourself and re-run:
   https://docs.astral.sh/uv/getting-started/installation/"
    # The installer adds uv to PATH via the shell profile, which this
    # non-interactive shell has not read. Find it now rather than telling the
    # user to open a new terminal and start over.
    for d in "$HOME/.local/bin" "$HOME/.cargo/bin"; do
        [ -x "$d/uv" ] && PATH="$d:$PATH" && export PATH && break
    done
    command -v uv >/dev/null 2>&1 || fail "uv installed but isn't on PATH. \
Open a new terminal and re-run this command."
fi

# ── 2. the Bridge ────────────────────────────────────────────────────────────
say "-  Installing web-speed-agent…"
uv tool install --upgrade web-speed-agent >/dev/null 2>&1 \
    || fail "uv could not install web-speed-agent. Run this to see why:
   uv tool install --upgrade web-speed-agent"

# uv puts console scripts in its own bin dir. Ask uv where that is rather than
# guessing — it differs across platforms and honours UV_TOOL_BIN_DIR.
UV_BIN="$(uv tool dir --bin 2>/dev/null || echo "$HOME/.local/bin")"
[ -d "$UV_BIN" ] && PATH="$UV_BIN:$PATH" && export PATH

command -v webspeed-configure >/dev/null 2>&1 \
    || fail "Installed, but webspeed-configure isn't on PATH (looked in $UV_BIN).
   Run:  uv tool update-shell   then open a new terminal."

# ── 3. write the client configs ──────────────────────────────────────────────
say "-  Configuring your AI clients…"
say ""
# --all covers every host we can configure without asking: Claude Desktop, Claude
# Code and the Antigravity CLI, plus the ChatGPT steps (which cannot be automated
# — ChatGPT never launches a local process). Hosts that aren't installed are
# skipped quietly rather than dumping commands nobody asked for.
webspeed-configure --key "$KEY" --all --apply

say ""
say "Done. Restart Claude Desktop, then ask it: \"Check my Web Speed account info\""
