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

A start with one model is never refused for memory (in the 2.0.13 source its
prompts can be, [6a](#6a-api-server-prompt-limit)). The app stops the running
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

### Estimate reply

All values are calculated, not measured.

| Field | Meaning |
|---|---|
| `models[]` | `repo`, `weights_bytes`, `safe_context` (tokens, `null` if unknown), `context_limit`. For a pair, `safe_context` is the smaller of `context_tokens` and the model's limit. 2.0.13 source, one model: the limit a server started now would apply ([6a](#6a-api-server-prompt-limit)); 0 means every prompt would be refused, and `context_warning` says so |
| `weights_bytes`, `reply_bytes`, `cache_bytes`, `margin_bytes`, `total_bytes` | the memory sum; `total_bytes` is the need. Reply room is cache bytes per token × context × 6.4 for one model, or × 3.1 for two (largest model only); margin is 1.4 GB; `cache_bytes` is 0 for one model. A pair below 8,192 is summed at 8,192 |
| `budget_bytes` | static = 0.52 × RAM. For a pair with readable free memory: max(0, min(static, `held_bytes` + `free_bytes` − 2 GB)). 2.0.13 source, one model with other apps' memory readable: max(0, 0.80 × RAM − what all but the running server holds). Otherwise static |
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
the start (an unusable `--studio-cache-limit-mb` value is ignored instead).

| Flag | Value |
|---|---|
| `--studio-also-model` | second model id |
| `--studio-max-models` | `1` or `2` (the app sends `2`) |
| `--studio-budget-bytes` | the estimate's `budget_bytes` at start, at least 1 |
| `--studio-kv-tokens` | context per model T, in tokens |

A set user setting adds `--studio-max-context` (ignored while two models run),
`--studio-memory-percent` or `--studio-cache-limit-mb`, with one model too ([6b](#6b-user-settings)).

The app also passes `--prompt-cache-bytes` (`cache_cap_bytes`) then. `mlx_lm`
trims to it only on its batch path, so in two-model mode the guard also sets the
cache's own byte limit, which covers requests run one at a time (for example with
`seed`). A one-model start passes none of the four flags in the table, and has a byte cap
only if `prompt_cache_bytes` is set. If the guard falls back to one model (`multi_off`), the flags were still
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

2.0.13 source (unreleased). A prompt longer than the model's limit on this server
(counted after the chat template, tools included) is answered `400` before
generation:

```json
{"error": {"message": "This model's maximum context length is 8192 tokens on this server. However, your prompt is 9000 tokens. Start a new chat, or let your client compact the conversation to fit.",
  "type": "invalid_request_error", "param": "messages", "code": "context_length_exceeded"}}
```

The limit is calculated, never above the model's own
([formula and figures](ARCHITECTURE.md#62-prompt-limit)). The one-model automatic
figure and a set max context take off the memory other apps held when the server
started. When the result is below 4096 tokens the code is `insufficient_memory`
(`param` stays `messages`), so a client should not compact and retry; the server
log says so once, and `message` is: "This server accepts no prompt for this model.
Its safe context is below 4096 tokens: with it the whole Mac would use more than
80% of this Mac's memory. Memory in use now: 82%. Close other apps (web browsers,
code editors, virtual machines, other model apps), then on the API Server page
press Stop, then Start Server. Or choose a smaller model." An accepted request's
`max_tokens` is lowered to what the prompt leaves of the limit, at least 1.
The percentage is `memory_limit_percent` when that is set. A set
`max_context_tokens` replaces the calculated one-model limit ([6b](#6b-user-settings)).

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
the group size, gets none: the app checks `config.json`, the guard checks the
loaded model as well, runs it in Standard and says so. `GET /api/config` returns
`performance` (`mode`, `reason`, `blocked`); saving a blocked choice for a model
is refused. `/api/state` has `server.performance`: `saved`, and per served model
`saved`, `in_force` (from the server, `null` until it says) and `note`. Memory
limits are the same in every mode.

### Per model

`models` holds overrides for one model id. For each key the value that applies is
the model's own, else the global one, else automatic. `memory_limit_percent` is
global only.

```json
{"memory_limit_percent": 70, "chat_max_reply_tokens": 1024,
 "models": {"org/name": {"max_context_tokens": 8192}}}
```

A model's values apply to a server started with that model; the Chat cap follows
the model asked. A server with two models takes one value for each key: the
stricter (lower) one. A model without a value does not loosen the other's, except
the server reply cap, where it counts as 8192 once either model sets one.
`max_context_tokens` is the exception: the server is given the global value
(`--studio-max-context`) and every model's own (`MLX_STUDIO_MODEL_CONTEXTS`, JSON
`{repo: tokens}`), and the guard applies the one of the model that is loaded, also
after a request switches models. It is not applied while two models run; in the
fallback to one model it is. `mlx_cache_limit_mb` is sent the same way
(`MLX_STUDIO_MODEL_CACHE_LIMITS`), except that two models get the lower of their
own figures as the flag. The card edits and shows the global values only.

Except the Chat values, a change applies at the next server start or **Apply changes**.

The file is read only if it is a regular file of at most 65536 bytes (a link is not
followed). A bad value falls back to its default, and a bad file to all defaults,
with one `warning` line in `app.log`. Unknown keys are ignored. Saving writes the
valid keys with mode `0600`, so unusable and unknown entries are dropped.

Not configurable, on purpose: ports, hosts, the token, the Origin, Host,
Content-Type and CSP checks, file paths and every other security check.

`POST /api/server/settings` takes `{"values": {key: number or null}}` (merged into
the global values), or `{"reset": true}` (drops the global values and keeps
`models`). With `"repo": "org/name"` both act on that model's overrides. 400 names the key and its range, 500
means the file could not be written. `/api/state` adds under `server`: `settings`
(`value`, `min`, `max`, `effective` for each key) and `settings_pending` (the
running server was started with other values) and `model_settings` (the
overrides by model id). The estimate adds
`context_warning`, `capped_by_setting` and `budget_percent`.

The same route takes `{"load_test": {key: value or null}}` (merged; `null` for the
whole block removes it). Saving and resetting other settings keep the block.

| `load_test` key | Default | Range |
|---|---|---|
| `sizes` (offered) | 4096, 8192, 16384, 32768 | 1 to 8 different sizes from 4096 |
| `last_sizes` | empty | as `sizes`, may be empty |
| `reply_tokens` | 256 | 32 to 1024 |
| `rounds` | 2 | 1 to 3 |
| `margin_percent` | 5 | 5 to 20 |
| `timeout_seconds` (per size) | 900 | 60 to 3600 |

## 6c. Configuration and Load Test

2.0.13 source (unreleased); used by the Configuration and Load Test pages. Same token, Host,
Origin and Content-Type rules as every other route.

| Route | Body, reply |
|---|---|
| `GET /api/config?repo=` | `settings` (for each key `value`, `own`, `inherited`, `effective`, `min`, `max`, `decimal`, `global_only`), `applied_context`, `context_message`, `load_test`. Without `repo`: the global values |
| `GET /api/loadtest?repo=` | `status`, `rows`, `results` (stored runs, each with `stale`), `sizes` the model allows, `load_test`, `blocked` |
| `POST /api/loadtest/start` | `{"repo", "sizes"}`: 1 to 8 different sizes from 4096 to the model's limit. 400 bad input or model not installed completely; 409 while the API server is running, starting or adopted, a test runs, an install runs, or the model is downloading or being deleted |
| `POST /api/loadtest/cancel` | `{}`; 404 when no test runs |
| `POST /api/loadtest/apply` | `{"repo", "run_id", "size"}`: saves `max_context_tokens` for the model, only from a stored, passed size of a run that is not stale. 400, 404 or 409 otherwise |

While a test runs, server start, install, and download or delete of that model
answer 409. `/api/state` adds `loadtest` (`active`, `repo`, `run_id`, `status`,
`reason`, `size`, `index`, `of`, `step`, `done`, `total`, `memory_percent`).

`mlx_loadtest.py` starts one guarded server for each size on a free port (never
8765 or 8080), sends generated filler prompts (cold and follow-up, twice by
default) and reports figures. The server of a test is started with a max context
of the largest size plus `reply_tokens`, which the guard lowers to the model's own
limit and the 80% memory ceiling. When the limit the server reports leaves a
follow-up prompt less than `reply_tokens` for its reply, that size is `skipped`
(the run ends as `memory`), not `passed`; this includes a size at the model's own
limit. A run is `done`, `cancelled`, `memory`, `failed` or
`interrupted`; a row is `passed`, `skipped`, `stopped`, `failed`, `cancelled` or
`interrupted`. A size that does not pass ends the run. Results are in
`~/.mlx_studio/loadtest.json` (mode `0600`, the last 5 runs for each model, no
prompt or reply text).

**Memory limits of a test.** The limit L is 80% of RAM, or the lower memory
percent. A load or request is sent only if the projected use is at most L - 5 (75%,
or lower with a wider `margin_percent`). The server is killed at L - 3 (77%), on
critical memory pressure, on swap growth over 0.5 GB, when memory cannot be read,
or when samples (every 0.1 s) are more than 1 s apart. Sampling **cannot guarantee**
that use never passes 80%: a jump between two samples cannot be stopped (one
measured jump was 5 points in 0.54 s). A passed size is evidence for the Mac as it
was during the test, not a promise.

## 7. Not applicable

Webhooks, SDKs and published API versions: the app has none.
