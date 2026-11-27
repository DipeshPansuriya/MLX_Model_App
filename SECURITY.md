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
| No user authentication on the loopback ports: `/api/*` on 8765 needs a session token (except ping and focus), but in 2.0.9 any local process can read it from the app page (the unreleased source no longer serves it, which is still not user authentication); 8080 has no authentication | [docs/SECURITY.md](docs/SECURITY.md) §3, §5 |
| The background API server's `server.log` can contain request content (the app's own chat log does not record prompts) | [docs/SECURITY.md](docs/SECURITY.md) §4.6 |
| Zero required PR approvals (single maintainer) | [docs/GITHUB-POLICY.md](docs/GITHUB-POLICY.md) §5 |

## Known issues

| Issue | Severity | Record |
|---|---|---|
| In 2.0.9 the page's Content-Security-Policy only sets `frame-ancestors`, so it adds no second line of defence against script injection. No live injection was found in the parts checked. Fixed in the unreleased 2.0.10 source | Low (hardening) | [docs/SECURITY.md](docs/SECURITY.md) §5 |
| Unreleased source, browser mode (L1): the token could reach the `open` command line if `osascript` failed | Low, fixed in the unreleased source | [docs/SECURITY.md](docs/SECURITY.md) §5 |
| Unreleased source, browser mode (L2): the browser may keep the address with the token in its history (not checked live) | Low, documented known limit | [docs/SECURITY.md](docs/SECURITY.md) §5 |
| Unreleased source, two models: a model that does not fit the memory budget is loaded alone (with a warning), a switch between loaded models is not checked, and any local program can switch models through port 8080. The memory limits are calculated; their real two-model re-test has not run | Low (availability, local), known limit | [docs/SECURITY.md](docs/SECURITY.md) §5 |
| Unreleased source (I6): the policy allows inline style attributes; scripts are not affected | Info, documented known limit | [docs/SECURITY.md](docs/SECURITY.md) §5 |
| Unreleased source: the app process does not set `HF_HUB_OFFLINE`, so a Chat load may contact the Hub or prefer a same-named local folder | UNVERIFIED, not tested | [docs/SECURITY.md](docs/SECURITY.md) §5 |

Also fixed in the unreleased 2.0.10 source: port 8765 needs exactly one
`Content-Type` on a POST; `PYWEBVIEW_LOG` is ignored; the memory budget file is
written and read without following links, and bad values in it are ignored (N1,
N2); `/api/load` accepts only a completely installed model (N3).

## More detail

- [docs/SECURITY.md](docs/SECURITY.md) — threat model and controls
- [docs/SECURITY-AUDIT.md](docs/SECURITY-AUDIT.md) — the audit and its findings
- [docs/PUSH-POLICY.md](docs/PUSH-POLICY.md) — the no-credential rule
- [docs/GITHUB-POLICY.md](docs/GITHUB-POLICY.md) — branch, tag and Actions policy
