---
type: ADR
title: "ADR-001 — No web framework: stdlib ThreadingHTTPServer"
status: "Accepted"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../ARCHITECTURE.md
  - ADR-002-web-ui-native-window.md
---

# ADR-001 — No web framework: stdlib `ThreadingHTTPServer`

## 1. Decision

The backend HTTP surface is implemented on `http.server.ThreadingHTTPServer` with
a single `BaseHTTPRequestHandler` subclass. No Flask, FastAPI, Starlette, Bottle or
Django is used.

## 2. Context

The server must serve four static files, fifteen JSON endpoints and one SSE
stream, on loopback, for one user. It runs inside a desktop app bundle that the
user installs from a DMG. Every third-party dependency is therefore a dependency
the user must already have, or one the installer must ship.

`pywebview` already pulls in `bottle` transitively, so a framework was technically
available for free. The question was whether to depend on it.

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Flask** | Adds a dependency and a request-context model for four static files and fifteen routes; nothing in the app needs blueprints, sessions, templating or extensions |
| **FastAPI + uvicorn** | Brings an ASGI server, `pydantic` and `starlette`; requires async handlers throughout, which complicates the threading model around a blocking `stream_generate` |
| **Bottle** (already present via pywebview) | Available, but relying on another package's transitive dependency is fragile: a pywebview change could remove it silently |
| **`http.server` (chosen)** | Verbose routing, but zero dependencies, full control of the SSE write loop, and no framework lifecycle to reconcile with `webview.start()` |

## 4. Consequences

**Good**

- The only runtime dependencies are `mlx`, `mlx-lm`, `huggingface_hub` and
  `pywebview`. Nothing else can break the install.
- The SSE handler writes directly to `self.wfile` and flushes per token; no
  framework buffering sits between the generator and the socket.
- Startup is a bind and a thread — no framework boot, no config discovery.
- The whole backend is one readable file.

**Bad**

- Routing is a hand-written `if path == …` chain in `do_GET`/`do_POST`
  (`app.py:555`, `app.py:583`). Adding endpoints means editing two methods.
- No automatic JSON serialisation, validation or error handling; each handler
  parses its own body and picks its own status code.
- No middleware layer, so cross-cutting concerns (logging, auth if ever added)
  must be applied by hand.
- `ThreadingHTTPServer` is not a production server; it is correct here only
  because the client is one local browser.

## 5. Revisit trigger

Adopt a framework if any of these become true: authentication is added, the
endpoint count passes roughly forty, streaming needs backpressure or
multiplexing, or the server must bind beyond loopback.

## 6. Evidence

| Claim | Location |
|---|---|
| Server construction | `app.py:741` (`serve`) |
| Handler class | `app.py:509` |
| Route dispatch | `app.py:555`, `app.py:583` |
| SSE write loop | `app.py:690` |
