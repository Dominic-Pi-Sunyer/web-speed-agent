"""Tests for the Agent class."""

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from web_speed_agent import Agent
from web_speed_agent.exceptions import AuthenticationError, InsufficientCreditsError, RateLimitError


class TestAgentInit:
    def test_no_key_still_creates(self):
        agent = Agent()
        assert agent._api_key is None
        assert agent._client is None

    def test_key_from_arg(self):
        agent = Agent(api_key="sk_test_123")
        assert agent._api_key == "sk_test_123"
        assert agent._client is not None

    def test_key_from_env(self, monkeypatch):
        monkeypatch.setenv("WEBSPEED_API_KEY", "sk_env_456")
        agent = Agent()
        assert agent._api_key == "sk_env_456"

    def test_no_key_raises_on_extract(self):
        agent = Agent()
        with pytest.raises(AuthenticationError):
            import asyncio
            asyncio.get_event_loop().run_until_complete(agent.extract("<html></html>"))


class TestExtract:
    @pytest.fixture
    def agent(self):
        return Agent(api_key="sk_test_abc")

    @pytest.mark.asyncio
    async def test_extract_calls_api(self, agent):
        mock_response = {
            "page_type": "article",
            "engine": "advanced",
            "article": {"sections": [], "intro": "", "links": []},
        }

        with patch.object(agent._client, "_client") as mock_http:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_resp.json.return_value = mock_response
            mock_http.post = AsyncMock(return_value=mock_resp)

            result = await agent.extract("<html><body>test</body></html>", page_type="article")

        assert result["engine"] == "advanced"
        assert result["page_type"] == "article"

    @pytest.mark.asyncio
    async def test_extract_raises_on_401(self, agent):
        with patch.object(agent._client, "_client") as mock_http:
            mock_resp = MagicMock()
            mock_resp.status_code = 401
            mock_resp.json.return_value = {"message": "Invalid key"}
            mock_http.post = AsyncMock(return_value=mock_resp)

            with pytest.raises(AuthenticationError):
                await agent.extract("<html></html>")

    @pytest.mark.asyncio
    async def test_extract_raises_on_429(self, agent):
        with patch.object(agent._client, "_client") as mock_http:
            mock_resp = MagicMock()
            mock_resp.status_code = 429
            mock_resp.json.return_value = {"message": "Rate limited"}
            mock_http.post = AsyncMock(return_value=mock_resp)

            with pytest.raises(RateLimitError):
                await agent.extract("<html></html>")


class TestCredentials:
    def test_store_and_get(self, tmp_path):
        agent = Agent(config_dir=str(tmp_path))

        with patch("web_speed_agent.credentials.keyring") as mock_kr:
            mock_kr.get_password.return_value = None

            agent.store_credential("test_site", "user@example.com", "hunter2")
            mock_kr.set_password.assert_called()

    def test_get_missing_returns_none(self, tmp_path):
        agent = Agent(config_dir=str(tmp_path))

        with patch("web_speed_agent.credentials.keyring") as mock_kr:
            mock_kr.get_password.return_value = None

            result = agent.get_credential("nonexistent_site")
            assert result is None


class TestBrowser:
    def test_browser_returns_managed_browser(self, tmp_path):
        agent = Agent(config_dir=str(tmp_path))
        ctx = agent.browser(session_name="test", headless=True)
        from web_speed_agent.browser import ManagedBrowser
        assert isinstance(ctx, ManagedBrowser)


class TestConfigureHosts:
    """Launch-command consistency across MCP hosts.

    The bug these guard against is silent: every host gets its own builder, so
    one can drift to a different launch form and only that host breaks — with no
    error here and no obvious link back to the configurator.
    """

    def test_agent_argv_module_form(self):
        from web_speed_agent.configure import agent_argv
        argv = agent_argv("/usr/bin/python3", None, use_module=True)
        assert argv == ["/usr/bin/python3", "-m", "web_speed_agent.mcp_server"]

    def test_agent_argv_shim_form(self):
        from pathlib import Path
        from web_speed_agent.configure import agent_argv
        argv = agent_argv("/usr/bin/python3", Path("/x/agent_mcp_server.py"),
                          use_module=False)
        assert argv == ["/usr/bin/python3", "/x/agent_mcp_server.py"]

    def test_every_host_launches_the_agent_identically(self):
        from web_speed_agent.configure import (agent_argv, agent_entry,
                                               antigravity_commands,
                                               claude_code_commands)
        py, key = "/usr/bin/python3", "wsp_test"
        for use_module in (True, False):
            path = None if use_module else __import__("pathlib").Path("/x/a.py")
            want = agent_argv(py, path, use_module)

            entry = agent_entry(key, path, py, use_module=use_module)
            assert [entry["command"], *entry["args"]] == want

            cc = claude_code_commands(key, py, False, True, path, use_module)[0]
            assert cc[cc.index("--") + 1:] == want

            ag = antigravity_commands("agy", key, py, False, True, path,
                                      use_module)[0][1]
            assert ag[ag.index("--") + 1:] == want

    def test_module_importable_rejects_a_bogus_interpreter(self):
        from web_speed_agent.configure import module_importable
        assert module_importable("/nonexistent/python") is False
