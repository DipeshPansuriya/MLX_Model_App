# Authentication and security

**Date:** 2026-11-25 | The full threat model and controls are in [SECURITY.md](../SECURITY.md). This
page is the short implementation map.

## Authentication and authorisation

| Surface | Authentication | Authorisation |
|---|---|---|
| App, `127.0.0.1:8765` | no user login; session token on `/api/*` except `GET /api/ping` and `POST /api/focus`; Host and Origin checks. In 2.0.9 a local caller can read the token from `/`; the unreleased source passes it in the URL fragment and never serves it | no roles |
| API server, `127.0.0.1:8080` | none; `mlx_guard.py` checks each request | no roles |

## Controls

| Control | Details |
|---|---|
| Request checks on 8765 | [SECURITY.md](../SECURITY.md) section 4.1, [API.md](../API.md) section 2 |
| API server guard | section 4.2, [ADR-009](../adr/ADR-009-api-server-guard.md) |
| Destructive actions | section 4.3 |
| Processes and model code | section 4.4 |
| Secrets and TLS | section 4.5 |
| Logs | section 4.6 |

## Vulnerability management

| Item | How |
|---|---|
| Secret scan | `python3 scripts/check_secrets.py`, also in hooks and CI ([PUSH-POLICY.md](../PUSH-POLICY.md)) |
| Security audit | `./scripts/security_audit.sh` ([SECURITY-AUDIT.md](../SECURITY-AUDIT.md)) |
| Dependencies | `pip-audit -r requirements.txt`; the CI `Security audit` job with `pip-audit` passed for 2.0.9 (2026-11-26) |
| Reporting | [root security policy](../../SECURITY.md) |

## Compliance and incidents

No compliance certification is claimed. For a suspected problem: stop the API server, quit the app,
export diagnostics (review them first), and report as the security policy describes.

## Rate limiting

None found in `app.py` or `mlx_guard.py`.

## Open and accepted risks

See [SECURITY.md](../SECURITY.md) section 5, which also lists the three Low items of the unreleased
source (browser-mode token fallback and history, two-model memory budget).
