---
type: Runbook
title: "Runbook — MLX Studio"
status: "Built at tag v2.0.3 (2026-11-25)"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.3"
related:
  - DEPLOYMENT.md
  - SECURITY.md
  - TESTING.md
---

# Runbook — MLX Studio

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

Expected: a 1360×900 window, sidebar pill `● No model loaded`, Setup reporting the
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
| Processes | `pgrep -fl "app.py\|mlx_lm server"` | one app, at most one server |
| Activity | `tail -5 ~/.mlx_studio/logs/activity.log` | recent JSON lines |
| Launch log | `tail -20 ~/.mlx_studio/logs/launch.log` | no traceback |

## 3. Common tasks

### 3.1 Load and chat

1. Chat tab → select a model → **Load**. Pill turns green.
2. Type, press Enter. Tokens stream; metrics appear under the answer.
3. **Stop** cancels; **New chat** clears history.

### 3.2 Free memory

Chat tab → **Unload**. This drops the model reference and calls
`mx.clear_cache()`. Verify with the pill returning to `● No model loaded`.

### 3.3 Download a model

Download tab → search → **Download**. Status goes `running` → `success`. Weights
land in `~/.cache/huggingface/hub/models--<org>--<name>/`.

### 3.4 Start the OpenAI-compatible server

API Server tab → select a model → **Start**.

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Qwen2.5-7B-Instruct-1M-4bit",
       "messages":[{"role":"user","content":"Hello!"}]}'
```

Point Claude Code or DeepSeek at `http://127.0.0.1:8080/v1` with any non-empty API
key.

### 3.5 Update the runtime

Setup tab → **Check for updates** → **Install / Update**. Output streams into the
panel; versions refresh afterwards. Equivalent CLI:

```bash
python3 -m pip install --upgrade mlx mlx-lm
```

### 3.6 Check for an app update

Setup tab → **Check for updates**. With `MLX_STUDIO_UPDATE_URL` set, a newer
manifest shows the update panel with notes and a **Download DMG** button.

## 4. Recovery procedures

### 4.1 The window does not appear

```bash
tail -40 ~/.mlx_studio/logs/launch.log
```

| Log says | Cause | Fix |
|---|---|---|
| `No Python 3.10+ interpreter found` | No suitable Python | Install from python.org or `brew install python` |
| `Missing app.py at …` | Bundle damaged | Reinstall from the DMG |
| `ModuleNotFoundError: mlx` | Runtime missing | Setup tab → Install, or `pip install mlx mlx-lm` |
| `ModuleNotFoundError: webview` | pywebview absent | `pip install pywebview`, or let it fall back to the browser |

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

### 4.4 An orphaned `mlx_lm server`

```bash
pgrep -fl "mlx_lm server"
pkill -f "mlx_lm server"
```

The app terminates its child on window close; an orphan means the parent was
killed hard (`kill -9`).

### 4.5 A model will not load

| Symptom | Cause | Fix |
|---|---|---|
| `Load failed`, pill red | Weights incomplete | Re-download the model |
| Out of memory | Model too large for free RAM | Unload, close apps, choose a smaller model |
| `409 another model is loading` | A load is in flight | Wait for the poll to settle |
| Missing tokenizer files | Partial snapshot | Delete the model and download again |

### 4.6 The activity log is corrupt

The reader tolerates bad lines by wrapping them as `raw`. To start clean:

```bash
mv ~/.mlx_studio/logs/activity.log ~/.mlx_studio/logs/activity.log.bak
```

The file is recreated on the next action.

### 4.7 Gatekeeper blocks the app

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

**Cause.** The `mlx_lm server` child exited. Common reasons: out of memory while
loading the model, a port clash on 8080, or the process was killed.

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

### 4.9 The log folder is missing

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
  pgrep -fl "app.py|mlx_lm server"
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
- [ ] `python3 -m py_compile app.py` passes
- [ ] `node --check ui/app.js` passes
- [ ] Backend smoke test passes
- [ ] CDP UI run reports `RESULT: PASS` with no JS errors
- [ ] `packaging/build_app.sh` succeeds
- [ ] `codesign --verify --deep --strict` passes
- [ ] `hdiutil verify` passes
- [ ] DMG mounted, app copied out, launched, `/api/ping` OK
- [ ] `release_manifest.py` regenerated with the new SHA-256
- [ ] `docs/` updated for any behaviour change
- [ ] Commit, annotated tag, `git push origin main --tags`
- [ ] GitHub release created with the DMG attached
- [ ] Asset downloaded and checksum compared

## 8. Escalation

| Symptom | First action | Then |
|---|---|---|
| App will not start | §4.1 launch log | Reinstall from DMG |
| Model will not load | §4.5 | Try a smaller model |
| Server will not bind | §4.3 | Stop the conflicting process |
| Server died banner | §4.8 `server.log` and `app.log` | Restart from the banner |
| Logs missing | §4.9 log folder | Export diagnostics |
| Update check fails | Check network and proxy | Manifest/PyPI unreachable is non-fatal |
| Data loss | Check `~/.mlx_studio` and the HF cache | Weights are re-downloadable |

## 9. Sources read for this file

| Source | Used for |
|---|---|
| `app.py` | Ports, endpoints, log paths, shutdown behaviour |
| `packaging/launcher.py` | Launch log path and interpreter order |
| `docs/TESTING.md` | Verified commands |
| Release verification run, 2026-11-24 | Expected outputs |
