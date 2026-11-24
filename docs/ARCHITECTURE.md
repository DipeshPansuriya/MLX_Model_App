---
type: Architecture
title: "Architecture — MLX Studio"
status: "Built at tag v2.0.0 (2026-11-24)"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.0"
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
the OpenAI-compatible server is a **separate child process** (`mlx_lm server`) on
`127.0.0.1:8080`. All durable state is files: the Hugging Face cache for weights,
and an append-only JSON-lines log for activity. There is no database, no account
system and no network listener beyond loopback.

## 2. Quality goals, in priority order

| # | Goal | Why it ranks here | How it is met |
|---|---|---|---|
| 1 | **Nothing leaves the machine** | The product's premise is local inference | Loopback binds only; no telemetry; network calls only to Hugging Face and PyPI on explicit action or poll |
| 2 | **Install without a toolchain** | Target user may never open a terminal | Stdlib server, no web framework, single-file backend, DMG installer |
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
│  child process: python -m mlx_lm server --port 8080  ──────────────┐ │
└────────────────────────────────────────────────────────────────────┼─┘
                                                                     │
                                        OpenAI-compatible clients ◀──┘
```

| Block | File | Responsibility |
|---|---|---|
| UI | `ui/` | Render state, capture intent, stream tokens |
| HTTP handler | `app.py:509` | Route `/api/*`, serve static assets, emit SSE |
| State | `app.py:214` | Single mutable snapshot behind a lock |
| Workers | `app.py:347–493` | Load, download, install, version check, server pump |
| Inference | `app.py:306` | `stream_generate` over a chat template |
| Log | `app.py:52` | Append JSON lines, read back for the Activity tab |
| Packaging | `packaging/` | Bundle, sign, DMG, manifest |

## 4. Process and thread model

| Execution unit | Count | Lifetime | Blocking work |
|---|---|---|---|
| Main thread | 1 | Process | `webview.start()` event loop |
| HTTP server thread | 1 | Process | `serve_forever()` |
| Request threads | 1 per request | Request | Routing; the chat request thread lives for the whole stream |
| Worker threads | 0–1 each | Task | Load, download, install, version check, server pump |
| `mlx_lm server` child | 0–1 | Until stopped | Its own process, own memory |

Consequences:

- **The UI never blocks.** Every long operation is a worker thread; the UI polls
  `/api/state` every 2 s and streams chat over its own connection.
- **Only one model is resident.** `State.model` is a single slot; a second load
  while one is in flight is refused with `409`.
- **`State` is guarded by a lock** for mutation, but readers take a snapshot
  without holding it for the whole render (`State.snapshot`, `app.py:248`).
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

`installed_models()` (`app.py:159`) walks each `models--*` directory, follows the
snapshot symlinks, and de-duplicates by `(st_dev, st_ino)` so a blob shared by two
models is counted once and a symlink is not counted as a second copy. This is the
fix recorded in [CHANGELOG.md](../CHANGELOG.md): sizes previously read `8.0 GB`
for a `4.0 GB` model.

### 5.2 Activity log (append-only)

`~/.mlx_studio/activity.log`, one JSON object per line:

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

### 5.3 Update manifest (read-only, remote)

`release/latest.json`, fetched when `MLX_STUDIO_UPDATE_URL` is set:

```json
{
  "version": "2.0.0",
  "url": "https://github.com/…/releases/download/v2.0.0/MLX.Studio-2.0.0.dmg",
  "notes": "First packaged release…",
  "published": "2026-11-24T16:37:17Z",
  "sha256": "e42366590bb4cc4e…",
  "size": 979129
}
```

## 6. Request paths

### 6.1 Chat (the hot path)

```
UI send → POST /api/chat {messages}
        → Handler._stream_chat                       app.py:690
        → State.generating = True, stop_flag = False
        → 200 text/event-stream
        → stream_chat(messages)                      app.py:306
            tokenizer.apply_chat_template(add_generation_prompt=True)
            make_sampler(temp=0.7, top_p=0.95)
            stream_generate(model, tokenizer, prompt, max_tokens=2048)
              → yield ("token", text)  → emit("token", text) → UI appends
              → yield ("done", meta)   → emit("done", meta)  → UI prints metrics
        → log_activity("chat", repo, "success", "N tokens @ X tok/s")
        → State.generating = False
```

Stop is a flag, not a kill: `POST /api/chat/stop` sets `State.stop_flag`, the
generator checks it per token, and the partial answer is preserved.

### 6.2 Model load

```
POST /api/load {repo}
  → 409 if another load is in flight
  → 200 immediately {ok, loading: repo}
  → worker_load(repo) on a thread           app.py:347
      mlx_lm.load(repo)
      State.model / tokenizer / loaded_repo set under lock
  → UI observes the transition on its next 2 s poll
```

Load is asynchronous by design: a 16 GB model takes tens of seconds and the HTTP
request must not hold a connection open that long.

### 6.3 API server lifecycle

```
POST /api/server/start {repo}
  → worker_server_start(repo)               app.py:457
      stop_server()                          # idempotent
      Popen([python, -m, mlx_lm, server, --model, repo, --host, 127.0.0.1, --port, 8080])
      pump stdout into State.server_lines[-400:]
  → on exit: State.server_proc = None, one "server stopped" log line

POST /api/server/stop → terminate(), wait(5), kill()
```

The stop is logged exactly once, by the pump that observes the process exit — not
by the stop endpoint. Logging in both places produced duplicate entries and was
fixed during release verification.

### 6.4 State poll

`GET /api/state` returns the whole snapshot: app identity, hardware, Python,
versions, models, loaded, server, downloads, install. The UI re-renders from it
every 2 s. This is deliberately a full snapshot rather than a diff: the payload is
a few KB and idempotent rendering removes a class of stale-state bugs.

## 7. Cross-cutting

| Concern | Approach | Location |
|---|---|---|
| Thread safety | One lock around `State` mutation; snapshot reads | `app.py:214` |
| Error surface | Every failure becomes a JSON `{error}` with a status code, or an SSE `error` event | `Handler` |
| Path safety | Static handler resolves the target and rejects anything outside `ui/` | `app.py:538` |
| Logging | JSON lines, append-only, truncated detail on errors | `app.py:52` |
| Version comparison | Numeric tuple compare, never string compare | `app.py:125` |
| TLS trust | `truststore` injected before PyPI/manifest fetches | `app.py:111` |
| Shutdown | Window close → stop server → `httpd.shutdown()` | `app.py:748` |

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
| [ADR-008](adr/ADR-008-single-model-slot.md) | One resident model |

## 9. Module inventory at `v2.0.0`

| File | Lines | Contents |
|---|---|---|
| `app.py` | 788 | Config, log, helpers, `State`, `stream_chat`, workers, `Handler`, `serve`, `main` |
| `ui/index.html` | 272 | Six views, panels, controls |
| `ui/style.css` | 303 | Tokens, layout, components, responsive rules |
| `ui/app.js` | 555 | API client, router, renderers, SSE reader, wiring |
| `packaging/build_app.sh` | ~170 | Bundle, plist, icon, sign, DMG |
| `packaging/launcher.py` | ~75 | Interpreter discovery, log redirect |
| `packaging/release_manifest.py` | ~95 | Manifest with SHA-256 |

## 10. Constraints and their consequences

| Constraint | Consequence |
|---|---|
| MLX is Apple Silicon only | No cross-platform build; `LSMinimumSystemVersion` 12.0 |
| `mlx_lm` API is not stable | `stream_generate` and `make_sampler` signatures verified per release; version shown in Setup |
| No bundled Python | The bundle locates a system interpreter; a missing one is a dialog, not a crash |
| Loopback only | No remote access by design; no auth needed |
| No database | Log grows unbounded until cleared; acceptable for a desktop tool |
| One process for UI and inference | A hard kill of the app frees model memory with the process |

## 11. Sources read for this file

| Source | Used for |
|---|---|
| `app.py` at `v2.0.0` | Every line reference, state shape, threading, endpoints |
| `ui/app.js`, `ui/index.html` | Poll interval, SSE parsing, view routing |
| `packaging/*` | Bundle layout and release flow |
| `release/latest.json` | Manifest schema |
| `CHANGELOG.md` | The inode and duplicate-log fixes |
| Release verification run, 2026-11-24 | Observed timings and throughput |
