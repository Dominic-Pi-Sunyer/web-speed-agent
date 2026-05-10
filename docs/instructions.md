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
| **`inspect_element`** | **Technical Deep-Dive** | Use when you need the exact technical metadata (ID, class, children) to build a custom automation script. |

---

## 3. High-Stakes Automation Patterns

### A. The "Golden Rule" of Hydration
Modern web apps (React, Vue, etc.) look loaded before they are interactive. 
- **Action**: Always wait **2000ms–4000ms** after a navigation or major click before your next action.
- **Verification**: Never assume an action worked. Use `evaluate` to check the DOM for your changes.

### B. Google Workspace Mastery (Docs, Slides, Sheets)
Google Apps render content on a `<canvas>`, making text invisible to standard tools.
1.  **Targeting**: The input layer is typically a hidden `iframe.docs-texteventtarget-iframe`.
2.  **State Sync**: Direct `.value` changes are ignored by React/Google state. 
3.  **Strategy**: Focus the `iframe` and use `document.execCommand('insertText', false, text)` on its `contentDocument`.
4.  **Blocking Elements**: Always check for and close sidebars (e.g., "Gemini", "Help") before interacting with the main editor.

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
