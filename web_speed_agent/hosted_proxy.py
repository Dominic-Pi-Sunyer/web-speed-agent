"""webspeed-hosted — the hosted Web Speed tools, with the API key from the keychain.

MCP hosts launch this as a local stdio server, and it relays each request to
https://api.getwebspeed.io/mcp. It replaces the old client entry:

    npx -y mcp-remote@latest https://api.getwebspeed.io/mcp/sse \\
        --header "X-Web-Speed-Key: wsp_..."

which had two problems. The key sat in plaintext in the client's config file.
And every launch of the AI app fetched and ran the newest `mcp-remote` from npm,
unpinned, with that key in its arguments — a supply-chain exposure on every
start. This proxy is part of the pip package you already installed, and reads the
key from the OS keychain (see keystore.py).

Why raw JSON-RPC over httpx instead of the MCP SDK's HTTP client: the hosted
endpoint is stateless and answers with plain JSON, so each call is exactly one
POST — there is no session to keep alive or lose. And the SDK client (1.x)
routes an HTTP error into its read stream rather than failing the pending
request, so a 401 — precisely what you get right after rotating your key — would
hang the call until a timeout instead of saying "your key was rejected".
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx
import mcp.types as types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server
from mcp.shared.exceptions import McpError

from . import keystore

DEFAULT_BASE = "https://api.getwebspeed.io"
# Generous: a JS-rendered page fetch on the server can legitimately take a while,
# and a proxy that gives up before the server does just wastes the credit.
CALL_TIMEOUT_S = 180.0

KEY_REJECTED = (
    "Web Speed rejected the API key stored on this machine. If you rotated it, "
    "run `webspeed-key set` and paste the new key, then restart your AI app. "
    "Your keys: https://api.getwebspeed.io/account"
)

# Shown in place of the real tools when no key is configured, so the model can
# tell the user what to do instead of the server silently exposing nothing.
SETUP_TOOL = types.Tool(
    name="web_speed_setup_required",
    description=("Web Speed is installed but has no API key on this machine. "
                 "Call this to get the one-line fix to give the user."),
    inputSchema={"type": "object", "properties": {}},
)


class UpstreamError(Exception):
    """A failure to show the user verbatim (no stack trace, no key)."""


def upstream_url() -> str:
    """The hosted MCP endpoint. HTTPS only, except plain HTTP to this machine
    (for local development) — the key must never cross a network in the clear."""
    url = (os.environ.get("WEBSPEED_MCP_URL")
           or (os.environ.get("WEBSPEED_SERVER_URL") or DEFAULT_BASE).rstrip("/") + "/mcp")
    parts = urlsplit(url)
    local = parts.hostname in ("localhost", "127.0.0.1", "::1")
    if parts.scheme != "https" and not (parts.scheme == "http" and local):
        raise UpstreamError(f"Refusing non-HTTPS Web Speed URL: {url}")
    return url


def _parse_body(resp: httpx.Response, want_id: int) -> dict[str, Any]:
    """The JSON-RPC response object, from a JSON body or an SSE stream.

    The server currently answers in JSON; SSE is handled too so a server-side
    change of response mode does not silently break every installed proxy.
    """
    ctype = resp.headers.get("content-type", "")
    if "text/event-stream" in ctype:
        for line in resp.text.splitlines():
            if not line.startswith("data:"):
                continue
            try:
                msg = json.loads(line[5:].strip())
            except ValueError:
                continue
            if isinstance(msg, dict) and msg.get("id") == want_id:
                return msg
        raise UpstreamError("Web Speed sent a stream with no reply to this request.")
    try:
        msg = resp.json()
    except ValueError:
        raise UpstreamError(f"Web Speed sent an unreadable reply (HTTP {resp.status_code}).") from None
    if not isinstance(msg, dict):
        raise UpstreamError("Web Speed sent an unexpected reply.")
    return msg


class Upstream:
    def __init__(self, key: str, url: str, client: httpx.AsyncClient | None = None):
        self._key = key
        self._url = url
        # One pooled client for the life of the process, so calls after the first
        # reuse the TLS connection instead of handshaking every time.
        self._client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(CALL_TIMEOUT_S, connect=15.0))
        self._ids = itertools.count(1)

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        rid = next(self._ids)
        headers = {
            "x-web-speed-key": self._key,
            "content-type": "application/json",
            "accept": "application/json, text/event-stream",
            "mcp-protocol-version": types.LATEST_PROTOCOL_VERSION,
        }
        body = {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}}
        try:
            resp = await self._client.post(self._url, json=body, headers=headers)
        except httpx.TimeoutException:
            raise UpstreamError("Web Speed did not respond in time. Try again.") from None
        except httpx.HTTPError as exc:
            raise UpstreamError(
                f"Could not reach Web Speed ({type(exc).__name__}). "
                "Check your internet connection.") from None

        if resp.status_code == 401:
            raise UpstreamError(KEY_REJECTED)
        if resp.status_code >= 400:
            # The server explains 402/403/429 itself (out of credits, suspended,
            # rate limited) — pass its words through rather than paraphrasing.
            detail = ""
            try:
                j = resp.json()
                detail = j.get("message") or j.get("detail") or ""
            except ValueError:
                pass
            raise UpstreamError(detail or f"Web Speed returned HTTP {resp.status_code}.")

        msg = _parse_body(resp, rid)
        if "error" in msg:
            err = msg["error"] or {}
            raise McpError(types.ErrorData(code=int(err.get("code", -32603)),
                                           message=str(err.get("message", "Upstream error")),
                                           data=err.get("data")))
        result = msg.get("result")
        if not isinstance(result, dict):
            raise UpstreamError("Web Speed sent a reply with no result.")
        return result

    async def aclose(self) -> None:
        await self._client.aclose()


def _error(text: str) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                isError=True)


def build_server(upstream: Upstream | None, *, version: str = "") -> Server:
    """The stdio-facing server. `upstream` is None when no key is configured."""
    server: Server = Server("webspeed", version=version or None,
                            instructions=None if upstream else keystore.MISSING_HELP)

    @server.list_tools()
    async def _list_tools() -> list[types.Tool]:
        if upstream is None:
            return [SETUP_TOOL]
        tools: list[types.Tool] = []
        cursor: str | None = None
        while True:
            try:
                res = await upstream.request("tools/list", {"cursor": cursor} if cursor else {})
            except UpstreamError as exc:
                # tools/list cannot carry an isError result, so surface the reason
                # as a protocol error the host will log and usually display.
                raise McpError(types.ErrorData(code=-32000, message=str(exc))) from None
            page = types.ListToolsResult.model_validate(res)
            tools.extend(page.tools)
            cursor = page.nextCursor
            if not cursor:
                return tools

    # validate_input=False: the server validates arguments authoritatively, and
    # validating twice would only let the two copies of the schema disagree.
    @server.call_tool(validate_input=False)
    async def _call_tool(name: str, arguments: dict[str, Any]) -> types.CallToolResult:
        if upstream is None:
            return _error(keystore.MISSING_HELP)
        try:
            res = await upstream.request("tools/call", {"name": name, "arguments": arguments or {}})
        except UpstreamError as exc:
            return _error(str(exc))
        return types.CallToolResult.model_validate(res)

    return server


async def _amain() -> None:
    from . import __version__

    key, source = keystore.resolve()
    if not key:
        print(f"[webspeed-hosted] {keystore.MISSING_HELP}", file=sys.stderr)
    try:
        url = upstream_url()
    except UpstreamError as exc:
        print(f"[webspeed-hosted] {exc}", file=sys.stderr)
        raise SystemExit(2) from None

    upstream = Upstream(key, url) if key else None
    if key:
        # Logged so a support thread can confirm WHICH key is in use without
        # anyone pasting it: only the last four characters.
        print(f"[webspeed-hosted] key {keystore.mask(key)} from {source} -> {url}",
              file=sys.stderr)
    server = build_server(upstream, version=__version__)
    try:
        async with stdio_server() as (read, write):
            await server.run(read, write, server.create_initialization_options())
    finally:
        if upstream is not None:
            await upstream.aclose()


def main() -> None:
    """Console-script entry point (webspeed-hosted)."""
    import anyio
    anyio.run(_amain)


if __name__ == "__main__":
    main()
