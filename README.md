# web-speed-agent

[![PyPI version](https://img.shields.io/pypi/v/web-speed-agent.svg)](https://pypi.org/project/web-speed-agent/)
[![Python](https://img.shields.io/pypi/pyversions/web-speed-agent.svg)](https://pypi.org/project/web-speed-agent/)
[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](https://www.gnu.org/licenses/gpl-3.0)

Local browser automation + [Web Speed API](https://getwebspeed.io) integration for authenticated web extraction.

Point an AI agent at any website — including ones that require login — and get back clean, structured data. Credentials stay on your machine. Only extracted HTML goes to the server.

```bash
pip install web-speed-agent
playwright install chromium
```

---

## How it works

```
Your machine                           Web Speed server
─────────────────────────────────      ──────────────────────────
Playwright browser (local)
  ↓ navigates, logs in, clicks
  ↓ gets page HTML
  ↓ (no passwords sent)
agent.extract(html)         ────────→  Advanced extraction engine
                            ←────────  Structured JSON
```

Credentials never leave your machine. The server only sees HTML.

---

## Quickstart

```python
import asyncio
from web_speed_agent import Agent

async def main():
    agent = Agent(api_key="wsp_...")       # or set WEBSPEED_API_KEY env var

    # Public pages — no browser needed
    result = await agent.map("https://techcrunch.com/some-article/")
    print(result["article"]["sections"])

    # Authenticated pages — browser runs locally
    agent.store_credential("mysite", "me@example.com", "mypassword")

    async with agent.browser(session_name="mysite") as browser:
        page = await browser.new_page()
        await page.goto("https://mysite.com/login")

        username, password = agent.get_credential("mysite")
        await page.fill('[name="email"]', username)
        await page.fill('[name="password"]', password)
        await page.click('button[type="submit"]')
        await page.wait_for_load_state("networkidle")

        # Now on a logged-in page — extract it
        html = await page.content()
        result = await agent.extract(html, page_type="listing")
        print(result["listing"]["items"])

asyncio.run(main())
```

Get an API key at [getwebspeed.io](https://getwebspeed.io).

---

## Installation

**Requirements:** Python 3.10+, a Web Speed API key

```bash
pip install web-speed-agent
playwright install chromium
export WEBSPEED_API_KEY="wsp_..."
```

---

## Core concepts

### Agent

The main class. Manages credentials, browser sessions, and API calls.

```python
from web_speed_agent import Agent

# API key from argument
agent = Agent(api_key="wsp_...")

# API key from environment variable (recommended)
# export WEBSPEED_API_KEY="wsp_..."
agent = Agent()

# Use as async context manager (auto-closes HTTP client)
async with Agent() as agent:
    ...
```

---

### Extracting public pages

No browser needed for pages that don't require login:

```python
# Fetch + extract in one call
result = await agent.map("https://example.com/article")

# With JavaScript rendering (for heavy SPAs)
result = await agent.map("https://example.com/spa", js=True)
```

---

### Extracting authenticated pages

Use a local browser session. The browser runs on your machine:

```python
async with agent.browser(session_name="mysite") as browser:
    page = await browser.new_page()
    await page.goto("https://mysite.com/dashboard")
    html = await page.content()

result = await agent.extract(html)
```

The `session_name` persists cookies to `~/.webspeed/sessions/<name>/` so subsequent runs skip the login step.

---

### Credential management

Credentials are stored in your system keychain (macOS Keychain, Windows Credential Manager, Linux secret-tool). They are **never sent to Web Speed servers**.

```python
# Store once
agent.store_credential("mysite", "me@example.com", "mypassword")

# Retrieve anywhere
username, password = agent.get_credential("mysite")

# Remove
agent.delete_credential("mysite")
```

---

### Extraction output

The server returns page-type-aware structured data:

```python
# Article
result = await agent.extract(html, page_type="article")
# result["page_type"]    → "article"
# result["title"]        → "Article Title"
# result["author"]       → "Jane Smith"
# result["published_date"] → "2026-05-06"
# result["article"]["sections"] → [{"heading": "...", "paragraphs": [...]}]
# result["article"]["links"]    → [{"text": "...", "url": "..."}]

# Product
result = await agent.extract(html, page_type="product")
# result["product"]["name"]         → "Wireless Headphones"
# result["product"]["price"]        → "$99.99"
# result["product"]["availability"] → "In Stock"
# result["product"]["rating"]       → "4.5"
# result["product"]["specs"]        → {"Battery": "30h", ...}

# Listing (search results, category pages)
result = await agent.extract(html, page_type="listing")
# result["listing"]["items"] → [{"title": "...", "url": "...", "price": "..."}]

# Auto-detect (default)
result = await agent.extract(html)
# result["page_type"] → "article" | "product" | "listing" | "other"
```

All results include `engine: "advanced"` — 60–85% more token-efficient than raw HTML.

---

## Examples

### Price monitor

```python
import asyncio
from web_speed_agent import Agent

async def check_price(url: str, site_name: str) -> str:
    async with Agent() as agent:
        agent.store_credential(site_name, "me@example.com", "password", overwrite=True)

        async with agent.browser(session_name=site_name) as browser:
            page = await browser.new_page()

            # Login
            await page.goto(f"https://{site_name}.com/login")
            user, pwd = agent.get_credential(site_name)
            await page.fill('[name="email"]', user)
            await page.fill('[name="password"]', pwd)
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")

            # Check product
            await page.goto(url)
            await page.wait_for_load_state("networkidle")
            html = await page.content()

        result = await agent.extract(html, page_type="product")
        return result.get("product", {}).get("price", "unknown")

price = asyncio.run(check_price("https://example.com/product/123", "example"))
print(f"Current price: {price}")
```

---

### Read a private dashboard

```python
import asyncio
from web_speed_agent import Agent

async def get_dashboard_data():
    async with Agent() as agent:
        async with agent.browser(session_name="analytics") as browser:
            page = await browser.new_page()

            # Login (first run only — session persists after)
            creds = agent.get_credential("analytics")
            if not creds:
                agent.store_credential("analytics", "me@company.com", "password")
                creds = agent.get_credential("analytics")

            await page.goto("https://analytics.company.com/login")
            await page.fill('[name="email"]', creds[0])
            await page.fill('[name="password"]', creds[1])
            await page.click('button[type="submit"]')
            await page.wait_for_load_state("networkidle")

            # Navigate to dashboard
            await page.goto("https://analytics.company.com/dashboard")
            await page.wait_for_selector(".metrics-table", timeout=10000)
            html = await page.content()

        result = await agent.extract(html)
        return result

asyncio.run(get_dashboard_data())
```

---

### Multi-page scrape while logged in

```python
import asyncio
from web_speed_agent import Agent

async def scrape_inbox():
    async with Agent() as agent:
        async with agent.browser(session_name="webmail") as browser:
            page = await browser.new_page()

            # Login
            await page.goto("https://mail.example.com/login")
            user, pwd = agent.get_credential("webmail")
            await page.fill('[name="username"]', user)
            await page.fill('[name="password"]', pwd)
            await page.click('[type="submit"]')
            await page.wait_for_load_state("networkidle")

            # Scrape multiple pages
            emails = []
            for page_num in range(1, 4):
                await page.goto(f"https://mail.example.com/inbox?page={page_num}")
                await page.wait_for_load_state("networkidle")
                html = await page.content()
                result = await agent.extract(html, page_type="listing")
                emails.extend(result.get("listing", {}).get("items", []))

        return emails

asyncio.run(scrape_inbox())
```

---

## AI agent integration (MCP)

The included MCP server lets Claude Desktop, Gemini CLI, and any MCP-compatible agent use the SDK directly. The agent can log in, navigate, click, and extract — all through natural language.

**Start the MCP server:**

```bash
WEBSPEED_API_KEY="wsp_..." python3 agent_mcp_server.py
```

**Add to Claude Desktop** (`~/Library/Application Support/Claude/claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "python3",
      "args": ["/path/to/agent_mcp_server.py"],
      "env": {
        "WEBSPEED_API_KEY": "wsp_..."
      }
    }
  }
}
```

**Add to Gemini CLI** (`~/.gemini/settings.json`):

```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "python3.11",
      "args": ["/path/to/agent_mcp_server.py"],
      "env": {
        "WEBSPEED_API_KEY": "wsp_...",
        "PYTHONPATH": "/path/to/web-speed-agent"
      }
    }
  }
}
```

Then tell the agent:

> *"Store my credentials for united — username me@example.com, password mypassword"*

> *"Log into united.com and find me the cheapest flight from SFO to JFK next Friday"*

**Available MCP tools:**

| Tool | Description |
|------|-------------|
| `store_credential` | Save login to system keychain |
| `login` | Open browser + sign in |
| `navigate` | Go to a URL in the active session |
| `extract_page` | Get structured data from current page |
| `click` | Click a button or link |
| `fill_field` | Type into a form field |
| `submit_form` | Submit a form |
| `close_browser` | End the browser session |
| `account_info` | Check API credit balance |

---

## API reference

### `Agent`

```python
Agent(
    api_key: str | None = None,
    server_url: str | None = None,
    config_dir: str = "~/.webspeed",
    headless: bool = True,
)
```

| Parameter | Description |
|-----------|-------------|
| `api_key` | Web Speed API key. Falls back to `WEBSPEED_API_KEY` env var. |
| `server_url` | Override API server URL. Default: `https://api.getwebspeed.io`. |
| `config_dir` | Directory for config, sessions, and logs. Default: `~/.webspeed`. |
| `headless` | Run browser headlessly. Default: `True`. |

---

### `agent.browser()`

```python
agent.browser(
    session_name: str | None = None,
    headless: bool | None = None,
    proxy: str | None = None,
) -> ManagedBrowser
```

Returns an async context manager. Inside the block, call `.new_page()` to get a Playwright `Page`.

| Parameter | Description |
|-----------|-------------|
| `session_name` | Persist cookies to `~/.webspeed/sessions/<name>/`. `None` = no persistence. |
| `headless` | Override instance `headless` for this session. |
| `proxy` | Proxy URL e.g. `"socks5://localhost:1080"`. |

**Session names** must be alphanumeric + hyphens/underscores, max 64 chars.

---

### `agent.extract()`

```python
await agent.extract(
    html: str,
    page_type: str = "auto",
) -> dict
```

Sends HTML to the Web Speed API. Costs 1 credit.

| Parameter | Description |
|-----------|-------------|
| `html` | Raw HTML string (e.g. from `page.content()`). |
| `page_type` | `"article"`, `"product"`, `"listing"`, or `"auto"`. |

---

### `agent.map()`

```python
await agent.map(
    url: str,
    js: bool = False,
) -> dict
```

Fetches and extracts a public URL via the server. No local browser needed. Costs 1 credit.

| Parameter | Description |
|-----------|-------------|
| `url` | Page URL. Must be `http://` or `https://`. |
| `js` | Render JavaScript before extracting. |

---

### `agent.account()`

```python
await agent.account() -> dict
```

Returns: `credits`, `tier`, `status`, `lifetime` (total/hits/misses).

---

### `agent.store_credential()`

```python
agent.store_credential(
    site: str,
    username: str,
    password: str,
    overwrite: bool = False,
) -> None
```

Saves to system keychain. Raises `CredentialError` if credential exists and `overwrite=False`.

---

### `agent.get_credential()`

```python
agent.get_credential(site: str) -> tuple[str, str] | None
```

Returns `(username, password)` or `None` if not found.

---

### `agent.delete_credential()`

```python
agent.delete_credential(site: str) -> None
```

Removes credential from keychain.

---

### Exceptions

```python
from web_speed_agent import (
    WebSpeedError,          # Base exception
    AuthenticationError,    # Invalid/missing API key
    InsufficientCreditsError, # No credits remaining
    APIError,               # API returned 4xx/5xx
    RateLimitError,         # 429 Too Many Requests
    CredentialError,        # Keychain error
    BrowserError,           # Playwright error
    NetworkError,           # Timeout or DNS failure
    PlaywrightNotInstalledError, # Run: playwright install chromium
)
```

```python
from web_speed_agent import Agent, InsufficientCreditsError, NetworkError

try:
    result = await agent.extract(html)
except InsufficientCreditsError:
    print("Out of credits — top up at getwebspeed.io")
except NetworkError as e:
    print(f"Connection failed: {e}")
```

---

## Configuration

### Environment variables

| Variable | Description |
|----------|-------------|
| `WEBSPEED_API_KEY` | API key (recommended over config file) |
| `WEBSPEED_SERVER_URL` | Override server URL (must be `https://`) |

### Config file

`~/.webspeed/config.yaml` — created automatically on first run. Permissions set to `0o600` (owner-only).

```yaml
api:
  server_url: https://api.getwebspeed.io
  timeout: 30

browser:
  headless: true
```

### Session files

Persisted browser sessions are stored in `~/.webspeed/sessions/<name>/storage.json`.

- Permissions: `0o600` (owner-only)
- Contains: cookies, localStorage, sessionStorage
- Safe to delete: agent will re-authenticate on next run

---

## Security

### What leaves your machine

When you call `agent.extract(html)`, the page HTML is sent to the Web Speed API for processing. Everything else stays local.

| Data | Where it goes |
|------|--------------|
| Login credentials | Never leave your machine (system keychain only) |
| Browser cookies / session | Never leave your machine (local Playwright) |
| Page HTML | Sent over HTTPS to Web Speed API for extraction |
| Extracted JSON | Returned to you |

### HTML scrubbing (on by default)

Before any HTML is transmitted, the SDK automatically scrubs it locally:

- Inline `<script>` and `<style>` blocks removed
- Hidden form fields with auth-related names (`csrf`, `token`, `nonce`, `session`, etc.) have their values blanked
- Sensitive `<meta>` content attributes cleared
- HTML comments removed

Visible content — text, links, tables, headings, product data — is untouched.

```python
# Default: scrubbing is on
result = await agent.extract(html)

# Turn off only if the page has no sensitive data
result = await agent.extract(html, scrub=False)

# Or scrub manually and inspect before sending
from web_speed_agent import scrub
clean_html = scrub(raw_html)
print(clean_html)  # inspect what will be sent
result = await agent.extract(clean_html, scrub=False)
```

### Server-side data handling

- **HTML processed in-memory only** — never written to disk, never logged, never cached
- **Auth-gated pages never cached** — pages requiring login are explicitly excluded from the shared registry
- **Usage logs store only**: a hash of your API key, a hash of the URL (or `"sdk-extract"`), timestamp, and detected page type — no content
- **No raw HTML in error responses** — exceptions are sanitized before any error is returned

### Other protections

- **Credentials** stored in system keychain, never in files, never sent to servers
- **Session files** written with `0o600` permissions (owner-only read/write)
- **Config directory** created with `0o700` permissions
- **TLS always verified** — `verify=True` on all HTTP calls, cannot be disabled
- **HTTPS enforced** — `server_url` must start with `https://`, plain HTTP rejected
- **Path traversal prevention** — session names validated against `[a-zA-Z0-9_-]` allowlist
- **No credential logging** — passwords never appear in logs or error messages

---

## License

GNU General Public License v3.0 — see [LICENSE](LICENSE).

Web Speed API usage is subject to the [Web Speed Terms of Service](https://getwebspeed.io/terms).
