# Security policy

## Supported versions

| Version | Supported |
|---|---|
| 2.0.x | ✅ current release |
| < 2.0 | ❌ superseded |

## Reporting a vulnerability

**Do not open a public issue for a security problem.**

Use GitHub's private reporting:
[**Report a vulnerability**](https://github.com/DipeshPansuriya/MLX_Model_App/security/advisories/new)

Private vulnerability reporting is enabled on this repository, so the report stays
visible only to the maintainer until an advisory is published.

Please include:

| Field | Detail |
|---|---|
| Affected version | e.g. `2.0.0`, from the sidebar or `GET /api/ping` |
| Component | UI, HTTP API, packaging, hooks, CI, or a dependency |
| Reproduction | The smallest steps that show the problem |
| Impact | What an attacker gains, and from where (local process, downloaded model, network) |
| Suggested fix | Optional |

## What to expect

| Stage | Target |
|---|---|
| Acknowledgement | 3 working days |
| Triage and severity | 7 working days |
| Fix or mitigation | 30 days for high and critical |
| Public advisory | After a fixed release ships |

## Scope

In scope:

- The HTTP API on `127.0.0.1:8765` and the OpenAI-compatible server on `127.0.0.1:8080`
- Static file serving and path handling
- The `.app` bundle, DMG and update manifest
- Git hooks, CI workflows and release scripts
- Credential handling in the repository

Out of scope:

- Anything requiring an attacker who already has your macOS user account
- Model weights and their provenance (data, not code)
- Vulnerabilities in `mlx`, `mlx-lm` or `huggingface_hub` — report those upstream
- The absence of notarisation, which is a documented accepted risk

## Accepted risks

These are known and documented, not vulnerabilities:

| Risk | Record |
|---|---|
| Ad-hoc signature, Gatekeeper warning on first launch | [ADR-007](docs/adr/ADR-007-adhoc-signing.md) |
| No authentication on the loopback API | [docs/SECURITY.md](docs/SECURITY.md) §5 |
| Prompts recorded in the activity log | [docs/SECURITY.md](docs/SECURITY.md) §4.6 |
| Zero required PR approvals (single maintainer) | [docs/GITHUB-POLICY.md](docs/GITHUB-POLICY.md) §5 |

## More detail

- [docs/SECURITY.md](docs/SECURITY.md) — threat model and controls
- [docs/SECURITY-AUDIT.md](docs/SECURITY-AUDIT.md) — the audit and its findings
- [docs/PUSH-POLICY.md](docs/PUSH-POLICY.md) — the no-credential rule
- [docs/GITHUB-POLICY.md](docs/GITHUB-POLICY.md) — branch, tag and Actions policy
