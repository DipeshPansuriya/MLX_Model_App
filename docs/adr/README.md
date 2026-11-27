---
type: Index
title: "Architecture decision records — index"
date: 2026-11-24
updated: "2026-11-28 — ADR-012 added (unreleased, 2.0.13 source)"
author: "Dipesh Pansuriya"
project: mlx-studio
related:
  - ../ARCHITECTURE.md
  - ../PRD.md
  - ../DEPLOYMENT.md
---

# Architecture decision records — index

Each record states one decision, the alternatives considered and why they were
rejected, and the consequences accepted. Records are immutable once accepted: a
change of mind is a new record that supersedes the old one.

| ADR | Title | Status | Supersedes |
|---|---|---|---|
| [ADR-001](ADR-001-no-web-framework.md) | No web framework: stdlib `ThreadingHTTPServer` | Accepted | — |
| [ADR-002](ADR-002-web-ui-native-window.md) | Web UI hosted in a native `pywebview` window | Accepted | — |
| [ADR-003](ADR-003-in-process-inference.md) | Inference in-process, API server as a child process | Superseded | ADR-011 |
| [ADR-004](ADR-004-sse-streaming.md) | SSE for chat, polling for state | Accepted | — |
| [ADR-005](ADR-005-jsonl-activity-log.md) | JSON-lines activity log instead of a database | Accepted | — |
| [ADR-006](ADR-006-manifest-updates.md) | Manifest-based app updates, PyPI for packages | Accepted | — |
| [ADR-007](ADR-007-adhoc-signing.md) | Ad-hoc code signature for distribution | Accepted, revisit | — |
| [ADR-008](ADR-008-single-model-slot.md) | One resident model at a time | Superseded | ADR-011 |
| [ADR-009](ADR-009-api-server-guard.md) | Request guard on the API server | Accepted | — |
| [ADR-010](ADR-010-two-models-one-process.md) | API server keeps two models in one process (unreleased, 2.0.10) | Accepted, revisit | — |
| [ADR-011](ADR-011-chat-through-the-api-server.md) | Chat asks the running API server (unreleased, 2.0.13) | Accepted | — |
| [ADR-012](ADR-012-load-test.md) | Load test in a supervisor process (unreleased, 2.0.13) | Accepted | — |

## Decision map

| Concern | Decision |
|---|---|
| Server runtime | ADR-001 |
| UI host | ADR-002 |
| Where inference runs | ADR-003 |
| How tokens reach the browser | ADR-004 |
| How activity is stored | ADR-005 |
| How updates are discovered | ADR-006 |
| How the bundle is trusted | ADR-007 |
| How many models are resident | ADR-008 |
| What the API server accepts | ADR-009 |
| How many models the API server keeps | ADR-010 |
| Where Chat replies come from | ADR-011 |

## Status words

| Word | Meaning |
|---|---|
| **Accepted** | In force; the code implements it |
| **Accepted, revisit** | In force, with a known trigger for reconsideration |
| **Superseded** | Replaced by a later record; kept for history |
| **Proposed** | Written, not adopted |

## Writing a new record

1. Take the next number.
2. Copy the front matter and section shape from an existing record.
3. State the decision in one sentence in §1.
4. List at least two rejected alternatives with the reason each lost.
5. Record consequences, including the bad ones.
6. Add a row to the table above and to the decision map.
7. If it replaces an earlier record, mark that record **Superseded** and link forward.
