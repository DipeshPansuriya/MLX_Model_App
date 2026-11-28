# MLX Studio - API

> **2.0.13 (unreleased):** MLX is `mlx_lm`'s own server on port 8080. The request guard,
> the 80% memory rule, two models, memory estimates and the settings
> `mlx_cache_limit_mb`, `two_model_context_tokens` and `memory_limit_percent` are
> removed. Where a section below still describes them, it applies to 2.0.12 and
> earlier. [What is no longer protected](../README.md#protections-removed-in-2013).

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
| `/api/chat` | `repo` (a served model), `messages`: non-empty list of `{role, content}` strings | 400, 409 (section 6) |
| `/api/chat/stop` | - | - |
| `/api/download` | `repo` (required) | 409 already downloading |
| `/api/download/cancel` | `repo` (required) | 404 not downloading |
| `/api/delete` | `repo` (required) | 404, 409, 500 |
| `/api/reveal` | `repo` (required) | 404, 500 |
| `/api/server/start` | `repo` (required), `background`; unreleased: `also`. Replaces a running server | 409 being deleted; unreleased: 400, 409 (section 5) |
| `/api/server/estimate` | unreleased: `repo` (required), `also` | 400 |
| `/api/server/stop` | - (also cancels a start that has not launched yet) | - |
| `/api/server/notice/clear` | - (unreleased: clears `down_notice` and `apply_notice`) | - |
| `/api/server/settings` | unreleased: `values` or `reset`; optional `repo` ([6b](#6b-user-settings)) | 400, 500 |
| `/api/ui/settings` | 2.0.14: `theme` ([6b](#6b-user-settings)) | 400, 500 |
| `/api/server/test` | 2.0.14: `{}`; sends the ready server a short reply and one tool call (never run) and returns `reply` and `tool`, each with `status` (`pass`, `warn`, `fail`, `skipped`) and `detail`; no key in the answer A test longer than 20 s answers 202 `{"running": true}`; ask `GET /api/server/test` until it returns the result. | 409 not ready, load test or test running, or another program on the port; GET: 404 before any test |
| `/api/autostart/default` | 2.0.14: `repo`, `engine`, `anthropic`; saves the default model (an MLX default switches start at login off) and returns the status | 400 types; 409 cannot be served |
| `/api/autostart` | 2.0.14: `enabled` (true or false); writes or removes the login item, starts nothing now. `GET /api/autostart` returns the status; no key in any answer | 400; 409 no default, MLX (no password), not the installed app |
| `/api/autostart/clear` | 2.0.14: `{}`; switches off and forgets the default model | 409, 500 |
| `/api/install` | `kind` (`reinstall` forces reinstall; other values upgrade) | 409 when an install is already running |
| `/api/logs/clear` | - | 500 |
| other paths | - | 404 |

## 5. One model (2.0.13, unreleased)

`POST /api/server/start` takes `{repo, background, engine, anthropic}`. A non-empty
`also` gets `400` ("the server serves one model"). `/api/server/estimate` and
`/api/loadtest/apply` are removed, as are the memory, resident-model and prompt-limit
fields of `/api/state`.

## 6. Chat stream

2.0.13 source (unreleased). `/api/chat` asks the running API server
(`POST /v1/chat/completions` on 8080, no `Origin`, no token) with `stream`,
`max_tokens` 2048, `temperature` 0.7 and `top_p` 0.95. The app loads no model.

409 before the stream: the server is stopped, starting or restarting, could not
load its model, does not serve `repo`, or a reply is already running.

| Event | Data |
|---|---|
| `status` | `{"state": "waiting"}` about once a second while nothing arrives; `{"state": "prompt", "done", "total"}` from the server's keepalive |
| `reasoning` | text from the server's `delta.reasoning` |
| `token` | text from `delta.content` |
| `done` | `tokens`, `tps`, `elapsed`, `stopped`, `truncated` (length limit), `repo`, `stats` |
| `error` | the reason, including the server's own refusal text |

`stats` holds `first_s` (request sent to the first reply or thinking text, this
app's clock), `reply_tokens` and `prompt_tokens` (the server's `usage`), `tps`
(`reply_tokens` over the seconds from the first text to the end) and `mode` (what
the server reports for the model). Each is `null` when unknown, for example after
**Stop**, when the server sends no counts. The browser sends only `role` and
`content` back as history.

Stop, or a browser that goes away, shuts the connection to the server.
See [ADR-011](adr/ADR-011-chat-through-the-api-server.md) and
[ADR-004](adr/ADR-004-sse-streaming.md).

## 6a. API server prompt limit

Removed in 2.0.13. The MLX server has no prompt limit; OptiQ drops older text beyond
the max context.

## 6b. User settings

2.0.13 source (unreleased). Optional limits in `~/.mlx_studio/settings.json`, a
JSON object, also edited on the **Configuration** page. An absent, `null`
or empty key is automatic: the behaviour without the file. All values are whole
numbers except `chat_temperature` and `chat_top_p`, which also take decimals.

| Key | Default when absent | Range | Effect when set |
|---|---|---|---|
| `max_context_tokens` | Calculated from memory ([6a](#6a-api-server-prompt-limit)) | 4096 to 100000000 | One model: the refusal limit, never above the model's own. It may be above the automatic figure, but never above what fits in the memory ceiling (80% of RAM, or the memory percent) by the same formula: a larger value is lowered to that figure, and the card says which figure applies. It does not count other apps' memory, so it does not by itself keep use under 80% |
| `two_model_context_tokens` | Calculated, at most 32768 | 8192 to 32768 | Two models: a cap on the context for each, rounded down to a step of 1024. It only lowers it |
| `memory_limit_percent` | 52% share for models, 80% ceiling | 30 to 80 | Share becomes the lower of 52 and this; ceiling becomes this. It only lowers them. A low figure can leave no room, and then every prompt is refused, also with `max_context_tokens` set |
| `server_max_reply_tokens` | 8192 | 64 to 131072 | `--max-tokens`: the reply cap for a request that sends none |
| `server_reasoning` | the model's own | `"on"` or `"off"` | `--chat-template-args` with `enable_thinking`; both engines. A model without a thinking switch ignores it |
| `chat_max_reply_tokens` | 2048 | 64 to 131072 | `max_tokens` of a Chat request. Applies to the next message. A request value, so it can be above the server's default |
| `chat_temperature` | 0.7 | 0 to 2 | `temperature` of a Chat request. Applies to the next message |
| `chat_top_p` | 0.95 | 0.05 to 1 | `top_p` of a Chat request. Applies to the next message |
| `prompt_cache_size` | 2 | 1 to 100 | `--prompt-cache-size` |
| `prompt_cache_bytes` | Calculated: 2 × cache bytes per token × the context limit | 100000000 to 100000000000 | `--prompt-cache-bytes`. Only lowers the calculated cap. With one model the guard sets the cache's own byte limit |
| `mlx_cache_limit_mb` | MLX default (nothing is set) | 0 to 1000000 | `--studio-cache-limit-mb`: `mx.set_cache_limit`, the free buffer memory MLX keeps for reuse, in decimal MB (10^6 bytes). 0 keeps none. It is not a limit on the model or its cache. One model: a model's own figure applies each time that model is loaded, else the global one, else MLX's default comes back. Two models: one process-wide figure, the lower of the pair's, set at start. An unusable flag value is ignored; if MLX has no such call, the server log says so and the server starts. Also used by the load test's server. Effect on speed and memory is UNVERIFIED |
| `performance_mode` | Standard: no flag | `standard`, `save_memory`, `advanced` | `save_memory` passes `--kv-bits 8 --prefill-step-size 256`; `advanced` passes whichever of the next three are set; `standard` passes none and ignores them |
| `kv_bits` | off | 4 or 8 | `--kv-bits`, Advanced only. 4 gave broken replies in testing. MLX also knows 1, 2, 3, 5, 6: in mlx_lm 0.32.2 a 5 or 6 bit cache fails, and 1 to 3 are coarser than 4, so none is offered. Compression starts at 5,000 tokens; requests are not batched |
| `kv_group_size` | 64 | 32, 64, 128 | `--kv-group-size`, Advanced only. The model's head size must divide by it |
| `prefill_step_size` | 2048 | 64 to 2048 | `--prefill-step-size`, Advanced only. Not above the default: the automatic prompt limit was measured there |

### Performance flags

The global flags are on the server's command line; a model that differs is sent in
`MLX_STUDIO_MODEL_PERFORMANCE` and applied when it is loaded, in one or two model
mode. A model with a sliding-window cache, or whose head size does not divide by
the group size, gets none: the app checks `config.json` and uses Standard. `GET /api/config` returns
`performance` (`mode`, `reason`, `blocked`); saving a blocked choice for a model
is refused. `/api/state` has `server.performance`: `saved`, and per served model
`saved`, `in_force` (the mode the ready server was started with, else `null`)
and `note`.

### Per model

`models` holds overrides for one model id. For each key the value that applies is
the model's own, else the global one, else automatic.

```json
{"chat_max_reply_tokens": 1024,
 "models": {"org/name": {"max_context_tokens": 8192}}}
```

A model's values apply to a server started with that model; the Chat cap follows
the model asked. `max_context_tokens` is passed to OptiQ only.

Except the Chat values, a change applies at the next server start or **Apply changes**.

The file is read only if it is a regular file of at most 65536 bytes (a link is not
followed). A bad value falls back to its default, and a bad file to all defaults,
with one `warning` line in `app.log`. Unknown keys are ignored. Saving writes the
valid keys with mode `0600`, so unusable and unknown entries are dropped. The `ui`
block (below) is the exception: it is written back as it is.

Not configurable, on purpose: ports, hosts, the token, the Origin, Host,
Content-Type and CSP checks, file paths and every other security check.

**Appearance (2.0.14).** `POST /api/ui/settings` takes `{"theme": "system" | "light" |
"dark"}`; 400 for any other key or value. It is saved in the `ui` block of the same
file and `/api/state` returns it as `ui`. An unusable saved value means `system`.
Resets keep the block.

`POST /api/server/settings` takes `{"values": {key: number or null}}` (merged into
the global values), or `{"reset": true}` (drops the global values and keeps
`models`). With `"repo": "org/name"` both act on that model's overrides. 400 names the key and its range, 500
means the file could not be written. `/api/state` adds under `server`: `settings`
(`value`, `min`, `max`, `effective` for each key) and `settings_pending` (the
running server was started with other values) and `model_settings` (the
overrides by model id).

The same route takes `{"load_test": {key: value or null}}` (merged; `null` for the
whole block removes it). Saving and resetting other settings keep the block.

| `load_test` key | Default | Range |
|---|---|---|
| `sizes` (offered) | 4096, doubled up to 1048576 | 1 to 12 different sizes from 4096; a saved list stays as saved |
| `last_sizes` | empty | as `sizes`, may be empty |
| `reply_tokens` | 256 | 32 to 1024 |
| `rounds` | 2 | 1 to 3 |
| `timeout_seconds` (per size) | 900 | 60 to 3600; a size above 32768 always gets 3600 |

## 6c. Configuration and Load Test

2.0.13 source (unreleased); used by the Configuration and Load Test pages. Same token, Host,
Origin and Content-Type rules as every other route.

| Route | Body, reply |
|---|---|
| `GET /api/config?repo=` | `settings` (for each key `value`, `own`, `inherited`, `effective`, `min`, `max`, `decimal`, `global_only`, now always false), `performance`, `load_test`. Without `repo`: the global values |
| `GET /api/loadtest?repo=` | `status`, `rows`, `results` (stored runs, each with `stale`), `sizes` the model allows, `load_test`, `blocked` |
| `POST /api/loadtest/start` | `{"repo", "sizes"}` and optional `"thinking"` (true or false; default on where `GET /api/loadtest` says `thinking.supported`, sent as `chat_template_kwargs.enable_thinking`; `true` is a 400 where it is not): 1 to 12 different sizes from 4096 to the model's limit. 400 bad input or model not installed completely; 409 while the API server is running, starting or adopted, a test runs, an install runs, or the model is downloading or being deleted |
| `POST /api/loadtest/cancel` | `{}`; 404 when no test runs |

While a test runs, server start, install, and download or delete of that model
answer 409. `/api/state` adds `loadtest` (`active`, `repo`, `run_id`, `status`,
`reason`, `size`, `index`, `of`, `step`, `done`, `total`, `memory_percent`).

`mlx_loadtest.py` starts the engine's own server for each size on a free port (never
8765 or 8080), sends generated filler prompts (cold and follow-up, twice by
default) and reports figures. At the model's own limit the prompts stop
`reply_tokens` short of it. A run is `done`, `cancelled`, `memory`, `failed` or
`interrupted`; a row is `passed`, `skipped`, `stopped`, `failed`, `cancelled` or
`interrupted`. A size that does not pass ends the run. Results are in
`~/.mlx_studio/loadtest.json` (mode `0600`, the last 5 runs for each model, no
prompt or reply text).

**Stops of a test.** There is no memory limit and no estimate: every size is tried.
The test server is killed on critical memory pressure, on swap growth over 0.5 GB,
when memory cannot be read, when samples (every 0.1 s) are more than 1 s apart, on
the size's time limit, or after 120 s without a byte of a reply. A passed size is
evidence for the Mac as it was during the test, not a promise.

## 7. Not applicable

Webhooks, SDKs and published API versions: the app has none.

## Engine routes

Same session token, Host and Origin checks as the other routes.

| Route | Body or query | Reply |
|---|---|---|
| `GET /api/engines` | `?repo=` | each engine with `eligible` and `reason` |
| `POST /api/engines/install`, `/api/engines/remove` | `{"engine": "optiq"}` | `{"ok": true}`; 400 other engines, 409 while in use |
| `POST /api/server/key` | `{}` | `{"key": ...}` only while our keyed server alone holds the port; else 409 |
| `POST /api/server/start` | also `"engine"` (absent: `mlx`) and `"anthropic"` (OptiQ, default on) | 400 unknown engine, 409 refused with the reason |
| `POST /api/loadtest/start` | also `"engine"` | as above |

`GET /api/state` adds `server.engine`, `server.anthropic`, `server.engines`,
`server.last_engine`. The server key is never in it. See [ENGINES.md](ENGINES.md).
