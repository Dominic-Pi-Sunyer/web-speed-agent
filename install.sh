#!/bin/sh
# Web Speed Bridge — one-command install.
#
#   curl -fsSL https://api.getwebspeed.io/install.sh | sh
#
# You are prompted for your API key (input hidden) and it is stored in your OS
# keychain — never written into any AI app's config file. `--key wsp_...` still
# works for scripted installs, but it leaves the key in your shell history.
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
SCOPE=""

while [ $# -gt 0 ]; do
    case "$1" in
        --key)
            KEY="${2:-}"
            shift
            if [ $# -gt 0 ]; then shift; fi
            ;;
        --key=*) KEY="${1#*=}"; shift ;;
        # Which servers to configure. Default: both the hosted mapping tools and
        # the local browser agent (the Bridge).
        --hosted-only|--agent-only) SCOPE="$1"; shift ;;
        -h|--help)
            echo "usage: install.sh [--hosted-only | --agent-only]   (prompts for your key)"
            echo "       WEBSPEED_API_KEY=wsp_... install.sh"
            exit 0 ;;
        *) echo "install.sh: unknown option '$1'" >&2; exit 2 ;;
    esac
done

say()  { printf '%s\n' "$*"; }
fail() { printf '\nx  %s\n' "$*" >&2; exit 1; }

# A key passed in (--key / WEBSPEED_API_KEY) is checked now, before anything is
# installed. With no key, the prompt comes AFTER the install: only then can we ask
# the installed package whether a key is already in the keychain — and re-running
# this script is also how people upgrade, so it must not demand the key every time.
if [ -n "$KEY" ]; then
    case "$KEY" in
        wsp_*) ;;
        *) fail "That key doesn't look right — Web Speed keys start with 'wsp_'." ;;
    esac
fi

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

# ── 3. the key ───────────────────────────────────────────────────────────────
if [ -z "$KEY" ] && webspeed-key status >/dev/null 2>&1; then
    say "-  Using the API key already stored in your keychain."
else
    # Prompt on the terminal itself. stdin is this script (piped from curl), so a
    # plain `read` would consume the script; /dev/tty is the user's keyboard. Echo is
    # off while typing so the key never appears on screen or in a screen share.
    # `( : < /dev/tty )` actually opens it: under cron/CI /dev/tty exists and looks
    # readable but has no terminal behind it, and a failed write would abort the
    # whole script under `set -e`.
    if [ -z "$KEY" ] && ( : < /dev/tty ) 2>/dev/null; then
        # Echo goes off BEFORE the prompt is printed. The other order leaves a
        # window where a fast paste — the normal reaction to a prompt — is echoed
        # to the screen before input is hidden.
        _stty="$(stty -g < /dev/tty 2>/dev/null || true)"
        trap '[ -n "$_stty" ] && stty "$_stty" < /dev/tty 2>/dev/null; exit 130' INT TERM
        stty -echo < /dev/tty 2>/dev/null || true
        printf 'Paste your Web Speed API key (input hidden): ' > /dev/tty
        IFS= read -r KEY < /dev/tty || KEY=""
        [ -n "$_stty" ] && stty "$_stty" < /dev/tty 2>/dev/null || stty echo < /dev/tty 2>/dev/null || true
        trap - INT TERM
        printf '\n' > /dev/tty
    fi



    if [ -z "$KEY" ]; then
        fail "No API key.

   Re-run and paste it when prompted:
   curl -fsSL https://api.getwebspeed.io/install.sh | sh

   Your key: https://api.getwebspeed.io/account
   Don't have one? A free key with 100 maps: https://getwebspeed.io/free"
    fi
    case "$KEY" in
        wsp_*) ;;
        *) fail "That key doesn't look right — Web Speed keys start with 'wsp_'." ;;
    esac
fi

# ── 4. write the client configs ──────────────────────────────────────────────
say "-  Configuring your AI clients…"
say ""
# --all covers every host we can configure without asking: Claude Desktop, Claude
# Code and the Antigravity CLI, plus the ChatGPT steps (which cannot be automated
# — ChatGPT never launches a local process). Hosts that aren't installed are
# skipped quietly rather than dumping commands nobody asked for.
# The key goes to the configurator through its environment, not its arguments:
# argv is visible to every process on the machine via `ps` while it runs. It is
# stored in the OS keychain and the client configs reference no secret at all.
# $SCOPE is unquoted on purpose: empty means "no flag", not an empty argument.
# shellcheck disable=SC2086
if [ -n "$KEY" ]; then
    WEBSPEED_API_KEY="$KEY" webspeed-configure --all --apply $SCOPE
else
    webspeed-configure --all --apply $SCOPE    # uses the key in the keychain
fi

say ""
say "Done. Restart Claude Desktop, then ask it: \"Check my Web Speed account info\""
