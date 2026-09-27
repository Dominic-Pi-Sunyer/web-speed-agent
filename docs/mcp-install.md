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
| `setup_browser` | One-time setup: installs a wrapper so Chrome/Edge always starts with the debug port, enabling the agent to open tabs in your real browser |
| `open_browser` | Open a browser. Chrome auto-connects to your running chrome-agent window (CDP). Firefox imports cookies from your profile. |
| `navigate` | Go to a URL |
| `login` | Fill and submit a login form |
| `read_page` | Extract structured data from the current page (costs 1 credit) |
| `click` | Click a button or link by CSS selector |
| `fill_field` | Type into a form field. Pass `use_keyboard=True` for React/contenteditable inputs (X tweet box, Notion, Slack, etc.) |
| `press_keys` | Send real keystrokes to the page rather than a field — for word games, canvas editors, terminals, and keyboard shortcuts that listen on `window`/`document` |
| `hover` | Move the pointer over an element — opens hover-only menus and tooltips |
| `scroll` | Scroll the page or bring an element into view — needed for infinite feeds and lazy-loaded images |
| `select_option` | Choose an option in a `<select>` (typing into one does nothing) |
| `go_back` | Go back in browser history |
| `submit_form` | Submit a form |
| `get_page_info` | Get the current URL, title, and visible text |
| `wait_for_element` | Wait for an element to appear, disappear, or change state |
| `wait_for_predicate` | Wait until a JS expression returns truthy — the general-purpose wait, and the right replacement for a fixed sleep |
| `wait_for_url` | Wait for the URL to change — useful after SPA navigation |
| `evaluate` | Run JavaScript in the page (Shadow DOM, iframes, embedded data) |
| `close_browser` | Close the tab/browser and save the session |
| `safety_status` | Report which safety controls are active (read-only, confirmation, site lists, audit log) |
| `account_info` | Check your Web Speed credit balance |

### Other MCP hosts

`webspeed-configure` sets up more than Claude Desktop. One command for everything:

```bash
webspeed-configure --key wsp_YOUR_KEY --all --apply
```

| Host | How it's configured | Flag |
|---|---|---|
| Claude Desktop | config file (merge-safe, backed up) | default |
| Claude Code | `claude mcp add` | `--claude-code` |
| Antigravity CLI | `agy mcp add` | `--antigravity` |
| ChatGPT | prints steps — see below | `--chatgpt` |

The CLI hosts go through their own `mcp add` command rather than a hand-written
file, because those tools own their config location and format. Add `--apply` to
run the commands; without it they're printed for you to copy. `--print` is a dry
run that changes nothing, and `--no-claude-desktop` skips the Desktop config if
you only want the others.

**ChatGPT is different and cannot be automated.** It never launches a local
process — its connectors are remote HTTPS endpoints — so:

- The **hosted** tools can be added as a custom connector in Developer mode,
  using `https://api.getwebspeed.io/mcp?key=wsp_...`. The key goes in the URL
  because ChatGPT connectors don't send custom headers, so treat that URL as a
  password.
- The **local agent** is only reachable through OpenAI's Secure MCP Tunnel,
  which runs the stdio server on your machine and proxies it outbound-only.
  That needs a tunnel id and an API key created in OpenAI's own UI, so
  `--chatgpt` prints the exact `tunnel-client` command rather than running it.

### "No matching distribution found" when installing

If `pip install web-speed-agent` says this:

```
ERROR: Ignored the following versions that require a different python version:
       … 0.6.1 Requires-Python >=3.10
ERROR: Could not find a version that satisfies the requirement web-speed-agent
       (from versions: none)
ERROR: No matching distribution found for web-speed-agent
```

**your Python is too old — the package is fine.** `from versions: none` reads as
though the package does not exist, which is why this is so confusing; the real
reason is the first line, which is easy to scroll past.

The Bridge needs **Python 3.10 or newer**. There is no upper limit — 3.13 and
3.14 both work. On macOS, `/usr/bin/python3` is still **3.9.6**, so `pip3
install` hits this on a stock machine.

The requirement is not ours to relax: `mcp` and `playwright` both require 3.10+,
so the install would fail on the dependencies even if we lowered it.

Check what you have, then fix it:

```bash
python3 --version
```

**Easiest fix — use the installer**, which fetches a suitable Python for you
regardless of what is on your system:

```bash
curl -fsSL https://api.getwebspeed.io/install.sh | sh -s -- --key wsp_YOUR_KEY
```

**Or install a newer Python** and use it explicitly:

```bash
brew install python@3.12
python3.12 -m pip install web-speed-agent
```

Point your MCP config at that same interpreter — `webspeed-configure` does this
for you, and refuses to write a config whose Python cannot import the package.

**No admin rights?** The hosted mapping tools need no install at all, and their
configurator runs on Python 3.9:

```bash
curl -fsSL https://api.getwebspeed.io/configure.py | python3 - --key wsp_YOUR_KEY --hosted-only
```

You lose the local browser agent (logged-in sites), but `site_map`,
`interpret_page` and the rest work.

### Safety controls

The Bridge drives a browser holding your live logins, so the blast radius of a
bad action is *anything you are signed into*. Five controls bound it, all set as
environment variables in your MCP host config and **off by default**:

| Setting | Values | What it does |
|---|---|---|
| `WEBSPEED_READONLY` | `1` | Blocks every non-GET request |
| `WEBSPEED_CONFIRM` | `off` · `writes` · `all` | Asks a human before acting |
| `WEBSPEED_ALLOW_SITES` | `a.com,b.com` | Only these hosts are reachable |
| `WEBSPEED_DENY_SITES` | `c.com` | Never these hosts (beats allow) |
| `WEBSPEED_AUDIT_LOG` | `1` or a path | Records every action as JSONL |

They are environment variables and not tool parameters on purpose: **an agent
cannot turn any of them off, because nothing it can call reaches the setting.** A
`confirm=true` argument would be theatre — the caller being restrained would hold
the key. Ask the `safety_status` tool to see what is currently in force.

Not all five are equally strong, and it matters which is which:

- **Boundaries** — read-only and the site lists. Enforced at the network layer,
  so they hold regardless of how an action was triggered.
- **A checkpoint** — confirmation. It asks a human through MCP elicitation and
  *fails closed*: if the client cannot ask, the action is refused rather than run
  unattended. Its risky-word matching (`post`, `delete`, `pay`…) reads the button
  label, so it has false negatives. Use it as a net, not a cage.
- **Evidence** — the audit log. It prevents nothing; it makes an incident
  reconstructible. It records field *names*, never what was typed, so it never
  becomes a keylogger, and it is written `0600`.

`WEBSPEED_CONFIRM` needs a client that implements MCP elicitation. If yours does
not, every confirmable action is refused — prefer `WEBSPEED_READONLY`, which
depends on nothing outside the Bridge.

#### Read-only mode

```json
"env": { "WEBSPEED_API_KEY": "wsp_...", "WEBSPEED_READONLY": "1" }
```

It is set in your MCP host config, never through a tool — and that is the point.
An agent cannot turn it off, because nothing it can call touches the setting. A
`confirm=true` parameter would be theatre: the caller being restrained would hold
the key.

Enforcement is at the network layer, not a guess about which button looks
dangerous. Every non-GET request is aborted before it leaves the browser, so it
does not matter whether a write was triggered by a click, an Enter key, a form
submit or a script — it never reaches the server. Blocked requests come back in
the tool result, so a stopped action reads as a refusal rather than a mystery.

Two honest limits:

- A state-changing `GET` (`…/delete?id=5`) is **not** caught. Bad practice, still real.
- Sites that *read* over POST (some GraphQL) will not load. That is the price of
  a rule you can reason about — turn the mode off for those tasks.

Sign in **before** enabling it: `login` is refused, but the session persists, so
read-only runs afterwards still reach logged-in pages.

### Keeping tasks fast

Every tool call costs a full model round-trip, which is almost always larger
than the thing you are waiting for. Two habits cut most of it:

- **Wait inside the action, not after it.** `click` and `press_keys` take
  `wait_for`, `wait_for_predicate` and `wait_until`, so a click-then-wait is one
  call instead of two.
- **Never sleep when you can watch.** `wait_ms` is a guess that is either too
  short (flaky) or too long (slow on every action). `wait_until="dom_settled"`
  returns the moment the DOM stops changing, and catches what `networkidle`
  misses — animations and hydration that issue no further requests.

A wait that times out does not fail the action. The click still happened, and
the unmet wait comes back in a `warnings` list so you can see that the modal you
expected never opened.

**Credentials are stored in your system keychain** (macOS Keychain, Windows Credential Manager, Linux Secret Service) and never sent to any server.

**`read_page` costs 1 Web Speed credit.** All navigation, clicking, and form-filling is free.

---

## Using your existing browser (recommended for protected sites)

By default the agent launches a fresh Playwright browser. For sites that detect automation — e-commerce checkouts, social networks, dashboards — you can instead use your real browser session with all your existing logins.

### Why can't the agent just open a tab in my running browser?

Browsers don't expose any control interface by default — that would be a serious security hole. The only official way to control a running browser externally is the **Chrome DevTools Protocol (CDP)**, and that must be enabled when the browser starts with `--remote-debugging-port=9222`.

The good news: once Chrome is set up to always start with the debug port, the agent *does* open a real new tab in your actual Chrome window, with all your existing sessions — exactly like a normal tab.

**One-time setup (ask the agent):**
```
setup_browser(browser="chrome")
```
This creates a dedicated Chrome profile (required — Chrome refuses the debug port on its default profile), copies your existing cookies into it, and installs a launcher:
- **macOS:** creates `~/.webspeed/chrome-debug/` and installs the `chrome-agent` shell script
- **Windows:** creates `%LOCALAPPDATA%\WebSpeedAgent\chrome-debug\` and writes `chrome-agent.bat` to your Desktop

After setup, always open Chrome using the launcher. Then `open_browser(browser="chrome")` — with no extra arguments — automatically detects the debug port and opens a new tab in your real Chrome window.

### Is the remote debugging port safe?

**Yes — it only listens on your own machine.** All three browsers bind `--remote-debugging-port` to `localhost` (`127.0.0.1`) by default, not to your network interface. This means:

- ✅ Other devices on your WiFi or local network **cannot reach it**
- ✅ It is invisible to the internet
- ⚠️ Any other application running locally on your machine can connect to it while the port is open
- ⚠️ A malicious website could theoretically attempt a DNS rebinding attack (a sophisticated, uncommon technique)

**Best practice:** only run the browser with the debug flag while you are actively using the agent. When you are done, close it and reopen your browser normally (without the flag). Don't leave it running overnight or when you're not using it.

---

### How it works — profile mode vs CDP

There are two ways to attach the agent to your existing browser:

**Profile mode (recommended for all browsers):** the agent opens your browser directly using its real installed binary and your existing profile folder. All your logins and cookies are there because they live on disk in the profile. The browser must be **fully quit first** — the profile directory is locked while the browser is open.

```
open_browser(browser="chrome",  profile_path="auto")
open_browser(browser="firefox", profile_path="auto")
open_browser(browser="edge",    profile_path="auto")
```

**CDP mode (Chrome):** Chrome exclusively uses CDP — the agent attaches to a Chrome window launched with a debug port. One-time setup creates a dedicated agent profile (required — Chrome refuses the debug port on its default profile). After setup, open Chrome with the provided launcher, and the agent connects automatically.

> **Firefox note:** Playwright ships its own Firefox Nightly build as the automation engine. The browser window will always show "Firefox Nightly" — this is expected and not a misconfiguration. Your real Firefox profile data (logins, bookmarks, cookies) is loaded from your standard Firefox profile. The Nightly label is cosmetic.

---

### Chrome

#### Profile mode 

No debug port needed. The agent loads your Chrome profile (cookies, logins, sessions) and opens it in Playwright's Chromium — no Chrome window appears, but all your logged-in sessions are there and ready.

> **Why Playwright's Chromium instead of system Chrome?** Chrome refuses remote debugging on its default profile directory ("DevTools remote debugging requires a non-default data directory"). Playwright's Chromium reads the same profile format without this restriction, and on macOS it uses the same system Keychain to decrypt cookies — so you stay logged in.

**Step 1 — Quit Chrome completely** (Cmd+Q on Mac, not just the window).

**Step 2 — Tell the agent to open Chrome:**
```
open_browser(browser="chrome", profile_path="auto")
```

`profile_path="auto"` finds your Chrome user data directory automatically:
- macOS: `~/Library/Application Support/Google/Chrome`
- Windows: `%LOCALAPPDATA%\Google\Chrome\User Data`

If Chrome isn't fully closed, you'll get a clear error saying the profile is locked.

---

#### CDP mode

Chrome attaches to a real Chrome window you launch with a debug port. You must use a **dedicated agent profile** (`--user-data-dir`) — Chrome refuses to open the debug port on its default profile directory.

The easiest way is to let the agent do the one-time setup for you.

---

**macOS setup (one-time, ask the agent):**
```
setup_browser(browser="chrome")
```
This creates `~/.webspeed/chrome-debug/` (copies your cookies into it) and installs a `chrome-agent` shell script.

After setup, always launch Chrome for agent use by typing `chrome-agent` in Terminal. Then:
```
open_browser(browser="chrome")
```
No arguments needed — it auto-detects the debug port.

---

**Windows setup (one-time, ask the agent):**
```
setup_browser(browser="chrome")
```
This creates `%LOCALAPPDATA%\WebSpeedAgent\chrome-debug\` (copies your cookies into it) and writes `chrome-agent.bat` to your Desktop.

After setup, always launch Chrome for agent use by double-clicking `chrome-agent.bat`. Then:
```
open_browser(browser="chrome")
```

---

**Windows — manual bat file** (if you prefer not to use `setup_browser`):

> ⚠️ You must **first** create the agent profile directory manually:
> `%LOCALAPPDATA%\WebSpeedAgent\chrome-debug\Default\`
> Chrome needs this non-default directory to allow the debug port.

Create `chrome-agent.bat` on your Desktop with this content:

```batch
@echo off
:: Auto-generated by web-speed-agent — do not edit the --user-data-dir line.
:: Chrome requires a non-default user-data-dir to allow --remote-debugging-port.
taskkill /F /IM chrome.exe /T 2>nul
timeout /t 2 /nobreak >nul
start "" "C:\Program Files\Google\Chrome\Application\chrome.exe" ^
  --user-data-dir="%LOCALAPPDATA%\WebSpeedAgent\chrome-debug" ^
  --remote-debugging-port=9222 ^
  --no-first-run ^
  --disable-default-apps
echo Chrome Agent started on port 9222.
```

Then tell the agent:
```
open_browser(browser="chrome", cdp_url="http://localhost:9222")
```

---

### Cookie safety

When you use `profile_path=` for any browser, cookies are read from your local browser profile and injected into the local Playwright browser. They are **only ever transmitted to the target websites** as normal HTTP request headers — exactly as if you were browsing normally. They are never sent to the Web Speed API or any third party. The `read_page` call only sends the page's HTML content to the API, not your cookies.

For Firefox specifically, the profile database is copied to a temporary file and read there — the original `cookies.sqlite` on disk is never modified.

---

### Firefox

Firefox does not support CDP. The agent uses **cookie import mode** — it reads your Firefox cookies directly from the profile database (read-only, your real profile is never touched or modified), then injects them into a fresh browser session. You'll be logged into every site you were logged into in Firefox.

> **"Firefox Nightly" in the title bar** — Playwright ships its own Firefox Nightly build. The browser will always show "Firefox Nightly". This is a Playwright limitation and does not affect functionality — your cookies and sessions work regardless.

**Step 0 — One-time Playwright setup** (only needed once):
```bash
playwright install firefox
```

**Step 1 — Firefox can be open or closed** — cookies are read from the profile database file directly, so it doesn't matter.

**Step 2 — Tell the agent to load your Firefox cookies:**
```
open_browser(browser="firefox", profile_path="auto")
```

`profile_path="auto"` finds your standard Firefox profile automatically (skipping Nightly/Dev Edition profiles). The response shows how many cookies were imported and which profile was used.

If it finds the wrong profile, pass the path explicitly:
- **macOS:** `~/Library/Application Support/Firefox/Profiles/` — subfolder ending in `.default-release`
- **Windows:** `%APPDATA%\Mozilla\Firefox\Profiles\` — subfolder ending in `.default-release`

```
open_browser(browser="firefox", profile_path="~/Library/Application Support/Firefox/Profiles/abc123.default-release")
```

---

### Microsoft Edge

#### Profile mode (recommended)

**Step 1 — Quit Edge completely.**

**Step 2 — Tell the agent to open Edge:**
```
open_browser(browser="edge", profile_path="auto")
```

`profile_path="auto"` finds your Edge user data directory automatically:
- Windows: `%LOCALAPPDATA%\Microsoft\Edge\User Data`
- macOS: `~/Library/Application Support/Microsoft Edge`

---

#### CDP mode (fallback)

Edge has the same single-instance enforcement as Chrome — kill first, then relaunch.

Windows:
```batch
taskkill /F /IM msedge.exe /T 2>nul
timeout /t 2 /nobreak >nul
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
```

macOS:
```bash
pkill -a -i "Microsoft Edge" 2>/dev/null; sleep 1.5; /Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --remote-debugging-port=9222
```

*macOS alias:*
```bash
alias edge-agent='pkill -a -i "Microsoft Edge" 2>/dev/null; sleep 1.5; /Applications/Microsoft\ Edge.app/Contents/MacOS/Microsoft\ Edge --remote-debugging-port=9222'
```

*Windows .bat file:*
```batch
@echo off
taskkill /F /IM msedge.exe /T 2>nul
timeout /t 2 /nobreak >nul
"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe" --remote-debugging-port=9222
```

Tell the agent to connect:
```
open_browser(browser="edge", cdp_url="http://localhost:9222")
```

---

### Tips

- **Profile mode: quit completely first** — the profile directory is locked while the browser is open. Quit fully (Cmd+Q on Mac, not just close the window) before calling `open_browser`. You'll get a clear error message if the lock is still held.
- **Your logins are not lost** — cookies and sessions live on disk in the profile folder and are still there after you quit.
- **You can watch the agent work** — the browser opens visibly so you can see every action in real time.
- **CDP fallback tips:**
  - Kill all processes first (use `pkill` / `taskkill`, not just Cmd+Q) — ghost helper processes block the debug port
  - Verify the port opened at `http://localhost:9222` before connecting
  - Port 9222 is the default; any unused port works if you update `cdp_url` to match

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

**"Could not connect to Chrome/Edge at http://localhost:9222" / port shows "Connection Refused"**

The most common cause is Chrome's single-instance enforcement: any Chrome process running when you launched the command caused the flag to be silently ignored. The port never opened.

**Fix — kill everything, then relaunch:**

macOS (Chrome):
```bash
pkill -a -i "Google Chrome" 2>/dev/null; sleep 1.5; /Applications/Google\ Chrome.app/Contents/MacOS/Google\ Chrome --remote-debugging-port=9222
```

Windows (Chrome):
```batch
taskkill /F /IM chrome.exe /T 2>nul
timeout /t 2 /nobreak >nul
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222
```

**Verify:** open `http://localhost:9222` in another browser — you should see a JSON list of tabs, not "Connection Refused".

If still failing:
- Check for stray processes: `lsof -i :9222` (Mac) or `netstat -ano | findstr :9222` (Windows)
- Open Activity Monitor (Mac) or Task Manager (Windows) and confirm no Chrome/Edge processes remain before relaunching

---

**Firefox opens a new window instead of using my existing session**

Firefox does not support CDP. Use cookie import mode instead — it reads your login cookies directly from the Firefox profile database (your profile is never modified):

```
open_browser(browser="firefox", profile_path="auto")
```

Firefox does not need to be closed first.

**"You've launched an older version of Firefox" dialog appeared**

This happened with the old approach (opening the profile directly). The current cookie-import method does not open the profile with Playwright at all — this dialog will no longer appear.

**Firefox always shows "Firefox Nightly" in the title bar**

Playwright ships a Firefox Nightly build as its automation engine. This label is cosmetic and unavoidable — it is a Playwright limitation. Your actual Firefox login cookies are imported and your sessions work normally.

**Firefox profile auto-detection picks the wrong profile / 0 cookies imported**

`profile_path="auto"` prefers `.default-release` profiles and skips Nightly/Dev Edition. The response shows which profile was found and how many cookies were imported. If it's wrong, pass the path explicitly:

- macOS: `~/Library/Application Support/Firefox/Profiles/` — subfolder ending in `.default-release`
- Windows: `%APPDATA%\Mozilla\Firefox\Profiles\` — subfolder ending in `.default-release`

```
open_browser(browser="firefox", profile_path="~/Library/Application Support/Firefox/Profiles/abc123.default-release")
```

**"Could not launch Firefox with profile" error**

- Firefox must be **fully quit** before calling `open_browser` with a profile — the profile is file-locked while Firefox is running.
- Make sure `playwright install firefox` has been run once.
- If the error persists, try passing the profile path explicitly (see above) instead of `"auto"`.

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
