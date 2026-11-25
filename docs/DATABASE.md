# MLX Studio - Data storage

**Date:** 2026-11-25 | **Applies to:** 2.0.9 source (unreleased) | **Architecture:** [ARCHITECTURE.md](ARCHITECTURE.md)

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
| Chat history | The server code writes no transcript to disk |

## 4. Migration and backup

- At start, older `activity.log` and `launch.log` files directly in `~/.mlx_studio` are moved into `logs/`.
- No backup feature. Model weights can be downloaded again. Logs cannot be recovered once removed.
