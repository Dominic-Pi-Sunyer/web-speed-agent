"""Bridge safety policy: read-only, site lists, confirmation, and the audit log.

Every control here is configured by ENVIRONMENT VARIABLE and nothing else. That
is deliberate and it is the whole design:

    An agent cannot turn any of this off, because nothing it can call
    reaches the setting.

A `confirm=true` tool parameter, or a "disable_safety" tool, would be theatre —
the caller being restrained would hold the key. The same reasoning rules out a
config file the browser could reach.

The controls are not equally strong, and it matters which is which:

  read-only    A boundary. Enforced at the network layer, so nothing that
               mutates leaves the browser regardless of how it was triggered.
  site lists   A boundary. Checked before navigation and again on every request.
  confirmation A checkpoint, not a boundary. It asks a HUMAN through MCP
               elicitation; it fails closed when no human can be reached. Its
               risky-word matching is a safety net with false negatives — never
               rely on it to contain an agent. Use read-only for that.
  audit log    Evidence, not prevention. It is what makes an incident
               reconstructible afterwards.

Settings (all optional, all off by default):

    WEBSPEED_READONLY=1                 block every non-GET request
    WEBSPEED_CONFIRM=off|writes|all     ask a human before acting
    WEBSPEED_ALLOW_SITES=a.com,b.com    only these hosts (empty = all)
    WEBSPEED_DENY_SITES=c.com           never these hosts (wins over allow)
    WEBSPEED_AUDIT_LOG=1|<path>         1 → ~/.webspeed/audit.jsonl
"""

from __future__ import annotations

import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# ── config ───────────────────────────────────────────────────────────────────

CONFIRM_LEVELS = ("off", "writes", "all")

READONLY: bool = False
CONFIRM: str = "off"
ALLOW_SITES: tuple[str, ...] = ()
DENY_SITES: tuple[str, ...] = ()
AUDIT_PATH: Path | None = None


def _flag(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in ("1", "true", "yes", "on")


def _hosts(name: str) -> tuple[str, ...]:
    raw = os.getenv(name, "")
    return tuple(h.strip().lower().lstrip(".") for h in raw.split(",") if h.strip())


def reload() -> None:
    """Re-read every setting from the environment. Called at import; tests reuse it."""
    global READONLY, CONFIRM, ALLOW_SITES, DENY_SITES, AUDIT_PATH

    READONLY = _flag("WEBSPEED_READONLY")

    level = os.getenv("WEBSPEED_CONFIRM", "off").strip().lower() or "off"
    if level not in CONFIRM_LEVELS:
        # A typo must not silently mean "off" — that is the failure where someone
        # believes they are being asked before anything happens, and never is.
        print(f"[web-speed-agent] WEBSPEED_CONFIRM={level!r} is not one of "
              f"{'/'.join(CONFIRM_LEVELS)} — treating it as 'all' until corrected.",
              file=sys.stderr, flush=True)
        level = "all"
    CONFIRM = level

    ALLOW_SITES = _hosts("WEBSPEED_ALLOW_SITES")
    DENY_SITES = _hosts("WEBSPEED_DENY_SITES")

    raw = os.getenv("WEBSPEED_AUDIT_LOG", "").strip()
    if not raw or raw.lower() in ("0", "false", "no", "off"):
        AUDIT_PATH = None
    elif raw.lower() in ("1", "true", "yes", "on"):
        AUDIT_PATH = Path("~/.webspeed/audit.jsonl").expanduser()
    else:
        AUDIT_PATH = Path(raw).expanduser()


reload()


def describe() -> dict[str, Any]:
    """Current policy, for the status tool and the audit log's first line."""
    return {
        "read_only": READONLY,
        "confirm": CONFIRM,
        "allow_sites": list(ALLOW_SITES),
        "deny_sites": list(DENY_SITES),
        "audit_log": str(AUDIT_PATH) if AUDIT_PATH else None,
    }


def any_enabled() -> bool:
    return bool(READONLY or CONFIRM != "off" or ALLOW_SITES or DENY_SITES or AUDIT_PATH)


# ── site lists ───────────────────────────────────────────────────────────────

def _host_of(url: str) -> str | None:
    try:
        return (urlparse(url).hostname or "").lower() or None
    except Exception:  # noqa: BLE001
        return None


def _matches(host: str, patterns: tuple[str, ...]) -> str | None:
    """The pattern that matched, or None. A bare domain covers its subdomains."""
    for p in patterns:
        if host == p or host.endswith("." + p):
            return p
    return None


def site_allowed(url: str) -> tuple[bool, str]:
    """(allowed, reason). Deny always beats allow.

    A URL with no hostname (about:blank, file://, data:) is allowed only when no
    allow-list is set. With one configured, "everything not named" includes these
    — failing open there would leave a trivially reachable hole.
    """
    if not ALLOW_SITES and not DENY_SITES:
        return True, ""

    host = _host_of(url)
    if host is None:
        if ALLOW_SITES:
            return False, (f"'{url[:80]}' has no hostname, and an allow-list is set. "
                           f"Only these are reachable: {', '.join(ALLOW_SITES)}")
        return True, ""

    if hit := _matches(host, DENY_SITES):
        return False, (f"{host} is blocked by WEBSPEED_DENY_SITES (matched '{hit}'). "
                       f"This is set outside the agent and cannot be changed from here.")

    if ALLOW_SITES and not _matches(host, ALLOW_SITES):
        return False, (f"{host} is not in WEBSPEED_ALLOW_SITES. Reachable hosts: "
                       f"{', '.join(ALLOW_SITES)}")

    return True, ""


# ── confirmation ─────────────────────────────────────────────────────────────

# Tools whose only purpose is to change something. Always confirmed at 'writes'.
WRITE_TOOLS = frozenset({
    "submit_form", "login", "workspace_write", "workspace_new_slide", "evaluate",
})

# Words that suggest an action is outward-facing or hard to undo. A NET, not a
# boundary: it reads the button you are about to press, so it misses an icon with
# no text, a non-English label, or a control named something bland. Worth having
# because the expensive mistakes are usually labelled honestly.
_RISKY_RE = re.compile(
    r"\b(post|publish|send|submit|pay|buy|purchase|order|checkout|delete|remove|"
    r"destroy|transfer|withdraw|share|invite|apply|enroll|unenroll|drop|archive|"
    r"report|deactivate|unsubscribe|accept|decline|confirm)\b", re.I)


def looks_risky(text: str | None) -> bool:
    return bool(text and _RISKY_RE.search(text))


def needs_confirmation(tool: str, target: str | None = None) -> bool:
    """Should a human be asked before running `tool`?

    `target` is whatever the caller is about to act on — a selector, the button's
    text, the URL. It is only consulted at the 'writes' level, where the point is
    to catch a risky click without confirming every ordinary one.
    """
    if CONFIRM == "off":
        return False
    if CONFIRM == "all":
        return True
    return tool in WRITE_TOOLS or looks_risky(target)


# ── audit log ────────────────────────────────────────────────────────────────

# Logged field NAMES, never their values. This is a safety log, not a keylogger:
# recording what was typed would put passwords, card numbers and private messages
# into a plaintext file, which is a bigger hazard than the one being audited.
_VALUE_FIELDS = frozenset({"value", "text", "password", "username", "key", "api_key"})


def _redact(params: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in params.items():
        if v is None:
            continue
        if k in _VALUE_FIELDS:
            out[k] = f"<{len(str(v))} chars>" if str(v) else "<empty>"
        elif isinstance(v, str):
            out[k] = v[:200]
        else:
            out[k] = v
    return out


def audit(tool: str, *, url: str | None = None, ok: bool | None = None,
          detail: str | None = None, **params: Any) -> None:
    """Append one line to the audit log. Never raises — auditing must not break work.

    Failures are reported once to stderr rather than silently swallowed: a log
    that quietly stopped recording is worse than no log, because it is trusted.
    """
    global AUDIT_PATH
    if AUDIT_PATH is None:
        return
    event = {
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "tool": tool,
        "url": (url or "")[:300] or None,
        "ok": ok,
        "detail": (detail or "")[:300] or None,
        "params": _redact(params) or None,
    }
    try:
        AUDIT_PATH.parent.mkdir(parents=True, exist_ok=True)
        first = not AUDIT_PATH.exists()
        with AUDIT_PATH.open("a", encoding="utf-8") as fh:
            if first:
                fh.write(json.dumps({"ts": event["ts"], "tool": "_policy",
                                     "params": describe()}, ensure_ascii=False) + "\n")
            fh.write(json.dumps({k: v for k, v in event.items() if v is not None},
                                ensure_ascii=False) + "\n")
        if first:
            # It records every host visited, so it must not be world-readable.
            try:
                AUDIT_PATH.chmod(0o600)
            except OSError:
                pass
    except Exception as exc:  # noqa: BLE001
        print(f"[web-speed-agent] audit log disabled — cannot write {AUDIT_PATH}: {exc}",
              file=sys.stderr, flush=True)
        AUDIT_PATH = None
