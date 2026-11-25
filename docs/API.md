# MLX Studio - API

**Date:** 2026-11-25 | **Applies to:** 2.0.9 source (unreleased) | **Security:** [SECURITY.md](SECURITY.md)

## 1. Overview

| API | Address | Use | Contract |
|---|---|---|---|
| App API | `http://127.0.0.1:8765` | the app's own UI | internal, not versioned, may change |
| Model API server | `http://127.0.0.1:8080/v1` | OpenAI-compatible clients | served by `mlx_lm` behind `mlx_guard.py` |

Client setup and server rules for port 8080 are in the [README](../README.md) and
[SECURITY.md](SECURITY.md) section 4.2. They are not repeated here.

### Replies on port 8080

The guard changes one thing in replies. In a tool call, a top-level argument that is `null` is removed when the request's
tool schema declares it, does not require it and gives it a single non-null type.
A call is never left without arguments, and nothing is removed when the rule
cannot be proven (for example a conditional schema, or zero or several tools with
that name). All other output is passed on as the model wrote it. A tool that
gives `null` its own meaning although its schema refuses it gets the argument
left out.

## 2. Authentication and request rules (port 8765)

| Rule | Failure |
|---|---|
| Exactly one `Host` header, `127.0.0.1:<port>` or `localhost:<port>`; `Origin`, if sent, must match (GET and POST) | 403 |
| POST content type is JSON | 415 |
| Every `/api/*` request, GET and POST, carries `X-MLX-Token`. Exceptions: `GET /api/ping` and `POST /api/focus` | 403 |
| Body is valid, has a length, and is at most 8 MiB | 400, 411, 413 |

Every reply has `X-Content-Type-Options: nosniff`. The token is created per run
and placed in `index.html` by the app. Static files, including that page, need no
token, so any local program can read it: it is not user authentication. There are
no user accounts.

Port 8080: images must be `data:` URLs. `http(s)` and other URLs get 400.

## 3. GET endpoints

| Path | Returns |
|---|---|
| `/api/state` | state snapshot; `server.unguarded` is `true` when an adopted API server is running without the request guard |
| `/api/models` | installed models |
| `/api/search?q=` | `mlx-community` search results. Each has `repo`, `downloads`, `params`, `size_gb`, `size_estimated`, `category`, `use`, `fit`. Unknown values are `null`, except `fit`, which is `"unknown"` |
| `/api/logs` | activity entries |
| `/api/logs/files` | log folder and file list |
| `/api/logs/app?limit=` | app log entries (default 500, 1 to 5000) |
| `/api/logs/export` | diagnostic zip |
| `/api/environment` | environment report |
| `/api/catalog` | model catalogue and hardware |
| `/api/versions` | version check result |
| `/api/ping` | `{"ok": true, "app": "<version>"}`; no token needed |
| other paths | static UI files from a startup allowlist |

## 4. POST endpoints

`repo` is a model id string. Where listed as required, a missing value gives 400.

| Path | Body | Errors |
|---|---|---|
| `/api/focus` | - | no token needed |
| `/api/load` | `repo` (required) | 409 busy or being deleted |
| `/api/unload` | - | 409 while generating |
| `/api/chat` | `messages`: non-empty list of `{role, content}` strings | 400, 409 |
| `/api/chat/stop` | - | - |
| `/api/download` | `repo` (required) | 409 already downloading |
| `/api/download/cancel` | `repo` (required) | 404 not downloading |
| `/api/delete` | `repo` (required) | 404, 409, 500 |
| `/api/reveal` | `repo` (required) | 404, 500 |
| `/api/server/start` | `repo` (required), `background` | 409 being deleted |
| `/api/server/stop` | - (also cancels a start that has not launched yet) | - |
| `/api/server/notice/clear` | - | - |
| `/api/install` | `kind` (`reinstall` forces reinstall; other values upgrade) | 409 when an install is already running |
| `/api/logs/clear` | - | 500 |
| other paths | - | 404 |

## 5. Chat stream

`/api/chat` answers with server-sent events named `token`, `done` and `error`, each with JSON
data. See [ADR-004](adr/ADR-004-sse-streaming.md).

## 6. Not applicable

Webhooks, SDKs and published API versions: the app has none.
