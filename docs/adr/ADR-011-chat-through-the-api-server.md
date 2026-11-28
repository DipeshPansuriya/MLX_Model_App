---
type: ADR
title: "ADR-011 — Chat asks the running API server"
status: "Accepted"
date: 2026-11-27
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: ADR-003, ADR-008
related:
  - ../ARCHITECTURE.md
  - ADR-004-sse-streaming.md
  - ADR-010-two-models-one-process.md
---

# ADR-011 — Chat asks the running API server

## 1. Decision

The app process loads no model. `/api/chat` forwards the conversation to the
running API server on `127.0.0.1:8080` and streams the reply back to the browser.
Unreleased (2.0.13 source).

## 2. Context

Chat held its own copy of a model beside the server's: the same weights could be
in memory twice, and the user was asked to load a model that was already served.

## 3. Rejected alternatives

| Alternative | Why it lost |
|---|---|
| Browser calls 8080 directly | Needs the page allowed as a client of 8080 and loses the app's token and Origin checks |
| Keep in-app inference as a fallback when the server is stopped | Keeps the second copy and two code paths |
| Start the server from Chat automatically | A start takes memory and replaces choices made on the API Server page |

## 4. Consequences

- Chat needs a started server. It never starts one.
- Chat takes turns with other clients; a long wait is shown and can be cancelled.
- Whether a busy server stops computing as soon as the connection closes is up to
  mlx_lm: it notices at its next write.
- Peak memory per reply is no longer shown; the server does not report it.
- The Chat memory checks and the writing of `server.budget` are gone, so the
  server's budget is no longer lowered by the app (ADR-010 text about a Chat
  model beside the server no longer applies). The guard still reads such a file.
- The API server refuses a prompt over its limit with `400
  context_length_exceeded`. The limit is never above the model's own; one model is
  not limited below 32,768 while that fits in 80% of RAM
  ([formula](../ARCHITECTURE.md#62-prompt-limit)).
