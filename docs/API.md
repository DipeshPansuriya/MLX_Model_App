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
| `/api/server/start` | `repo` (required), `background`; unreleased: `also`. Replaces a running server | 409 being deleted; unreleased: 400, 409 (section 5) |
| `/api/server/estimate` | unreleased: `repo` (required), `also` | 400 |
| `/api/server/stop` | - (also cancels a start that has not launched yet) | - |
| `/api/server/notice/clear` | - (unreleased: clears `down_notice` and `apply_notice`) | - |
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
| 409 | `/api/server/start` with a pair: it does not fit at 8,192 tokens each. `error` is the refusal text and `estimate` is the estimate reply below |
| 409 | `/api/server/start`: a model is being deleted |
| 400 | 2.0.12 source: `/api/server/start` with one model: it is not installed completely. Nothing is launched, and a running server is left alone |

A start with one model is never refused for memory. The app stops the running
server before the new one starts and then checks memory again. If the pair is
refused at that point, the call has already answered 200 and `/api/state` tells
the outcome.

### Replacing a running server (unreleased, 2.0.12 source)

A start that replaces a server remembers a fallback: the models and mode of the
last server that was ready, else of the running one. A further start before the new
server is ready keeps that fallback. It is started again, once, when the new server:

| Case | Detected |
|---|---|
| Is refused for memory after the stop, cannot be spawned, or the start raises an unexpected error (a process already spawned is stopped) | At once |
| Exits before it is ready | By the watcher, every 2 s |
| Cannot load its first model (still running) | `load-failed` or `preload_done` with nothing resident; the process is stopped |

Ready means a model is resident and, for two, `preload_done` is `true` (not needed
with `multi_off`). A server still running 10 minutes after its start counts as
ready. `/api/server/stop` forgets the fallback. Nothing is started again after it,
while a newer start waits, or when there is no fallback and no server was running.

| Outcome | Field |
|---|---|
| Previous server started again | `apply_notice`: **The new server did not start: … was started again.** |
| That failed too | `down_notice`: both reasons, ending **The API server is stopped.** |
| The restored server stops later | `down_notice`: the `apply_notice` text followed by **API server for … stopped unexpectedly**; `apply_notice` is `null`. Nothing more is started |
| No earlier server, and the new one cannot be started | `down_notice`: **The API server did not start: …** (a memory refusal keeps its own text) |

Three further fields under `server`: `apply_notice` (text or `null`), `starting`
(`true` while a requested start has not launched yet) and `last_models` (up to two
ids of the last server that reported ready, else `[]`). `apply_notice` is cleared
by the next start, by stop and by `/api/server/notice/clear`.

`last_models` is read from `~/.mlx_studio/server.last.json`, written when the
watcher sees the ready report within 10 minutes of the start (not by the 10 minute
rule above, and not after a failed load). It may name a model that has since been
removed; the page leaves those out of the card and of **Restart**, and
`/api/server/start` refuses them. Reading it starts nothing. Anything but a regular
file of at most 4 KB holding printable ids of at most 200 characters reads as `[]`.

### `/api/load`

| Answer | When |
|---|---|
| 400 | `repo` is not installed completely: for example an unknown name, a folder path or a partial download. This is checked before the busy checks |
| 409 | A two-model server is running and either check below fails. The message gives the figures. The loaded Chat model stays; there is no override |
| 200 `already` | `repo` is the Chat model already loaded; neither check above applies |

The memory 409 does not apply when the server is stopped, serves one model, or
reports `multi_off`. The two checks, in order:

| Check | Refused when | Message starts |
|---|---|---|
| Fixed share | server memory + Chat weights > 0.52 × RAM. Server memory is the larger of the pair's calculated need and the measured use plus reply room and margin | `not enough memory: the API server keeps two models` |
| Live free memory | Chat weights + max(0, need − server `active_bytes`) > free − 2 GB. Free memory is read afresh; without a measurement, `active_bytes` counts as 0. Skipped if free memory cannot be read | `not enough free memory:` |

The old Chat model's memory is not counted as free, so replacing one Chat model
with another may be refused until `/api/unload`.

### Estimate reply

All values are calculated, not measured.

| Field | Meaning |
|---|---|
| `models[]` | `repo`, `weights_bytes`, `safe_context` (tokens, `null` if unknown), `context_limit`. For a pair, `safe_context` is the smaller of `context_tokens` and the model's limit |
| `weights_bytes`, `reply_bytes`, `cache_bytes`, `margin_bytes`, `total_bytes` | the memory sum; `total_bytes` is the need. Reply room is 3.1 × cache bytes per token × context, largest model only; margin is 1.4 GB; `cache_bytes` is 0 for one model. A pair below 8,192 is summed at 8,192 |
| `budget_bytes` | static = 0.52 × RAM, less the Chat model's weights if one is loaded or loading. For a pair with readable free memory: max(0, min(static, `held_bytes` + `free_bytes` − 2 GB)). Otherwise static |
| `free_bytes` | free memory in bytes (cached up to 10 s for this route, read afresh for a start). `null` if `vm_stat` cannot be read, and always for one model, where it is not read |
| `held_bytes` | measured `active_bytes` of the running server, which a restart gives back; 0 if there is none, for one model, or without `free_bytes` |
| `context_tokens` | pair: context per model T, a multiple of 1,024, at most 32,768; below 8,192 (down to 0) means refused. One model: 32,768 |
| `cache_cap_bytes` | pair: 2 × cache bytes per token × T (at least 8,192), using the larger of the two models' bytes per token (131,072 if a config cannot be read). 0 for one model |
| `fits` | pair: T is at least 8,192 and the guard's admission rule accepts both. A start refuses a pair when this is `false`. One model: `false` if the guard's rule would count it as not fitting; it is still started |
| `refused` | pair that does not fit: the refusal text. Otherwise `null` |
| `free_is_limit` | `true` when `held_bytes` + `free_bytes` − 2 GB is below the static budget; `false` for one model or unreadable free memory |

Refusal text, with free memory readable and the static budget large enough:
`Not enough free memory for two models: X GB free, Y GB needed for 8K each. ...`.
Y is the need at 8,192 plus the 2 GB reserve, less `held_bytes`; X is rounded down
and Y up, to 0.1 GB. Otherwise:
`Not enough memory for two models: X GB allowed for the API server, Y GB needed for 8K each. ...`.

### New fields in `/api/state` under `server`

| Field | Meaning |
|---|---|
| `repos` | models the server was started with; the first equals `repo` |
| `resident` | models in memory now, the current one first |
| `memory` | `budget_bytes`, `active_bytes`, `peak_bytes`, `measured`, or `null`. Updated at a model load or switch; with two models also at evictions, trims and about every 10 s from `mlx_lm`'s request loop |
| `events` | the last 5 events, by name: `hit`, `load`, `evict`, `trim`, `alone`, `load-failed`, `preload-skipped`. The periodic refresh adds none |
| `oversize` | models loaded alone although they do not fit the budget; empty list if none |
| `multi_off` | why only one model is kept, when the installed `mlx_lm` no longer matches; else `null` |
| `context_tokens` | context per model of a running two-model server: the guard's figure, or the one the app started it with. `null` for one model or with `multi_off`. It does not depend on `events` |
| `preload_done` | `true` once the guard has finished preloading, however that ended (both loaded, second skipped, or a load failed); else `false` |
| `memory_low` | `null` or `{free_bytes, short_by_bytes}`. Stays `null` until `preload_done` is `true`, without a measurement, if free memory cannot be read, for one model and with `multi_off` |

`short_by_bytes` is (need − 0.4 GB − `active_bytes`) − (`free_bytes` − 2 GB), and
`memory_low` is set when that is above 0. Need is the larger of the pair asked for
and the pair loaded now, at the running context. The 0.4 GB process overhead is
taken off because the running process already uses it. Nothing is unloaded
because of this field.

### Guard flags on port 8080

The app adds these to the server command only when a second model is chosen. The
guard reads them and does not pass them to `mlx_lm`; a bad or repeated flag stops
the start.

| Flag | Value |
|---|---|
| `--studio-also-model` | second model id |
| `--studio-max-models` | `1` or `2` (the app sends `2`) |
| `--studio-budget-bytes` | the estimate's `budget_bytes` at start, at least 1 |
| `--studio-kv-tokens` | context per model T, in tokens |

The app also passes `--prompt-cache-bytes` (`cache_cap_bytes`) then. `mlx_lm`
trims to it only on its batch path, so in two-model mode the guard also sets the
cache's own byte limit, which covers requests run one at a time (for example with
`seed`). A one-model start passes none of these flags, so it has no byte cap from
the app. If the guard falls back to one model (`multi_off`), the flags were still
passed, so `mlx_lm` receives the cache cap but the guard sets no byte limit.

At each load of a model that is not in memory, at the preload of the second model
and at each periodic refresh, the guard works out its budget as the smallest of:
the start budget, the number in the `MLX_STUDIO_BUDGET_FILE` file
([DATABASE.md](DATABASE.md)), and its own active memory plus free memory minus
2 GB (skipped, with one line on stderr, if `vm_stat` cannot be read). It never
goes above the start budget. A switch between loaded models loads nothing and is
not checked. Clients keep using one base URL and choose by the `model` field.
Models take turns.

## 6. Chat stream

`/api/chat` answers with server-sent events named `token`, `done` and `error`, each with JSON
data. See [ADR-004](adr/ADR-004-sse-streaming.md).

## 7. Not applicable

Webhooks, SDKs and published API versions: the app has none.
