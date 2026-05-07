# Installing the Web Speed Agent MCP Server

The Web Speed Agent MCP server lets Claude, Gemini, and any MCP-compatible AI client log into websites, navigate pages, and extract structured data — all through natural language. The browser runs on your machine, so your passwords never leave.

> **You'll need a Web Speed API key.** The MCP server is the local bridge; the extraction engine runs in the cloud on your key.
> Get one at **[getwebspeed.io](https://getwebspeed.io)** 

---

## What you can do once it's set up

Tell your AI assistant things like:

- *"Store my LinkedIn credentials — username me@example.com, password hunter2"*
- *"Log into LinkedIn and pull my connection requests from the last week"*
- *"Go to my Shopify dashboard and give me today's revenue"*
- *"Search Airbnb for a 2-bedroom in Lisbon next month and list the top 5 results"*

The AI handles the login, navigation, and data extraction. You get clean structured results.

---

## Prerequisites

| Requirement | Notes |
|-------------|-------|
| Python 3.10 or later | `python3 --version` to check |
| pip | Comes with Python |
| Web Speed API key | [getwebspeed.io](https://getwebspeed.io) |

---

## Step 1 — Install the package

```bash
pip install web-speed-agent
```

Then install the Chromium browser that Playwright will control:

```bash
playwright install chromium
```

> **Windows note:** If `playwright` isn't found after install, use `python -m playwright install chromium`.

---

## Step 2 — Get the MCP server file

Download `agent_mcp_server.py` from the repository and save it somewhere permanent — the path goes into your config and shouldn't change.

**Suggested locations:**

| Platform | Path |
|----------|------|
| macOS / Linux | `~/tools/agent_mcp_server.py` |
| Windows | `C:\Users\YourName\tools\agent_mcp_server.py` |

---

## Step 3 — Configure your AI client

Pick the client you use. You only need to do one.

---

### Claude Desktop

**macOS** — open `~/Library/Application Support/Claude/claude_desktop_config.json`

**Windows** — open `%APPDATA%\Claude\claude_desktop_config.json`

Add the `web-speed-agent` entry. If you already have other MCP servers, add it inside the existing `mcpServers` block.

```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "python3",
      "args": ["/Users/yourname/tools/agent_mcp_server.py"],
      "env": {
        "WEBSPEED_API_KEY": "wsp_your_key_here"
      }
    }
  }
}
```

> **Windows:** Use `python` instead of `python3`, and use forward slashes or escaped backslashes in the path:
> ```json
> "command": "python",
> "args": ["C:/Users/YourName/tools/agent_mcp_server.py"]
> ```

Save the file and **restart Claude Desktop**. You should see "web-speed-agent" listed under connected tools.

---

### Claude Code (CLI)

Run this once from any terminal:

```bash
claude mcp add web-speed-agent python3 /Users/yourname/tools/agent_mcp_server.py
```

Then set your API key in the environment before starting Claude Code:

```bash
export WEBSPEED_API_KEY="wsp_your_key_here"
claude
```

To make the key permanent, add the export line to your shell profile (`~/.zshrc`, `~/.bashrc`, etc.).

---

### Gemini CLI

Open `~/.gemini/settings.json` and add:

```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "python3",
      "args": ["/Users/yourname/tools/agent_mcp_server.py"],
      "env": {
        "WEBSPEED_API_KEY": "wsp_your_key_here"
      }
    }
  }
}
```

Restart Gemini CLI after saving.

---

## Step 4 — Test it

Ask your AI assistant:

> *"Check my Web Speed account info"*

It should respond with your credit balance and account status. If it does, everything is working.

---

## Available tools

Once connected, the AI can use these tools automatically — you don't need to call them by name.

| Tool | What it does |
|------|-------------|
| `store_credential` | Save a site's login to your system keychain |
| `open_browser` | Launch a local browser (visible or headless) |
| `navigate` | Go to a URL |
| `login` | Fill and submit a login form |
| `read_page` | Extract structured data from the current page |
| `click` | Click a button or link by CSS selector |
| `fill_field` | Type into a form field |
| `submit_form` | Submit a form |
| `get_page_info` | Get the current URL, title, and visible text |
| `wait_for_element` | Wait for an element to appear |
| `close_browser` | Save the session and close the browser |
| `account_info` | Check your Web Speed credit balance |

**Credentials are stored in your system keychain** (macOS Keychain, Windows Credential Manager, Linux Secret Service) and never sent to any server.

**`read_page` costs 1 Web Speed credit.** All navigation, clicking, and form-filling is free.

---

## Credits and pricing

Each `read_page` call costs 1 credit. Navigation, login, clicking, and form-filling are free — you're only charged when structured data is extracted.

View your balance and top up at **[getwebspeed.io/account](https://getwebspeed.io/account)**, or ask the AI:

> *"How many credits do I have left?"*

---

## Troubleshooting

**"No module named 'web_speed_agent'"**
The package isn't installed in the Python that the MCP server is running under. Find your Python path with `which python3` and use the full path in your config:
```json
"command": "/usr/local/bin/python3"
```

**"playwright: command not found" or browser won't launch**
Run `playwright install chromium` again using the same Python that runs the server:
```bash
python3 -m playwright install chromium
```

**Claude Desktop shows the server as disconnected**
- Check the JSON config is valid (no trailing commas, matching brackets)
- Make sure the path to `agent_mcp_server.py` is absolute, not relative
- Restart Claude Desktop fully (quit from the menu bar, not just close the window)

**"WEBSPEED_API_KEY not set"**
The key must be in the `env` block of your config, not just exported in your terminal — Claude Desktop and Gemini CLI don't inherit shell environment variables.

**Windows: path errors on startup**
Use forward slashes in JSON paths (`C:/Users/...`) or escape backslashes (`C:\\Users\\...`). Both work.

---

## Keeping credentials safe

- Your login passwords live in the system keychain only — they are never written to files or sent anywhere
- Your API key is the only thing that leaves your machine (it goes to `api.getwebspeed.io` to authenticate extraction requests)
- Browser session cookies are stored in `~/.webspeed/sessions/` with owner-only permissions (`600`)
- Revoke your API key at any time from [getwebspeed.io/account](https://getwebspeed.io/account)
