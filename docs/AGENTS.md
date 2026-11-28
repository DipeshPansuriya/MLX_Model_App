# MLX Studio - Agent and team assignment map

> **2.0.13 supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).
>
> In this file **unreleased** means not in 2.0.9: those changes shipped in 2.0.10 to 2.0.13 (latest release, 2026-11-28).

**Date:** 2026-11-25 | **Team rules:** the AI Agent hub rulebook (not part of this repository)

## 1. Project

Single-user macOS app for local MLX models. See [PRD.md](PRD.md) and [ARCHITECTURE.md](ARCHITECTURE.md).

## 2. Work assignment

Dipesh talks to Mira (orchestrator). Mira gives each task one named owner and a ledger row.

| Area | Files | Owner |
|---|---|---|
| App code | `app.py`, `mlx_guard.py`, `ui/` | arjun-fullstack |
| CI, packaging, release | `.github/`, `packaging/`, `scripts/install.sh` | anand-devops |
| Tests and UI verification | `tests/` | aditya-qa |
| Security review | whole repository, [SECURITY.md](SECURITY.md) | karan-security |
| Code review | changes before merge | varun-review |
| Design decisions | [adr/](adr/README.md) | mahesh-architect |
| Documentation | `docs/`, `README.md`, `CHANGELOG.md` | riya-content |

A task may give an owner a narrower or different file lane. The task brief wins over this table.

## 3. Handoff

- Parallel agents work in separate lanes and do not share a file.
- "Done" needs evidence: command and output, file path or diff. Otherwise write `UNVERIFIED`.
- Publishing a release or anything else outward waits for Dipesh.

## 4. Rules for any agent or contributor

- Follow [CONTRIBUTING.md](../CONTRIBUTING.md) and run its checks before handing over.
- Never commit a credential ([PUSH-POLICY.md](PUSH-POLICY.md)).
- Branch and merge rules: [BRANCH-POLICY.md](BRANCH-POLICY.md).
- Verify claims against current code, not against older documents.
