# Setup and initialisation

**Date:** 2026-11-25 | Installing the released app: see the [README](../../README.md).

## Prerequisites (from source)

| Need | Detail |
|---|---|
| Mac | Apple Silicon. Launcher target is macOS 12.0; macOS 12 was not tested |
| Python | 3.10 or newer; 3.11 or newer to generate release files |
| Node | for `node --check ui/app.js` (CI uses Node 20) |
| Build | Xcode command line tools (`clang`) |

## Steps

```bash
git clone https://github.com/DipeshPansuriya/MLX_Model_App.git
cd MLX_Model_App
./run.sh
```

- Install `mlx` and `mlx-lm` from the in-app **Setup** tab (pinned versions).
- Optional native window: `python3 -m pip install pywebview`. Without it the app opens in a browser.
- If the system Python refuses `pip`, use a virtual environment and run from source with it.
- Development tools: `python3 -m pip install pytest ruff`.
- Git hooks: `./scripts/install_hooks.sh`, then `./scripts/install_hooks.sh --check`.

## Environment variables

None is required. There is no project `.env` file.

| Variable | Effect |
|---|---|
| `HF_HUB_CACHE`, `HUGGINGFACE_HUB_CACHE`, `HF_HOME`, `XDG_CACHE_HOME` | model cache location |
| `HF_TOKEN` | Hugging Face access token, if you need one; never commit it |
| `MLX_STUDIO_UPDATE_URL` | URL of an update manifest; unset means no app update check |

## Verify

```bash
curl http://127.0.0.1:8765/api/ping
```

Expected: JSON with `"ok": true` and the app version.

## Troubleshooting

See [RUNBOOK.md](../RUNBOOK.md). Test and lint commands: [TESTING.md](../TESTING.md).
