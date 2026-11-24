---
type: Security
title: "Security — MLX Studio"
status: "Built at tag v2.0.0 (2026-11-24)"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.0"
related:
  - ARCHITECTURE.md
  - DEPLOYMENT.md
  - PRD.md
---

# Security — MLX Studio

## 1. Threat model in one paragraph

MLX Studio is a single-user desktop application. It binds two HTTP servers to
`127.0.0.1` and executes local model weights. The realistic threats are therefore
**local**: another process on the same Mac reaching the unauthenticated API, a
malicious model repository executing code through the tokenizer, a path-traversal
attempt against the static handler, and a tampered update manifest. Remote
attackers are out of scope because nothing listens beyond loopback. There is no
account, no credential store, no telemetry and no server-side data.

## 2. Trust boundaries

| Boundary | Inside | Outside | Control |
|---|---|---|---|
| Loopback HTTP | UI, API clients | Any other local process | None — see §4.1 |
| Python process | `app.py`, `mlx_lm`, weights | OS, filesystem | Standard process isolation |
| Child server | `mlx_lm server` | Parent process | Separate PID; terminate/kill on stop |
| Remote fetch | PyPI, Hugging Face, manifest host | Local process | TLS via `truststore`; explicit action |
| Filesystem | `~/.mlx_studio`, HF cache | Everything else | Static handler confined to `ui/` |

## 3. What is exposed

| Surface | Bind | Auth | Reachable by |
|---|---|---|---|
| UI + API | `127.0.0.1:8765` | none | Any local process, any local user |
| OpenAI-compatible server | `127.0.0.1:8080` | none | Any local process, any local user |
| Static files | `127.0.0.1:8765/*` | none | Confined to `ui/` by path resolution |

Both binds are literal `127.0.0.1`, not `0.0.0.0`. Nothing is reachable from the
LAN.

## 4. Controls in place

### 4.1 Path traversal

`Handler._static` (`app.py:560`) resolves the requested path against `UI_DIR` and
rejects anything that is not a descendant of it:

```python
root = UI_DIR.resolve()
target = (root / rel).resolve()
if not target.is_relative_to(root) or not target.is_file():
    self._send(404, b"not found", "text/plain")
```

`is_relative_to` compares path **components**, so a sibling directory such as
`ui-evil/` cannot pass by sharing the `ui` string prefix. An earlier
`str.startswith` check had exactly that flaw; see
[SECURITY-AUDIT.md](SECURITY-AUDIT.md) F-01.

### 4.2 Destructive actions require intent

| Action | Guard |
|---|---|
| Delete a model | `409` if it is the loaded model; confirmation dialog in the UI |
| Clear the log | Confirmation dialog |
| Stop the server | Explicit endpoint; idempotent |
| Install / update | Explicit endpoint; refuses if already running (`409`) |

### 4.3 Subprocess hygiene

- The API server is started with an argument list, never a shell string.
- `sys.executable` is used, so the child runs the same interpreter as the parent.
- The child binds `127.0.0.1` explicitly.
- Stop is `terminate()` → `wait(5)` → `kill()`, so a wedged process cannot linger.

### 4.4 TLS verification

PyPI and manifest fetches use `urllib` with certificate verification on.
python.org builds on macOS ship without a usable system trust store, which
previously produced `CERTIFICATE_VERIFY_FAILED`; `truststore.inject_into_ssl()`
is called first so the macOS keychain is used. Verification is **never** disabled.

### 4.5 No secrets in the repository

`.gitignore` excludes `.env`, `*.pem`, `*.key`, `secrets.json`, model weights and
build output. No credential is read, stored or transmitted by the app.

### 4.6 Log hygiene

`log_activity` truncates error detail to 300 characters and never writes
environment variables, tokens or file contents. Prompts are recorded up to 120
characters for chat entries — a deliberate product choice so the Activity tab can
answer "what did I send to which model".

Because the log holds prompt text, it is owner-only: `~/.mlx_studio` is `0700` and
`activity.log` is `0600`, enforced on every write.

## 5. Accepted risks

| Risk | Severity | Why accepted | Mitigation available |
|---|---|---|---|
| **No auth on loopback API** | Medium | Single-user desktop; adding auth would break `curl` and local tooling | Bind check, documented in §6; a token could be added later |
| **Ad-hoc signature, not notarised** | Medium | No Apple Developer account | Documented right-click → Open; Developer ID is PRD OD-1 |
| **`trust_remote_code` not enabled** | — | Deliberate: no remote code execution from model repos | Users who need it must use the CLI |
| **Prompts stored in the activity log** | Low | Required by the product ("what did I send") | Log is user-owned and clearable |
| **Model weights are trusted input** | Medium | Weights are data, but tokenizers can carry code | `trust_remote_code` stays off |
| **No integrity check on downloaded weights** | Low | Hugging Face serves content-addressed blobs | Library-level hashing |
| **Manifest URL is user-supplied** | Low | Whoever sets the env var already controls the process | HTTPS + SHA-256 published for manual verification |

## 6. Hardening guidance for users

| If you… | Then |
|---|---|
| Share the Mac with other accounts | Treat the API as open to those accounts; stop the server when not in use |
| Expose the API to another machine | Do not. Use an SSH tunnel: `ssh -L 8080:127.0.0.1:8080 you@mac` |
| Need the server on the LAN | Not supported; the bind is fixed at `127.0.0.1` |
| Want a verified download | Compare the DMG's `shasum -a 256` with `release/latest.json` |
| Want to audit prompts | Read `~/.mlx_studio/activity.log` |

## 7. What would change the model

| Change | Consequence |
|---|---|
| Binding to `0.0.0.0` | Auth becomes mandatory before shipping |
| Adding an account system | Introduces credential storage and session management |
| Enabling `trust_remote_code` | Model repositories gain code execution |
| Auto-installing updates | The manifest becomes a code-execution channel; signing becomes mandatory |
| Adding telemetry | Creates a data-egress path that contradicts the premise |

## 8. Verification performed

| Check | Result |
|---|---|
| Path traversal via `/../app.py` | 404 |
| Sibling-prefix bypass via `/../ui-evil/leak.txt` | 404 (was 200; fixed) |
| Static handler confined to `ui/` | confirmed by component-wise containment |
| Both listeners bound to loopback | `lsof` shows `127.0.0.1` only |
| No credential in repo | `.gitignore` + tracked-file scan |
| TLS verification enabled | `truststore` injected; verification never disabled |
| Signature valid | `codesign --verify --deep --strict` passes |
| DMG checksum | matches published SHA-256 |
| Dependency advisories | `pip-audit`: none known |
| Runtime log permissions | `0700` / `0600` |

The full audit, including three remediated findings, is in
[SECURITY-AUDIT.md](SECURITY-AUDIT.md). Re-run it with
`./scripts/security_audit.sh`.

## 9. Sources read for this file

| Source | Used for |
|---|---|
| `app.py` (`_static`, `_body`, `log_activity`, subprocess calls, binds) | §4 controls |
| `packaging/build_app.sh` | Signing behaviour |
| `.gitignore` | §4.5 |
| `scripts/security_audit.sh` | §8 results |
| Release verification run, 2026-11-24 | §8 table |
