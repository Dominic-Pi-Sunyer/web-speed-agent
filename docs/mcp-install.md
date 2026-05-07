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
| Python 3.10 or later | Check: `python --version` (Windows) or `python3 --version` (Mac/Linux) |
| pip | Comes with Python |
| Web Speed API key | [getwebspeed.io](https://getwebspeed.io) |

---

## Step 1 — Install the package

**macOS / Linux:**
```bash
pip3 install web-speed-agent
playwright install chromium
```

**Windows:**
```
pip install web-speed-agent
python -m playwright install chromium
```

> **Windows tip:** Always use `python -m playwright` on Windows — the plain `playwright` command is often not found even after install.

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

Open Claude Desktop, then go to **Settings → Developer → Edit Config**.

This opens the config file in your default text editor. Add the `web-speed-agent` block shown below. If you already have other MCP servers, add it inside the existing `mcpServers` block — don't replace what's already there.

**macOS / Linux:**
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

**Windows:**
```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "python",
      "args": ["C:/Users/YourName/tools/agent_mcp_server.py"],
      "env": {
        "WEBSPEED_API_KEY": "wsp_your_key_here"
      }
    }
  }
}
```

> **Windows path format:** Use forward slashes (`C:/Users/...`) or escaped backslashes (`C:\\Users\\...`) in JSON. Regular backslashes will cause an error.

Save the file and **restart Claude Desktop** (quit fully from the system tray, then reopen). You should see "web-speed-agent" listed under connected tools in Settings → Developer.

---

### Claude Code (CLI)

**macOS / Linux** — run this once:
```bash
claude mcp add web-speed-agent python3 /Users/yourname/tools/agent_mcp_server.py
```

**Windows** — run this once:
```
claude mcp add web-speed-agent python C:/Users/YourName/tools/agent_mcp_server.py
```

Then set your API key. Add this to your shell profile (`~/.zshrc`, `~/.bashrc`) on Mac/Linux, or set it as a Windows environment variable in System Properties:

```bash
export WEBSPEED_API_KEY="wsp_your_key_here"
```

---

### Gemini CLI

Open `~/.gemini/settings.json` and add:

**macOS / Linux:**
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

**Windows:**
```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "python",
      "args": ["C:/Users/YourName/tools/agent_mcp_server.py"],
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

**"Python was not found" or Windows opens the Microsoft Store**
Windows has an App Execution Alias that intercepts the `python3` command and redirects to the Store. The fix: use `python` (not `python3`) as the command in your config. If it still fails, use the full path to your Python executable:
```json
"command": "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe"
```
You can find the exact path by running `where python` in a Command Prompt.

**"No module named 'web_speed_agent'"**
The package isn't installed in the Python that the MCP server is running under. Confirm which Python you're using with `where python` (Windows) or `which python3` (Mac/Linux), then use that full path as the `command` in your config:
```json
"command": "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe"
```

**Playwright browser won't launch**
Run the install using the same Python that runs the MCP server:
```
python -m playwright install chromium
```

**Claude Desktop shows the server as disconnected**
- Open Settings → Developer → Edit Config and check the JSON is valid (no trailing commas, all brackets matched)
- Make sure the path to `agent_mcp_server.py` is absolute, not relative
- Quit Claude Desktop fully from the system tray icon, then reopen it

**"WEBSPEED_API_KEY not set"**
The key must be in the `env` block of your config file — Claude Desktop and Gemini CLI don't inherit variables from your terminal session.

---

## Keeping credentials safe

- Login passwords live in the system keychain only — never written to files, never sent to any server
- Your API key is the only credential that leaves your machine (sent over HTTPS to `api.getwebspeed.io`)
- Browser session cookies are stored in `~/.webspeed/sessions/` with owner-only permissions
- Revoke your API key at any time from [getwebspeed.io/account](https://getwebspeed.io/account)
