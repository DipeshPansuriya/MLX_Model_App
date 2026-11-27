---
type: Index
title: "Diagrams — MLX Studio"
date: 2026-11-24
updated: "2026-11-24 — initial set for v2.0.0"
author: "Dipesh Pansuriya"
project: mlx-studio
related:
  - ../ARCHITECTURE.md
  - ../PRD.md
  - ../adr/README.md
---

# Diagrams

Every diagram here is **Mermaid source inside Markdown**, so it renders on GitHub
with no build step and stays reviewable as text. Each file is the source of truth
for its own picture; the prose documents link to these files rather than
duplicating them.

| # | Diagram | File | Shows |
|---|---|---|---|
| 1 | Runtime view | [runtime.md](runtime.md) | Processes, ports, threads, child server |
| 2 | Chat request path | [chat-flow.md](chat-flow.md) | Send → SSE → metrics → log |
| 3 | Model lifecycle | [model-lifecycle.md](model-lifecycle.md) | Cache → served → chat → delete |
| 4 | Release flow | [release-flow.md](release-flow.md) | Version bump → DMG → manifest → installed copies |
| 5 | Update decision | [update-decision.md](update-decision.md) | How the app decides to show an update |
| 6 | State model | [state-model.md](state-model.md) | The `/api/state` snapshot shape |

## Conventions

| Convention | Meaning |
|---|---|
| Solid arrow | Synchronous call or data flow |
| Dashed arrow | Asynchronous or optional path |
| Rounded box | A process or thread |
| Cylinder | Durable file storage |
| Hexagon | A decision point |
| Node label | The component name as it appears in the source |

## Keeping diagrams true

A diagram changes in the same commit as the code it describes. If a port, a
process boundary or an endpoint changes, the affected diagram is updated and
[ARCHITECTURE.md](../ARCHITECTURE.md) is checked for a contradicting sentence.
