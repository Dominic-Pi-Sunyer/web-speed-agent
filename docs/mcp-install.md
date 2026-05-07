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
| Python 3.10 or later | Check: `python3 --version`. **macOS:** Don't use CommandLineTools Python (usually too old). Install via Homebrew (`brew install python@3.13`) or [python.org](https://www.python.org/downloads/). |
| pip | Comes with Python |
| Web Speed API key | [getwebspeed.io](https://getwebspeed.io) |

**Note:** The `web-speed-agent` package is currently in early release. If you're installing for local development or testing, follow the "Local development install" section below. For production, wait for the PyPI release.

---

## Local development install

If you have the source code checked out locally:

**macOS / Linux:**
```bash
pip3 install -e "/path/to/web-speed-agent[mcp]"
playwright install chromium
```

**Windows:**
```batch
pip install -e "C:/path/to/web-speed-agent[mcp]"
python -m playwright install chromium
```

Replace the path with wherever you cloned or downloaded the repo. The `-e` flag installs in "editable" mode so code changes take effect immediately.

---

---

## Step 1 — Install the package

**macOS / Linux:**

Modern Python installations require a virtual environment. Open Terminal and run:

```bash
python3 -m venv ~/web-speed-agent-env
source ~/web-speed-agent-env/bin/activate
pip install web-speed-agent
pip install mcp
playwright install chromium
```

This creates an isolated environment at `~/web-speed-agent-env`. Note the path — you'll need it when configuring Claude Desktop (see Step 3).

> **Common mistake:** Don't run `python3 pip3 install ...` — just use `pip install ...` directly (after activating the venv).
> 
> **Alternative (if you prefer no venv):** You can use `pipx install web-speed-agent` but you'll still need to install `mcp` and `playwright` separately.

**Windows — IMPORTANT: Use the full Python path**

Open **Command Prompt** (not PowerShell) and run these commands. Replace the path with your actual Python location (find it with `where python`):

```batch
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install --upgrade pip
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install web-speed-agent
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install mcp
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m playwright install chromium
```

> **Why the full path?** Windows has multiple Python versions, and `python` or `python3` commands can pick the wrong one or fail. Using the full path ensures you're installing into the correct Python version.
>
> **Upgrade pip first:** Old pip versions (pre-2022) may have stale caches. Upgrade with `--upgrade pip` to get the latest package index.

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

If you created a virtual environment (recommended), use the Python from inside it:

```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "/Users/yourname/web-speed-agent-env/bin/python",
      "args": ["/Users/yourname/tools/agent_mcp_server.py"],
      "env": {
        "WEBSPEED_API_KEY": "wsp_your_key_here"
      }
    }
  }
}
```

If you installed globally instead (not recommended), use:
```json
"command": "python3"
```

**Windows — IMPORTANT: Use the full Python path for `command`**

Don't use `python` or `python3` — instead use the **full path** to the Python executable where you installed the package. Find your Python path by running `where python` in Command Prompt.

```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe",
      "args": ["D:/Web Speed testing/agent_mcp_server.py"],
      "env": {
        "WEBSPEED_API_KEY": "wsp_your_key_here"
      }
    }
  }
}
```

> **Why the full path?** Windows has multiple Python versions and the `python` / `python3` commands don't always work reliably. The full path ensures Claude Desktop uses the correct Python that has `web-speed-agent` installed.
>
> **Path format:** Use forward slashes (`C:/Users/...`) or escaped backslashes (`C:\\Users\\...`) in JSON. Regular backslashes will cause an error.
> **File path:** Make sure the `agent_mcp_server.py` path is also absolute and uses forward slashes.

Save the file and **restart Claude Desktop** (quit fully from the system tray, then reopen). You should see "web-speed-agent" listed under connected tools in Settings → Developer.

---

### Claude Code (CLI)

**macOS / Linux** — run this once:
```bash
claude mcp add web-speed-agent python3 /Users/yourname/tools/agent_mcp_server.py
```

**Windows** — run this once (use the full Python path):
```
claude mcp add web-speed-agent "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe" "C:/Users/YourName/tools/agent_mcp_server.py"
```

Then set your API key. Add this to your shell profile (`~/.zshrc`, `~/.bashrc`) on Mac/Linux, or set it as a Windows environment variable:

**macOS / Linux:**
```bash
export WEBSPEED_API_KEY="wsp_your_key_here"
```

**Windows (Command Prompt):**
```batch
setx WEBSPEED_API_KEY "wsp_your_key_here"
```

Then restart your terminal or Command Prompt for the variable to take effect.

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

**Windows — use the full Python path:**
```json
{
  "mcpServers": {
    "web-speed-agent": {
      "command": "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe",
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

**Claude Desktop keeps disconnecting (Windows)**
The most common cause: you're using `python` or `python3` as the `command` instead of the **full path**. On Windows, the short command names are unreliable. 

**Fix:** Go to Settings → Developer → Edit Config and change:
```json
"command": "python"
```
to:
```json
"command": "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe"
```

Get your exact Python path by running `where python` in Command Prompt. Then use the **full path** in your config — this is required on Windows.

**"externally-managed-environment" (Mac/Linux)**
Modern Python installations (especially from Homebrew) prevent pip from installing globally. Use a virtual environment:

```bash
python3 -m venv ~/web-speed-agent-env
source ~/web-speed-agent-env/bin/activate
pip install web-speed-agent
pip install mcp
playwright install chromium
```

Then in Claude Desktop config, use the path to the venv Python:
```json
"command": "/Users/yourname/web-speed-agent-env/bin/python"
```

**"Requires-Python >=3.10" or "No matching distribution found" (Mac/Linux)**
You're using Python < 3.10. Check with `python3 --version`. On macOS, CommandLineTools Python is often too old.

**Fix:** Install a proper Python 3.10+:

**Via Homebrew (recommended):**
```bash
brew install python@3.13
python3.13 -m pip install web-speed-agent
python3.13 -m pip install mcp
python3.13 -m playwright install chromium
```

**Or from [python.org](https://www.python.org/downloads/):**
Download Python 3.13, then use:
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pip install web-speed-agent
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pip install mcp
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m playwright install chromium
```

Don't rely on CommandLineTools Python — it has outdated versions and permission issues.

**"No module named 'mcp'"**
The MCP SDK wasn't installed. Re-run the install with the `[mcp]` extra using the **same Python** that your config's `command` field points to.

**Windows:** Find your Python path with `where python`, then install using the full path:
```batch
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install "web-speed-agent[mcp]"
```

**Mac/Linux:**
```bash
pip3 install "web-speed-agent[mcp]"
```

The Python in your config's `command` field must be the same one where you ran the install.

**Wrong Python version — "No module named ..." after install**
Windows often has multiple Python versions installed. If you installed the package using Python 3.13 but your config points at Python 3.9, the packages won't be found. Always use the same Python executable in your config that you used to run `pip install`. Find the right path:
```
where python
```
Then use the Python 3.13 entry (e.g. `C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe`) as the `command` in your config. Python 3.9 is also below the minimum version (3.10) and won't work regardless.

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

**"No matching distribution found for web-speed-agent[mcp]"**
The optional `[mcp]` dependency isn't available on PyPI yet. Install separately:
```bash
pip3 install web-speed-agent
pip3 install mcp
```

Also upgrade pip first to ensure you have the latest package index:
```bash
pip3 install --upgrade pip
```

**"can't open file '/Users/.../ pip3'" (Mac/Linux)**
You likely ran `python3 pip3 install ...` instead of just `pip3 install ...`. The correct syntax is:
```bash
pip3 install web-speed-agent
```
Or if `pip3` is not found:
```bash
python3 -m pip install web-speed-agent
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
