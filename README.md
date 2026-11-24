# MLX Studio

**A native macOS app for running local AI models on Apple Silicon — LM Studio / Ollama style, built on Apple's MLX.**

[![Release](https://img.shields.io/github/v/release/DipeshPansuriya/MLX_Model_App?color=4f8cff&label=release)](https://github.com/DipeshPansuriya/MLX_Model_App/releases/latest)
[![Platform](https://img.shields.io/badge/platform-macOS%2012%2B%20Apple%20Silicon-000000?logo=apple)](#requirements)
[![Python](https://img.shields.io/badge/python-3.10%2B-3776ab?logo=python&logoColor=white)](#requirements)
[![License](https://img.shields.io/badge/license-MIT-37d399)](#license)
[![Stars](https://img.shields.io/github/stars/DipeshPansuriya/MLX_Model_App?color=f0a23c)](https://github.com/DipeshPansuriya/MLX_Model_App/stargazers)

MLX Studio wraps Apple's MLX runtime in a desktop UI: load a model, chat with
streaming replies, download and delete weights, serve an OpenAI-compatible API
for Claude Code or DeepSeek, and review a day-to-day activity log. It also
reports installed versions and tells you when updates are available.

![Chat view](screenshots/02-chat-loaded.png)

---

## Install

### Download the DMG (recommended)

1. Grab the latest **`MLX Studio-<version>.dmg`** from
   [**Releases**](https://github.com/DipeshPansuriya/MLX_Model_App/releases/latest).
2. Open the DMG and drag **MLX Studio** onto the **Applications** folder.
3. First launch: right-click the app → **Open** → **Open** (the build is ad-hoc
   signed, not notarised).

### Run from source

```bash
git clone https://github.com/DipeshPansuriya/MLX_Model_App.git
cd MLX_Model_App
./run.sh
```

`Run_MLX_Manager.command` is the double-clickable Finder launcher.

---

## Requirements

| | |
|---|---|
| OS | macOS 12+ on Apple Silicon (M1–M4) |
| Python | 3.10+ — [python.org](https://www.python.org/downloads/macos/) or `brew install python` |
| Packages | `mlx`, `mlx-lm` — installed from the in-app **Setup** tab |
| Optional | `pywebview` for the native window (falls back to the browser) |

```bash
python3 -m pip install pywebview
```

---

## Screens

| Chat | Models |
|---|---|
| ![Chat](screenshots/02-chat-loaded.png) | ![Models](screenshots/03-models.png) |

| Download | API Server |
|---|---|
| ![Download](screenshots/04-download.png) | ![Server](screenshots/05-server.png) |

| Activity | Setup |
|---|---|
| ![Activity](screenshots/06-activity.png) | ![Setup](screenshots/07-setup.png) |

---

## Features

### Chat
- Load / unload models in-process with a live state pill: `Not loaded` →
  pulsing `Loading…` → green `Loaded — <model>`.
- Token-by-token streaming; each turn reports `tokens · tok/s · GB peak`.
- **Stop** cancels generation, **New chat** resets history.
- `Enter` sends, `Shift+Enter` inserts a newline; the composer grows as you type.

### Models
- Card per cached model with size and repo id.
- **Load**, **Reveal**, **Delete** (blocked while the model is loaded).

### Download
- Search the [`mlx-community`](https://huggingface.co/mlx-community) org on
  Hugging Face, or browse everything.
- Per-model download status: `running` / `success` / `failed`.

### API Server
- Starts `mlx_lm server` on `http://127.0.0.1:8080/v1`.
- Live server output, copyable endpoint, ready-to-run `curl` example.

### Activity
- Filterable log of every chat, load, download, delete, install and server event.
- Persisted to `~/.mlx_studio/activity.log` (one JSON object per line).

### Setup
- Installed versions of MLX, MLX-LM and huggingface-hub, each with a pill:
  `up to date`, `update → <version>`, or `missing`.
- App version, latest release, update channel, hardware and last-check time.
- **Check for updates** re-queries PyPI; a `!` badge marks the nav item when
  anything is outdated.
- **Install / Update** runs pip and streams output into the panel.

---

## Version and update checks

| Component | Source of truth |
|---|---|
| MLX Studio | `APP_VERSION` in [`app.py`](app.py) |
| MLX / MLX-LM | `importlib.metadata` vs. the PyPI JSON API |
| App updates | `MLX_STUDIO_UPDATE_URL` release manifest |

Installed copies check for a newer release by fetching a small JSON manifest:

```json
{
  "version": "2.1.0",
  "url": "https://github.com/DipeshPansuriya/MLX_Model_App/releases/download/v2.1.0/MLX.Studio-2.1.0.dmg",
  "sha256": "…",
  "notes": "Faster model loading",
  "published": "2026-11-24T22:00:00Z"
}
```

Point the app at it:

```bash
export MLX_STUDIO_UPDATE_URL="https://raw.githubusercontent.com/DipeshPansuriya/MLX_Model_App/main/release/latest.json"
```

When the manifest version is newer, Setup shows an **App update available**
panel with the release notes and a **Download DMG** button.

---

## Build a DMG

```bash
cd packaging
./build_app.sh              # dist/MLX Studio.app + dist/MLX Studio-<version>.dmg
./build_app.sh --app-only   # app bundle only
./build_app.sh --no-sign    # skip ad-hoc codesigning
```

The script reads `APP_VERSION` from `app.py`, generates `Info.plist`, embeds the
icon, ad-hoc signs the bundle, and produces a compressed DMG with an
`Applications` symlink and a `Read Me.txt`.

Generate the update manifest for a release:

```bash
./release_manifest.py \
  --dmg "../dist/MLX.Studio-2.0.0.dmg" \
  --url "https://github.com/DipeshPansuriya/MLX_Model_App/releases/download/v2.0.0/MLX.Studio-2.0.0.dmg" \
  --notes "Initial packaged release" \
  --out ../release/latest.json
```

---

## Connect Claude Code / DeepSeek

1. Start a model in the **API Server** tab.
2. Point any OpenAI-compatible client at it:

```
Base URL: http://127.0.0.1:8080/v1
API Key:  local
Model:    <the model you started>
```

```bash
export OPENAI_BASE_URL="http://127.0.0.1:8080/v1"
export OPENAI_API_KEY="local"
export OPENAI_MODEL_NAME="mlx-community/Qwen2.5-7B-Instruct-1M-4bit"
```

```bash
curl http://127.0.0.1:8080/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model":"mlx-community/Qwen2.5-7B-Instruct-1M-4bit",
       "messages":[{"role":"user","content":"Hello!"}]}'
```

---

## Model sizing for 32 GB unified memory

| Model | Download | Peak RAM | Use |
|---|---|---|---|
| `mlx-community/Llama-3.2-3B-Instruct-4bit` | ~2 GB | ~2 GB | fastest replies |
| `mlx-community/Qwen2.5-7B-Instruct-1M-4bit` | ~4 GB | ~4.4 GB | **recommended default** |
| `mlx-community/Qwen3.5-9B-4bit` | ~5–6 GB | ~6 GB | step up in quality |
| `mlx-community/Qwen3.8-27B-4bit` | ~16 GB | ~17 GB | strongest that still fits |

---

## Documentation

Full product, architecture and operational documentation lives in [`docs/`](docs/README.md).

| Document | Answers |
|---|---|
| [docs/PRD.md](docs/PRD.md) | What the product is, users, scope, 32 acceptance criteria, open decisions |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Processes, threads, state, data model, request paths |
| [docs/DESIGN.md](docs/DESIGN.md) | Layout, design tokens, state vocabulary, screen specs, accessibility |
| [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) | Bundle layout, build, signing, DMG, release, rollback |
| [docs/SECURITY.md](docs/SECURITY.md) | Threat model, trust boundaries, controls, accepted risks |
| [docs/SECURITY-AUDIT.md](docs/SECURITY-AUDIT.md) | 2026-11-24 audit: 21 checks, 3 findings remediated, re-run procedure |
| [docs/PUSH-POLICY.md](docs/PUSH-POLICY.md) | No-credential rule, 19 secret rules, git hooks, CI enforcement |
| [docs/GITHUB-POLICY.md](docs/GITHUB-POLICY.md) | Branch ruleset, 7 required checks, automated PR review, zero-cost setup |
| [docs/TESTING.md](docs/TESTING.md) | What was verified, measurements, defects found, gaps |
| [docs/RUNBOOK.md](docs/RUNBOOK.md) | Start/stop, health checks, recovery, release checklist |
| [docs/adr/](docs/adr/README.md) | 8 architecture decision records with rejected alternatives |
| [docs/diagrams/](docs/diagrams/README.md) | 6 Mermaid diagrams: runtime, chat flow, lifecycle, release, updates, state |

---

## Project layout

```
MLX_Model_App/
├── app.py                      # backend: HTTP + JSON/SSE API, native window host
├── ui/
│   ├── index.html              # single-page interface
│   ├── style.css               # dark LM Studio–style theme
│   └── app.js                  # routing, streaming, polling
├── packaging/
│   ├── build_app.sh            # .app + .dmg builder
│   ├── launcher.py             # bundle entry point
│   ├── release_manifest.py     # update manifest generator
│   ├── icon.html               # icon source
│   ├── icon_1024.png           # rendered icon
│   └── AppIcon.icns            # compiled icon
├── scripts/
│   ├── security_audit.sh       # re-runnable security checks
│   ├── check_secrets.py        # 19-rule credential scanner
│   └── install_hooks.sh        # activate git hooks
├── .githooks/                  # pre-commit, pre-push
├── .github/                    # workflows (security, quality, pr-review), CODEOWNERS
├── docs/                       # PRD, architecture, design, ADRs, diagrams
├── release/
│   └── latest.json             # update manifest served to installed copies
├── screenshots/                # captured views
├── Run_MLX_Manager.command     # Finder launcher
├── run.sh                      # terminal launcher
├── CHANGELOG.md
└── LICENSE
```

## Security

```bash
./scripts/install_hooks.sh           # activate pre-commit and pre-push hooks
./scripts/check_secrets.py           # scan the working tree
./scripts/check_secrets.py --all-history
./scripts/security_audit.sh          # 21 checks, exit 0 on pass
./scripts/security_audit.sh --quick  # skip the dependency scan
```

**No credential may enter the repository.** Enforced at three points: pre-commit
(staged content), pre-push (commits leaving the machine), and CI (working tree
plus full history). 19 rules cover platform tokens, key material, generic
assignments and connection strings. See
[docs/PUSH-POLICY.md](docs/PUSH-POLICY.md) and
[docs/SECURITY-AUDIT.md](docs/SECURITY-AUDIT.md).

**`main` is protected.** A ruleset requires a pull request and seven green status
checks, forbids direct pushes, force-pushes and deletion, and has no bypass
actors. An automated review comment reports lint, format, secret, link and syntax
results on every PR. See [docs/GITHUB-POLICY.md](docs/GITHUB-POLICY.md).

---

## HTTP API

| Method | Path | Purpose |
|---|---|---|
| GET | `/api/state` | Full snapshot (app, versions, models, loaded, server, downloads, install) |
| GET | `/api/models` | Installed models |
| GET | `/api/search?q=` | Search mlx-community |
| GET | `/api/logs` | Activity entries |
| GET | `/api/versions` | Re-check PyPI, then return versions |
| POST | `/api/load` `{repo}` | Load a model |
| POST | `/api/unload` | Unload and clear the MLX cache |
| POST | `/api/chat` `{messages}` | SSE stream: `token`, `done`, `error` |
| POST | `/api/chat/stop` | Cancel generation |
| POST | `/api/download` `{repo}` | Start a download |
| POST | `/api/delete` `{repo}` | Delete a cached model |
| POST | `/api/server/start` `{repo}` | Start the OpenAI-compatible server |
| POST | `/api/server/stop` | Stop it |
| POST | `/api/install` `{kind}` | `install` \| `update` \| `reinstall` |
| POST | `/api/logs/clear` | Truncate the activity log |

---

## Storage

| What | Where |
|---|---|
| Model weights | `~/.cache/huggingface/hub/models--<org>--<name>/` |
| Activity log | `~/.mlx_studio/activity.log` |
| Launch log | `~/.mlx_studio/launch.log` |

---

## Troubleshooting

**"MLX Studio is damaged" / Gatekeeper warning** — right-click the app → **Open**.
For a permanently trusted build, sign with a Developer ID and notarise.

**No native window** — `python3 -m pip install pywebview`; otherwise the app
opens in your default browser.

**Port 8765 busy** — another MLX Studio instance is running; quit it first.

**Port 8080 busy** — stop the other server before starting the API server.

**Update check says "unknown"** — the PyPI request failed; check network/proxy.
The app injects `truststore` to fix macOS certificate chains.

**Delete refused** — unload the model in Chat first.

---

## Contributing

Issues and pull requests are welcome. Please run `python3 -m py_compile app.py`
and `node --check ui/app.js` before opening a PR.

---

## License

[MIT](LICENSE) © Dipesh Pansuriya
