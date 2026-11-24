---
type: Index
title: "MLX Studio — documentation index"
date: 2026-11-24
updated: "2026-11-24 — initial documentation set for v2.0.0"
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.0"
related:
  - PRD.md
  - ARCHITECTURE.md
  - DESIGN.md
  - DEPLOYMENT.md
  - SECURITY.md
  - TESTING.md
  - RUNBOOK.md
  - adr/README.md
  - diagrams/README.md
---

# MLX Studio — documentation index

MLX Studio is a native macOS application for running local AI models on Apple
Silicon through Apple's MLX runtime. It provides a chat surface with streaming
inference, a model manager backed by the Hugging Face cache, an
OpenAI-compatible HTTP server, a persistent activity log, and version/update
reporting for both the app and its runtime packages.

This directory holds the product, architecture and operational documentation for
release **2.0.0**. Every statement below is derived from the shipped source at
tag [`v2.0.0`](https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.0);
where a document describes intent rather than code, it says so explicitly.

## Reading order

| # | Document | Answers |
|---|---|---|
| 1 | [PRD.md](PRD.md) | What the product is, who it is for, what is in and out of scope, how acceptance is judged |
| 2 | [ARCHITECTURE.md](ARCHITECTURE.md) | How the system is built: processes, modules, state, data flow, threading |
| 3 | [DESIGN.md](DESIGN.md) | What the interface looks like and why: layout, tokens, states, interaction rules |
| 4 | [DEPLOYMENT.md](DEPLOYMENT.md) | How a build becomes an installed app: bundling, signing, DMG, release channel |
| 5 | [SECURITY.md](SECURITY.md) | Trust boundaries, what is exposed, what is deliberately not defended |
| 5a | [SECURITY-AUDIT.md](SECURITY-AUDIT.md) | The 2026-11-24 audit: 21 checks, 3 findings remediated, re-run procedure |
| 5b | [PUSH-POLICY.md](PUSH-POLICY.md) | No-credential rule, 19 secret rules, hooks, suppression, CI enforcement |
| 6 | [TESTING.md](TESTING.md) | What was verified, how, and what remains unverified |
| 7 | [RUNBOOK.md](RUNBOOK.md) | Operator procedures: start, stop, recover, rotate a release |
| 8 | [adr/README.md](adr/README.md) | The decisions behind the design, with alternatives rejected |
| 9 | [diagrams/README.md](diagrams/README.md) | Rendered views of the runtime, request paths and release flow |

## Document conventions

| Convention | Meaning |
|---|---|
| **Built** | Present in the shipped code and exercised during release verification |
| **Design** | Decided and documented, not necessarily implemented |
| **Open** | Not decided; the document names who must decide |
| Front matter | `type`, `title`, `date`, `author`, `project`, `related` — machine-readable index metadata |
| Section numbering | Stable within a document; cross-references use `FILE.md §N` |
| Anchors | Source references use `path:line` against the tagged tree |

## Status at a glance

| Area | State | Evidence |
|---|---|---|
| Chat with streaming inference | Built | [TESTING.md](TESTING.md) §3 |
| Model load / unload with cache release | Built | [TESTING.md](TESTING.md) §3.2 |
| Model download and delete | Built | [TESTING.md](TESTING.md) §3.3 |
| OpenAI-compatible server | Built | [TESTING.md](TESTING.md) §3.4 |
| Activity log | Built | [TESTING.md](TESTING.md) §3.5 |
| Version and update reporting | Built | [TESTING.md](TESTING.md) §3.6 |
| `.app` + `.dmg` packaging | Built | [DEPLOYMENT.md](DEPLOYMENT.md) §3 |
| GitHub release with asset | Built | [DEPLOYMENT.md](DEPLOYMENT.md) §5 |
| Developer ID signing / notarisation | Open | [SECURITY.md](SECURITY.md) §5 |
| Security audit | Built — 21 checks, 3 findings fixed | [SECURITY-AUDIT.md](SECURITY-AUDIT.md) |
| Secret scanning on commit and push | Built — 19 rules, 3 enforcement points | [PUSH-POLICY.md](PUSH-POLICY.md) |
| CI security workflow | Built — 3 jobs on push and PR | [PUSH-POLICY.md](PUSH-POLICY.md) §7 |
| Automated CI build on tag | Open | [DEPLOYMENT.md](DEPLOYMENT.md) §7 |
| Multi-turn context trimming | Open | [PRD.md](PRD.md) §10 |

## Source layout

```
MLX_Model_App/
├── app.py                      # backend: HTTP + JSON/SSE API, native window host
├── ui/                         # single-page interface (no build step)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── packaging/                  # .app/.dmg builder, launcher, manifest generator
├── scripts/                    # security_audit.sh, check_secrets.py, install_hooks.sh
├── .githooks/                  # pre-commit, pre-push
├── .github/workflows/          # security.yml
├── release/latest.json         # update manifest served to installed copies
├── docs/                       # this documentation set
├── screenshots/                # captured views referenced by DESIGN.md
├── Run_MLX_Manager.command     # Finder launcher
└── run.sh                      # terminal launcher
```

## Changing this documentation

Documentation changes ship in the same commit as the code they describe. A
release that changes behaviour without updating the affected document is
incomplete; [RUNBOOK.md](RUNBOOK.md) §7 lists the checklist.
