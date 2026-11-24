---
type: Diagram
title: "Runtime view — processes, ports, threads"
date: 2026-11-24
project: mlx-studio
related:
  - ../ARCHITECTURE.md
  - chat-flow.md
---

# Runtime view

What runs when the app is open, and what talks to what.

```mermaid
flowchart TB
    subgraph MAC["macOS · Apple Silicon · 32 GB unified memory"]
        subgraph APP["MLX Studio.app — one Python process"]
            direction TB

            subgraph UI["pywebview window · WKWebView"]
                PAGE["ui/index.html<br/>style.css · app.js"]
            end

            subgraph HTTP["ThreadingHTTPServer · 127.0.0.1:8765"]
                ROUTER["Handler<br/>routes · JSON · SSE · static"]
            end

            subgraph STATE["State — single source of truth"]
                SLOT["model · tokenizer<br/>loaded_repo"]
                SESS["server_proc · server_lines"]
                PROG["downloads · install"]
                VERS["versions · app_release"]
            end

            subgraph WORK["worker threads"]
                WLOAD["worker_load"]
                WDL["worker_download"]
                WINST["worker_install"]
                WVER["worker_check_versions"]
                WSRV["worker_server_start"]
            end

            subgraph MLX["mlx_lm — in process"]
                LOAD["load()"]
                GEN["stream_generate()"]
            end

            subgraph FILES["durable files"]
                LOG[("~/.mlx_studio/logs<br/>activity · app · server")]
                CACHE[("~/.cache/huggingface/hub<br/>models--org--name")]
            end
        end

        subgraph CHILD["child process"]
            SRV["python -m mlx_lm server<br/>127.0.0.1:8080/v1"]
        end

        subgraph EXT["remote, on explicit action"]
            HF["Hugging Face Hub"]
            PYPI["PyPI JSON API"]
            GH["release/latest.json"]
        end
    end

    PAGE <-->|"HTTP + SSE<br/>same origin"| ROUTER
    ROUTER <-->|"read snapshot<br/>mutate under lock"| STATE
    ROUTER -->|"spawn"| WORK
    WORK -->|"update"| STATE
    WLOAD --> LOAD
    LOAD -->|"weights"| CACHE
    ROUTER -->|"stream"| GEN
    GEN --> SLOT
    WDL -->|"snapshot_download"| HF
    WDL --> CACHE
    WVER --> PYPI
    WVER --> GH
    WINST -->|"pip"| PYPI
    WSRV -->|"Popen"| SRV
    SRV -.->|"stdout pump"| SESS
    ROUTER -->|"append"| LOG
    STATE -->|"render"| PAGE

    CLIENT["Claude Code · DeepSeek<br/>curl · any OpenAI client"]
    CLIENT -.->|"HTTP"| SRV
```

## Reading the picture

| Element | Meaning |
|---|---|
| Solid arrow | Synchronous call in the same process |
| Dashed arrow | Asynchronous, optional, or cross-process |
| Cylinder | Durable storage on disk |
| `127.0.0.1` on both listeners | Nothing is reachable from the LAN |

## Facts the picture encodes

| Fact | Value | Source |
|---|---|---|
| UI/API port | `127.0.0.1:8765` | `app.py:43` |
| OpenAI server port | `127.0.0.1:8080` | `app.py:44` |
| Server is a child process | `Popen([... "mlx_lm", "server" ...])` | `app.py:466` |
| Inference is in-process | `stream_generate(model, ...)` | `app.py:315` |
| One model slot | `State.model` | `app.py:223` |
| Logs are files | `~/.mlx_studio/logs/` | `app.py:39` |
| Weights are a file tree | `~/.cache/huggingface/hub` | `app.py:37` |
| Remote calls are explicit | download, install, version check | `app.py:378`, `383`, `410` |

## What is deliberately absent

- No database process.
- No message broker.
- No authentication service.
- No telemetry endpoint.
- No listener on `0.0.0.0`.
