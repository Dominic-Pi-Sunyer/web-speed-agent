"""webspeed-key — manage the Web Speed API key in your OS keychain.

    webspeed-key set        paste your key (hidden); stored in the keychain
    webspeed-key status     which key is in use, from where, and any plaintext
                            copies still sitting in config files
    webspeed-key delete     remove it from the keychain

`set` never takes the key as a command-line argument. A key on the command line
lands in your shell history and is visible to other processes in `ps` while the
command runs — the same exposure the keychain exists to remove. It reads from a
hidden prompt, or from stdin when piped (`webspeed-key set < key.txt`).
"""
from __future__ import annotations

import argparse
import getpass
import os
import platform
import sys
from pathlib import Path

from . import keystore

_ACCOUNT_URL = "https://api.getwebspeed.io/account"


def known_config_files() -> list[Path]:
    """Every file an earlier install may have written a plaintext key into."""
    home = Path.home()
    out: list[Path] = []
    system = platform.system()
    if system == "Darwin":
        desktop = home / "Library" / "Application Support" / "Claude"
    elif system == "Windows":
        desktop = Path(os.environ.get("APPDATA") or home / "AppData" / "Roaming") / "Claude"
    else:
        desktop = Path(os.environ.get("XDG_CONFIG_HOME") or home / ".config") / "Claude"
    out.append(desktop / "claude_desktop_config.json")
    # webspeed-configure's own backups: each one is a full copy of the config,
    # key included, made every time it ran.
    if desktop.is_dir():
        out.extend(sorted(desktop.glob("claude_desktop_config.json.bak-*")))
    out += [
        home / ".claude.json",                              # Claude Code
        home / ".gemini" / "config" / "mcp_config.json",    # Antigravity CLI
        home / ".gemini" / "settings.json",                 # standalone Gemini CLI
        home / ".cursor" / "mcp.json",
        home / ".webspeed" / "config.yaml",                 # this package, pre-0.7
    ]
    return out


def find_plaintext_keys(paths: list[Path] | None = None) -> list[tuple[Path, int]]:
    """(file, count) for every known file containing a wsp_ key. Reports where,
    never what — the keys themselves are not returned."""
    hits: list[tuple[Path, int]] = []
    for p in paths if paths is not None else known_config_files():
        try:
            if not p.is_file():
                continue
            n = len(keystore.KEY_RE.findall(p.read_text(encoding="utf-8", errors="ignore")))
        except OSError:
            continue
        if n:
            hits.append((p, n))
    return hits


def _read_key_interactively() -> str:
    if sys.stdin.isatty():
        return getpass.getpass("Paste your Web Speed API key (input hidden): ")
    return sys.stdin.readline()


def verify(key: str, timeout: float = 10.0) -> bool | None:
    """True = server accepts it, False = rejected, None = couldn't tell (offline)."""
    import httpx
    base = (os.environ.get("WEBSPEED_SERVER_URL") or "https://api.getwebspeed.io").rstrip("/")
    try:
        r = httpx.get(f"{base}/v1/usage", headers={"x-web-speed-key": key}, timeout=timeout)
    except httpx.HTTPError:
        return None
    if r.status_code == 200:
        return True
    if r.status_code in (401, 403):
        return False
    return None


def cmd_set(args: argparse.Namespace) -> int:
    if not keystore.available():
        print("x  No system keychain is available on this machine (common on "
              "headless Linux).\n   Set WEBSPEED_API_KEY in the environment of "
              "your MCP client instead.")
        return 2
    key = _read_key_interactively().strip()
    if not key.startswith("wsp_"):
        print("x  That doesn't look like a Web Speed key — they start with 'wsp_'.")
        return 2
    if not args.no_verify:
        ok = verify(key)
        if ok is False:
            print("x  Web Speed rejected that key, so it was NOT saved.\n"
                  f"   Check you copied it in full, or get your current key at {_ACCOUNT_URL}")
            return 1
        if ok is None:
            print("!  Couldn't reach Web Speed to check the key (offline?). Saving it anyway.")
    keystore.set(key)
    print(f"OK Saved {keystore.mask(key)} to your system keychain.")
    if os.environ.get(keystore.ENV_VAR):
        print(f"!  {keystore.ENV_VAR} is set in this shell and takes precedence "
              "over the keychain. Unset it, or the stored key will not be used.")
    print("   Restart your AI app (fully quit and reopen) to pick it up.")
    _warn_plaintext()
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    key, source = keystore.resolve()
    if key:
        where = {"env": f"the {keystore.ENV_VAR} environment variable",
                 "keychain": "your system keychain"}[source]
        print(f"Key in use: {keystore.mask(key)} (from {where})")
        if source == "env" and keystore.get():
            print(f"   A different key may also be in the keychain; {keystore.ENV_VAR} wins.")
    else:
        print(f"No key configured. Run `webspeed-key set`.")
    print(f"Keychain available: {'yes' if keystore.available() else 'no'}")
    _warn_plaintext(always=True)
    return 0 if key else 1


def cmd_delete(_args: argparse.Namespace) -> int:
    if keystore.delete():
        print("OK Removed the Web Speed key from your keychain.")
        return 0
    print("-  No Web Speed key was stored in the keychain.")
    return 0


def _warn_plaintext(always: bool = False) -> None:
    hits = find_plaintext_keys()
    if not hits:
        if always:
            print("No plaintext Web Speed keys found in known config files.")
        return
    print("\n!  Plaintext Web Speed keys are still in these files:")
    for p, n in hits:
        print(f"     {p}  ({n})")
    print("   Re-run `webspeed-configure --all --apply` to rewrite the client configs "
          "without the key.\n   Old backups (*.bak-*) are safe to delete once things work. "
          "If any of these files were\n   shared, synced or committed, rotate your key "
          f"at {_ACCOUNT_URL}.")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="webspeed-key",
                                 description="Manage your Web Speed API key in the OS keychain.")
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("set", help="Store your key (prompts; never pass it as an argument).")
    s.add_argument("--no-verify", action="store_true",
                   help="Don't check the key against the Web Speed API before saving.")
    s.set_defaults(func=cmd_set)
    sub.add_parser("status", help="Show which key is in use and find plaintext copies."
                   ).set_defaults(func=cmd_status)
    sub.add_parser("delete", help="Remove the key from the keychain.").set_defaults(func=cmd_delete)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
