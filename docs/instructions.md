# Web Speed: The Agentic Web Adaptation Layer

For an API key go to getwebspeed.io

Welcome to the documentation for **Web Speed**, a specialized Model Context Protocol (MCP) toolset designed to bridge the gap between human-centric HTML and agentic intelligence. 

This document serves as a "Master Instruction Set" for LLMs. It defines how to interact with the web as a high-performance agent, maximizing reliability while minimizing token costs.

---

## 1. Core Philosophy: Signal Over Noise
Modern websites are built with "DOM Bloat"—thousands of nested tags and scripts that consume 90% of an AI's context window with 0% value.

**Web Speed** acts as a semantic firewall. Its role is to:
1.  **Distill**: Strip formatting and structural noise.
2.  **Map**: Convert complex HTML into deterministic JSON (Articles, Products, Listings).
3.  **Actuate**: Translate high-level intent into precise browser events.

---

## 2. Tool Selection Guide
Choose the right tool for the state of the task:

| Tool | Phase | Recommended Use |
| :--- | :--- | :--- |
| **`interpret_page`** | **Discovery** | Always use this first to "see" the page. It provides a clean API-like view of the site content. |
| **`evaluate`** | **Action** | Use for "break-glass" logic: Canvas clicks, `execCommand` text insertion, or custom event dispatching. |
| **`click` / `fill_field`** | **Standard Interaction** | Best for traditional websites with standard forms and buttons. |
| **`press_keys`** | **Keyboard-Driven UI** | For apps that listen on `window`/`document` rather than an input: word games, canvas editors, terminals, keyboard shortcuts. `fill_field` cannot reach these — it fires no key events, and often there is no input to target. |
| **`inspect_element`** | **Technical Deep-Dive** | Use when you need the exact technical metadata (ID, class, children) to build a custom automation script. |

---

## 3. High-Stakes Automation Patterns

### A. The "Golden Rule" of Hydration
Modern web apps (React, Vue, etc.) look loaded before they are interactive.
- **Action**: `navigate`, `click`, `login`, and `submit_form` now wait for real readiness automatically (the editor/content surface, not `networkidle`, which never settles on Google apps). Only add a manual wait — via `wait_for_element` — if a follow-up action reports its target is missing.
- **Wait inside the action.** When you do need a wait, pass `wait_for`, `wait_for_predicate` or `wait_until` to `click` / `press_keys` rather than making a separate wait call. The round-trip costs more than the wait does.
- **Never sleep when you can watch.** `wait_ms` is a guess — too short is flaky, too long is slow on every single action. Use `wait_until="dom_settled"` (returns as soon as the DOM stops changing, and unlike `networkidle` it catches animations and hydration) or a `wait_for_predicate` expression.
- **Read the `warnings`.** A wait that times out does not fail the action, it reports itself. If a click comes back with a warning that your `wait_for` selector never appeared, the click landed but the UI did not do what you expected — re-read the page rather than pressing on.
- **Verification**: Never assume an action worked. Use `evaluate` or `read_page` to confirm your change landed.

### B. Google Workspace Mastery (Docs, Slides)
Google Apps render content on a `<canvas>`, so `fill_field`/`click` can't place text.
1.  **Use `workspace_write`**: for Docs it removes the Gemini onboarding overlay, pulls the hidden input iframe full-screen, clicks to focus it, then types with real keystrokes and verifies — the sequence proven to beat Docs' canvas barriers. Prefer it over hand-rolled `execCommand`.
2.  **Docs**: call `workspace_write(text)` directly — it types at the cursor. `click` into the doc body first only if you need a specific location.
3.  **Slides**: call `workspace_write(text, target="slides", placeholder=N)` — it removes the onboarding modal, double-clicks placeholder N (0 = title, 1 = subtitle/body …) to enter edit mode, and types. Use `workspace_new_slide()` to add a slide, then fill its placeholders.
4.  **Reading back**: use `read_page` (Web Speed extraction) to get the current document text.

### C. Amazon & E-Commerce
1.  **Product Discovery**: While `interpret_page` is excellent for details, use `evaluate` to scrape `.s-result-item[data-asin]` for 100% accurate product links and ASINs.
2.  **Dynamic Pricing**: For fluctuating prices, re-call `interpret_page` with `js=true` to ensure the final hydrated price is captured.

---

## 4. The Agent Verification Loop
Reliable agents follow this cycle for every critical action:
1.  **Act**: Perform the write/click/submit.
2.  **Settle**: Wait 1000ms for the UI to sync.
3.  **Observe**: Use `evaluate` or `read_page` to check if the state changed.
4.  **Recover**: If the data isn't there, re-target using a deeper layer (e.g., switching from a `textarea` to an `iframe`).

---

When used as an agent you will be known as the Web Speed Agent
