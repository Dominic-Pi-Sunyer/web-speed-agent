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

If you also want to use **Firefox**, run one extra line:
```bash
playwright install firefox
```

> **Common mistake:** Don't run `python3 pip3 install ...` — just use `pip install ...` directly (after activating the venv).

**Windows — IMPORTANT: Use the full Python path**

Open **Command Prompt** (not PowerShell) and run these commands. Replace the path with your actual Python location (find it with `where python`):

```batch
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install --upgrade pip
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install web-speed-agent
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install mcp
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m playwright install chromium
```

For Firefox support, add:
```batch
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m playwright install firefox
```

> **Why the full path?** Windows has multiple Python versions, and `python` or `python3` commands can pick the wrong one. Using the full path ensures you're installing into the correct Python version.

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

**Windows — IMPORTANT: Use the full Python path for `command`**

Don't use `python` or `python3` — use the **full path** to the Python executable where you installed the package. Find it with `where python` in Command Prompt.

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

> **Path format:** Use forward slashes (`C:/Users/...`) or escaped backslashes (`C:\\Users\\...`) in JSON. Regular backslashes will cause an error.

Save the file and **restart Claude Desktop** (quit fully from the system tray, then reopen).

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

Restart your terminal for the variable to take effect.

---

### Gemini CLI

**macOS:**

The settings file is at `~/.gemini/settings.json`.

**Option 1 — Using Terminal (recommended):**

```bash
nano ~/.gemini/settings.json
```

Paste the configuration below, then press `Ctrl+O`, Enter, `Ctrl+X` to save.

**Option 2 — Using Finder:**

1. Open Finder → press `Cmd+Shift+G` → type `~/.gemini` → click Go
2. Double-click `settings.json` (or right-click → Open With → TextEdit)
3. Paste the configuration and save with `Cmd+S`

**Configuration:**

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

Replace `yourname` with your macOS username (find it with `whoami`) and `wsp_your_key_here` with your API key.

**Windows:**

Open `%APPDATA%\.gemini\settings.json` (press `Win+R`, paste the path). Add:

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

Save and restart Gemini CLI.

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
| `open_browser` | Open a browser — choose Chrome, Firefox, or Edge; or attach to one already running |
| `navigate` | Go to a URL |
| `login` | Fill and submit a login form |
| `read_page` | Extract structured data from the current page (costs 1 credit) |
| `click` | Click a button or link by CSS selector |
| `fill_field` | Type into a form field |
| `submit_form` | Submit a form |
| `get_page_info` | Get the current URL, title, and visible text |
| `wait_for_element` | Wait for an element to appear, disappear, or change state |
| `wait_for_url` | Wait for the URL to change — useful after SPA navigation |
| `evaluate` | Run JavaScript in the page (Shadow DOM, iframes, embedded data) |
| `close_browser` | Close the tab/browser and save the session |
| `account_info` | Check your Web Speed credit balance |

**Credentials are stored in your system keychain** (macOS Keychain, Windows Credential Manager, Linux Secret Service) and never sent to any server.

**`read_page` costs 1 Web Speed credit.** All navigation, clicking, and form-filling is free.

---

## Using your existing browser (recommended for protected sites)

By default the agent launches a fresh Playwright browser. For sites that detect automation — e-commerce checkouts, social networks, dashboards — you can instead **attach to your own running Chrome, Firefox, or Edge**. No new window opens, you stay logged in, and the site sees your real browser fingerprint.

### Is the remote debugging port safe?

**Yes — it only listens on your own machine.** All three browsers bind `--remote-debugging-port` to `localhost` (`127.0.0.1`) by default, not to your network interface. This means:

- ✅ Other devices on your WiFi or local network **cannot reach it**
- ✅ It is invisible to the internet
- ⚠️ Any other application running locally on your machine can connect to it while the port is open
- ⚠️ A malicious website could theoretically attempt a DNS rebinding attack (a sophisticated, uncommon technique)

**Best practice:** only run the browser with the debug flag while you are actively using the agent. When you are done, close it and reopen your browser normally (without the flag). Don't leave it running overnight or when you're not using it.

---

### How it works

The debug port has to be enabled when the browser **starts** — you cannot attach it to an already-running browser. The workflow is always:

1. **Quit your browser completely** (Cmd+Q on Mac, not just close the window)
2. Relaunch it with the debug flag using the command for your browser below
3. Your logins and cookies are still there — they are stored in your browser profile on disk, not in the running process. The browser loads the same profile on restart.
4. Tell the agent to connect

The agent opens a **new tab** in your relaunched browser. When it calls `close_browser()`, only that tab is closed.

---

### Chrome

**Step 1 — Quit Chrome completely**, then:

**macOS — run in Terminal:**
```bash
/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

**Windows — run in Command Prompt:**
```batch
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**Make a permanent shortcut so you don't have to type it each time:**

*macOS* — add this alias to `~/.zshrc` (open it with `nano ~/.zshrc`):
```bash
alias chrome-agent='/Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222'
```
Run `source ~/.zshrc` once. Then just type `chrome-agent` in Terminal.

*Windows* — right-click your desktop → New → Shortcut → paste this as the target:
```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```
Name it "Chrome (Agent)" and double-click it whenever you need it.

**Step 2 — Tell the agent to connect:**
```
open_browser(browser="chrome", cdp_url="http://localhost:9222")
```

---

### Firefox

Firefox does not support CDP connections in Playwright. Instead of a debug port, Firefox uses **profile mode** — the agent launches Firefox using your existing profile, so all your logins and cookies are already there.

**Step 0 — One-time Playwright setup** (only needed once):
```bash
playwright install firefox
```

**Step 1 — Quit Firefox completely** (Cmd+Q on Mac, not just close the window).

Your logins are not lost — they are stored in your Firefox profile folder on disk.

**Step 2 — Tell the agent to launch Firefox with your profile:**
```
open_browser(browser="firefox", profile_path="auto")
```

`profile_path="auto"` automatically finds and uses your default Firefox profile. If it picks the wrong profile, find your profile folders here:

- **macOS:** `~/Library/Application Support/Firefox/Profiles/`
- **Windows:** `%APPDATA%\Mozilla\Firefox\Profiles\`

Each subfolder is a separate profile (e.g. `abc123.default-release`). Pass the full path to use a specific one:
```
open_browser(browser="firefox", profile_path="~/Library/Application Support/Firefox/Profiles/abc123.default-release")
```

> **Firefox Nightly / Developer Edition** each have their own separate profile directory. If you regularly use Nightly, pass its profile path explicitly rather than using `"auto"`.

---

### Microsoft Edge

Edge is Chromium-based and works identically to Chrome.

**Step 1 — Quit Edge completely**, then:

**Windows — run in Command Prompt:**
```batch
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
```

**macOS — run in Terminal:**
```bash
/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --remote-debugging-port=9222
```

**Make a permanent shortcut:**

*macOS* — add to `~/.zshrc`:
```bash
alias edge-agent='/Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --remote-debugging-port=9222'
```

*Windows* — right-click desktop → New → Shortcut → paste the full command → name it "Edge (Agent)".

**Step 2 — Tell the agent to connect:**
```
open_browser(browser="edge", cdp_url="http://localhost:9222")
```

---

### Tips for CDP mode

- **Quit, don't just close** — on Mac, Cmd+Q quits; clicking the red dot just closes the window. The browser must be fully quit before relaunching with the flag.
- **Your logins are not lost** — cookies and sessions are stored in the profile on disk. Relaunching with the flag reloads the same profile.
- **Close it when done** — once finished, quit the browser and reopen it normally (without the flag) to stop the debug port.
- **Port 9222 is the default** — any unused port works. If you change it, update the `cdp_url` to match (e.g. `http://localhost:9333`).
- **Only one browser can use a port at a time** — don't run two browsers on the same port.
- **You can watch the agent work** — the new tab opens in your own browser window so you can see every action in real time.

---

## Credits and pricing

Each `read_page` call costs 1 credit. Navigation, login, clicking, and form-filling are free — you're only charged when structured data is extracted.

View your balance and top up at **[getwebspeed.io/account](https://getwebspeed.io/account)**, or ask the AI:

> *"How many credits do I have left?"*

---

## Troubleshooting

**Claude Desktop keeps disconnecting (Windows)**
The most common cause: using `python` or `python3` as `command` instead of the full path.

**Fix:** Go to Settings → Developer → Edit Config and replace:
```json
"command": "python"
```
with:
```json
"command": "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe"
```
Get your exact path by running `where python` in Command Prompt.

---

**"externally-managed-environment" (Mac/Linux)**
Modern Python installations (especially from Homebrew) prevent pip from installing globally. Use a virtual environment:

```bash
python3 -m venv ~/web-speed-agent-env
source ~/web-speed-agent-env/bin/activate
pip install web-speed-agent
pip install mcp
playwright install chromium
```

Then in your config, use the venv Python:
```json
"command": "/Users/yourname/web-speed-agent-env/bin/python"
```

---

**"Requires-Python >=3.10" or "No matching distribution found" (Mac/Linux)**
You're using Python < 3.10. On macOS, CommandLineTools Python is often too old.

**Via Homebrew (recommended):**
```bash
brew install python@3.13
python3.13 -m pip install web-speed-agent mcp
python3.13 -m playwright install chromium
```

**From [python.org](https://www.python.org/downloads/):**
```bash
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m pip install web-speed-agent mcp
/Library/Frameworks/Python.framework/Versions/3.13/bin/python3 -m playwright install chromium
```

---

**"No module named 'mcp'"**
The MCP SDK wasn't installed. Re-run using the same Python your config points to:

**Windows:**
```batch
C:\Users\YourName\AppData\Local\Programs\Python\Python313\python.exe -m pip install mcp
```

**Mac/Linux:**
```bash
pip3 install mcp
```

---

**"No module named 'web_speed_agent'"**
The package isn't installed in the Python the MCP server is running under. Use `where python` (Windows) or `which python3` (Mac/Linux) to find your Python path, then use that full path as `command` in your config.

---

**"No matching distribution found for web-speed-agent[mcp]"**
Install separately:
```bash
pip3 install web-speed-agent
pip3 install mcp
```

---

**"Python was not found" or Windows opens the Microsoft Store**
Windows App Execution Alias intercepts `python3` and redirects to the Store. Use the full path:
```json
"command": "C:/Users/YourName/AppData/Local/Programs/Python/Python313/python.exe"
```

---

**Wrong Python version — "No module named ..." after install**
Windows often has multiple Python versions. If you installed with Python 3.13 but your config points at Python 3.9, the packages won't be found. Always use the same Python in your config that you ran `pip install` with. Find the right path with `where python` and use the Python313 entry.

---

**"can't open file '/Users/.../pip3'"**
You ran `python3 pip3 install ...` instead of just `pip3 install ...`. Correct syntax:
```bash
pip3 install web-speed-agent
```
Or if `pip3` isn't found:
```bash
python3 -m pip install web-speed-agent
```

---

**Playwright browser won't launch**
Run the install using the same Python that runs the MCP server:
```bash
python -m playwright install chromium
python -m playwright install firefox   # only if you use Firefox
```

---

**"Could not connect to Chrome/Edge at http://localhost:9222"**

- Chrome and Edge must be **fully closed** before relaunching with `--remote-debugging-port`. If they're already open, the new command attaches to the existing process (which doesn't have the flag).
- Verify Chrome is running with the flag: open `http://localhost:9222` in another browser — you should see a JSON response listing open tabs.
- Check nothing else is using port 9222: `lsof -i :9222` (Mac/Linux) or `netstat -ano | findstr :9222` (Windows).

---

**Firefox opens a new window instead of using my existing session**

Firefox does not support CDP connections in Playwright, so the `cdp_url` approach used for Chrome and Edge does not work with Firefox. Use profile mode instead:

```
open_browser(browser="firefox", profile_path="auto")
```

This launches Firefox with your existing profile (all logins and cookies intact). Firefox must be fully closed first (Cmd+Q on Mac).

**"Could not launch Firefox with profile" error**

- Firefox must be **fully quit** before calling `open_browser` with a profile — the profile is file-locked while Firefox is running.
- Make sure `playwright install firefox` has been run once.
- If `profile_path="auto"` picks the wrong profile, find your profiles at `~/Library/Application Support/Firefox/Profiles/` (Mac) or `%APPDATA%\Mozilla\Firefox\Profiles\` (Windows) and pass the full path explicitly.

---

**Claude Desktop shows the server as disconnected**
- Open Settings → Developer → Edit Config and check the JSON is valid (no trailing commas, all brackets matched)
- Make sure the path to `agent_mcp_server.py` is absolute, not relative
- Quit Claude Desktop fully from the system tray icon, then reopen it

---

**"WEBSPEED_API_KEY not set"**
The key must be in the `env` block of your config file — Claude Desktop and Gemini CLI don't inherit variables from your terminal session.

---

## Keeping credentials safe

- Login passwords live in the system keychain only — never written to files, never sent to any server
- Your API key is the only credential that leaves your machine (sent over HTTPS to `api.getwebspeed.io`)
- Browser session cookies are stored in `~/.webspeed/sessions/` with owner-only permissions
- Revoke your API key at any time from [getwebspeed.io/account](https://getwebspeed.io/account)
