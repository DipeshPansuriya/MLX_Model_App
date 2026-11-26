# MLX Studio - API

**Date:** 2026-11-25 | **Applies to:** 2.0.9 (released 2026-11-26); parts marked unreleased describe the working source | **Security:** [SECURITY.md](SECURITY.md)

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

Every reply has `X-Content-Type-Options: nosniff`. In 2.0.9 the token is created
per run and placed in `index.html` by the app. Static files, including that page,
need no token, so any local program can read it: it is not user authentication.
There are no user accounts.

**Unreleased (2.0.10 source):**

- The token is not served. The app opens its window at `/#t=<token>`; static files
  carry no token.
- Every `Origin` header sent must match, not only the first.
- A POST needs exactly one `Content-Type` header, and it must be JSON (`415`).
- Every reply also carries `Content-Security-Policy` ([SECURITY.md](SECURITY.md)
  section 4.1).
- The refused-token message is `invalid session token; open MLX Studio again from
  its icon`.

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
| `/api/focus` | - | no token needed. Unreleased: in browser mode it opens a new tab, at most once every 3 seconds |
| `/api/load` | `repo` (required) | 409 busy or being deleted; unreleased: 400, 409 (section 5) |
| `/api/unload` | - | 409 while generating |
| `/api/chat` | `messages`: non-empty list of `{role, content}` strings | 400, 409 |
| `/api/chat/stop` | - | - |
| `/api/download` | `repo` (required) | 409 already downloading |
| `/api/download/cancel` | `repo` (required) | 404 not downloading |
| `/api/delete` | `repo` (required) | 404, 409, 500 |
| `/api/reveal` | `repo` (required) | 404, 500 |
| `/api/server/start` | `repo` (required), `background`; unreleased: `also` | 409 being deleted; unreleased: 400, 409 (section 5) |
| `/api/server/estimate` | unreleased: `repo` (required), `also` | 400 |
| `/api/server/stop` | - (also cancels a start that has not launched yet) | - |
| `/api/server/notice/clear` | - | - |
| `/api/install` | `kind` (`reinstall` forces reinstall; other values upgrade) | 409 when an install is already running |
| `/api/logs/clear` | - | 500 |
| other paths | - | 404 |

## 5. Two models (unreleased, 2.0.10 source)

### Request fields

`also` is optional: a list with at most one model id.

| Answer | When |
|---|---|
| 400 | `also` is not a list of strings, holds more than one name, repeats `repo`, or, with a second name, either model is not installed completely |
| 400 | `/api/server/estimate` with `also` empty: `repo` is not installed completely |
| 409 | `/api/server/start`: the pair does not fit the memory budget at 8,192 tokens each (the reply includes `estimate`), or a model is being deleted |

### `/api/load`

| Answer | When |
|---|---|
| 400 | `repo` is not installed completely: for example an unknown name, a folder path or a partial download. This is checked before the busy checks |
| 409 | A two-model server is running, and the Chat model's weights plus the server's memory would pass 0.52 of RAM **or** the live free memory check fails. The message gives the figures and says to stop the API server or choose a smaller model. The loaded Chat model stays; there is no override |
| 200 `already` | `repo` is the Chat model already loaded; neither check above applies |

The memory 409 does not apply when the server is stopped, serves one model, or
reports `multi_off`.

### Estimate reply

All values are calculated, not measured.

| Field | Meaning |
|---|---|
| `models[]` | `repo`, `weights_bytes`, `safe_context` (tokens, `null` if unknown), `context_limit` |
| `weights_bytes`, `reply_bytes`, `cache_bytes`, `margin_bytes`, `total_bytes` | the memory sum; `cache_bytes` is 0 for one model |
| `budget_bytes` | the smaller of 0.52 × RAM (less the Chat model's weights if loaded) and live free memory (free + speculative + purgeable + file-backed − 2 GB) |
| `free_bytes` | live free memory in bytes, or `null` if `vm_stat` unreadable |
| `held_bytes` | memory held by a running server (weights + cache); 0 if no server |
| `fits` | whether the models can be kept loaded together (T ≥ 8,192 and admission plan admits) |
| `refused` | refusal message with figures when `fits` is false |
| `context_tokens` | context per model T (1,024-step, capped at 32,768) |
| `cache_cap_bytes` | prompt cache cap = 2 × KV × T |
| `free_is_limit` | true when live free memory is the limiting factor (not the 52% rule) |

### New fields in `/api/state` under `server`

| Field | Meaning |
|---|---|
| `repos` | models the server was started with; the first equals `repo` |
| `resident` | models in memory now, the current one first |
| `memory` | `budget_bytes`, `active_bytes`, `peak_bytes`, `measured`, or `null`; updated at a model load or switch, not after each reply |
| `events` | the last 5 model events: load, switch, cache trim, eviction, load over budget, skipped preload, failed load; refresh events are not recorded |
| `oversize` | models loaded alone although they do not fit the budget; empty list if none |
| `multi_off` | why only one model is kept, when the installed `mlx_lm` no longer matches; else `null` |
| `memory_low` | `null` or `{free_bytes, short_by_bytes}` when free memory dropped below need |

### Guard flags on port 8080

The app adds these to the server command only when a second model is chosen. The
guard reads them and does not pass them to `mlx_lm`; a bad or repeated flag stops
the start.

| Flag | Value |
|---|---|
| `--studio-also-model` | second model id |
| `--studio-max-models` | `1` or `2` (the app sends `2`) |
| `--studio-budget-bytes` | memory budget at start, at least 1 |
| `--studio-kv-tokens` | context per model T, in tokens |

The app also passes `--prompt-cache-bytes` (2 × KV × T bytes) then. `mlx_lm` trims to it only
on its batch path, so in two-model mode the guard also sets the cache's own byte
limit, which covers requests run one at a time (for example with `seed`). At each
load the guard reads the file in `MLX_STUDIO_BUDGET_FILE` and uses the lower of it
and the start budget ([DATABASE.md](DATABASE.md)). Clients keep using one base URL
and choose by the `model` field. Models take turns.

## 6. Chat stream

`/api/chat` answers with server-sent events named `token`, `done` and `error`, each with JSON
data. See [ADR-004](adr/ADR-004-sse-streaming.md).

## 7. Not applicable

Webhooks, SDKs and published API versions: the app has none.
