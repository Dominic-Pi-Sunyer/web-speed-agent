"""Site lists, confirmation, and the audit log.

The confirmation tests are the ones that earn their keep. A checkpoint that
silently stops asking is the worst outcome available: the operator believes every
action was approved, and none of them were. So the fail-closed paths — no
Context, a client without elicitation, a declined prompt, a transport error — are
each pinned separately.

Run:  python3.11 -m pytest tests/test_policy.py -q --asyncio-mode=auto
"""

from __future__ import annotations

import json

import pytest

from web_speed_agent import mcp_server as srv
from web_speed_agent import safety


def _res(raw: str) -> dict:
    return json.loads(raw)


@pytest.fixture(autouse=True)
def clean_policy(monkeypatch):
    """Every test starts from the shipped defaults: nothing enabled."""
    monkeypatch.setattr(safety, "READONLY", False)
    monkeypatch.setattr(safety, "CONFIRM", "off")
    monkeypatch.setattr(safety, "ALLOW_SITES", ())
    monkeypatch.setattr(safety, "DENY_SITES", ())
    monkeypatch.setattr(safety, "AUDIT_PATH", None)
    yield


# ── fake MCP clients ─────────────────────────────────────────────────────────

class _Caps:
    def __init__(self, elicitation):
        self.elicitation = elicitation


class _Session:
    def __init__(self, elicitation):
        self.client_params = type("P", (), {"capabilities": _Caps(elicitation)})()


class _Ctx:
    """A client that can elicit, answering however the test says."""

    def __init__(self, answer="accept", approve=True, raises=False):
        self.session = _Session(object())
        self._answer, self._approve, self._raises = answer, approve, raises
        self.asked: list[str] = []

    async def elicit(self, message, schema):
        self.asked.append(message)
        if self._raises:
            raise RuntimeError("transport died")
        data = type("D", (), {"approve": self._approve})()
        return type("R", (), {"action": self._answer, "data": data})()


class _CtxNoElicit(_Ctx):
    """A client that never declared the elicitation capability."""

    def __init__(self):
        super().__init__()
        self.session = _Session(None)


# ── site lists ───────────────────────────────────────────────────────────────

def test_no_lists_allows_everything():
    assert safety.site_allowed("https://anything.example")[0] is True


def test_deny_list_blocks_the_host_and_its_subdomains(monkeypatch):
    monkeypatch.setattr(safety, "DENY_SITES", ("schoology.com",))
    assert safety.site_allowed("https://schoology.com/x")[0] is False
    assert safety.site_allowed("https://app.schoology.com/x")[0] is False
    assert safety.site_allowed("https://example.com")[0] is True


def test_allow_list_blocks_everything_else(monkeypatch):
    monkeypatch.setattr(safety, "ALLOW_SITES", ("wikipedia.org",))
    assert safety.site_allowed("https://en.wikipedia.org/wiki/X")[0] is True
    assert safety.site_allowed("https://example.com")[0] is False


def test_deny_beats_allow(monkeypatch):
    monkeypatch.setattr(safety, "ALLOW_SITES", ("example.com",))
    monkeypatch.setattr(safety, "DENY_SITES", ("secret.example.com",))
    assert safety.site_allowed("https://www.example.com")[0] is True
    assert safety.site_allowed("https://secret.example.com")[0] is False


def test_a_lookalike_host_is_not_a_match(monkeypatch):
    """'notexample.com' must not satisfy an allow-list entry of 'example.com'."""
    monkeypatch.setattr(safety, "ALLOW_SITES", ("example.com",))
    assert safety.site_allowed("https://notexample.com")[0] is False


def test_hostless_urls_fail_closed_only_when_an_allow_list_exists(monkeypatch):
    assert safety.site_allowed("about:blank")[0] is True
    monkeypatch.setattr(safety, "ALLOW_SITES", ("example.com",))
    assert safety.site_allowed("about:blank")[0] is False


def test_refusal_explains_where_the_rule_came_from(monkeypatch):
    monkeypatch.setattr(safety, "DENY_SITES", ("bank.com",))
    ok, why = safety.site_allowed("https://bank.com")
    assert ok is False
    assert "WEBSPEED_DENY_SITES" in why and "cannot be changed from here" in why


@pytest.mark.asyncio
async def test_navigate_refuses_a_blocked_host(monkeypatch):
    monkeypatch.setattr(safety, "DENY_SITES", ("blocked.test",))
    out = _res(await srv.navigate("https://blocked.test/page"))
    assert out["ok"] is False
    assert "WEBSPEED_DENY_SITES" in out["error"]


# ── confirmation levels ──────────────────────────────────────────────────────

def test_off_asks_for_nothing():
    assert safety.needs_confirmation("submit_form") is False
    assert safety.needs_confirmation("click", "Delete everything") is False


def test_writes_covers_write_tools_and_risky_labels(monkeypatch):
    monkeypatch.setattr(safety, "CONFIRM", "writes")
    assert safety.needs_confirmation("submit_form") is True
    assert safety.needs_confirmation("evaluate") is True
    assert safety.needs_confirmation("click", "#next Delete account") is True
    assert safety.needs_confirmation("click", "#next Next page") is False


def test_all_asks_for_everything(monkeypatch):
    monkeypatch.setattr(safety, "CONFIRM", "all")
    assert safety.needs_confirmation("click", "Next page") is True
    assert safety.needs_confirmation("navigate", "https://example.com") is True


def test_an_unknown_level_is_treated_as_all_not_off(monkeypatch):
    """A typo must fail toward asking. Falling back to 'off' would leave someone
    believing they are being consulted when nothing ever asks."""
    monkeypatch.setenv("WEBSPEED_CONFIRM", "yes-please")
    safety.reload()
    try:
        assert safety.CONFIRM == "all"
    finally:
        monkeypatch.delenv("WEBSPEED_CONFIRM", raising=False)
        safety.reload()


# ── confirmation behaviour, including every fail-closed path ─────────────────

@pytest.mark.asyncio
async def test_approval_lets_the_action_through(monkeypatch):
    monkeypatch.setattr(safety, "CONFIRM", "all")
    ctx = _Ctx(answer="accept", approve=True)
    assert await srv._confirm(ctx, "submit_form", "Submit?") is None
    assert ctx.asked == ["Submit?"]


@pytest.mark.asyncio
async def test_declining_blocks_the_action(monkeypatch):
    monkeypatch.setattr(safety, "CONFIRM", "all")
    out = _res(await srv._confirm(_Ctx(answer="decline"), "submit_form", "Submit?"))
    assert out["ok"] is False and "did not approve" in out["error"]


@pytest.mark.asyncio
async def test_accepting_but_answering_no_still_blocks(monkeypatch):
    """action='accept' only means the prompt was answered, not that it was a yes."""
    monkeypatch.setattr(safety, "CONFIRM", "all")
    out = _res(await srv._confirm(_Ctx(answer="accept", approve=False),
                                  "submit_form", "Submit?"))
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_cancelling_blocks_the_action(monkeypatch):
    monkeypatch.setattr(safety, "CONFIRM", "all")
    out = _res(await srv._confirm(_Ctx(answer="cancel"), "submit_form", "Submit?"))
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_client_without_elicitation_fails_closed(monkeypatch):
    """The important one: no human can be asked, so nothing runs."""
    monkeypatch.setattr(safety, "CONFIRM", "all")
    out = _res(await srv._confirm(_CtxNoElicit(), "submit_form", "Submit?"))
    assert out["ok"] is False
    assert "does not support elicitation" in out["error"]
    assert "WEBSPEED_READONLY" in out["error"], "should point at the control that does not depend on the client"


@pytest.mark.asyncio
async def test_missing_context_fails_closed(monkeypatch):
    monkeypatch.setattr(safety, "CONFIRM", "all")
    out = _res(await srv._confirm(None, "submit_form", "Submit?"))
    assert out["ok"] is False


@pytest.mark.asyncio
async def test_a_broken_elicitation_fails_closed(monkeypatch):
    monkeypatch.setattr(safety, "CONFIRM", "all")
    out = _res(await srv._confirm(_Ctx(raises=True), "submit_form", "Submit?"))
    assert out["ok"] is False
    assert "transport died" in out["error"]


@pytest.mark.asyncio
async def test_nothing_is_asked_when_confirmation_is_off():
    ctx = _Ctx()
    assert await srv._confirm(ctx, "submit_form", "Submit?") is None
    assert ctx.asked == [], "asked for approval while disabled"


# ── audit log ────────────────────────────────────────────────────────────────

def test_audit_is_silent_when_disabled(tmp_path):
    safety.audit("click", url="https://x.test")
    assert not list(tmp_path.iterdir())


def test_audit_writes_jsonl_and_records_the_policy_first(monkeypatch, tmp_path):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(safety, "AUDIT_PATH", log)
    safety.audit("click", url="https://x.test", selector="#go")
    safety.audit("navigate", url="https://y.test")

    lines = [json.loads(l) for l in log.read_text().splitlines()]
    assert lines[0]["tool"] == "_policy", "the log should open with the policy in force"
    assert [l["tool"] for l in lines[1:]] == ["click", "navigate"]
    assert lines[1]["params"]["selector"] == "#go"


def test_audit_records_field_names_but_never_typed_values(monkeypatch, tmp_path):
    """A safety log must not become a keylogger — it would hold passwords."""
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(safety, "AUDIT_PATH", log)
    safety.audit("fill_field", url="https://x.test", selector="#pw",
                 value="hunter2-very-secret")
    body = log.read_text()
    assert "hunter2" not in body, "a typed value reached the audit log"
    assert "#pw" in body, "the field acted on should still be identifiable"
    assert "19 chars" in body


def test_audit_file_is_owner_only(monkeypatch, tmp_path):
    log = tmp_path / "audit.jsonl"
    monkeypatch.setattr(safety, "AUDIT_PATH", log)
    safety.audit("click", url="https://x.test")
    assert (log.stat().st_mode & 0o077) == 0, "audit log is readable by others"


def test_audit_failure_disables_itself_instead_of_breaking_work(monkeypatch, tmp_path):
    bad = tmp_path / "nope"
    bad.write_text("i am a file, not a directory")
    monkeypatch.setattr(safety, "AUDIT_PATH", bad / "sub" / "audit.jsonl")
    safety.audit("click", url="https://x.test")     # must not raise
    assert safety.AUDIT_PATH is None, "a failing log should switch itself off loudly"


# ── status tool ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_reports_no_restrictions_by_default():
    out = _res(await srv.safety_status())
    assert out["ok"] is True
    assert "No restrictions" in out["summary"]


@pytest.mark.asyncio
async def test_status_lists_every_active_limit(monkeypatch, tmp_path):
    monkeypatch.setattr(safety, "READONLY", True)
    monkeypatch.setattr(safety, "CONFIRM", "writes")
    monkeypatch.setattr(safety, "DENY_SITES", ("bank.com",))
    monkeypatch.setattr(safety, "AUDIT_PATH", tmp_path / "a.jsonl")
    out = _res(await srv.safety_status())
    joined = " ".join(out["limits"])
    assert "Read-only" in joined and "bank.com" in joined
    assert "Confirmation" in joined and "a.jsonl" in joined
