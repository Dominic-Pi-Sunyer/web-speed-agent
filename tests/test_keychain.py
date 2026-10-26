"""Keychain-backed API key (0.7.0): nothing secret in any MCP client config.

Everything runs against an in-memory keyring backend — these tests never touch
the real system keychain.
"""
from __future__ import annotations

import io
import json
import sys

import httpx
import keyring
import mcp.types as types
import pytest
from keyring.backend import KeyringBackend
from keyring.errors import PasswordDeleteError

from web_speed_agent import configure, hosted_proxy, key_cli, keystore

KEY = "wsp_TESTKEY_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1234"
OTHER = "wsp_OTHERKEY_bbbbbbbbbbbbbbbbbbbbbbbbbbbbb9999"


class MemKeyring(KeyringBackend):
    priority = 1

    def __init__(self):
        self.d: dict[tuple[str, str], str] = {}

    def get_password(self, s, u):
        return self.d.get((s, u))

    def set_password(self, s, u, p):
        self.d[(s, u)] = p

    def delete_password(self, s, u):
        if (s, u) not in self.d:
            raise PasswordDeleteError()
        del self.d[(s, u)]


@pytest.fixture
def kc(monkeypatch):
    original = keyring.get_keyring()
    mem = MemKeyring()
    keyring.set_keyring(mem)
    monkeypatch.delenv("WEBSPEED_API_KEY", raising=False)
    yield mem
    keyring.set_keyring(original)


@pytest.fixture
def no_kc(monkeypatch):
    from keyring.backends import fail
    original = keyring.get_keyring()
    keyring.set_keyring(fail.Keyring())
    monkeypatch.delenv("WEBSPEED_API_KEY", raising=False)
    yield
    keyring.set_keyring(original)


# ── keystore ──────────────────────────────────────────────────────────────────

class TestKeystore:
    def test_env_beats_keychain(self, kc, monkeypatch):
        keystore.set(KEY)
        assert keystore.resolve() == (KEY, "keychain")
        monkeypatch.setenv("WEBSPEED_API_KEY", OTHER)
        assert keystore.resolve() == (OTHER, "env")

    def test_nothing_configured(self, kc):
        assert keystore.resolve() == (None, "none")

    def test_rejects_non_keys(self, kc):
        with pytest.raises(ValueError):
            keystore.set("sk_live_whatever")

    def test_no_keychain_is_detected_and_never_raises_on_read(self, no_kc):
        assert keystore.available() is False
        assert keystore.get() is None
        with pytest.raises(keystore.KeystoreUnavailable):
            keystore.set(KEY)

    def test_delete(self, kc):
        keystore.set(KEY)
        assert keystore.delete() is True
        assert keystore.get() is None
        assert keystore.delete() is False

    def test_mask_hides_the_key(self):
        m = keystore.mask(KEY)
        assert m.endswith(KEY[-4:]) and KEY[4:-4] not in m

    def test_config_reads_the_keychain(self, kc, tmp_path):
        from web_speed_agent.config import Config
        keystore.set(KEY)
        assert Config(tmp_path).api_key == KEY


# ── configure: the written configs carry no secret ───────────────────────────

def _run_configure(tmp_path, monkeypatch, argv, verified=True, existing=None):
    cfg = tmp_path / "claude_desktop_config.json"
    if existing is not None:
        cfg.write_text(json.dumps(existing))
    monkeypatch.setattr(configure, "module_importable", lambda _py: True)
    monkeypatch.setattr(configure, "_verify", lambda _k, timeout=10.0: verified)
    # Keep the leftover-plaintext sweep inside the sandbox.
    monkeypatch.setattr(key_cli, "known_config_files", lambda: sorted(tmp_path.glob("*")))
    monkeypatch.setattr(sys, "stdin", io.StringIO(""))  # never prompt in tests
    code = configure.main(["--config-path", str(cfg), *argv])
    return code, cfg


class TestConfigureKeychainMode:
    def test_configs_contain_no_key(self, kc, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBSPEED_API_KEY", KEY)
        code, cfg = _run_configure(tmp_path, monkeypatch, [])
        assert code == 0
        text = cfg.read_text()
        assert "wsp_" not in text
        servers = json.loads(text)["mcpServers"]
        hosted, agent = servers["webspeed"], servers["web-speed-agent"]
        assert hosted["args"] == ["-m", "web_speed_agent.hosted_proxy"]
        assert "env" not in agent
        assert keystore.get() == KEY

    def test_replaces_plaintext_entries_and_keeps_other_servers(self, kc, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBSPEED_API_KEY", KEY)
        existing = {"mcpServers": {
            "webspeed": {"command": "npx", "args": ["-y", "mcp-remote@latest",
                         "https://api.getwebspeed.io/mcp/sse", "--header",
                         f"X-Web-Speed-Key: {OTHER}"]},
            "web-speed-agent": {"command": "python3", "args": ["x"],
                                "env": {"WEBSPEED_API_KEY": OTHER}},
            "unrelated": {"command": "foo", "env": {"FOO_TOKEN": "keep-me"}},
        }}
        code, cfg = _run_configure(tmp_path, monkeypatch, [], existing=existing)
        assert code == 0
        text = cfg.read_text()
        assert "wsp_" not in text
        assert json.loads(text)["mcpServers"]["unrelated"]["env"]["FOO_TOKEN"] == "keep-me"

    def test_backup_is_redacted(self, kc, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBSPEED_API_KEY", KEY)
        existing = {"mcpServers": {"web-speed-agent": {
            "command": "python3", "env": {"WEBSPEED_API_KEY": OTHER}}}}
        _run_configure(tmp_path, monkeypatch, [], existing=existing)
        backups = list(tmp_path.glob("claude_desktop_config.json.bak-*"))
        assert len(backups) == 1
        b = backups[0].read_text()
        assert OTHER not in b and "wsp_REDACTED" in b
        assert oct(backups[0].stat().st_mode & 0o777) == "0o600"

    def test_uses_stored_key_without_asking(self, kc, tmp_path, monkeypatch):
        keystore.set(KEY)
        code, cfg = _run_configure(tmp_path, monkeypatch, [])
        assert code == 0 and "wsp_" not in cfg.read_text()

    def test_rejected_key_changes_nothing(self, kc, tmp_path, monkeypatch):
        monkeypatch.setenv("WEBSPEED_API_KEY", KEY)
        code, cfg = _run_configure(tmp_path, monkeypatch, [], verified=False)
        assert code == 1
        assert not cfg.exists()
        assert keystore.get() is None

    def test_no_key_anywhere_fails_cleanly(self, kc, tmp_path, monkeypatch):
        code, cfg = _run_configure(tmp_path, monkeypatch, [])
        assert code == 2 and not cfg.exists()

    def test_no_keychain_falls_back_to_plaintext(self, no_kc, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("WEBSPEED_API_KEY", KEY)
        code, cfg = _run_configure(tmp_path, monkeypatch, [])
        assert code == 0
        assert KEY in cfg.read_text()
        assert "No system keychain" in capsys.readouterr().out

    def test_reports_leftover_plaintext(self, kc, tmp_path, monkeypatch, capsys):
        monkeypatch.setenv("WEBSPEED_API_KEY", KEY)
        (tmp_path / "old_notes.json").write_text(f'{{"k": "{OTHER}"}}')
        _run_configure(tmp_path, monkeypatch, [])
        out = capsys.readouterr().out
        assert "old_notes.json" in out and OTHER not in out


class TestHostCommandsCarryNoKey:
    PY = "/usr/bin/python3"

    def test_claude_code(self):
        cmds = configure.claude_code_commands(None, self.PY, True, True)
        assert not any("wsp_" in a for c in cmds for a in c)
        hosted = cmds[0]
        assert hosted[hosted.index("--") + 1:] == configure.hosted_argv(self.PY)
        assert all(c[3:5] == ["-s", "user"] for c in cmds)

    def test_antigravity(self):
        cmds = configure.antigravity_commands("agy", None, self.PY, True, True)
        assert not any("wsp_" in a for _n, c in cmds for a in c)
        hosted = cmds[0][1]
        assert hosted[hosted.index("--") + 1:] == configure.hosted_argv(self.PY)

    def test_chatgpt_never_prints_the_key(self, capsys):
        configure.print_chatgpt(KEY, self.PY, True, True)
        configure.print_chatgpt(None, self.PY, True, True)
        assert KEY not in capsys.readouterr().out


# ── webspeed-key ─────────────────────────────────────────────────────────────

class TestKeyCli:
    def test_set_reads_stdin_not_argv(self, kc, monkeypatch, capsys):
        monkeypatch.setattr(key_cli, "verify", lambda _k, timeout=10.0: True)
        monkeypatch.setattr(key_cli, "known_config_files", lambda: [])
        monkeypatch.setattr(sys, "stdin", io.StringIO(KEY + "\n"))
        assert key_cli.main(["set"]) == 0
        assert keystore.get() == KEY
        assert KEY not in capsys.readouterr().out

    def test_rejected_key_is_not_saved(self, kc, monkeypatch):
        monkeypatch.setattr(key_cli, "verify", lambda _k, timeout=10.0: False)
        monkeypatch.setattr(sys, "stdin", io.StringIO(KEY + "\n"))
        assert key_cli.main(["set"]) == 1
        assert keystore.get() is None

    def test_find_plaintext_reports_where_not_what(self, tmp_path):
        f = tmp_path / "c.json"
        f.write_text(f'{{"a": "{KEY}", "b": "{OTHER}"}}')
        hits = key_cli.find_plaintext_keys([f, tmp_path / "missing.json"])
        assert hits == [(f, 2)]


# ── webspeed-hosted proxy ────────────────────────────────────────────────────

TOOL = {"name": "interpret_page", "description": "Map a page",
        "inputSchema": {"type": "object", "properties": {"url": {"type": "string"}}}}


def _upstream(handler) -> hosted_proxy.Upstream:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return hosted_proxy.Upstream(KEY, "https://api.example.test/mcp", client=client)


async def _list(server):
    res = await server.request_handlers[types.ListToolsRequest](
        types.ListToolsRequest(method="tools/list"))
    return res.root


async def _call(server, name="interpret_page", args=None):
    req = types.CallToolRequest(method="tools/call",
                                params=types.CallToolRequestParams(name=name, arguments=args or {}))
    res = await server.request_handlers[types.CallToolRequest](req)
    return res.root


class TestHostedProxy:
    async def test_relays_tools_and_results_with_the_key_in_a_header(self):
        seen = []

        def handler(req: httpx.Request):
            seen.append(req.headers.get("x-web-speed-key"))
            body = json.loads(req.content)
            if body["method"] == "tools/list":
                return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"],
                                                 "result": {"tools": [TOOL]}})
            return httpx.Response(200, json={"jsonrpc": "2.0", "id": body["id"], "result": {
                "content": [{"type": "text", "text": "mapped " + body["params"]["arguments"]["url"]}],
                "isError": False}})

        server = hosted_proxy.build_server(_upstream(handler))
        tools = await _list(server)
        assert [t.name for t in tools.tools] == ["interpret_page"]
        result = await _call(server, args={"url": "https://x.test"})
        assert result.content[0].text == "mapped https://x.test" and not result.isError
        assert seen == [KEY, KEY]

    async def test_401_says_the_key_was_rejected_instead_of_hanging(self):
        server = hosted_proxy.build_server(_upstream(
            lambda req: httpx.Response(401, json={"error": True, "message": "Unknown API key."})))
        await _list_cache_prime(server)
        result = await _call(server)
        assert result.isError and "webspeed-key set" in result.content[0].text
        assert KEY not in result.content[0].text

    async def test_server_explanations_pass_through(self):
        server = hosted_proxy.build_server(_upstream(
            lambda req: httpx.Response(402, json={"error": True,
                                                  "message": "Out of credits. Top up at /account"})))
        await _list_cache_prime(server)
        result = await _call(server)
        assert result.isError and result.content[0].text == "Out of credits. Top up at /account"

    async def test_sse_reply_is_understood(self):
        def handler(req):
            rid = json.loads(req.content)["id"]
            msg = json.dumps({"jsonrpc": "2.0", "id": rid, "result": {"tools": [TOOL]}})
            return httpx.Response(200, text=f"event: message\ndata: {msg}\n\n",
                                  headers={"content-type": "text/event-stream"})
        tools = await _list(hosted_proxy.build_server(_upstream(handler)))
        assert tools.tools[0].name == "interpret_page"

    async def test_without_a_key_it_explains_the_fix(self):
        server = hosted_proxy.build_server(None)
        tools = await _list(server)
        assert [t.name for t in tools.tools] == ["web_speed_setup_required"]
        result = await _call(server, name="web_speed_setup_required")
        assert result.isError and "webspeed-key set" in result.content[0].text

    def test_refuses_plain_http_except_to_this_machine(self, monkeypatch):
        monkeypatch.setenv("WEBSPEED_MCP_URL", "http://api.getwebspeed.io/mcp")
        with pytest.raises(hosted_proxy.UpstreamError):
            hosted_proxy.upstream_url()
        monkeypatch.setenv("WEBSPEED_MCP_URL", "http://localhost:8000/mcp")
        assert hosted_proxy.upstream_url() == "http://localhost:8000/mcp"


async def _list_cache_prime(server):
    """The low-level server looks a tool up in its cache before calling it; give
    the cache an entry so the call path under test is the one that runs."""
    server._tool_cache["interpret_page"] = types.Tool(**TOOL)


class TestStandaloneScript:
    """`curl .../configure.py | python3 -` runs this file with no package around
    it — the documented path for machines that cannot install anything."""

    def test_runs_without_the_package(self, tmp_path):
        import subprocess
        from pathlib import Path
        src = Path(configure.__file__).read_text()
        cfg = tmp_path / "claude_desktop_config.json"
        env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path), "WEBSPEED_API_KEY": KEY}
        r = subprocess.run([sys.executable, "-", "--hosted-only", "--config-path", str(cfg)],
                           input=src, capture_output=True, text=True, env=env,
                           cwd=tmp_path, timeout=60)
        assert r.returncode == 0, r.stdout + r.stderr
        assert "standalone script" in r.stdout
        assert KEY in cfg.read_text()        # nowhere else to put it
