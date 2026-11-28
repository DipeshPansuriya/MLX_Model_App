# MLX Studio - Project summary

> **2.0.13 (unreleased) supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).

**Date:** 2026-11-28 | **Latest release:** 2.0.9 (2026-11-26, `v2.0.9`) | **Working source:** `APP_VERSION` 2.0.13, with the 2.0.10, 2.0.11, 2.0.12 and 2.0.13 changes not in the latest release ([CHANGELOG](../CHANGELOG.md))

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
| 18, 19 | Load and capacity | One user. One model in the app; the API server holds one, or up to two in the unreleased source. Memory sizing: [README](../README.md). Not load-tested |
| 20 | Critique | Open risks below |
| 21 | Self-check | Verification state below |

## Additional analysis

| Topic | State |
|---|---|
| Supply chain | Versions pinned in `requirements.txt`; no hash pinning. The CI `Security audit` job, which runs `pip-audit`, passed for the 2.0.9 release (2026-11-26) |
| Cost | No hosted service in the app. CI and release hosting are on GitHub; plan cost not reviewed |
| Bus factor | Single maintainer ([GITHUB-POLICY.md](GITHUB-POLICY.md)) |
| Privacy | Chat runs on this Mac. Network use is listed in [Integrations](implementation/Integrations.md) |
| Disaster recovery | Reinstall the app and download models again; logs are not backed up |
| Technical debt | Backend in one large file; `app.py:NNN` line references in older documents go stale |
| Modernisation | Not assessed |

## Unreleased changes (2.0.10 source, not released on 2026-11-26)

| Change | Automated tests | Not verified |
|---|---|---|
| API server keeps two models | Passed with a fake loader and the real `mlx_lm` classes | Real models: memory, safe context, cache trim, eviction, switch time, swap |
| Chat load refused beside two models; **Loaded without enough memory** warning; load-failure panel | Passed with stand-ins | Real memory, real window, VoiceOver |
| Two-model memory limits: context per model from live free memory, shrink or refuse below 8,192, prompt cache cap, **Free memory is low** | Passed with a faked `vm_stat` and stand-in models | The real two-model re-test has not run; all figures are calculated. An earlier real run, before these limits, had swap rise from 0 to 4.60 GB |
| Token handed over in the URL fragment | Passed; offscreen web view | Real window, live browser mode |
| Full Content-Security-Policy | Passed; headless Chrome and offscreen web view | Real window |
| Bundle name and icon in the Dock and confirm boxes | Passed with stubbed AppKit; offscreen alert | Real Dock, menu bar, permission prompts |
| Unwritable launch log recovery; build stops on signing or version-line failure | Passed locally | Reaches users only with a new build |
| macOS CI runs guard and packaging tests with no skips; behaviour-based packaging tests | Passed locally | First run on GitHub |

## Open risks and verification

| Item | State |
|---|---|
| Token on 8765 is readable by any local program (2.0.9; no longer served in the unreleased source); 8080 has no authentication | Accepted ([SECURITY.md](SECURITY.md) section 5) |
| Ad-hoc signature, unsigned pkg, no notarisation | Accepted |
| CI on GitHub and the released 2.0.9 app on one Mac (ping, guarded server, 403 refusals) | Passed 2026-11-26 ([DEPLOYMENT.md](DEPLOYMENT.md) sections 5.3 and 11) |
| App window and UI clicks, pkg install, DeepSeek Harness end to end, macOS 12 | UNVERIFIED |
| Content-Security-Policy only sets `frame-ancestors` | Known issue in 2.0.9; fixed in the unreleased source |
| Browser-mode token history, inline style allowance; two-model limits (load without enough memory, unchecked switch, open port 8080, memory limits not re-tested) | Known limits, unreleased source; the token fallback is fixed ([SECURITY.md](SECURITY.md) section 5) |
| App process without `HF_HUB_OFFLINE`: possible Hub contact or same-named local folder on a Chat load | UNVERIFIED |
| Security review of the unreleased token, CSP and two-model changes, and re-check of the fixes | Verdict SHIP at source level (2026-11-26); three later fixes (N1 to N3) and the later memory limits have no security re-check recorded here |
| Code review of the same changes | First review: Needs changes. Re-check of the fixes: Pass (F1 to F8, F10 to F12), 2026-11-26. A later re-check of the F9 and follow-up fixes passed. The review of the memory limits said needs changes; no completed re-check of the fixes made after it is recorded here |
| Full test run | 2.0.9: 1059 passed reported. Unreleased tree with the memory limits: 1503 passed, 0 skipped, reported 2026-11-26; not re-run for this document |

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
