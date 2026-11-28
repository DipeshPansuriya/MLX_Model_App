---
type: Index
title: "MLX Studio — documentation index"
date: 2026-11-24
updated: "2026-11-28 — release status updated for v2.0.13"
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.9"
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

> **2.0.13 supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).
>
> In this file **unreleased** means not in 2.0.9: those changes shipped in 2.0.10 to 2.0.13 (latest release, 2026-11-28).

MLX Studio is a native macOS application for running local AI models on Apple
Silicon through Apple's MLX runtime. It provides a chat surface with streaming
inference, a model manager backed by the Hugging Face cache, an
OpenAI-compatible HTTP server, a persistent activity log, and version/update
reporting for both the app and its runtime packages.

This directory holds the product, architecture and operational documentation. It is
was last reviewed in full against **2.0.9** (`v2.0.9`, 2026-11-26). The latest
release is **2.0.13** (2026-11-28).
Dated records (audit, recorded test results, "Status at a glance" evidence)
describe the release named in them. Where a document describes intent rather than
code, it says so.

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
| 5c | [BRANCH-POLICY.md](BRANCH-POLICY.md) | **Single source of truth**: every control in force, planned, cost and verification |
| 5c-i | [GITHUB-POLICY.md](GITHUB-POLICY.md) | Detailed ruleset and workflow reference |
| 5d | [REPO-HARDENING-CHECKLIST.md](REPO-HARDENING-CHECKLIST.md) | Reusable 79-item checklist: 11 areas, verify commands, cost, scorecard |
| 6 | [TESTING.md](TESTING.md) | What was verified, how, and what remains unverified |
| 7 | [RUNBOOK.md](RUNBOOK.md) | Operator procedures: start, stop, recover, rotate a release |
| 8 | [adr/README.md](adr/README.md) | The decisions behind the design (up to [ADR-012](adr/ADR-012-load-test.md)), with alternatives rejected |
| 9 | [diagrams/README.md](diagrams/README.md) | Rendered views of the runtime, request paths and release flow |
| 10 | [API.md](API.md) | Endpoints, request rules, chat stream |
| 11 | [DATABASE.md](DATABASE.md) | Stored files (there is no database) |
| 12 | [implementation/](implementation/Setup-Initialisation.md) | [Setup](implementation/Setup-Initialisation.md), [features](implementation/Feature-Development.md), [auth and security](implementation/Authentication-Security.md), [MCP](implementation/MCP-Connection.md), [integrations](implementation/Integrations.md), [testing and debugging](implementation/Testing-QA-Debugging.md) |
| 13 | [AGENTS.md](AGENTS.md), [AI-PROMPTS.md](AI-PROMPTS.md) | Ownership and suggested prompts |
| 14 | [Project_Summary.md](Project_Summary.md) | One-page summary and open risks |

## Document conventions

| Convention | Meaning |
|---|---|
| **Built** | Present in the shipped code and exercised during release verification |
| **Design** | Decided and documented, not necessarily implemented |
| **Open** | Not decided; the document names who must decide |
| Front matter | `type`, `title`, `date`, `author`, `project`, `related` — machine-readable index metadata |
| Section numbering | Stable within a document; cross-references use `FILE.md §N` |
| Anchors | Older `path:line` references point at the release named in that document |

## Status at a glance

| Area | State | Evidence |
|---|---|---|
| Chat with streaming inference | Built | [TESTING.md](TESTING.md) §7 |
| Model load / unload with cache release (released builds; replaced in 2.0.13, ADR-011) | Built | [TESTING.md](TESTING.md) §7 |
| Model download and delete | Built | [TESTING.md](TESTING.md) §7 |
| OpenAI-compatible server | Built | [TESTING.md](TESTING.md) §7 |
| Activity log | Built | [TESTING.md](TESTING.md) §7 |
| Version and update reporting | Built | [TESTING.md](TESTING.md) §7 |
| `.app` + `.dmg` packaging | Built | [DEPLOYMENT.md](DEPLOYMENT.md) §3 |
| GitHub release with asset | Built | [DEPLOYMENT.md](DEPLOYMENT.md) §5 |
| Developer ID signing / notarisation | Open | [SECURITY.md](SECURITY.md) §5 |
| Security audit | Built — 21 checks, 3 findings fixed | [SECURITY-AUDIT.md](SECURITY-AUDIT.md) |
| Secret scanning on commit and push | Built — 19 rules, 3 enforcement points | [PUSH-POLICY.md](PUSH-POLICY.md) |
| CI security workflow | Built — 3 jobs on push and PR | [PUSH-POLICY.md](PUSH-POLICY.md) §7 |
| Branch protection on `main` | Built — ruleset, no bypass; 8 required checks in the script, the eighth not yet applied | [BRANCH-POLICY.md](BRANCH-POLICY.md) §2 |
| CodeQL code scanning | Built — 3 languages, weekly, 3 alerts fixed | [BRANCH-POLICY.md](BRANCH-POLICY.md) §7.2 |
| Automated PR review | Built — sticky report comment | [GITHUB-POLICY.md](GITHUB-POLICY.md) §3.1 |
| Automated CI build on tag | Built: the `v2.0.9` tag run passed 6 of 6 jobs (2026-11-26) | [DEPLOYMENT.md](DEPLOYMENT.md) §8 |
| Multi-turn context trimming | Open | [PRD.md](PRD.md) §10 |
| 2.0.10 (two models removed again in 2.0.13): two models on the API server, token handoff, full CSP, Dock icon, launcher, build and CI fixes | Released after 2.0.9; real window and real two-model memory UNVERIFIED; the re-test of the new two-model memory limits has not run | [CHANGELOG](../CHANGELOG.md) |

## Source layout

```
MLX_Model_App/
├── app.py                      # backend: HTTP + JSON/SSE API, native window host
├── mlx_guard.py                # request guard for the API server
├── ui/                         # single-page interface (no build step)
│   ├── index.html
│   ├── style.css
│   └── app.js
├── packaging/                  # .app/.dmg/.pkg builder, launcher, manifest generator
├── scripts/                    # install.sh, security_audit.sh, check_secrets.py, check_docs.py
├── .githooks/                  # pre-commit, pre-push
├── .github/                    # workflows (security, quality, pr-review), CODEOWNERS
├── release/latest.json         # update manifest served to installed copies
├── release/SHA256SUMS          # release asset checksums, read by the installer
├── tests/                      # pytest suite and UI harness
├── docs/                       # this documentation set
├── screenshots/                # captured views referenced by DESIGN.md
├── Run_MLX_Manager.command     # Finder launcher
└── run.sh                      # terminal launcher
```

## Changing this documentation

Documentation changes ship in the same commit as the code they describe. A
release that changes behaviour without updating the affected document is
incomplete; [RUNBOOK.md](RUNBOOK.md) §7 lists the checklist.
