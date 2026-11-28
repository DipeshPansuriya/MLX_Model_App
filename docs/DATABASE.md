# MLX Studio - Data storage

> **2.0.13 supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).
>
> In this file **unreleased** means not in 2.0.9: those changes shipped in 2.0.10 to 2.0.13 (latest release, 2026-11-28).

**Date:** 2026-11-25 | **Applies to:** 2.0.9 (released 2026-11-26) and rows marked unreleased | **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)

## 1. Overview

MLX Studio has **no database**. There is no schema, server, ORM or migration. State is held in
memory (the `State` class in `app.py`) and in the files below.

## 2. What is stored

| Data | Location | Format | Written by |
|---|---|---|---|
| Activity log | `~/.mlx_studio/logs/activity.log` | one JSON object per line: `timestamp`, `action`, `model`, `status`, `detail` | `log_activity` |
| App log | `~/.mlx_studio/logs/app.log` | one JSON object per line: `timestamp`, `level`, `message`, extra fields | `log_app` |
| Launcher log | `~/.mlx_studio/logs/launch.log` | text | `packaging/launcher.c` |
| API server output | `~/.mlx_studio/logs/server.log` | raw process output | background API server |
| Server identity | `~/.mlx_studio/server.pid` | pidfile used to re-find a background server | `app.py` |
| Served model names | `~/.mlx_studio/server.models` | model names the running API server accepted, one per line, mode `0600`, cleared at each server start | `mlx_guard.py` |
| Loaded models and memory (unreleased) | `~/.mlx_studio/server.resident.json` | server `pid`, `resident`, `budget_bytes`, `active_bytes`, `peak_bytes`, recent `events`, `prompt_limits`; with two models also `multi`, `reason`, `oversize` (models loaded alone although they do not fit), `context_tokens`, `free_bytes` (once a memory check has run) and `preload_done` (`true` once preload has ended, however it ended). Written at a model load or switch, with one model too; with two models also at evictions, trims, a skipped preload, the end of preload and about every 10 s from the server's request loop. Mode `0600`, removed at server start and stop | `mlx_guard.py` |
| User limits (unreleased) | `~/.mlx_studio/settings.json` | JSON object of numbers (plus the word `performance_mode`), per-model overrides the load test values and the `ui` block (2.0.14: theme) ([API.md](API.md#6b-user-settings)), mode `0600`; absent until saved, removed by **Reset this entry** when nothing else is saved | `app.py` |
| Load test results (unreleased) | `~/.mlx_studio/loadtest.json` | Last 5 runs per model, figures only, no prompt or reply text, mode `0600`, read up to 256 KB | `app.py` |
| Load test processes (unreleased) | `~/.mlx_studio/loadtest.pid`, `loadtest-run-*/` | Runner and server pid with start time; temporary files; removed when the run ends or at the next app start | `app.py` |
| Saved chats (2.0.14) | `~/.mlx_studio/chats/` | One plain-text file per chat: messages, thinking, figures, system prompt, settings and attached file text. Folder `0700`, files `0600`; no expiry; delete is not a secure erase | `chat_store.py` |
| Project list (2.0.14) | `~/.mlx_studio/projects.json` | Chosen folder paths | `projects.py` |
| Start at login (2.0.14) | `~/.mlx_studio/default-model.json`, `autostart.key`, `login-start.json`, `login-start.lock`; `~/Library/LaunchAgents/com.mlxstudio.app.login-server.plist` | Default model and switch, the OptiQ key (removed when switched off), last result, lock; the plist holds no key or model | `login_agent.py` |
| Model alias (2.0.14) | `settings.json` `ui.models.<repo>.alias`; `server.pid` keys `name`, `alias`; `~/.mlx_studio/engines/optiq/run`, `engines/mlx/hub`, `engines/mlx/run` | Names and links to cached model files | `app.py`, `engines.py` |
| Model weights | Hugging Face cache | Hugging Face layout | `huggingface_hub` |
| Download marker | `<cache>/models--<org>--<name>/.mlx_studio_download.json` | JSON | `app.py` |

The cache folder follows `HF_HUB_CACHE`, `HUGGINGFACE_HUB_CACHE`, `HF_HOME` or `XDG_CACHE_HOME`;
the default is under `~/.cache/huggingface`.

## 3. Retention and permissions

| Topic | Behaviour |
|---|---|
| Rotation | At 2 MiB, with 3 backups (`LOG_MAX_BYTES`, `LOG_BACKUPS`) |
| Clear | The activity log clear action removes `activity.log*` only |
| Permissions | Folders `0700`, files the app writes `0600` ([SECURITY.md](SECURITY.md) section 4.6) |
| Sensitive content | `server.log` can contain request content ([SECURITY.md](SECURITY.md) section 4.6) |
| Chat history | Up to 2.0.13 no transcript is written. 2.0.14 saves chats by default (**Save history**); **Temporary** chats are not saved |
| Budget file reading (unreleased) | The guard accepts only a plain file holding a number above 0 and never follows a link. It uses the lower of this number and the start budget |

## 4. Migration and backup

- At start, older `activity.log` and `launch.log` files directly in `~/.mlx_studio` are moved into `logs/`.
- No backup feature. Model weights can be downloaded again. Logs cannot be recovered once removed.
