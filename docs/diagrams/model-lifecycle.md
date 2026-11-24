---
type: Diagram
title: "Model lifecycle — cache to delete"
date: 2026-11-24
project: mlx-studio
related:
  - ../ARCHITECTURE.md
  - chat-flow.md
  - ../DESIGN.md
---

# Model lifecycle

Every state a model can be in, and the transition that moves it.

```mermaid
stateDiagram-v2
    [*] --> NotCached: app starts

    NotCached --> Downloading: POST /api/download
    Downloading --> Cached: snapshot_download ok
    Downloading --> NotCached: failed

    Cached --> Loading: POST /api/load
    Loading --> Loaded: mlx_lm.load ok
    Loading --> Cached: load failed (error surfaced)

    Loaded --> Cached: POST /api/unload + mx.clear_cache()
    Loaded --> Loaded: POST /api/chat

    Cached --> NotCached: POST /api/delete
    Loaded --> Loaded: delete refused 409

    state "Not cached" as NotCached
    state "Downloading" as Downloading
    state "Cached on disk" as Cached
    state "Loading" as Loading
    state "Loaded in memory" as Loaded
```

## Transition table

| From | Trigger | To | Guard | Logged |
|---|---|---|---|---|
| Not cached | `POST /api/download` | Downloading | not already running | `download/running` |
| Downloading | library returns | Cached | — | `download/success` |
| Downloading | library raises | Not cached | — | `download/failed` |
| Cached | `POST /api/load` | Loading | no other load in flight | `model/running` |
| Loading | `mlx_lm.load` returns | Loaded | — | `model/success` |
| Loading | `mlx_lm.load` raises | Cached | error stored | `model/failed` |
| Loaded | `POST /api/unload` | Cached | — | `model/info` |
| Loaded | `POST /api/delete` | — | refused `409` | — |
| Cached | `POST /api/delete` | Not cached | — | `delete/success` |

## What each state looks like in the UI

| State | Sidebar pill | Chat header | Models card | Load button |
|---|---|---|---|---|
| Not cached | — | — | absent | — |
| Downloading | — | — | — | — |
| Cached | `● No model loaded` | `● Not loaded` | no tag | **Load** |
| Loading | `● Loading <name>…` blue pulse | `● Loading <name>…` | — | disabled |
| Loaded | `● <name>` green | `● Loaded — <name>` | `loaded` tag | **Unload** |
| Load failed | `● No model loaded` | `● Load failed` red | — | **Load** |

## Disk and memory effects

```mermaid
flowchart LR
    subgraph DISK["disk · ~/.cache/huggingface/hub"]
        BLOBS[("blobs/<br/>content-addressed")]
        SNAP["snapshots/&lt;sha&gt;/<br/>symlinks"]
        SNAP --> BLOBS
    end    subgraph RAM["memory · app process"]
        W["weights resident"]
        KV["KV cache per turn"]
    end

    BLOBS -->|"load"| W
    W -->|"stream_generate"| KV
    W -->|"unload + clear_cache"| FREED["returned to OS"]
    KV -->|"turn ends"| FREED
```

| Action | Disk | Memory |
|---|---|---|
| Download | + size of weights | unchanged |
| Load | unchanged | + weights |
| Chat turn | unchanged | + KV cache for the prompt |
| Unload | unchanged | weights and cache released |
| Delete | − weights (shared blobs kept if another model uses them) | unchanged |

## Size accounting

`installed_models()` follows snapshot symlinks and de-duplicates by
`(st_dev, st_ino)`. Consequences:

- A blob shared by two models is counted once per model in the model's own total,
  but the on-disk total is smaller than the sum of the cards.
- A `4.0 GB` model reports `4.0 GB`, not `8.0 GB`. This was a defect fixed in
  2.0.0; see [CHANGELOG.md](../../CHANGELOG.md).
