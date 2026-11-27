---
type: ADR
title: "ADR-012 — Load test in a supervisor process, settings on their own page"
status: "Accepted"
date: 2026-11-27
author: "Dipesh Pansuriya"
project: mlx-studio
related:
  - ../ARCHITECTURE.md
  - ../API.md
  - ADR-010-two-models-one-process.md
---

# ADR-012 — Load test in a supervisor process

Unreleased, 2.0.13 source. Verified with fakes only.

## Context

A usable context size depends on the model, the settings and what other apps hold.
The formula underestimated measured memory, and memory jumped by 5 points in about
half a second, so a watcher that only reacts is not enough.

The one-model formula was corrected afterwards (factor 6.4 and other apps' memory,
[prompt limit](../ARCHITECTURE.md#62-prompt-limit)). The load test keeps its own
estimate and measures, as the formula is fitted to one model and one Mac.

## Decision

- The app starts `mlx_loadtest.py` (own session, commands on stdin, JSON lines
  out). It starts the same guarded server as a normal start, on a free port, only
  while the API server is stopped.
- A fresh server per size, ascending. Memory is checked against a gate before the
  load and before every request, and sampled every 0.1 s with a kill line.
- A closed pipe ends the runner and its server, so nothing is left behind.
- Results hold figures only, no prompt or reply text.
- Settings and the test each have a page. Apply saves a passed, current size as
  that model's max context.

## Rejected

| Option | Why not |
|---|---|
| Threads in `app.py` start and watch the server | An app crash leaves an orphan; the watchdog shares the app's interpreter |
| Generate directly in a worker, no server | Does not measure the path users run |
| Rely on `mx.set_memory_limit` | Its effect as a hard cap is not verified |

## Consequences

- Cautious: sizes are often skipped on a busy Mac, and results depend on it.
- Sampling cannot guarantee that memory stays under 80%.
- One model only; two-model use and long sessions are not covered.
