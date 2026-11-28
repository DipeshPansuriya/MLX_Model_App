---
type: Runbook
title: "Runbook — MLX Studio"
status: "Written for the 2.0.7 source (2026-11-25); guard, process and unreleased notes added 2026-11-26; other steps not re-reviewed"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.7"
related:
  - DEPLOYMENT.md
  - SECURITY.md
  - TESTING.md
---

# Runbook — MLX Studio

> **2.0.13 (unreleased) supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).

Operator procedures for the desktop app. Every command is safe to copy.

## 1. Start and stop

### 1.1 Normal start

```bash
open "/Applications/MLX Studio.app"
```

or from a checkout:

```bash
cd "/Users/dipesh/Desktop/Dipesh/Project Idea/MLX_Model_App"
./run.sh
```

Expected: a 1360×900 window on the API Server page (2.0.13), sidebar pill
`● No model served` (released: `● No model loaded`), Setup reporting the
installed MLX versions.

### 1.2 Normal stop

Close the window. The app stops the API server, shuts down the HTTP server and
exits. Both ports are released.

### 1.3 Verify it is running

```bash
curl -s http://127.0.0.1:8765/api/ping
# {"ok": true, "app": "2.0.0"}
```

## 2. Health checks

| Check | Command | Healthy |
|---|---|---|
| App server | `curl -s http://127.0.0.1:8765/api/ping` | `{"ok": true, …}` |
| State | `curl -s http://127.0.0.1:8765/api/state \| python3 -m json.tool` | full snapshot |
| API server | `curl -s http://127.0.0.1:8080/v1/models` | `200` with repo id |
| Ports | `lsof -nP -iTCP:8765 -iTCP:8080 -sTCP:LISTEN` | `127.0.0.1` only |
| Processes | `pgrep -fl "app.py\|mlx_guard.py\|mlx_lm server"` | one app, at most one server |
| Activity | `tail -5 ~/.mlx_studio/logs/activity.log` | recent JSON lines |
| Launch log | `tail -20 ~/.mlx_studio/logs/launch.log` | no traceback |

## 3. Common tasks

### 3.2 Chat with a model

2.0.13 source (unreleased): start the API server (API Server page), wait for
**Ready** on the Chat tab, then type. **Stop** on the API Server page frees the
memory. Released builds load a model on the Chat tab instead (**Load**,
**Unload**).

### 3.3 Download a model

Download tab → search → **Download**. Status goes `running` → `success`. Weights
land in `~/.cache/huggingface/hub/models--<org>--<name>/`.

### 3.4 Start the OpenAI-compatible server

API Server tab → **+ Add model** → choose one → **Start Server**. In the
unreleased 2.0.12 source the models that last started are already chosen after the
app opens; delete `~/.mlx_studio/server.last.json` to open with an empty list.

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Qwen2.5-7B-Instruct-1M-4bit",
       "messages":[{"role":"user","content":"Hello!"}]}'
```

Point DeepSeek Harness or another OpenAI-compatible client at `http://127.0.0.1:8080/v1` with any non-empty API
key.

Since 2.0.10, to keep two models loaded add a second one, then read the memory
line, any warning and **Details** before **Start Server**. Set the client's context
window to the shown safe context or lower. The limits are
calculated and have not been re-tested with real models, so watch memory pressure.

2.0.13 source (unreleased), one model: the safe context follows the memory other
apps hold. Below 4096 tokens the server refuses every prompt and the card and
server log say so, with the memory in use. Close other apps (web browsers, code
editors, virtual machines, other model apps), then on the API Server page press
**Stop**, then **Start Server** (the figure is read at start only). Or choose a
smaller model. A **Max context** set on the Configuration page follows the same
80% rule, other apps included: it is lowered to what fits, and below 4096 tokens
nothing is accepted. There is no override.

| You see | Meaning and what to do |
|---|---|
| **Shorter context: about …K tokens per model** | The pair starts, or runs, with a context shorter than 32,768. If **Close other apps for more** follows, free memory is the limit: close apps, then **Start Server** |
| **This model may not fit** or **Shorter context: about …K tokens** (one model) | A warning only; the model can still be started. Choose a smaller one if the server stops |
| **Not enough free memory for two models** | Less than 8,192 tokens each would fit. Close apps until the "needed" figure is free, or start one model |
| **Not enough memory for two models** | The 52% rule is the limit, or free memory could not be read. Choose smaller models or start one model |
| **Free memory could not be read** | `/usr/bin/vm_stat` failed; only the fixed 52% rule applies. Watch memory pressure |
| **Free memory is low** | Other apps took memory the server counted on. Close apps, or restart the server for a smaller context. Nothing is unloaded just for this; the next model load may trim or evict |
| **Loaded without enough memory** | A model was loaded on its own although it does not fit. Free memory or choose a smaller model |
| **Keep prompts to … tokens or fewer** | Set the client's context to that figure or lower. The prompt cache holds about 2T tokens in total; above that, older prompts are dropped and read again. A longer conversation may push the other model's cache out, or be read again on every turn |
| A client gets `insufficient_memory` (2.0.13) | The server accepts no prompt: close other apps, then **Stop**, then **Start Server**, or choose a smaller model |
| A client gets `context_length_exceeded` (2.0.13) | The prompt is over the limit on the API Server card (set it on **Configuration**). Start a new chat or let the client compact |

The context and the budget are set at start. After closing apps, restart the
server to get more.

**Changing a running server** (unreleased, 2.0.12 source): edit the list or
**Background**, then **Apply changes**. The server restarts, so a reply in progress
is interrupted.

| You see | Meaning and what to do |
|---|---|
| **Changes not applied** | The new server did not start; the last working models were started again. Read the reason, change the choice or free memory, apply again |
| **API server stopped**, naming two reasons | Neither the new nor the previous models started. Fix the reasons (memory, model files, Setup), then **Restart** or **Start Server**. **Restart** may retry the failed new set, not the previous one: check the list on the card. **Background** mode is not remembered after the app is closed: tick it again if you want it |
| **API server stopped**: **… was started again**, then **stopped unexpectedly** | The restored server stopped as well; nothing more is started. Check the server log and free memory, then **Restart** or **Start Server** |
| **Apply changes** is off | Nothing differs from what runs, no model is listed (add one or **Stop**), or two do not fit |

### 3.5 Update the runtime

Setup tab → **Check for updates** → **Install / Update**. Output streams into the
panel; versions refresh afterwards. It installs `mlx`, `mlx-lm` and
`huggingface-hub` 2.4.0, keeps newer versions, and the hub row shows an update
pill. Equivalent CLI on a machine without newer versions:

```bash
python3 -m pip install mlx==0.32.4 mlx-lm==0.32.2 huggingface-hub==2.4.0
```

### 3.6 Check for an app update

Setup tab → **Check for updates**. With `MLX_STUDIO_UPDATE_URL` set, a newer
manifest shows the update panel with notes and a **Download installer** button
(the published manifest URL is the `.pkg`).

### 3.7 After an update

If the app shows **server unprotected**, open API Server and press **Restart now**.
The older background server then restarts with the same model and mode, behind
the request guard.

### 3.7a Run a load test safely

2.0.13 source (unreleased). One 4096-token run on OptiQ has passed with a real model;
treat any other size or MLX as a trial.

1. Save your work and close apps you do not need. Their memory counts: more free
   memory means more sizes run.
2. Keep the Mac awake, plugged in and otherwise idle. Sleep ends the test as
   interrupted.
3. **API Server** page: **Stop**. On **Load Test** the **Stop API server** button
   does the same. The app never stops it for you.
4. Pick the model. Tick the smallest size first; add larger ones after it passes.
   Leave the run values and the 5% margin as they are.
5. **Start test**. Watch **Memory now**; open Activity Monitor's Memory tab too.
6. Stop at once with **Cancel test** if the Mac slows or memory pressure turns
   yellow or red.
7. Apply only a size that **passed**. A pass covers one model, this configuration
   and a short run, not two models or hours of use.

| You see | Meaning and what to do |
|---|---|
| **Stopped for memory safety**, row **skipped** | Nothing was sent: the projection was over the gate. Close apps or use a smaller size or model |
| Row **stopped**, memory reached N% | The test server was killed at the kill line. Do not retry that size now |
| **Test failed** | One reason is shown; the last log lines are in the app log |
| **Test interrupted** | The app quit, the Mac slept or memory sampling gapped. Run again |
| **stale** | Settings, software or the model changed. Run again before applying |
| Start is off | The line above it says why: server running, install, download or delete |

Sampling cannot guarantee memory stays under 80%: a sudden jump can pass the kill
line before the next sample. Results are in `~/.mlx_studio/loadtest.json`.

### 3.7b Compare performance modes (not yet run on a real model)

Set the mode on **Configuration**, **Save**, then **Stop** and **Start Server**
(or **Apply changes**). Chat names the mode in force. If replies are garbled or
fail, choose **Standard (recommended)**, save and apply again. The server log line
`mlx_guard: <model>: Standard is used, ...` means that model cannot compress.

## 4. Recovery procedures

### 4.1 The window does not appear

```bash
tail -40 ~/.mlx_studio/logs/launch.log
```

| Log says | Cause | Fix |
|---|---|---|
| `no Python 3.10+ interpreter found` | No suitable Python | Install from python.org or `brew install python` |
| `cannot find …/app.py` | Bundle damaged | Reinstall the app |
| `ModuleNotFoundError: mlx` | Runtime missing | Setup tab → Install, or `pip install mlx mlx-lm` |
| `ModuleNotFoundError: webview` | pywebview absent | `pip install pywebview`, or let it fall back to the browser |

Unreleased: **"This page has no session"** means the page was opened without the
app (typed address or bookmark). Open MLX Studio from its icon. In browser mode a
second launch opens a new tab with a session; whether an icon click on the running
app causes that was not checked live. If no tab opens, quit the app and open it
again (quitting stops a server that is not in **Background**).

### 4.2 Port 8765 is in use

```bash
lsof -nP -iTCP:8765 -sTCP:LISTEN
```

Another MLX Studio instance is running. Quit it, or:

```bash
lsof -ti:8765 | xargs kill -9
```

### 4.3 Port 8080 is in use

```bash
lsof -nP -iTCP:8080 -sTCP:LISTEN
lsof -ti:8080 | xargs kill -9
```

Common causes: a previous `mlx_lm server`, Ollama, or another local model server.

### 4.4 An orphaned API server

The app starts the server through `mlx_guard.py`; a server from an older build
shows as `mlx_lm server`. Check the list before killing anything.

```bash
pgrep -fl "mlx_guard.py|mlx_lm server"
pkill -f "mlx_guard.py|mlx_lm server"
```

The app terminates its child on window close; an orphan means the parent was
killed hard (`kill -9`).

### 4.5 A model will not load

| Symptom | Cause | Fix |
|---|---|---|
| `Load failed`, pill red | Weights incomplete | Re-download the model |
| Out of memory | Model too large for free RAM | Stop the API server, close apps, choose a smaller model |
| `409 another model is loading` | A load is in flight | Wait for the poll to settle |
| Missing tokenizer files | Partial snapshot | Delete the model and download again |

### 4.6 The activity log is corrupt

The reader tolerates bad lines by wrapping them as `raw`. To start clean:

```bash
mv ~/.mlx_studio/logs/activity.log ~/.mlx_studio/logs/activity.log.bak
```

The file is recreated on the next action.

### 4.7 Gatekeeper blocks the app

These steps were written for the DMG. How current macOS treats a downloaded
unsigned `.pkg` is UNVERIFIED.

**Symptom.** After dragging the app to `/Applications` and double-clicking it:

> **"MLX Studio" Not Opened** — Apple could not verify "MLX Studio.app" is free of
> malware that may harm your Mac or compromise your privacy.

**Cause.** The bundle is ad-hoc signed and not notarised by Apple
([ADR-007](adr/ADR-007-adhoc-signing.md)). The DMG is downloaded through a
browser, so macOS tags it `com.apple.quarantine`; on first launch
`syspolicyd`/`AppleSystemPolicy` refuses the process because there is no
Developer ID team and no notarisation ticket. Confirmed in the unified log:

```
(AppleSystemPolicy) ASP: Security policy would not allow process: 6048,
  /Applications/MLX Studio.app/Contents/MacOS/MLXStudio
```

`spctl -a -vvv -t exec "/Applications/MLX Studio.app"` returns `rejected`, and
`codesign --verify --deep --strict` returns `valid on disk` — the signature is
intact, it just is not a signature Apple trusts for distribution.

**Fix — allow it once.**

| macOS | Route |
|---|---|
| **15 Sequoia or newer** (incl. 26, 27) | **System Settings → Privacy & Security → Security → Open Anyway**, authenticate, confirm **Open** |
| **14 Sonoma or older** | Right-click the app → **Open** → **Open** |

Control-click → Open was removed in macOS 15
([Apple, Aug 2024](https://developer.apple.com/news/?id=saqachfa)), so on current
systems the right-click route shows no **Open** button. `spctl --add` and
`spctl --master-disable` are also no longer supported (`This operation is no
longer supported`, rc=4).

**Fix — prevent it instead of allowing it afterwards.** The quarantine flag comes
from the browser, not from the app. Two routes never set it:

```bash
# 1. the installer: downloads, checks sha256, installs, clears quarantine
curl -fsSL https://raw.githubusercontent.com/DipeshPansuriya/MLX_Model_App/main/scripts/install.sh | bash

# 2. clear the flag on the DMG before installing
xattr -dr com.apple.quarantine ~/Downloads/MLX.Studio-2.0.3.dmg
```

Verified: both produce an app with no `com.apple.quarantine` attribute, and it
launches with `GET /api/ping` returning `{"ok": true, "app": "2.0.3"}`.

**Fix — clear it after install (any macOS version).**

```bash
xattr -dr com.apple.quarantine "/Applications/MLX Studio.app"
open "/Applications/MLX Studio.app"
```

Verified: after the strip, `open` starts the app, `GET /` on
`http://127.0.0.1:8765/` returns `200`, and `/api/state` reports
`"version": "2.0.3"`.

**What does not work** (measured, [ADR-007](adr/ADR-007-adhoc-signing.md) §7):

- `brew install --cask` from a tap — Homebrew applies quarantine and the app is
  rejected. `HOMEBREW_CASK_OPTS=--no-quarantine` was ignored, and a
  `quarantine false` stanza is not a valid cask directive.
- A self-signed certificate, even trusted in the login keychain — `spctl` still
  returns `rejected` with `origin=…`.
- `spctl --add`, `spctl --master-disable` — no longer supported.

**Permanent fix.** Sign with a Developer ID and notarise (PRD OD-1). Until then
every browser download shows the prompt once.

### 4.8 The API server died

**Symptom.** The Server view shows a red banner — *"API server for <model>
stopped unexpectedly (exited with status N)"* — and a desktop notification
fires.

**Cause.** The server child exited. Common reasons: out of memory while
loading the model, a port clash on 8080, or the process was killed. Unreleased:
the message names both models and adds "killed, possibly out of memory" when the
process was killed. With two models, one crash stops both.

**Diagnose.**

```bash
tail -40 ~/.mlx_studio/logs/server.log   # background servers only
tail -20 ~/.mlx_studio/logs/app.log      # "api server died" with exit_code
lsof -nP -iTCP:8080 -sTCP:LISTEN         # is something else holding the port?
```

**Fix.** Press **Restart** in the banner, or start it again from the Server view.
Dismiss hides the banner without restarting. A background server is adopted on
the next launch from `~/.mlx_studio/server.pid`, so a stale pidfile is cleaned up
automatically when the pid is gone.

### 4.9 "mlx_guard: refusing to start the API server"

The guard found that the installed `mlx_lm` cannot be imported or its server no
longer matches what the guard patches. It fails closed. Read `server.log` and
`app.log`, then install the pinned version from the **Setup** tab. Do not bypass
the guard ([ADR-009](adr/ADR-009-api-server-guard.md)).

This message also appears if the `mlx_lm` tool call formatter has changed.

### 4.10 The log folder is missing

`~/.mlx_studio/logs/` is created on start. Logs written by 2.0.0 in
`~/.mlx_studio/` are moved into it on first launch. If the move could not happen,
the old files are still readable in place:

```bash
ls -la ~/.mlx_studio ~/.mlx_studio/logs
```

## 5. Ports and paths

| Item | Value |
|---|---|
| UI + API | `127.0.0.1:8765` |
| OpenAI-compatible server | `127.0.0.1:8080/v1` |
| Log folder | `~/.mlx_studio/logs/` |
| Activity log | `~/.mlx_studio/logs/activity.log` |
| Application log | `~/.mlx_studio/logs/app.log` |
| Launch log | `~/.mlx_studio/logs/launch.log` |
| Background server output | `~/.mlx_studio/logs/server.log` |
| Background server pid | `~/.mlx_studio/server.pid` |
| Last models that started (unreleased) | `~/.mlx_studio/server.last.json` |
| Model cache | `~/.cache/huggingface/hub/` |
| Update manifest | `release/latest.json` in the repo |
| Bundle | `/Applications/MLX Studio.app` |

## 6. Diagnostic bundle

The in-app route is **Activity → Export diagnostics**, which writes
`mlx-studio-diagnostics-<stamp>.zip` containing `environment.json` and every file
in the log folder. Ask the user for that file first; it is one attachment and
needs no shell.

Collect these only when the app will not start at all:

```bash
{
  echo "== versions =="
  python3 -c "import mlx.core, mlx_lm; print('mlx', mlx.core.__version__); print('mlx_lm', mlx_lm.__version__)"
  sw_vers
  uname -m
  echo "== ports =="
  lsof -nP -iTCP:8765 -iTCP:8080 -sTCP:LISTEN
  echo "== processes =="
  pgrep -fl "app.py|mlx_guard.py|mlx_lm server"
  echo "== log folder =="
  ls -la ~/.mlx_studio/logs
  echo "== launch log =="
  tail -40 ~/.mlx_studio/logs/launch.log
  echo "== app log =="
  tail -40 ~/.mlx_studio/logs/app.log
  echo "== activity =="
  tail -20 ~/.mlx_studio/logs/activity.log
  echo "== server =="
  tail -20 ~/.mlx_studio/logs/server.log 2>/dev/null
  cat ~/.mlx_studio/server.pid 2>/dev/null
} > ~/mlx-studio-diag.txt
```

## 7. Release checklist

- [ ] `APP_VERSION` bumped in `app.py`
- [ ] `CHANGELOG.md` has an entry for the new version
- [ ] `./scripts/install_hooks.sh --check` reports hooks active
- [ ] `./scripts/check_secrets.py --all-history` finds no credentials
- [ ] `./scripts/security_audit.sh` passes (0 failures)
- [ ] `python3 -m py_compile app.py mlx_guard.py` passes
- [ ] `node --check ui/app.js` passes
- [ ] `python3 -m pytest` passes
- [ ] Backend smoke test passes
- [ ] CDP UI run reports `RESULT: PASS` with no JS errors
- [ ] `packaging/build_app.sh` succeeds (app, dmg, pkg, `dist/SHA256SUMS`)
- [ ] `codesign --verify --deep --strict` passes
- [ ] `hdiutil verify` passes
- [ ] App installed from the built file, launched, `/api/ping` OK
- [ ] `release_manifest.py` regenerated and `--verify --release` passes
- [ ] `docs/` updated for any behaviour change
- [ ] Owner approval, then tag the built commit and publish that `.dmg` and `.pkg` as a pre-release
- [ ] Assets downloaded and checksums compared
- [ ] Pull request with `release/latest.json`, `release/SHA256SUMS`, `CHANGELOG.md`
- [ ] Installer tested with `--version`, then owner approval to promote to latest

Order and commands: [DEPLOYMENT.md](DEPLOYMENT.md) section 5.

## 8. Escalation

| Symptom | First action | Then |
|---|---|---|
| App will not start | §4.1 launch log | Reinstall the app |
| Model will not load | §4.5 | Try a smaller model |
| Server will not bind | §4.3 | Stop the conflicting process |
| Guard refuses to start | §4.9 | Reinstall the pinned runtime |
| Server died banner | §4.8 `server.log` and `app.log` | Restart from the banner |
| Logs missing | §4.10 log folder | Export diagnostics |
| Update check fails | Check network and proxy | Manifest/PyPI unreachable is non-fatal |
| Data loss | Check `~/.mlx_studio` and the HF cache | Weights are re-downloadable |

## 9. Sources read for this file

| Source | Used for |
|---|---|
| `app.py` | Ports, endpoints, log paths, shutdown behaviour |
| `packaging/launcher.c` | Launch log path and interpreter order |
| `docs/TESTING.md` | Verified commands |
| Release verification run, 2026-11-24 | Expected outputs |
