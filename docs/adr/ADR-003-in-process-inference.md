---
type: ADR
title: "ADR-003 — Inference in-process, API server as a child process"
status: "Accepted"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../ARCHITECTURE.md
  - ADR-008-single-model-slot.md
---

# ADR-003 — Inference in-process, API server as a child process

## 1. Decision

Chat inference runs **inside** the app process, on a worker thread, using
`mlx_lm.load` and `mlx_lm.stream_generate` directly. The OpenAI-compatible server
runs as a **separate child process** (`python -m mlx_lm server`) on
`127.0.0.1:8080`.

## 2. Context

Two consumers need inference: the chat UI, which needs token-by-token streaming
into a transcript, and external OpenAI clients, which need an HTTP API. Both could
be served by one mechanism or by two.

`mlx_lm` offers both a Python API and a `server` subcommand. The Python API gives
direct control of the sampler, the prompt cache and the stop condition. The
subcommand gives a complete OpenAI-compatible surface for free.

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Everything in-process** | The app would have to reimplement the OpenAI surface (chat completions, streaming, model listing, error shapes) and keep it compatible; that is a second product |
| **Everything in the child** | Chat would have to go through HTTP to its own subprocess, adding a hop, losing direct sampler control, and coupling the UI's streaming to the server's lifecycle |
| **Two in-process models** | Doubles memory for the same weights; on a 32 GB machine with a 16 GB model that is fatal |
| **Split (chosen)** | Chat gets the direct API; the server gets the maintained implementation; each can fail without taking the other down |

## 4. Consequences

**Good**

- Chat controls the sampler (`temp=0.7`, `top_p=0.95`), the token cap
  (`max_tokens=2048`) and the stop flag per token.
- The OpenAI surface is whatever `mlx_lm server` implements today — no
  compatibility work, and it improves when the library improves.
- A wedged or crashed server is a PID to kill; the UI keeps running.
- Server memory is reclaimable independently of the UI.

**Bad**

- Two copies of the weights can be resident if a model is loaded for chat **and**
  started as a server. Nothing prevents this; the UI does not warn. On a 32 GB
  machine with a 27B model this is an out-of-memory risk.
- The server's model is chosen independently of the chat model, so the two can
  disagree. This is intentional (serve one model, chat with another) but easy to
  misread.
- The child must be terminated explicitly; a hard kill of the parent orphans it.
  Documented in [RUNBOOK.md](../RUNBOOK.md) §4.4.
- Two ports to reason about instead of one.

## 5. Revisit trigger

Reconsider if double residency becomes a real problem: then either refuse to start
the server while a chat model is loaded, or start the server only for the loaded
model and share it.

## 6. Evidence

| Claim | Location |
|---|---|
| In-process load | `app.py:347` (`worker_load`) |
| In-process streaming | `app.py:306` (`stream_chat`) |
| Sampler and cap | `app.py:321`, `app.py:324` |
| Child server start | `app.py:457` (`worker_server_start`) |
| Terminate-then-kill | `app.py:493` (`stop_server`) |
