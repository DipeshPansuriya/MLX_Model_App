---
type: Architecture
title: "Architecture — MLX Studio"
status: "2.0.9 released 2026-11-26; parts marked unreleased describe the working source"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.9"
related:
  - PRD.md
  - DESIGN.md
  - DEPLOYMENT.md
  - SECURITY.md
  - adr/README.md
  - diagrams/README.md
---

# Architecture — MLX Studio

## 1. In one paragraph

One Python process (`app.py`) runs two things: a `ThreadingHTTPServer` on
`127.0.0.1:8765` that serves a single-page UI and a JSON/SSE API, and — when the
user loads a model — the MLX model itself, resident in that same process. The UI
is plain HTML/CSS/JS with no build step, hosted in a native `pywebview` window
that points at the same origin. Inference happens in-process on a worker thread;
the OpenAI-compatible server is a **separate child process** (`mlx_lm` server started through `mlx_guard.py`) on
`127.0.0.1:8080`. All durable state is files: the Hugging Face cache for weights,
and an append-only JSON-lines log for activity. There is no database, no account
system and no network listener beyond loopback.

## 2. Quality goals, in priority order

| # | Goal | Why it ranks here | How it is met |
|---|---|---|---|
| 1 | **Nothing leaves the machine** | The product's premise is local inference | Loopback binds only; no telemetry; network calls only to Hugging Face and PyPI on explicit action or poll |
| 2 | **Install without a toolchain** | Target user may never open a terminal | Stdlib server, no web framework, single-file backend, pkg and DMG installers |
| 3 | **Honest state** | A wrong "loaded" indicator destroys trust | One `State` object is the single source of truth; every surface renders from `/api/state` |
| 4 | **Recoverable** | Ports collide, models fail to load, servers orphan | Explicit unload, terminate-then-kill, documented runbook |
| 5 | **Auditable** | Users ask "what did I send to which model" | Append-only JSON-lines activity log |
| 6 | **Updatable** | Runtime moves fast | PyPI comparison for packages; manifest for the app |

## 3. Building blocks

```
┌──────────────────────────────────────────────────────────────────────┐
│  MLX Studio.app  (pywebview window, 1360×900)                        │
│  ┌────────────────────────────────────────────────────────────────┐  │
│  │  ui/index.html + style.css + app.js   (no build step)          │  │
│  └───────────────────────────┬────────────────────────────────────┘  │
└──────────────────────────────┼───────────────────────────────────────┘
                               │ HTTP + SSE, same origin
┌──────────────────────────────┼───────────────────────────────────────┐
│  app.py  (one Python process)│                                       │
│  ┌───────────────────────────▼────────────────────────────────────┐  │
│  │  Handler(BaseHTTPRequestHandler)  — routes, JSON, SSE, static  │  │
│  └───────┬───────────────────────────────┬────────────────────────┘  │
│          │                               │                           │
│  ┌───────▼─────────┐            ┌────────▼─────────┐                 │
│  │  State (lock)   │            │  worker threads  │                 │
│  │  model, tok,    │◀───────────│  load/download/  │                 │
│  │  loaded_repo,   │            │  install/versions│                 │
│  │  server, logs   │            └────────┬─────────┘                 │
│  └───────┬─────────┘                     │                           │
│          │                      ┌────────▼─────────┐                 │
│  ┌───────▼─────────┐            │  mlx_lm (in-proc)│                 │
│  │  files          │            │  stream_generate │                 │
│  │  activity.log   │            └──────────────────┘                 │
│  │  HF hub cache   │                                                 │
│  └─────────────────┘                                                 │
│                                                                      │
│  child process: python mlx_guard.py … --port 8080   ──────────────┐ │
└────────────────────────────────────────────────────────────────────┼─┘
                                                                     │
                                        OpenAI-compatible clients ◀──┘
```

| Block | File | Responsibility |
|---|---|---|
| UI | `ui/` | Render state, capture intent, stream tokens |
| HTTP handler | `app.py`, class `Handler` | Route `/api/*`, serve static assets, emit SSE |
| State | `app.py`, class `State` | Single mutable snapshot behind a lock |
| Workers | `app.py`, `worker_*` functions | Load, download, install, version check, server pump |
| Inference | `app.py`, `stream_chat` | `stream_generate` over a chat template |
| Log | `app.py`, `log_app`, `log_activity` | Append JSON lines, read back for the Activity tab |
| Packaging | `packaging/` | Bundle, sign, DMG, pkg, manifest, launcher |

## 4. Process and thread model

| Execution unit | Count | Lifetime | Blocking work |
|---|---|---|---|
| Main thread | 1 | Process | `webview.start()` event loop |
| HTTP server thread | 1 | Process | `serve_forever()` |
| Request threads | 1 per request | Request | Routing; the chat request thread lives for the whole stream |
| Worker threads | 0–1 each | Task | Load, download, install, version check, server pump |
| Guarded `mlx_lm` server child | 0–1 | Until stopped | Its own process, own memory |

Consequences:

- **The UI never blocks.** Every long operation is a worker thread; the UI polls
  `/api/state` every 2 s and streams chat over its own connection.
- **The app process holds one model.** `State.model` is a single slot; a second
  load while one is in flight is refused with `409`. The server child holds one
  model in 2.0.9; the unreleased source can keep two (section 6).
- **`State` is guarded by a lock** for mutation, but readers take a snapshot
  without holding it for the whole render (`State.snapshot`).
- **The server is deliberately out-of-process** so a crashed or wedged server
  cannot take the UI with it, and so its memory is reclaimable by killing a PID.

## 5. Data model

There is no database. Three file-backed stores:

### 5.1 Hugging Face cache (read/write, owned by the library)

```
~/.cache/huggingface/hub/
└── models--<org>--<name>/
    ├── blobs/           # content-addressed weights (shared)
    ├── refs/            # branch → commit
    ├── snapshots/<sha>/ # symlinks into ../../blobs
    └── trees/
```

`installed_models()` walks each `models--*` directory, follows the
snapshot symlinks, and de-duplicates by `(st_dev, st_ino)` so a blob shared by two
models is counted once and a symlink is not counted as a second copy. This is the
fix recorded in [CHANGELOG.md](../CHANGELOG.md): sizes previously read `8.0 GB`
for a `4.0 GB` model.

A download started in the app counts as complete once the files MLX reads are
present, or a newer revision is complete.

### 5.2 Activity log (append-only)

`~/.mlx_studio/logs/activity.log`, one JSON object per line:

```json
{"timestamp":"2026-11-24 22:08:38","action":"chat","model":"mlx-community/Qwen2.5-7B-Instruct-1M-4bit","status":"success","detail":"3 tokens @ 52.6 tok/s"}
```

| Field | Type | Values |
|---|---|---|
| `timestamp` | string | `YYYY-MM-DD HH:MM:SS`, local time |
| `action` | string | `chat`, `model`, `download`, `delete`, `server`, `install`, `update`, `app`, `raw` |
| `model` | string | repo id, or a component name for `install`/`update` |
| `status` | string | `running`, `success`, `failed`, `info`, `stopped` |
| `detail` | string | free text, truncated to 300 chars on error paths |

Append-only by construction: `log_activity` opens in `"a"` mode and writes one
line. `read_activity` returns the last N entries and tolerates malformed lines by
wrapping them as `raw`.

### 5.2a Log folder

All four logs share `~/.mlx_studio/logs/` and one writer, `_append`, which rotates
at 2 MB into three numbered backups and forces `0600` on every write:

| File | Written by | Contents |
|---|---|---|
| `activity.log` | `log_activity` | One JSON object per user-visible action |
| `app.log` | `log_app` | `level` + `message` + fields: start/stop, HTTP ≥ 400, model load failure, download failure, install result, `api server started` / `died` / `stopped`, diagnostic export |
| `launch.log` | `packaging/launcher.c` | Timestamped launcher steps and the child's stdout. Unreleased: an unwritable file is moved to `launch.log.1`, and parallel launches take turns |
| `server.log` | guarded `mlx_lm` server (background only) | Raw server output, since a detached child has no pipe to the UI |

`build_diagnostic_bundle()` zips the folder with a generated `environment.json`
(versions, paths, hardware, installed model ids) and streams it from
`GET /api/logs/export`. The bundle is written on demand and never uploaded.

Logs from 2.0.0 that sat directly in `~/.mlx_studio/` are moved into the folder by
`_migrate_legacy_logs()` on the first start after upgrade.

### 5.3 Update manifest (read-only, remote)

`release/latest.json`, fetched when `MLX_STUDIO_UPDATE_URL` is set:

```json
{
  "version": "x.y.z",
  "url": "https://github.com/…/releases/download/vx.y.z/MLX.Studio-x.y.z.pkg",
  "notes": "…",
  "published": "<timestamp>",
  "sha256": "<checksum of the pkg>",
  "size": 0
}
```

## 6. Request paths

### 6.1 Chat (the hot path)

```
UI send → POST /api/chat {messages}
        → Handler._stream_chat
        → State.generating = True, new State.stop_event
        → 200 text/event-stream
        → stream_chat(messages)
            tokenizer.apply_chat_template(add_generation_prompt=True)
            make_sampler(temp=0.7, top_p=0.95)
            stream_generate(model, tokenizer, prompt, max_tokens=2048)
              → yield ("token", text)  → emit("token", text) → UI appends
              → yield ("done", meta)   → emit("done", meta)  → UI prints metrics
        → log_activity("chat", repo, "success", "N tokens @ X tok/s")
        → State.generating = False
```

Stop is an event, not a kill: `POST /api/chat/stop` sets `State.stop_event`, the
generator checks it per token, and the partial answer is preserved.

### 6.2 Model load

```
POST /api/load {repo}
  → unreleased, repo not loaded yet:
      400 if it is not installed completely
      409 if it does not fit beside a two-model API server (section 6.3)
  → 200 {ok, already: true} if it is already loaded
  → 409 if a load or reply is in progress, or it is being deleted
  → 200 immediately {ok, loading: repo}
  → worker_load(repo) on a thread
      release the old model
      unreleased: rewrite server.budget (two-model server only)
      mlx_lm.load(repo)
      State.model / tokenizer / loaded_repo set under lock
  → UI observes the transition on its next 2 s poll
```

Load is asynchronous by design: a 16 GB model takes tens of seconds and the HTTP
request must not hold a connection open that long.

### 6.3 API server lifecycle

```
POST /api/server/start {repo, background}
  → worker_server_start(repo, background)
      stop_server()                          # idempotent
      child command: mlx_guard.py, HF_HUB_OFFLINE=1
      unreleased: env also sets MLX_STUDIO_RESIDENT_FILE and MLX_STUDIO_BUDGET_FILE
      foreground: Popen(..., stdout=PIPE)    # pump into State.server_lines[-400:]
      background: Popen(..., stdout=server.log, start_new_session=True)
      write ~/.mlx_studio/server.pid {pid, repo, background, started_at, port, started}
      spawn _watch_server(pid, repo, proc)

POST /api/server/stop → terminate(), wait(5), kill(); pidfile cleared
```

**Reply change.** The guard wraps `mlx_lm`'s `ToolCallFormatter._format`, which
serves streaming and non-streaming replies. In a tool call, a top-level argument that is `null` is removed when the request's
tool schema declares it, does not require it and gives it a single non-null type.
A call is never left without arguments, and nothing is removed when the rule
cannot be proven (for example a conditional schema, or zero or several tools with
that name). All other output is passed on as the model wrote it. A tool that
gives `null` its own meaning although its schema refuses it gets the argument
left out.

**Two models (unreleased, 2.0.10 source).** When a second model is chosen, one
server process keeps up to two models in `mlx_lm`'s own `ModelProvider`; there is
no router or second port.

| Part | Behaviour |
|---|---|
| Slots | The guard wraps `ModelProvider._load` and `load_default`; the least recently used model goes first. The server starts on the first model and preloads the second only if both fit |
| Turns | `mlx_lm` loads only when nothing is generating, so a request for the other model waits for the running replies, then switches |
| Admission | Checked when a model is loaded: held memory (MLX active), plus the new weights, plus the largest reply room (3.1 × cache bytes per token × context), plus 1.4 GB margin (1 GB + 0.4 GB process overhead) must fit the budget. With both slots full the least recently used model goes first. If trimming the prompt cache alone then closes the gap, the cache is trimmed and the other model stays; else that model is evicted too, and the cache is trimmed to what fits. The guard measures and plans again after each eviction or trim. A model that still does not fit is loaded alone, with a warning. A switch between two loaded models is not checked |
| Free memory | `free_bytes()` in `mlx_guard.py` runs `/usr/bin/vm_stat` (no shell, 2 s timeout) and adds the free, speculative, purgeable and file-backed pages. Any failure gives no figure, and the live check is skipped |
| Budget at start | App: static = 0.52 × RAM less the Chat model's weights; live = a running server's active memory + free − 2 GB. The smaller one is used (static alone without a figure) and passed as `--studio-budget-bytes` |
| Budget later | Guard: the smallest of the start budget, the `server.budget` file and its active memory + free − 2 GB. Worked out at each non-hit load, at the second preload and at each refresh. It never exceeds the start budget |
| Context per model (T) | T = min(32,768, (budget − weights − 1.4 GB) / (5.1 × KV per token), rounded down to 1,024). KV per token is the larger of the two models' (131,072 bytes if unreadable). Below 8,192 the pair is refused; between 8,192 and 32,767 it starts shrunk and the UI says so. T is fixed until a restart and passed as `--studio-kv-tokens` |
| Chat model beside it | `POST /api/load` answers 409 when either check fails (`chat_load_problem`): the server's memory plus the Chat weights above 0.52 × RAM, or the Chat weights plus what the server may still take above free − 2 GB. The Chat model's own reply cache is not counted |
| Prompt cache | Cap of 2 × KV × T bytes, passed to `mlx_lm` as `--prompt-cache-bytes`. `mlx_lm` trims to it on the batch path; the guard also sets the cache's own byte limit (`max_bytes`), which applies at every insert. A one-model start passes no byte cap |
| Preload | The guard writes `preload_done` to the status file when preload ends, whatever the result (`finally`) |
| Free memory low | Only after `preload_done` and with a measurement, the snapshot sets `server.memory_low = {free_bytes, short_by_bytes}` when need − 0.4 GB − active is more than free − 2 GB. The app caches the free figure for 10 s. No automatic unload. `server.context_tokens` is set for any two-model server and does not depend on events |
| Release | An evicted model object is emptied (`release`), because the generation thread still refers to it until the next load returns |
| Fallback | If the hooked places in `mlx_lm` have moved, including the cache's byte limit and `ResponseGenerator._next_request`, one model is kept and the reason is reported. The request guard still fails closed |
| Status | The guard writes `server.resident.json` with `context_tokens` (T), `free_bytes`, memory figures, `preload_done` and events. Besides loads, switches, evictions and trims, it refreshes the figures from `mlx_lm`'s request loop (`_next_request`) at most every 10 s, without an event. A reply served one at a time holds that loop, so no refresh runs during it. The app reads the file only for the running process id |
| Shared formula | The app imports `mlx_guard.py` for its estimate with bytecode writing turned off, to protect the signed bundle. A lock covers this import |

The one-model server is unchanged: no cache byte cap, no live memory check and no
memory refusal. The real two-model re-test of these limits has not run yet; the
figures are calculated, and cache trim, eviction, switch time and swap with the
new limits are not measured.
Design: [ADR-010](adr/ADR-010-two-models-one-process.md). Budget limits:
[SECURITY.md](SECURITY.md) section 5.

**Down detection.** `_watch_server` polls every 2 s. For a server this process
started it polls the `Popen` handle, because a detached child stays a zombie and
`os.kill(pid, 0)` still succeeds on a zombie. For a server adopted from a pidfile
it falls back to the pid probe. On death it clears state, removes the pidfile,
sets `server_down_notice`, and logs `api server died` with the exit code. The UI
raises a banner and a desktop notification from that field.

**Restart safety.** A restart installs a successor before the old watcher wakes,
so the watcher only acts when `State.server_pid` still equals its own pid, and
`_clear_server_pidfile(only_pid=…)` refuses to delete a pidfile that describes a
different process. Without both guards a restart produced a false
"stopped unexpectedly" banner and orphaned the new pidfile.

**Adoption.** `restore_server_state()` runs at startup: if the pidfile names a
live pid it is adopted, so a background server survives an app restart instead of
colliding on port 8080.

The foreground stop is logged once, by the pump that observes the process exit —
not by the stop endpoint. Logging in both places produced duplicate entries and
was fixed during release verification.

### 6.4 State poll

`GET /api/state` returns the whole snapshot: app identity, hardware, Python,
versions, models, loaded, server, downloads, install. The UI re-renders from it
every 2 s. This is deliberately a full snapshot rather than a diff: the payload is
a few KB and idempotent rendering removes a class of stale-state bugs.

## 7. Cross-cutting

| Concern | Approach | Location |
|---|---|---|
| Thread safety | One lock around `State` mutation; snapshot reads | class `State` |
| Error surface | Every failure becomes a JSON `{error}` with a status code, or an SSE `error` event | `Handler` |
| Path safety | Static files come from an allowlist built at startup ([SECURITY.md](SECURITY.md) §4.1) | `_build_static_allowlist`, `Handler._static` |
| Logging | JSON lines, append-only, truncated detail on errors | `log_app`, `log_activity` |
| Version comparison | Numeric tuple compare, never string compare | `is_newer` |
| TLS trust | `truststore` injected before PyPI/manifest fetches | `latest_pypi_version`, `latest_app_version` |
| Shutdown | Window close → stop server → `httpd.shutdown()` | `main` |

## 8. Decisions

| ADR | Decision |
|---|---|
| [ADR-001](adr/ADR-001-no-web-framework.md) | Stdlib `ThreadingHTTPServer`, no Flask/FastAPI |
| [ADR-002](adr/ADR-002-web-ui-native-window.md) | Web UI in a `pywebview` window, browser fallback |
| [ADR-003](adr/ADR-003-in-process-inference.md) | Inference in-process; API server as a child process |
| [ADR-004](adr/ADR-004-sse-streaming.md) | SSE for chat, 2 s polling for state |
| [ADR-005](adr/ADR-005-jsonl-activity-log.md) | JSON-lines log, no database |
| [ADR-006](adr/ADR-006-manifest-updates.md) | Manifest-based app updates |
| [ADR-007](adr/ADR-007-adhoc-signing.md) | Ad-hoc code signature |
| [ADR-008](adr/ADR-008-single-model-slot.md) | One resident model in the app process |
| [ADR-009](adr/ADR-009-api-server-guard.md) | Request guard on the API server |
| [ADR-010](adr/ADR-010-two-models-one-process.md) | API server keeps two models in one process (unreleased, 2.0.10) |

## 9. Module inventory

| File | Contents |
|---|---|
| `app.py` | Config, logs, `State`, `stream_chat`, workers, `Handler`, `serve`, `main` |
| `mlx_guard.py` | Request guard that starts the API server ([ADR-009](adr/ADR-009-api-server-guard.md)) |
| `ui/index.html` | Views, panels, controls |
| `ui/style.css` | Tokens, layout, components, responsive rules |
| `ui/app.js` | API client, router, renderers, SSE reader, wiring |
| `packaging/build_app.sh` | Bundle, plist, icon, sign, DMG, pkg |
| `packaging/launcher.c` | Interpreter discovery, log redirect |
| `packaging/release_manifest.py` | Release manifest and checksums |
| `scripts/install.sh` | One-line installer |

## 10. Constraints and their consequences

| Constraint | Consequence |
|---|---|
| MLX is Apple Silicon only | No cross-platform build; `LSMinimumSystemVersion` 12.0 |
| `mlx_lm` API is not stable | `stream_generate` and `make_sampler` signatures verified per release; version shown in Setup |
| No bundled Python | The bundle locates a system interpreter; a missing one is a dialog, not a crash |
| Loopback only | No remote access by design. Local processes can still reach both ports: `/api/*` on 8765 needs a session token (readable from `/` in 2.0.9, no longer served in the unreleased source), 8080 has no authentication ([SECURITY.md](SECURITY.md) §3) |
| No database | Log grows unbounded until cleared; acceptable for a desktop tool |
| One process for UI and inference | A hard kill of the app frees model memory with the process |

## 11. Sources read for this file

| Source | Used for |
|---|---|
| `app.py`, `mlx_guard.py` | State shape, threading, endpoints, server lifecycle |
| `ui/app.js`, `ui/index.html` | Poll interval, SSE parsing, view routing |
| `packaging/*` | Bundle layout and release flow |
| `release/latest.json` | Manifest schema |
| `CHANGELOG.md` | The inode and duplicate-log fixes |
| Release verification run, 2026-11-24 | Observed timings and throughput |
