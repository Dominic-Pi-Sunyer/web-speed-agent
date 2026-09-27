"""Read-only enforcement, settle timing, and the pointer/keyboard input tools.

The read-only tests are the ones that matter most. A safety control that is
merely *believed* to work is worse than none, because the user browses a logged-in
session thinking writes are impossible. So these assert against what the SERVER
received, not against what the tool returned: the only proof that a write was
blocked is that nothing arrived.

Run:  python3.11 -m pytest tests/test_input_safety.py -q --asyncio-mode=auto
"""

from __future__ import annotations

import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

pytest.importorskip("playwright", reason="Playwright is required for browser tests")

from playwright.async_api import async_playwright  # noqa: E402

from web_speed_agent import mcp_server as srv  # noqa: E402
from web_speed_agent import safety  # noqa: E402


# ── a tiny origin that records what actually reached it ──────────────────────

class _Recorder(BaseHTTPRequestHandler):
    received: list[str] = []

    def _reply(self, body: bytes = b"ok") -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):          # noqa: N802
        _Recorder.received.append("GET " + self.path)
        self._reply(b"<!doctype html><html><body><p>page</p></body></html>")

    def do_POST(self):         # noqa: N802
        _Recorder.received.append("POST " + self.path)
        self._reply()

    def log_message(self, *a):  # silence
        return


@pytest.fixture
def origin():
    _Recorder.received = []
    srv_http = ThreadingHTTPServer(("127.0.0.1", 0), _Recorder)
    t = threading.Thread(target=srv_http.serve_forever, daemon=True)
    t.start()
    try:
        yield f"http://127.0.0.1:{srv_http.server_address[1]}"
    finally:
        srv_http.shutdown()


# ── page fixtures ────────────────────────────────────────────────────────────

INPUTS = """<!doctype html><html><body style="height:4000px">
<div id="menu-root"><button id="trigger">Menu</button>
  <ul id="menu" style="display:none"><li id="item">Item</li></ul></div>
<select id="sel">
  <option value="a">Apple</option>
  <option value="b">Banana</option>
  <option value="c">Cherry</option>
</select>
<a id="to-b" href="b.html">go to B</a>
<script>
  document.getElementById('trigger').addEventListener('mouseenter', function(){
    document.getElementById('menu').style.display = 'block';
  });
  window.selected = null;
  document.getElementById('sel').addEventListener('change', function(e){
    window.selected = e.target.value;
  });
</script></body></html>"""

PAGE_B = "<!doctype html><html><body><h1 id='b'>Page B</h1></body></html>"

# Never stops mutating — stands in for Google Calendar, which never reaches
# networkidle because of its persistent connections.
RESTLESS = """<!doctype html><html><body><div id="t"></div>
<script>setInterval(function(){
  document.getElementById('t').textContent = String(Date.now());
}, 50);</script></body></html>"""


@pytest.fixture
async def page(tmp_path):
    (tmp_path / "a.html").write_text(INPUTS, encoding="utf-8")
    (tmp_path / "b.html").write_text(PAGE_B, encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        await pg.goto((tmp_path / "a.html").as_uri())
        prev = srv._page
        srv._page = pg
        try:
            yield pg
        finally:
            srv._page = prev
            await browser.close()


def _res(raw: str) -> dict:
    return json.loads(raw)


# ── read-only: the write must not reach the server ───────────────────────────

@pytest.mark.asyncio
async def test_readonly_blocks_post_from_reaching_the_server(origin, monkeypatch):
    monkeypatch.setattr(safety, "READONLY", True)
    srv._blocked_writes.clear()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await srv._install_readonly_guard(ctx)
        pg = await ctx.new_page()
        await pg.goto(origin + "/start")
        status = await pg.evaluate(
            """async (u) => {
                 try { const r = await fetch(u, {method:'POST', body:'x'}); return r.status; }
                 catch (e) { return 'blocked'; }
               }""", origin + "/write")
        await browser.close()

    assert status == "blocked", "the POST was not aborted in the browser"
    assert not any(r.startswith("POST") for r in _Recorder.received), (
        f"a write REACHED the server: {_Recorder.received}")
    assert any("POST" in b for b in srv._blocked_writes), srv._blocked_writes


@pytest.mark.asyncio
async def test_readonly_still_allows_reads(origin, monkeypatch):
    monkeypatch.setattr(safety, "READONLY", True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await srv._install_readonly_guard(ctx)
        pg = await ctx.new_page()
        await pg.goto(origin + "/read")
        text = await pg.inner_text("body")
        await browser.close()
    assert "page" in text
    assert any(r.startswith("GET") for r in _Recorder.received)


@pytest.mark.asyncio
async def test_guard_is_not_installed_when_mode_is_off(origin, monkeypatch):
    """The default must stay fully functional — read-only is opt-in."""
    monkeypatch.setattr(safety, "READONLY", False)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await srv._install_readonly_guard(ctx)          # no-op
        pg = await ctx.new_page()
        await pg.goto(origin + "/start")
        status = await pg.evaluate(
            """async (u) => { const r = await fetch(u, {method:'POST', body:'x'}); return r.status; }""",
            origin + "/write")
        await browser.close()
    assert status == 200
    assert any(r.startswith("POST") for r in _Recorder.received)


@pytest.mark.asyncio
async def test_write_tools_refuse_with_a_clear_reason(page, monkeypatch):
    monkeypatch.setattr(safety, "READONLY", True)
    for raw in (await srv.submit_form(), await srv.workspace_new_slide(),
                await srv.login(site="x")):
        out = _res(raw)
        assert out["ok"] is False
        assert "read-only" in out["error"].lower(), out["error"]
        assert "WEBSPEED_READONLY" in out["error"], "must say how to turn it off"


@pytest.mark.asyncio
async def test_write_tools_work_normally_when_mode_is_off(page, monkeypatch):
    monkeypatch.setattr(safety, "READONLY", False)
    out = _res(await srv.submit_form())
    # It may fail for page reasons, but never with the read-only refusal.
    assert "read-only" not in json.dumps(out).lower()


# ── settle timing: the Google Calendar case ──────────────────────────────────

@pytest.mark.asyncio
async def test_settle_is_fast_when_the_network_never_idles(origin):
    """The Google Calendar case: ~6.4s per navigation, 13 times a session.

    A site holding a socket can never reach networkidle, so the old code paid its
    full 6s timeout every time. The DOM going quiet is the signal that does
    arrive, so the race returns on that instead.
    """
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        await pg.set_content(
            "<div id=c>cal</div><script>setInterval(function(){"
            f"fetch('{origin}/ping').catch(function(){{}});" "},150);</script>")
        start = time.monotonic()
        await srv._settle(pg)
        elapsed = time.monotonic() - start
        await browser.close()
    assert elapsed < 2.0, f"settle took {elapsed:.2f}s — networkidle is dominating again"


@pytest.mark.asyncio
async def test_settle_is_fast_when_the_dom_never_quiets(tmp_path):
    """The mirror case, and a real regression caught during this change.

    Swapping networkidle for a DOM-quiet watcher made THIS slower: a ticking
    clock or a carousel never goes quiet, so it paid the full cap even though the
    network had been idle since load. Only racing both signals is fast for both
    shapes of page.
    """
    f = tmp_path / "restless.html"
    f.write_text(RESTLESS, encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        await pg.goto(f.as_uri())
        start = time.monotonic()
        await srv._settle(pg)
        elapsed = time.monotonic() - start
        await browser.close()
    assert elapsed < 1.5, (
        f"settle took {elapsed:.2f}s on a page whose network was already idle — "
        f"the DOM watcher is being waited on alone again")


@pytest.mark.asyncio
async def test_settle_returns_quickly_on_a_calm_page(page):
    start = time.monotonic()
    await srv._settle(page)
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, f"settle took {elapsed:.2f}s on a static page"


# ── the new input tools ──────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_hover_opens_a_hover_only_menu(page):
    assert await page.evaluate("getComputedStyle(document.getElementById('menu')).display") == "none"
    out = _res(await srv.hover("#trigger", wait_for="#item"))
    assert out["ok"] is True, out
    assert "warnings" not in out, out.get("warnings")
    assert await page.evaluate("getComputedStyle(document.getElementById('menu')).display") == "block"


@pytest.mark.asyncio
async def test_scroll_to_bottom_and_report_position(page):
    out = _res(await srv.scroll(to="bottom"))
    assert out["ok"] is True
    assert out["scroll"]["atBottom"] is True, out["scroll"]
    back = _res(await srv.scroll(to="top"))
    assert back["scroll"]["y"] == 0


@pytest.mark.asyncio
async def test_scroll_by_pixels(page):
    _res(await srv.scroll(to="top"))
    out = _res(await srv.scroll(to="down", amount_px=500))
    assert out["ok"] is True
    assert out["scroll"]["y"] > 0


@pytest.mark.asyncio
async def test_scroll_element_into_view(page):
    out = _res(await srv.scroll(selector="#to-b"))
    assert out["ok"] is True
    assert "#to-b" in out["message"]


@pytest.mark.asyncio
async def test_select_option_by_value_label_and_index(page):
    out = _res(await srv.select_option("#sel", value="b"))
    assert out["ok"] is True and out["selected"] == ["b"], out
    assert await page.evaluate("window.selected") == "b", "no change event fired"

    assert _res(await srv.select_option("#sel", label="Cherry"))["selected"] == ["c"]
    assert _res(await srv.select_option("#sel", index=0))["selected"] == ["a"]


@pytest.mark.asyncio
async def test_select_option_requires_exactly_one_criterion(page):
    assert _res(await srv.select_option("#sel"))["ok"] is False
    assert _res(await srv.select_option("#sel", value="a", index=1))["ok"] is False


@pytest.mark.asyncio
async def test_select_option_reports_a_miss_instead_of_claiming_success(page):
    start = time.monotonic()
    out = _res(await srv.select_option("#sel", value="nope"))
    elapsed = time.monotonic() - start
    assert out["ok"] is False
    assert "no option with" in out["error"].lower(), out["error"]
    # The error must name the real choices, and must not cost a Playwright
    # timeout to produce — a caller that waits 8s learns nothing it can use.
    assert "Banana" in out["error"], out["error"]
    assert elapsed < 2.0, f"took {elapsed:.2f}s — should fail immediately"


@pytest.mark.asyncio
async def test_go_back_returns_to_the_previous_page(page):
    await srv.click("#to-b")
    assert await page.evaluate("!!document.getElementById('b')") is True
    out = _res(await srv.go_back())
    assert out["ok"] is True, out
    assert await page.evaluate("!!document.getElementById('trigger')") is True


@pytest.mark.asyncio
async def test_go_back_with_no_history_is_an_error_not_a_lie(page):
    out = _res(await srv.go_back())
    assert out["ok"] is False
    assert "nothing to go back to" in out["error"].lower(), out["error"]


@pytest.mark.asyncio
async def test_a_plain_click_pays_nothing_for_safety(page, monkeypatch):
    """With every control off, a click must not do extra work.

    An earlier version read the element's label on every click to feed the
    risky-word check. That is only needed when confirmation or auditing is on —
    unconditionally it added a DOM round-trip to every click, and a full 1.5s
    timeout to every click whose selector did not match.
    """
    from web_speed_agent import safety
    monkeypatch.setattr(safety, "CONFIRM", "off")
    monkeypatch.setattr(safety, "AUDIT_PATH", None)

    calls = []
    real = srv._element_text

    async def spy(pg, sel):
        calls.append(sel)
        return await real(pg, sel)

    monkeypatch.setattr(srv, "_element_text", spy)
    out = _res(await srv.click("#trigger"))
    assert out["ok"] is True
    assert calls == [], "read the element label with all safety disabled"

    # ...but it must still be read when the risky-word check needs it.
    monkeypatch.setattr(safety, "CONFIRM", "writes")
    await srv.click("#trigger")
    assert calls, "label not read when confirmation is on"


@pytest.mark.asyncio
async def test_a_blocked_write_is_reported_back_to_the_caller(origin, monkeypatch):
    """The docs promise blocked requests come back in the result. Make that true.

    Without this the agent sees a click that "worked" and a page that did not
    change, which is exactly the confusion read-only is supposed to remove.
    """
    monkeypatch.setattr(safety, "READONLY", True)
    srv._blocked_writes.clear()
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        ctx = await browser.new_context()
        await srv._install_readonly_guard(ctx)
        pg = await ctx.new_page()
        await pg.goto(origin + "/form")
        await pg.set_content(
            f"<button id=go onclick=\"fetch('{origin}/write',{{method:'POST'}})\">Go</button>")
        prev, srv._page = srv._page, pg
        try:
            out = _res(await srv.click("#go", wait_until="dom_settled"))
        finally:
            srv._page = prev
            await browser.close()

    assert out["ok"] is True
    assert out.get("read_only_blocked"), "the blocked write was not reported"
    assert any("POST" in b for b in out["read_only_blocked"])
