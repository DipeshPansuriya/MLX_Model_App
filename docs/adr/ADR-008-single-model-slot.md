---
type: ADR
title: "ADR-008 — One resident model at a time"
status: "Superseded"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../ARCHITECTURE.md
  - ADR-003-in-process-inference.md
---

# ADR-008 — One resident model at a time

> **Superseded** by [ADR-011](ADR-011-chat-through-the-api-server.md) in the
> 2.0.13 source (unreleased). Released builds still follow this record.

## 1. Decision

`State` holds a single model slot (`model`, `tokenizer`, `loaded_repo`). Loading a
second model requires unloading the first. A load request while another load is in
flight is refused with `409`.

## 2. Context

Unified memory is finite and shared with the OS. A 7B 4-bit model peaks around
4.4 GB; a 27B 4-bit model peaks around 17 GB. On a 32 GB machine, two mid-size
models plus the browser and the OS is a plausible out-of-memory condition that
manifests as a swap storm rather than a clean error.

Switching models is also a user-visible action with a clear intent: the chat
header names the model, so the user knows what they are talking to.

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Multiple resident models with a picker per message** | Doubles or triples memory; the UI would need per-conversation model binding; the benefit is marginal for a single-user tool |
| **LRU cache of loaded models** | Unpredictable memory ceiling; a user cannot tell what is resident without a new surface |
| **Load on demand per request, unload after** | Reload cost (seconds to tens of seconds) on every turn |
| **Single slot (chosen)** | Memory ceiling is one model; state is one value; the pill is unambiguous |

## 4. Consequences

**Good**

- The memory ceiling is the largest single model the machine can hold, which is
  exactly the sizing table in the README.
- The state surface is one value, so the pill, the chat header and the models card
  cannot disagree.
- Unload explicitly calls `mx.clear_cache()`, returning memory to the OS rather
  than waiting for garbage collection.
- A concurrent-load race is refused rather than silently interleaved.

**Bad**

- Switching models costs a full reload. For a 27B model that is tens of seconds.
- Chat history is cleared on unload, because the tokenizer changed; a user who
  switches mid-conversation loses context.
- The API server can still hold a second copy of the same or a different model,
  because it is a separate process (see
  [ADR-003](ADR-003-in-process-inference.md)). The single-slot rule applies to the
  app process only.
- No preloading or warming: the first token after a load pays the full load cost.

**Update 2026-11-26 (unreleased, 2.0.10 source):** the single-slot rule for the
app process is unchanged. The API server child can now keep up to two models,
taking turns, within a memory budget (`mlx_guard.py`, `install_multi`). Real
two-model memory use is unmeasured; limits are in
[SECURITY.md](../SECURITY.md) section 5. Decision recorded in
[ADR-010](ADR-010-two-models-one-process.md).

## 5. Revisit trigger

Reconsider if machines with 64 GB or more become the common target, or if a
multi-model comparison feature is requested.

## 6. Evidence

| Claim | Location |
|---|---|
| Single slot | `app.py:223` (`State.__init__`) |
| Concurrent-load refusal | `app.py:608` (`POST /api/load`, `409`) |
| Cache release | `app.py:243` (`State.unload`) |
| History clear on unload | `ui/app.js` `unload` handler resets `state.messages` |
