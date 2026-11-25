# MLX Studio - Agent and team assignment map

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
