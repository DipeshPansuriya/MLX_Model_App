---
type: Security
title: "Security — MLX Studio"
status: "Matches the 2.0.9 source tree (not released)"
date: 2026-11-25
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.9"
related:
  - ARCHITECTURE.md
  - DEPLOYMENT.md
  - PRD.md
---

# Security — MLX Studio

This file describes the source tree. An app built before 2.0.9, or an API server
still running from it, does not have the 2.0.9 controls (see §5).

## 1. Threat model

MLX Studio is a single-user desktop app with two HTTP servers on `127.0.0.1`.
Nothing listens on the network, but loopback is still reachable by:

| Attacker | Example | Position |
|---|---|---|
| A web page in your browser | Cross-site POST, DNS rebinding | Refused on both ports (§4.1, §4.2) |
| Another process or user on this Mac | `curl` against either port | **Not blocked.** Accepted risk (§5) |
| A model repository | Custom code shipped with weights | Custom code stays disabled (§4.4) |
| A tampered download | Replaced installer or update file | Checksums only; no publisher signature (§5) |

There is no account system and no telemetry in the app.

## 2. Trust boundaries

| Boundary | Control |
|---|---|
| Browser to app, port 8765 | Host and Origin checks; session token on `/api/*` (not `/api/ping`, `/api/focus`); POST also needs JSON |
| Client to API server, port 8080 | Request guard `mlx_guard.py` |
| App to API server process | Separate process, argument list (no shell), pidfile identity check |
| App to internet | Hugging Face (search, download), PyPI (version check, `pip install`), update manifest URL if set |
| App to disk | Static files from a startup allowlist; `~/.mlx_studio` owner-only |

## 3. What is exposed

| Surface | Bind | Authentication |
|---|---|---|
| UI and API | `127.0.0.1:8765` | No user authentication. Session token on `/api/*` GET and POST (not `GET /api/ping` or `POST /api/focus`) |
| OpenAI-compatible API server | `127.0.0.1:8080` | None |

## 4. Controls in place

### 4.1 App server, port 8765

| Rule | Refusal | Code (`app.py`) |
|---|---|---|
| Exactly one `Host` header, `127.0.0.1:<port>` or `localhost:<port>` (GET and POST) | 403 | `_foreign_request` |
| `Origin`, if sent, must be one of those two (GET and POST) | 403 | `_foreign_request` |
| POST must be `application/json` | 415 | `_post_refusal` |
| Every `/api/*` GET and POST must carry `X-MLX-Token`; `GET /api/ping` and `POST /api/focus` are exempt | 403 | `do_GET`, `_post_refusal` |
| Malformed or oversized body (limit 8 MiB), connection closed | 400, 411, 413 | `_body` |
| Framing by another page is denied | — | response headers |
| Every reply carries `X-Content-Type-Options: nosniff` | — | `end_headers` |

The token is new on every app start and is written into `index.html` when it is
served. `GET /` needs no token, so any local program or macOS user can read it
from there: it is a defence against web pages, **not** against local software.
`/api/ping` stays open because a second launch cannot know the token; it returns
only `ok` and the version.

Static files come from a list built at startup from `ui/` (html, css, js, svg,
png, ico). The request path is only a lookup key, so it never builds a
filesystem path.

### 4.2 API server guard, port 8080

MLX Studio starts `mlx_lm server` through `mlx_guard.py`.

| Request | Answer |
|---|---|
| `Host` missing, repeated, or not `127.0.0.1`, `localhost` or `[::1]` (any port) | 403 |
| `Origin` sent and not an app origin, including `null`; `*` is ignored | 403 |
| POST that is not `application/json` | 415 |
| `model` other than `default_model`, the loaded model, or a complete downloaded repo; any local path | 400 |
| `draft_model` other than empty or `default_model` | 400 |
| Any `adapters` value | 400 |
| An image URL in `messages` that is not a `data:` URL, or messages whose images cannot be read | 400 |

| Also | Detail |
|---|---|
| Offline | `HF_HUB_OFFLINE=1` in the server process; `MLXLM_USE_MODELSCOPE` removed |
| Fail closed | If the installed `mlx_lm` lacks the patched hook points or image helpers, or cannot be imported, the server does not start (`mlx_guard: refusing to start ...`) |
| CORS | The app passes only its own two origins to `--allowed-origins` |
| Replies | One change: an optional tool call argument that is `null`, and that the request's schema does not allow to be `null`, is removed. It only removes, never adds or changes a value ([API.md](API.md) section 1) |

Clients must use a loopback host name, send `Content-Type: application/json` on
POST, and name a model that is already downloaded. See
[ADR-009](adr/ADR-009-api-server-guard.md).

### 4.3 Destructive actions

| Action | Guard |
|---|---|
| Delete a model | 409 while the model is in use; confirmation dialog in the UI. The guard records the name of each accepted model so the app does not delete it while served |
| Clear the activity log | Confirmation dialog in the UI; removes `activity.log*` only |
| Install or reinstall MLX packages | Explicit action; fixed package list from `app.py` |

The dialogs are UI controls. A direct API call skips them.

### 4.4 Processes and model code

- The API server and `pip` are started from argument lists with `sys.executable`.
- A process is treated as the app's server only if its command line, port and
  start time match the pidfile record.
- The app never passes `trust_remote_code`. In the installed `mlx_lm` it
  defaults to off, and a model that needs a custom module is refused.

### 4.5 Secrets and TLS

- The app stores no credential. `huggingface_hub` may use your `HF_TOKEN`; the
  diagnostic bundle records it only as `set` or empty.
- `truststore` is used for the PyPI and update-manifest requests when it is
  available, otherwise the default CA bundle. The app does not switch
  certificate verification off.
- Repository rule and scanner: [PUSH-POLICY.md](PUSH-POLICY.md),
  `scripts/check_secrets.py`.

### 4.6 Logs

| File in `~/.mlx_studio/logs/` | What it can hold |
|---|---|
| `activity.log` | Action, model, status. A chat entry holds token count and speed, not the prompt. A failed chat holds error text up to 300 characters |
| `app.log` | Lifecycle events, HTTP errors, worker failures |
| `server.log` | Raw `mlx_lm` output of a background API server. **It can contain request content**: `mlx_lm` logs the body when the JSON is invalid or is not an object (read in 0.32.2 source). The log export zips it |

Directories are set to `0700`, and the files the app writes to `0600`. Review a
diagnostic bundle before sharing: it includes the logs, local paths, model names and the value of
`MLX_STUDIO_UPDATE_URL`.

## 5. Accepted and open risks

| Risk | State | Reason or workaround |
|---|---|---|
| Local processes and users can call 8765. Reads and POSTs need the token, but any local caller can get it from `index.html`, then read state and logs, export diagnostics, delete models or run install | Accepted | Single-user desktop. Quit the app when others share the Mac |
| No authentication on 8080; local processes can generate and switch between downloaded models | Accepted | Stop the server when not in use |
| App bundle is ad-hoc signed, the `.pkg` is unsigned, nothing is notarised | Accepted | No Apple Developer account, [ADR-007](adr/ADR-007-adhoc-signing.md) |
| One-line installer runs a script from `main` and takes checksums from the same repository | Accepted | Proves the download is intact, not who published it. Read the script first |
| The update manifest URL is not forced to HTTPS. A download link is shown only if it is HTTPS; its checksum is displayed, not enforced | Accepted | Set an HTTPS URL; compare the checksum yourself |
| Package versions are pinned; `pip` is not run with hash checking | Accepted | — |
| No extra integrity check on model weights beyond `huggingface_hub` | Accepted | — |
| A background server started by an older build is adopted and stays unguarded until restarted; the app flags it | Release step | If the app shows **server unprotected**, open API Server and press **Restart now** |
| The page's Content-Security-Policy only sets `frame-ancestors 'none'`; no `default-src` or `script-src`, so an unescaped value would be able to run inline script. No such place was found in the new cards, search fields or download cards | Open, Low: known issue in 2.0.9 | Hardening not yet done |

## 6. Hardening guidance for users

| If you… | Then |
|---|---|
| Share the Mac with other accounts | Treat both ports as open to them; quit the app and stop the server |
| Need the API from another machine | Use an SSH tunnel, `ssh -L 8080:127.0.0.1:8080 you@mac`, and call it as `127.0.0.1` |
| Need the server on the LAN | Not supported; the bind is fixed |
| Want to check a download | Compare `shasum -a 256 <file>` with the checksum published for that release |
| Share diagnostics | Open the zip and remove anything private |

## 7. What would change the model

| Change | Consequence |
|---|---|
| Binding to `0.0.0.0` | Authentication becomes mandatory |
| Adding accounts | Credential storage and sessions to protect |
| Enabling `trust_remote_code` | Model repositories gain code execution |
| Auto-installing updates | The manifest becomes a code-execution channel; signing becomes mandatory |
| Upgrading `mlx-lm` | Re-check the guard hooks; run `tests/test_guard.py` |

## 8. Verification

| Check | Evidence | Date |
|---|---|---|
| Guard policy, real handler, fail-closed, launch | `tests/test_guard.py`; 545 tests passed in that run | 2026-11-25 |
| Guard end to end with `mlx_lm` 0.32.2 and tiny random-weight models: listener on loopback, refusals as in §4.2 | Same run, temporary port | 2026-11-25 |
| App server Host, Origin, token and body rules | `tests/test_security.py`; full run of 714 passed reported for this tree, not re-run for this document | 2026-11-25 |
| Image URL refusal with the installed `mlx_lm` handler and a stubbed download | `tests/test_guard.py`, same reported run | 2026-11-25 |
| `null` argument rule: code review and edge-case probes; full suite 1059 passed | Independent security check, offline; live server not probed by it | 2026-11-26 |
| Guard with a real model, rebuilt app, GUI client | — | UNVERIFIED |
| Signature, checksums and dependency scan for 2.0.9 | — | UNVERIFIED; run `./scripts/security_audit.sh` |

[SECURITY-AUDIT.md](SECURITY-AUDIT.md) is the historical audit of 2.0.0. To
report a vulnerability, use [the security policy](../SECURITY.md).

## 9. Sources read for this file

`app.py`, `mlx_guard.py`, `scripts/install.sh`, `packaging/build_app.sh`,
`scripts/security_audit.sh`, installed `mlx_lm` (`server.py`, `utils.py`).
