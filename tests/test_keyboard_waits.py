"""Live-browser tests for press_keys and the condition-based waits.

These drive a real Chromium against a local HTML file. Unlike test_login_live.py
they need no credentials, no account, and no network — just a browser — so they
are hermetic and safe to run anywhere Chromium is installed.

The fixture page reproduces the two shapes that motivated these tools:

  1. A window-level `keydown` listener, which is how word games, canvas editors
     and terminal emulators read input. `fill_field` cannot reach one: there is
     no <input> to write into, and setting a value fires no key events.
  2. A page that mutates for a while and then stops, plus one that never stops —
     the difference between "wait for the DOM to go quiet" working and hanging.

Run:  python3.11 -m pytest tests/test_keyboard_waits.py -q
"""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright", reason="Playwright is required for browser tests")

from playwright.async_api import async_playwright  # noqa: E402

from web_speed_agent import mcp_server as srv  # noqa: E402

# Mutates every 100 ms, six times, then stops (~600 ms of churn).
# __READY flips at 500 ms. Keydown is bound on window, not on an input.
PAGE = """<!doctype html>
<html><body>
<div id="tiles"></div><div id="log"></div>
<button id="go">Go</button>
<script>
  window.typed = ""; window.submitted = false;
  window.addEventListener('keydown', function (e) {
    if (e.key === 'Enter') {
      window.submitted = true;
      document.getElementById('log').textContent = 'SUBMITTED:' + window.typed;
    } else if (e.key === 'Backspace') {
      window.typed = window.typed.slice(0, -1);
    } else if (e.key.length === 1) {
      window.typed += e.key;
    }
    document.getElementById('tiles').textContent = window.typed;
  });
  var n = 0;
  var iv = setInterval(function () {
    var d = document.createElement('div');
    d.textContent = 'row' + (n++);
    document.body.appendChild(d);
    if (n >= 6) clearInterval(iv);
  }, 100);
  setTimeout(function () { window.__READY = true; }, 500);
</script>
</body></html>
"""

# Never goes quiet — the case the hard cap exists for.
FOREVER = """<!doctype html>
<html><body><div id="x"></div>
<script>
  setInterval(function () {
    document.getElementById('x').textContent = String(Date.now());
  }, 50);
</script>
</body></html>
"""


def _res(raw: str) -> dict:
    """Tools return a JSON string; decode it."""
    return json.loads(raw)


@pytest.fixture
async def page(tmp_path):
    """A real page, wired into the module global the tools read."""
    f = tmp_path / "fixture.html"
    f.write_text(PAGE, encoding="utf-8")

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        await pg.goto(f.as_uri())
        prev = srv._page
        srv._page = pg          # the tools resolve the page via _require_page()
        try:
            yield pg
        finally:
            srv._page = prev
            await browser.close()


# ── press_keys ───────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_types_and_submits_via_window_listener(page):
    """The motivating case: type a word and press Enter, with no input element."""
    out = _res(await srv.press_keys(text="crane", keys=["Enter"]))
    assert out["ok"] is True, out
    assert await page.evaluate("window.typed") == "crane"
    assert await page.evaluate("window.submitted") is True
    assert await page.evaluate("document.getElementById('log').textContent") == "SUBMITTED:crane"


@pytest.mark.asyncio
async def test_text_is_typed_before_keys(page):
    """Ordering matters: keys must land after text, or Enter submits an empty word."""
    await srv.press_keys(text="slate", keys=["Enter"])
    assert await page.evaluate("document.getElementById('log').textContent") == "SUBMITTED:slate"


@pytest.mark.asyncio
async def test_repeat_presses_key_sequence(page):
    await srv.press_keys(text="abcde")
    out = _res(await srv.press_keys(keys=["Backspace"], repeat=2))
    assert out["ok"] is True
    assert await page.evaluate("window.typed") == "abc"


@pytest.mark.asyncio
async def test_focus_selector_then_type(page):
    out = _res(await srv.press_keys(text="x", selector="#go"))
    assert out["ok"] is True
    assert await page.evaluate("document.activeElement.id") == "go"


@pytest.mark.asyncio
async def test_requires_text_or_keys(page):
    out = _res(await srv.press_keys())
    assert out["ok"] is False
    assert "text" in out["error"] and "keys" in out["error"]


@pytest.mark.asyncio
async def test_bad_key_name_names_the_key(page):
    """A typo'd key is the likeliest failure — the error must say which one."""
    out = _res(await srv.press_keys(keys=["Enter", "NotARealKey"]))
    assert out["ok"] is False
    assert "NotARealKey" in out["error"]


@pytest.mark.asyncio
async def test_bad_wait_until_lists_valid_choices(page):
    out = _res(await srv.press_keys(text="a", wait_until="whenever"))
    assert out["ok"] is False
    assert "dom_settled" in out["error"]


@pytest.mark.asyncio
async def test_missing_page_is_a_clean_error():
    prev = srv._page
    srv._page = None
    try:
        with pytest.raises(RuntimeError, match="No browser open"):
            await srv.press_keys(text="a")
    finally:
        srv._page = prev


# ── condition waits ──────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_dom_settled_returns_after_churn_stops(page):
    """Returns when the page goes quiet (~600 ms), not at the 5 s cap."""
    start = time.monotonic()
    settled = await srv._wait_dom_settled(page, quiet_ms=300, timeout_ms=5_000)
    elapsed = time.monotonic() - start
    assert settled is True
    assert elapsed < 3.0, f"took {elapsed:.2f}s — should track the page, not the cap"


@pytest.mark.asyncio
async def test_dom_settled_gives_up_on_a_page_that_never_stops(tmp_path):
    """The important edge case: a ticking page must hit the cap, never hang."""
    f = tmp_path / "forever.html"
    f.write_text(FOREVER, encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        await pg.goto(f.as_uri())
        start = time.monotonic()
        settled = await srv._wait_dom_settled(pg, quiet_ms=300, timeout_ms=1_000)
        elapsed = time.monotonic() - start
        await browser.close()
    assert settled is False
    assert elapsed < 4.0, f"took {elapsed:.2f}s — the hard cap did not fire"


@pytest.mark.asyncio
async def test_wait_for_predicate_succeeds_when_condition_flips(page):
    out = _res(await srv.wait_for_predicate("window.__READY === true", timeout_ms=5_000))
    assert out["ok"] is True


@pytest.mark.asyncio
async def test_wait_for_predicate_reports_timeout(page):
    out = _res(await srv.wait_for_predicate("window.__NEVER === true", timeout_ms=600))
    assert out["ok"] is False
    assert "truthy" in out["error"]


# ── inline waits on actions (the round-trip saver) ───────────────────────────

@pytest.mark.asyncio
async def test_click_accepts_inline_predicate(page):
    out = _res(await srv.click("#go", wait_for_predicate="window.__READY === true"))
    assert out["ok"] is True
    assert "warnings" not in out


@pytest.mark.asyncio
async def test_click_surfaces_unmet_wait_as_warning_not_failure(page):
    """The click happened; the modal didn't open. Both facts must survive."""
    out = _res(await srv.click("#go", wait_for=".never-appears"))
    assert out["ok"] is True, "a missed wait must not fail the click itself"
    assert out["warnings"], "a missed wait must be reported, not swallowed"
    assert ".never-appears" in out["warnings"][0]


@pytest.mark.asyncio
async def test_click_rejects_bad_wait_until(page):
    out = _res(await srv.click("#go", wait_until="soon"))
    assert out["ok"] is False
    assert "dom_settled" in out["error"]


@pytest.mark.asyncio
async def test_press_keys_inline_wait_until_dom_settled(page):
    out = _res(await srv.press_keys(text="ab", wait_until="dom_settled"))
    assert out["ok"] is True
    assert await page.evaluate("window.typed") == "ab"


@pytest.mark.asyncio
async def test_unsettled_dom_is_reported_as_a_warning(tmp_path):
    """Regression: _wait_dom_settled once resolved true even when it hit its cap,
    so a page that never stopped moving was reported as ready. The warning is the
    only signal the caller gets, so it has to survive."""
    f = tmp_path / "forever.html"
    f.write_text(FOREVER, encoding="utf-8")
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        pg = await browser.new_page()
        await pg.goto(f.as_uri())
        notes = await srv._apply_waits(pg, wait_until="dom_settled", timeout_ms=1_000)
        await browser.close()
    assert any("still changing" in n for n in notes), notes
