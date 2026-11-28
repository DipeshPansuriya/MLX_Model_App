---
type: ADR
title: "ADR-009 — Request guard on the API server"
status: "Superseded (2.0.13, unreleased)"
date: 2026-11-25
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../SECURITY.md
  - ../ARCHITECTURE.md
  - ADR-003-in-process-inference.md
---

# ADR-009 — Request guard on the API server

> **Superseded on 2026-11-28 (row 887, the owner's decision):** the request guard is removed in
> 2.0.13, with no replacement. [What is no longer protected](../../README.md#protections-removed-in-2013).

## 1. Decision

MLX Studio starts `mlx_lm server` through `mlx_guard.py`: same process, same
port. The guard patches `APIHandler.parse_request` (headers) and
`APIHandler.validate_model_parameters` (body), then calls
`mlx_lm.server.main()`. The server process runs offline (`HF_HUB_OFFLINE=1`).
If the patched places are missing, the guard exits and nothing is started.

The guard records the name of each accepted model so the app does not delete it
while served.

## 2. Context

Checked in the installed `mlx_lm` 0.32.2, without the guard:

| Gap | Effect |
|---|---|
| No Content-Type or Origin check on POST | Any web page could send a request; only the reply was hidden by CORS |
| No `Host` check | A DNS-rebound page could also read replies |
| The body chooses `model`, `draft_model`, `adapters` | A request could load a local path or download a repo |

Custom model code was already refused by default, so this was not remote code
execution as the app launched the server. A client of this app also switches
between downloaded models on one server, so switching must keep working.

## 3. Options considered

| | A. Guard wrapper (chosen) | B. Proxy on 8080 | C. Offline variable only |
|---|---|---|---|
| Foreign web POST | Stopped | Stopped | Not stopped |
| DNS rebinding read | Stopped | Stopped | Not stopped |
| Download by request | Stopped | Stopped | Stopped |
| Web-driven load of a cached model | Stopped | Stopped | Not stopped |
| Extra process and memory | None | Second process | None |
| Effort (design estimate) | 0.5 to 1 day | 3 to 5 days | 1 hour |
| Main risk | Depends on `mlx_lm` internals | Backend port stays open, so it needs A anyway | Looks fixed but is not |

## 4. Rules

The refusal table is in [SECURITY.md](../SECURITY.md) §4.2. Two choices behind it:

| Choice | Why |
|---|---|
| Any port is accepted in `Host` | An SSH tunnel keeps working |
| A downloaded model needs weights and every indexed shard | The `mlx_lm` lookup alone can pass a partial folder |

**Update 2026-11-26: one change to replies.** The guard also removes a tool call
argument that is `null` where the request's schema only allows leaving it out
(`drop_null_arguments`; full rule in [API.md](../API.md) section 1). Without it,
a client with strict schema checks rejects the call.

**Update 2026-11-26 (unreleased, 2.0.10 source): two models.** The guard can also
keep two models loaded (`--studio-*` flags). The request checks are unchanged. If
only these extra hooks have moved in `mlx_lm`, the server still starts and keeps
one model; missing request-check hooks still stop the start. Decision recorded in
[ADR-010](ADR-010-two-models-one-process.md).

## 5. Consequences

| Good | Cost |
|---|---|
| Browser attacks on 8080 are refused | Clients must send JSON and use a loopback host name |
| No request can download a model | A model must be downloaded in the app before it is named |
| No extra process | Every `mlx-lm` upgrade must re-check the hooks (`tests/test_guard.py`) |
| Fails closed | A server with an unexpected `mlx_lm` does not start |

## 6. Not covered

- Other local processes can still use 8080.
- Update 2026-11-25: remote image URLs are now covered. Only `data:` URLs pass;
  others get `400` (`check_images`).
- A server started by an older build stays unguarded until it is restarted.

## 7. Evidence

`mlx_guard.py`, `app.py` (`_launch_server`, `_is_our_server`),
`tests/test_guard.py`, `tests/test_guard_null_args.py`. The end-to-end run used tiny random-weight models; a run
with a real model is UNVERIFIED.
