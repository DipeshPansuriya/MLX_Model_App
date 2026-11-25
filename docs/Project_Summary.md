# MLX Studio - Project summary

**Date:** 2026-11-25 | **Source version:** 2.0.9 (unreleased)

## Executive summary

MLX Studio is a macOS app for running local AI models on Apple Silicon through Apple's MLX
runtime: chat, model download and delete, an OpenAI-compatible API server, activity logs and
version checks. One Python backend (`app.py`), a plain HTML/CSS/JS interface (`ui/`), and a
request guard for the API server (`mlx_guard.py`). No database, cloud service or account.

## System design phases

| Phase | Topic | Answer or link |
|---|---|---|
| 0, 1 | Tool and AI environment setup | [Setup](implementation/Setup-Initialisation.md), [AI-PROMPTS.md](AI-PROMPTS.md) |
| 2 | Description | [PRD.md](PRD.md) |
| 3 | Folders and stack | Python 3.10+, `mlx`, `mlx-lm`, `huggingface_hub`, optional `pywebview`; [docs index](README.md) |
| 4 | Running | [README](../README.md) |
| 5 | Request tracing | [ARCHITECTURE.md](ARCHITECTURE.md) section 6, [chat flow](diagrams/chat-flow.md) |
| 6 | APIs | [API.md](API.md) |
| 7, 8 | Database and scaling | Not applicable: files only, one user ([DATABASE.md](DATABASE.md)) |
| 9 | Cross-cutting | [ARCHITECTURE.md](ARCHITECTURE.md) section 7 |
| 10 | Caching | Model weights in the Hugging Face cache; in-memory model scan cache re-walked after 30 s at most when unchanged. No cache server |
| 11 | Third parties | [Integrations](implementation/Integrations.md) |
| 12 | Diagrams | [diagrams/](diagrams/README.md) |
| 13 | Failure handling | [RUNBOOK.md](RUNBOOK.md) |
| 14 | Observability | Local logs and a diagnostic zip; no telemetry ([DATABASE.md](DATABASE.md)) |
| 15 | Tests and security | [TESTING.md](TESTING.md), [SECURITY.md](SECURITY.md) |
| 16 | Rate limiting | None found in `app.py` or `mlx_guard.py`. Both ports are loopback only; busy actions return 409 |
| 17 | Deployment and CI | [DEPLOYMENT.md](DEPLOYMENT.md) |
| 18, 19 | Load and capacity | One user, one loaded model. Memory sizing: [README](../README.md). Not load-tested |
| 20 | Critique | Open risks below |
| 21 | Self-check | Verification state below |

## Additional analysis

| Topic | State |
|---|---|
| Supply chain | Versions pinned in `requirements.txt`; no hash pinning. Scan result for 2.0.9: UNVERIFIED |
| Cost | No hosted service in the app. CI and release hosting are on GitHub; plan cost not reviewed |
| Bus factor | Single maintainer ([GITHUB-POLICY.md](GITHUB-POLICY.md)) |
| Privacy | Chat runs on this Mac. Network use is listed in [Integrations](implementation/Integrations.md) |
| Disaster recovery | Reinstall the app and download models again; logs are not backed up |
| Technical debt | Backend in one large file; `app.py:NNN` line references in older documents go stale |
| Modernisation | Not assessed |

## Open risks and verification

| Item | State |
|---|---|
| Token on 8765 is readable by any local program; 8080 has no authentication | Accepted ([SECURITY.md](SECURITY.md) section 5) |
| Ad-hoc signature, unsigned pkg, no notarisation | Accepted |
| Rebuilt 2.0.9 app, GUI, macOS 12, CI on GitHub | UNVERIFIED |
| Content-Security-Policy only sets `frame-ancestors` | Open, Low: known issue in 2.0.9 |
| Full test run | 1059 passed reported 2026-11-26; not re-run for this document |

## Team and tracking

Ownership: [AGENTS.md](AGENTS.md). Task tracking is in the maintainer's AI Agent hub ledger, outside
this repository.

## Quick commands

```bash
./run.sh
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider
python3 scripts/check_docs.py
./packaging/build_app.sh
```
