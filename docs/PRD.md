---
type: PRD
title: "Product requirements — MLX Studio"
status: "2.0.7 source (2026-11-25), held by tag v2.0.8; requirements derived from the shipped code, not from a prior specification; unreleased two-model requirement added 2026-11-26, other rows not re-reviewed"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.7"
related:
  - README.md
  - ARCHITECTURE.md
  - DESIGN.md
  - TESTING.md
---

# Product requirements — MLX Studio

> **2.0.13 supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).
>
> In this file **unreleased** means not in 2.0.9: those changes shipped in 2.0.10 to 2.0.13 (latest release, 2026-11-28).

> **Provenance.** This PRD was written *after* the 2.0.0 implementation, from the
> shipped source and the release verification run. It therefore records what the
> product does and what "done" means, rather than specifying a product that was
> then built. Where a requirement is aspirational it is marked **Design** or
> **Open** and listed in §9. Nothing here claims a customer sign-off.

> 2.0.13 source (unreleased): F5, F7 and F8 (load, unload, loaded-model delete
> guard) are replaced. Chat uses the models the running API server serves
> ([ADR-011](adr/ADR-011-chat-through-the-api-server.md)). The text below that
> mentions **Load** or **Unload** describes released builds.

> 2.0.13 source (unreleased) also adds:
>
> | ID | Requirement | Accepted when |
> |---|---|---|
> | C1 | Settings for all models or one, on their own page | Each field shows the effective value; Save stores only changes; Reset clears one entry; invalid values are named and not sent; a lowered context is said |
> | L1 | Load test of one model at chosen sizes | Offered and typed sizes; refused while the API server runs; progress and Cancel; no chosen size built in |
> | L2 | Memory safety, shown plainly | Skip over the gate, kill at the kill line, larger sizes not run; the page says 80% cannot be guaranteed |
> | L3 | Results that can be used | Size, speeds, first-token time, peak memory, settings; earlier runs; Apply only on passed, current rows |
>
> Verified with fakes only; no real model.

## 1. Purpose and premise

Apple Silicon Macs run large language models well because CPU and GPU share one
memory pool, so a model larger than a discrete GPU's VRAM still fits. Apple's
**MLX** framework targets that hardware directly. The obstacle is not the
runtime — it is the absence of a usable surface around it: `mlx_lm` is a CLI,
model weights live in an opaque Hugging Face cache, and there is no way to see
which model is resident, what it answered, or whether the runtime is current.

MLX Studio closes that gap with a desktop application that owns the whole loop:
**find a model → download it → load it → chat with it → serve it over HTTP →
review what happened → keep the runtime current.**

**The test of the premise:** a Mac user who has never opened a terminal can
install the app, download a model, hold a streaming conversation, and point an
OpenAI-compatible client at it — without reading documentation and without a
single `pip` command typed by hand.

## 2. The worked example — the acceptance test of the whole idea

A user installs `MLX.Studio-2.0.0.dmg`, drags the app to Applications, and opens
it. The Setup tab reports MLX `0.32.4` and MLX-LM `0.32.2` as `up to date`. They
open Download, search `qwen2.5-7b`, and download
`mlx-community/Qwen2.5-7B-Instruct-1M-4bit`. They switch to Chat, press **Load**,
watch the pill turn green, type a question, and read the answer as it streams,
ending with `3 tokens · 52.6 tok/s · 4.37 GB peak`. They open API Server, press
**Start**, and `curl http://127.0.0.1:8080/v1/models` returns `200`. The Activity
tab lists every one of those steps with timestamps. They press **Unload** and the
pill returns to `No model loaded`.

Verified end to end on 2026-11-24; see [TESTING.md](TESTING.md) §7.

## 3. Users and roles

| Role | Needs | What the product gives them |
|---|---|---|
| **Local developer** | A private model endpoint for editor and CLI tooling | OpenAI-compatible server on `127.0.0.1:8080/v1`; endpoint copy button; `curl` example |
| **AI tinkerer** | Try models without a Python session | Download browser, one-click load, streaming chat, per-turn throughput |
| **Privacy-conscious user** | Confidence nothing leaves the machine | All inference local; no telemetry; no account; explicit storage paths |
| **Operator of their own Mac** | Know what is installed and whether it is current | Setup tab with versions, update pills, nav badge, app update panel |
| **Maintainer** | Ship a new version and have installed copies notice | `release/latest.json` manifest, `MLX_STUDIO_UPDATE_URL`, DMG asset on GitHub Releases |

There is no multi-user model, no account system and no server-side tenancy. One
person, one Mac, one process.

## 4. Vocabulary

| Term | Meaning |
|---|---|
| **Model** | A quantised weight set plus tokenizer, identified by a Hugging Face repo id such as `mlx-community/Qwen2.5-7B-Instruct-1M-4bit` |
| **Loaded** | Resident in this process's memory, ready to answer |
| **Cached** | Present on disk under the Hugging Face hub cache, not necessarily loaded |
| **Repo id** | `org/name`; the stable key for download, load, delete and logging |
| **Pill** | The coloured status chip in the sidebar and tab headers |
| **Activity entry** | One JSON object per line in `~/.mlx_studio/logs/activity.log` |
| **Log folder** | `~/.mlx_studio/logs/` — activity, app, launch and server logs |
| **Fit rating** | `great` / `good` / `tight` / `too large`, from 4-bit weight size ÷ installed RAM |
| **Manifest** | `release/latest.json`, the app's update channel document |
| **API server** | The `mlx_lm server` subprocess exposing an OpenAI-compatible surface |
| **Background server** | The same subprocess started detached, so it outlives the window |

## 5. Functional requirements and acceptance criteria

### 5.1 Application shell

| ID | Requirement | Acceptance |
|---|---|---|
| F1 | Launch into a native window, not a terminal | A 1360×900 window opens; `pywebview` present. Fallback: default browser at the same URL |
| F2 | Eight destinations reachable without a reload (six in released builds) | API Server, Chat, Models, Download, Configuration, Load Test, Activity, Setup switch instantly (2.0.13 order; the app opens on API Server); the URL hash tracks the active view and browser Back works |
| F3 | Report the app version | Sidebar shows `v2.0.0`; Setup shows version, latest release and channel |
| F4 | Report hardware | Sidebar and Setup show chip and unified memory, e.g. `Apple M6 · 32 GB` |

### 5.2 Model lifecycle

| ID | Requirement | Acceptance |
|---|---|---|
| F5 | Load a cached model in-process | Pill goes grey → pulsing → green with the model name; Load becomes Unload |
| F6 | Indicate load failure | Pill shows `Load failed`; the error text is surfaced; Load is re-enabled |
| F7 | Unload and release memory | `mx.clear_cache()` runs; pill returns to `No model loaded`; history is cleared |
| F8 | Refuse to delete a loaded model | Delete returns `409` with `unload the model first` |
| F9 | Report true on-disk size | Symlinked blobs followed and de-duplicated by inode; a 4 GB model reports `4.0 GB`, not `8.0 GB` |

### 5.3 Chat

| ID | Requirement | Acceptance |
|---|---|---|
| F10 | Stream tokens | Tokens appear incrementally over SSE; no full-response wait |
| F11 | Report per-turn metrics | Each answer ends with `N tokens · X tok/s · Y GB peak` |
| F12 | Cancel generation | Stop interrupts within one token and the partial answer is kept |
| F13 | Refuse to chat with no model | `409 no model loaded`; UI prompts to load first |
| F14 | Keyboard ergonomics | `Enter` sends, `Shift+Enter` newlines, composer grows to a cap |
| F15 | Reset the conversation | New chat clears transcript and history |

### 5.4 Model acquisition

| ID | Requirement | Acceptance |
|---|---|---|
| F16 | Search the `mlx-community` org | Query returns repo ids; empty query returns the catalogue head |
| F17 | Download with visible status | Card shows `running` → `success` or `failed` |
| F18 | Refuse concurrent duplicate downloads | Second request for the same repo returns `409` |
| F18a | Recommend models for this machine | 25 curated models rated `great` / `good` / `tight` / `too large` from measured 4-bit weight size against installed RAM; filterable by band and category |
| F18b | Never offer a model that cannot fit | `too large` cards show a disabled **Needs more RAM** button |

### 5.5 API server

| ID | Requirement | Acceptance |
|---|---|---|
| F19 | Serve OpenAI-compatible endpoints | `/v1/models` returns `200` with the loaded repo id |
| F20 | Show server state | Pill green while running, amber when stopped; nav dot mirrors it |
| F21 | Stream server output | Subprocess stdout appears in the Server tab |
| F22 | Stop cleanly | Terminate, then kill after 5 s; state returns to stopped; logged once |
| F22a | Run in the background | With **Background** ticked the child is detached (`start_new_session`), keeps serving after the window closes, and writes `~/.mlx_studio/logs/server.log` |
| F22b | Survive an app restart | The pid is written to `~/.mlx_studio/server.pid`; the next launch adopts the running server instead of starting a second one |
| F22c | Warn when the server dies | A 2 s watchdog flips state to stopped, shows a red banner with the exit status and a **Restart** button, fires a desktop notification, and logs `api server died` |
| F22d | Keep two models loaded (unreleased, 2.0.10 source) | A second installed model can be added; both answer on one address and take turns. Context per model is at most 32,768 tokens in steps of 1,024, worked out from the smaller of 0.52 × RAM (less the Chat model) and live free memory less 2 GB; the estimate says when it is shorter. **Start Server** and **Apply changes** are refused below 8,192 tokens each. If free memory cannot be read, the 52% rule alone applies and the view says so. A later shortage shows **Free memory is low**; a model that does not fit is loaded alone with **Loaded without enough memory**; a Chat load that does not fit beside the pair is refused. The one-model server is unchanged. Figures are calculated; the real two-model re-test of these limits has not run |
| F22e | One simple API Server card (unreleased, 2.0.12 source) | **Models (max 2)** lists the chosen models with a context figure when known; **+ Add model** opens the searchable list, is off at two with a reason and never offers a model twice. One **Memory: X / Y GB** line, longer text under **Details**; one model shows no two-model text; a refusal or shorter context shows without **Details**. **Start Server** is off with a reason when nothing is chosen or two do not fit; one large model is warned about, not blocked. When the app opens with the server stopped, the last set that reported ready is already chosen (models no longer installed completely left out); nothing starts by itself |
| F22f | Change a running server safely (unreleased, 2.0.12 source) | The list shows what runs; polls, and a start under way, keep edits. **Restart** brings back what last ran. The second click of a double click is ignored. **Apply changes** is enabled only when the set or **Background** differs, says a reply in progress will be interrupted, and restarts the server (no hot swap). A new server that is refused, cannot start, exits or cannot load its first model before it is ready is replaced once by the last set that worked (kept through further applies until a new server is ready, forgotten by **Stop**), with the reason; if that fails too, or the restored server stops later, the stop and both reasons are shown |

### 5.6 Activity log

| ID | Requirement | Acceptance |
|---|---|---|
| F23 | Record every action | Load, unload, chat, download, delete, server, install, update, app launch |
| F24 | Persist across restarts | Entries append to `~/.mlx_studio/logs/activity.log`, one JSON object per line |
| F25 | Filter and colour | Filter by action; green success, red failure, blue running |
| F26 | Clear on request | Clear truncates the activity log after confirmation; `app.log` and `launch.log` are kept |
| F26a | Keep a log folder | `~/.mlx_studio/logs/` holds `activity.log`, `app.log`, `launch.log`, `server.log`; 2 MB rotation with three backups; folder `0700`, files `0600` |
| F26b | Let the user share logs | **Export diagnostics** produces one zip of the log folder plus `environment.json` for a GitHub issue |

### 5.7 Versions and updates

| ID | Requirement | Acceptance |
|---|---|---|
| F27 | Show installed runtime versions | MLX, MLX-LM, huggingface-hub with a pill per row, including an update pill for the hub; Install / Update pins `huggingface-hub` 2.4.0 |
| F28 | Compare against PyPI | `up to date`, `update → x.y.z`, or `missing` |
| F29 | Badge when anything is outdated | `!` on the Setup nav item |
| F30 | Install or update packages | pip runs, output streams into the panel, versions refresh after |
| F31 | Check the app's own release channel | Manifest version newer than `APP_VERSION` shows an update panel with notes and a DMG button |
| F32 | Degrade without a channel | No `MLX_STUDIO_UPDATE_URL` reports `local build`, no false update |

## 6. Decisions applied to the requirements

| Decision | Effect on requirements | Record |
|---|---|---|
| No web framework | Server is `ThreadingHTTPServer`; keeps install to stdlib + mlx | [ADR-001](adr/ADR-001-no-web-framework.md) |
| Web UI in a native window | F1–F4 satisfied without a Tk widget tree | [ADR-002](adr/ADR-002-web-ui-native-window.md) |
| In-process inference, subprocess server | F5–F7 in-process; F19–F22 isolated in a child | [ADR-003](adr/ADR-003-in-process-inference.md) |
| SSE for chat | F10 without polling | [ADR-004](adr/ADR-004-sse-streaming.md) |
| JSON-lines log | F24 append-only, greppable, no database | [ADR-005](adr/ADR-005-jsonl-activity-log.md) |
| Manifest-based app updates | F31 without an update server | [ADR-006](adr/ADR-006-manifest-updates.md) |
| Ad-hoc signing | Ships without an Apple Developer account | [ADR-007](adr/ADR-007-adhoc-signing.md) |

## 7. Non-functional figures

| Attribute | Target | Actual |
|---|---|---|
| Cold start to interactive UI | < 3 s | ~2 s (server binds, UI hydrates on first poll) |
| State poll interval | 2 s | 2 s |
| Chat first token | < 1 s on a 7B 4-bit model | 0.4–0.9 s |
| Throughput, 7B 4-bit, M6 32 GB | > 30 tok/s | 42–53 tok/s observed |
| Peak memory, 7B 4-bit | < 6 GB | 4.37 GB |
| Installer size | < 2 MB | 979 KB |
| Dependencies beyond stdlib | mlx, mlx-lm, huggingface_hub, pywebview | as listed |
| Telemetry | none | none |
| Network use | Hugging Face and PyPI only, on explicit action or poll | as designed |

## 8. Out of scope

| Excluded | Why |
|---|---|
| Fine-tuning, LoRA training, dataset preparation | Different product surface; `mlx_lm lora` remains a CLI concern |
| Multi-user auth, accounts, remote access | Single-user desktop app; server binds loopback only |
| Windows and Linux | MLX is Apple Silicon only |
| Model conversion from other formats | `mlx_lm convert` remains a CLI concern |
| Prompt libraries, RAG, embeddings, vector stores | Not required by the premise |
| Cloud sync of settings or history | No account, no backend |
| Notarised distribution | Requires an Apple Developer account; see [SECURITY.md](SECURITY.md) §5 |
| Auto-download of updates | The app links to the installer; installation stays a user action |

## 9. Open decisions

| ID | Question | Owner | Current behaviour |
|---|---|---|---|
| OD-1 | Developer ID signing and notarisation? | Dipesh | Ad-hoc signed; user allows it once via System Settings → Privacy & Security → Open Anyway (macOS 15+) or right-click → Open (macOS 14−) |
| OD-2 | CI builds the DMG on tag push? | Dipesh | Built locally by `packaging/build_app.sh` |
| OD-3 | Context trimming for long conversations? | Dipesh | Full history sent; `max_tokens=2048` per turn |
| OD-4 | Sampling defaults exposed in the UI? | Dipesh | Fixed at `temp=0.7`, `top_p=0.95` |
| OD-5 | Multiple concurrent loaded models? | Dipesh | Released: one model resident at a time. Unreleased source: the API server can keep up to two; the Chat tab still holds one |
| OD-6 | Where is the manifest hosted long-term? | Dipesh | `raw.githubusercontent.com` on `main` |

## 10. Known limitations

| Limitation | Consequence | Mitigation |
|---|---|---|
| One model resident (Chat tab; also the API server in released builds) | Switching models costs a reload | Unload is explicit and fast; cache is cleared. Unreleased: the API server can keep two |
| No context trimming | Very long chats grow the prompt until the model's window is reached | New chat; OD-3 |
| Chat history is in-memory | Closing the app loses the transcript | Activity log records actions and outcomes, not prompt text |
| Port 8765 and 8080 are fixed | A second instance or another server conflicts | Documented in [RUNBOOK.md](RUNBOOK.md) §5 |
| `mlx_lm server` is a child process | Orphaned on hard kill of the parent | Terminated on window close; a background server is intentional and adopted from the pidfile; [RUNBOOK.md](RUNBOOK.md) §4.4, §4.8 |
| A background server outlives the app | A user may forget it is running | Visible as a `background` pill, pid in `~/.mlx_studio/server.pid`, stop from the Server view |
| Ad-hoc signature | Gatekeeper warning on first launch | Documented install step |

## 11. How we know it works

Acceptance is judged by the release verification run recorded in
[TESTING.md](TESTING.md): a scripted browser session drives load → chat → models →
server → logs → settings → unload, and the packaged DMG is downloaded, mounted,
signature-checked and launched. The run must report `RESULT: PASS` with zero
JavaScript errors before a tag is pushed.

## 12. Risks

| Risk | Likelihood | Impact | Handling |
|---|---|---|---|
| MLX API change breaks `stream_generate` | Medium | High | Version pins reported in Setup; `mlx_lm` pinned by the user's environment |
| Hugging Face rate limits | Medium | Medium | Unauthenticated requests warned; `HF_TOKEN` respected by the library |
| Model larger than free memory | Medium | Medium | Sizing table in README; load failure surfaces the error |
| Port collision | Low | Medium | Documented recovery in the runbook |
| Unsigned build blocked by Gatekeeper | High on first launch | Low | Documented one-time allow: Open Anyway (macOS 15+) or right-click → Open (macOS 14−) |

## 13. Sources read for this file

| Source | Used for |
|---|---|
| `app.py` at tag `v2.0.0` | Endpoints, state shape, limits, ports, log format |
| `ui/index.html`, `ui/app.js`, `ui/style.css` | Screens, states, interaction rules |
| `packaging/build_app.sh`, `packaging/release_manifest.py` | Packaging and release claims |
| `release/latest.json` | Manifest shape |
| Release verification run, 2026-11-24 | §2, §7 figures, §11 |
| `CHANGELOG.md` | Scope of 2.0.0 |
