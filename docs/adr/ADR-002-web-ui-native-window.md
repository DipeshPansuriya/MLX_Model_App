---
type: ADR
title: "ADR-002 — Web UI hosted in a native pywebview window"
status: "Accepted"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../ARCHITECTURE.md
  - ../DESIGN.md
  - ADR-001-no-web-framework.md
---

# ADR-002 — Web UI hosted in a native `pywebview` window

## 1. Decision

The interface is HTML, CSS and JavaScript with no build step, served by the app's
own HTTP server and displayed in a native macOS window via `pywebview`
(WKWebView). If `pywebview` is unavailable, the app opens the same URL in the
default browser.

## 2. Context

An earlier prototype used Tkinter. It worked, but every visual change meant widget
code, and the result could not approach the density and typography of LM Studio or
Ollama. The interface needs: a scrolling transcript, streaming text updates, a
card grid, a data table, colour tokens, and hover states.

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Tkinter / ttk** (the prototype) | No CSS, no flexbox, no card hover, no web typography; every state is imperative widget mutation; the result looked dated, which was the user's explicit complaint |
| **SwiftUI native** | Best platform fidelity, but the whole backend would need rewriting in Swift or bridging to Python, and `mlx_lm` is a Python library |
| **Electron** | Ships a ~150 MB Chromium runtime for a 1 MB app; contradicts the "small installer" goal |
| **Tauri** | Requires a Rust toolchain and a compile step; heavier build than the product justifies |
| **Browser tab only** | Simplest, but no app icon, no dock presence, no window chrome; feels like a web page, not an app |
| **`pywebview` (chosen)** | Uses the system WKWebView (no bundled browser), ~2 MB of dependencies, a real window with an icon, and a graceful browser fallback |

## 4. Consequences

**Good**

- The design language is CSS: tokens, flexbox, grid, transitions, media queries.
  [DESIGN.md](../DESIGN.md) documents it as data rather than widget calls.
- No build step: `index.html`, `style.css` and `app.js` are shipped as written, so
  there is no bundler, no transpiler and no source map.
- The same UI is reachable at `http://127.0.0.1:8765` for debugging and for
  headless verification over CDP.
- The window is a real macOS window: 1360×900, resizable, minimum 1080×720.

**Bad**

- `pywebview` is an extra dependency (`pyobjc` frameworks on macOS). It is
  optional in practice because of the fallback, but its absence degrades the
  experience to a browser tab.
- The UI cannot call Python directly; every interaction is an HTTP round trip.
  This is why `/api/state` returns a full snapshot rather than fine-grained
  events.
- Web-technology constraints apply: no native menus, no system tray, no drag and
  drop of files into the window.
- The window is a web view, so a rendering bug is a browser bug to diagnose.

## 5. Revisit trigger

Reconsider if native macOS integration becomes a requirement (menu bar item,
Services, Spotlight, drag-and-drop of model files), or if the app must run without
a local HTTP server.

## 6. Evidence

| Claim | Location |
|---|---|
| Window creation and fallback | `app.py:768` (`main`) |
| Window size and minimum | `app.py` `create_window(..., width=1360, height=900, min_size=(1080, 720))` |
| No build step | `ui/` contains only `.html`, `.css`, `.js` |
| Static serving | `app.py:560` (`_static`) |
