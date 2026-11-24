---
type: Testing
title: "Testing — MLX Studio"
status: "Verification performed for v2.0.0 (2026-11-24)"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.0"
related:
  - PRD.md
  - ARCHITECTURE.md
  - RUNBOOK.md
---

# Testing — MLX Studio

## 1. Approach

There is no unit-test suite. Verification for 2.0.0 was **black-box, end-to-end,
against the running application**: a scripted browser session drove the real UI
over the Chrome DevTools Protocol while the real backend served it, and the
packaged DMG was downloaded, mounted and launched. This is deliberate — the risk
in this product is integration with MLX and with the browser, not arithmetic in a
helper function — but it is also a gap, recorded in §7.

Three layers were exercised:

| Layer | Method | Where |
|---|---|---|
| Backend HTTP | Direct `urllib` calls to every endpoint | §2 |
| UI behaviour | CDP `Runtime.evaluate` against the live page | §3 |
| Packaging | `hdiutil`, `codesign`, `open`, checksum | §4 |

## 2. Backend verification

### 2.1 Static and meta

| Check | Expected | Result |
|---|---|---|
| `GET /` | 200, HTML | 200, 10024 bytes |
| `GET /style.css` | 200, CSS | 200 |
| `GET /app.js` | 200, JS | 200 |
| `GET /api/ping` | `{"ok": true, "app": "2.0.0"}` | pass |
| `GET /api/state` | full snapshot | app, hardware, versions, models, loaded, server present |
| `GET /api/search?q=qwen2.5-7b` | results | 2+ repo ids returned |

### 2.2 Model lifecycle

| Check | Expected | Result |
|---|---|---|
| `POST /api/load` | `{"ok": true, "loading": repo}` | pass |
| Poll until loaded | `loaded.repo` set, `loading` null | pass |
| `POST /api/unload` | `{"ok": true}` | pass |
| Delete while loaded | 409 | pass |
| Size of a 4 GB model | `4.0 GB` | pass (was `8.0 GB` before the inode fix) |

### 2.3 Chat SSE

```
POST /api/chat {"messages":[{"role":"user","content":"Reply with exactly: hello world"}]}
```

| Check | Expected | Result |
|---|---|---|
| Response is `text/event-stream` | yes | pass |
| `token` events arrive incrementally | 3 events | pass |
| Reassembled text | `"hello world"` | pass |
| `done` event | metrics object | `{tokens: 3, tps: 43.5, peak_gb: 4.37, prompt_tokens: 25, elapsed: 0.42}` |
| `POST /api/chat` with no model | 409 | pass |

### 2.4 API server

| Check | Expected | Result |
|---|---|---|
| `POST /api/server/start` | `{"ok": true}` | pass |
| `GET http://127.0.0.1:8080/v1/models` | 200 with repo id | pass |
| `state.server.running` | true, endpoint correct | pass |
| Output captured | ≥ 4 lines | pass |
| `POST /api/server/stop` | running false | pass |
| Stop logged exactly once | one entry | pass (duplicate fixed) |

## 3. UI verification

Driven over CDP at 1360×900. Final run: **`RESULT: PASS`, zero JavaScript errors.**

### 3.1 Shell and routing

| Check | Result |
|---|---|
| Title `MLX Studio` | pass |
| Sidebar version `v2.0.0` | pass |
| Hardware line `Apple M6` / `32 GB unified` | pass |
| Model count badge | pass |
| Hash routing `#chat…#settings` | pass |
| Nav click updates hash | pass |
| Browser Back restores previous view | pass |
| Unknown hash falls back to `#chat` | pass |
| No horizontal overflow on any view | pass (overflowX = 0 on all six) |

### 3.2 Layout measurements

| View | Topbar height | Scroll height | Overflow |
|---|---|---|---|
| Chat | 65 px | 755 px | 0 |
| Models | 63 px | 837 px | 0 |
| Download | 63 px | 837 px | 0 |
| Server | 65 px | 835 px | 0 |
| Activity | 65 px | 835 px | 0 |
| Setup | 63 px | 837 px | 0 |

Composer 80 px at rest; input 47 px; Send button 62×34.

### 3.3 Model state indication

| Step | Expected | Result |
|---|---|---|
| Initial | `● Not loaded`, Load visible, Unload hidden | pass |
| After Load | `● Loaded — Qwen2.5-7B-Instruct-1M-4bit` | pass |
| Sidebar pill | green with model name | pass |
| Buttons | Load hidden, Unload visible | pass |
| Models card | `loaded` tag | pass |
| After Unload | `● Not loaded`, Load visible | pass |

### 3.4 Chat

| Check | Result |
|---|---|
| Two messages rendered | pass |
| Assistant text `"hello world"` | pass |
| Metrics line `3 tokens · 52.6 tok/s · 4.37 GB peak` | pass |
| Send re-enabled after completion | pass |
| Stop hidden when idle | pass |

### 3.5 Server, logs, settings

| Check | Result |
|---|---|
| Server pill `Running — <model>` | pass |
| Endpoint `http://127.0.0.1:8080/v1` | pass |
| Server output ≥ 7 lines | pass |
| Stop returns pill to `Stopped` | pass |
| Log table populated (18–52 rows across runs) | pass |
| Newest-first ordering | pass |
| Status colouring | pass |
| `setMlx 0.32.4` / `up to date` | pass |
| `setMlxLm 0.32.2` / `up to date` | pass |
| Hint "Everything is installed and up to date." | pass |

### 3.6 App update panel

With `MLX_STUDIO_UPDATE_URL` pointing at a 2.1.0 manifest:

| Check | Result |
|---|---|
| App pill `update → 2.1.0` | pass |
| Update panel visible | pass |
| Version and notes rendered | pass |
| Download DMG button present | pass |
| Nav `!` badge visible | pass |
| Without a channel: `local build`, no panel | pass |

## 4. Packaging verification

| Check | Result |
|---|---|
| `codesign --verify --deep --strict` | valid (ad-hoc) |
| `hdiutil verify` | CRC32 valid |
| Mounted layout | app, `Applications` symlink, `Read Me.txt` |
| Copy out and `open` | launches |
| `/api/ping` from bundle | `{"ok": true, "app": "2.0.0"}` |
| Native window | 1360×900 |
| Release asset HTTP status | 200 |
| Asset size | 979129 bytes |
| SHA-256 download vs manifest | identical |
| Live manifest from GitHub raw | `latest 2.0.0`, `update false` |

## 5. Defects found and fixed during verification

| # | Defect | Symptom | Fix |
|---|---|---|---|
| 1 | Cross-thread Tk callbacks | `RuntimeError: main thread is not in main loop` | Worker updates queued and drained on the main thread |
| 2 | PyPI check always failed | `CERTIFICATE_VERIFY_FAILED` | `truststore.inject_into_ssl()` before fetch |
| 3 | Model size double-counted | 4 GB model reported `8.0 GB` | Follow symlinks, de-duplicate by `(st_dev, st_ino)` |
| 4 | Server stop logged twice | duplicate activity rows | Log only in the pump that observes exit |
| 5 | Deprecated CLI form | `--interactive` rejected by `mlx_lm` | Use `mlx_lm chat` |

## 6. Regression procedure

```bash
# 1. syntax
python3 -m py_compile app.py
node --check ui/app.js

# 2. backend
python3 - <<'PY'
import importlib.util, json, urllib.request
spec = importlib.util.spec_from_file_location("app", "app.py")
app = importlib.util.module_from_spec(spec); spec.loader.exec_module(app)
httpd = app.serve()
with urllib.request.urlopen(f"http://{app.HOST}:{app.PORT}/api/ping") as r:
    print(json.loads(r.read()))
httpd.shutdown()
PY

# 3. UI over CDP (headless Chrome with --remote-debugging-port=9222)
python3 /tmp/cdp_test.py        # expects RESULT: PASS

# 4. packaging
cd packaging && ./build_app.sh
codesign --verify --deep --strict "../dist/MLX Studio.app"
hdiutil verify "../dist/MLX.Studio-<version>.dmg"
```

## 7. Gaps

| Gap | Risk | Note |
|---|---|---|
| No unit tests | Helper regressions (version compare, size formatting) are caught only by end-to-end runs | `vt`, `is_newer`, `human_size`, `installed_models` are pure and easy to test |
| No automated UI test in the repo | The CDP script lives outside the repository | Would need a checked-in harness |
| No CI | Regressions are caught only when someone runs the procedure | PRD OD-2 |
| Single hardware profile | Only M6 / 32 GB verified | Other chips and RAM sizes untested |
| No load or soak test | Long sessions and repeated load/unload cycles unmeasured | Memory growth after many cycles unknown |
| No accessibility audit | §9 of DESIGN.md lists known gaps | No screen-reader pass performed |

## 8. Sources read for this file

| Source | Used for |
|---|---|
| Backend test run, 2026-11-24 | §2 tables |
| CDP UI run, 2026-11-24 | §3 tables and measurements |
| Packaging run, 2026-11-24 | §4 table |
| `CHANGELOG.md` | §5 defect list |
