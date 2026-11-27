---
type: ADR
title: "ADR-010 — API server keeps two models in one process"
status: "Accepted, revisit (unreleased: 2.0.10 source)"
date: 2026-11-26
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../ARCHITECTURE.md
  - ../SECURITY.md
  - ../API.md
  - ADR-003-in-process-inference.md
  - ADR-008-single-model-slot.md
  - ADR-009-api-server-guard.md
---

# ADR-010 — API server keeps two models in one process

> **Not released.** This is work planned for 2.0.10. Version 2.0.9 and earlier
> serve one model at a time. The memory limits of §4.5 have **not** been re-tested
> with real models; their figures are calculated (see §7).

## 1. Decision

The API server child (`mlx_lm server` behind `mlx_guard.py`, port 8080) keeps up
to **two models loaded in the same process**. The guard wraps mlx_lm's own
`ModelProvider._load` and `load_default` so that a second model stays in a slot
instead of being dropped. There is no router, no second server and no new port.
The models take turns: one reply batch at a time, as today.

The app process itself still holds one model ([ADR-008](ADR-008-single-model-slot.md)
is unchanged for the app).

## 2. Context

- Wanted: one URL (`127.0.0.1:8080`, clients change nothing), two models loaded,
  a memory guard for a 32 GB Apple Silicon Mac, and a start screen that can pick
  a second model.
- `mlx_lm` 0.32.2 holds one model. Its `_load` resets the provider, runs
  `gc.collect()` and `mx.clear_cache()`, then loads the new one; `load()` calls
  `_load` only when the model key changes.
- One generation thread serves every request. `load()` is called only when no
  batch is running (`batch_generator is None`) or at thread start. A request for
  another model waits until the running batch drains. So a model is never
  dropped in the middle of a reply.
- Prompt caches are keyed by model key, so two models' caches live side by side
  in one `LRUPromptCache`.
- The guard already patches the request path and fails closed if those hooks
  move ([ADR-009](ADR-009-api-server-guard.md)).

## 3. Options considered

| | A. Router + two child servers | **B. One process, two slots (chosen)** | C. Built-in swap only (as 2.0.9) |
|---|---|---|---|
| Client sees | One URL, two models | One URL, two models | One URL, reload on every switch |
| Replies on two models at once | Yes (GPU shared, each slower) | No, models take turns | No |
| Processes and pidfiles | 3 and 3 | 1 and 1 (as today) | 1 and 1 |
| Request guard | Must be redone in the router; child ports also reachable on loopback | Unchanged, same patched handler | Unchanged |
| Streaming | Router must proxy SSE and both-way disconnects | Unchanged | Unchanged |
| Memory | Each child has its own MLX pool and wired limit: two pools can overcommit | One pool, one wired limit, measured with `mx.get_active_memory()` | One model |
| Switch between the two models | Instant | Pointer swap, no disk read | Disk reload every time |
| One crash or OOM kill | Other child keeps serving | Both models go | One model |
| Coupling to mlx_lm internals | Low (CLI only) | Medium: wraps `ModelProvider._load`, `load_default`, `ResponseGenerator.__init__` and `_next_request`, checked by `multi_problem` | None |

- **A lost** because it adds a proxy, two more processes and a second guard for
  a gain (parallel replies) that was not asked for. It stays the upgrade path if
  parallel replies are wanted later.
- **C lost** because the approved scope is two models loaded; a reload on each
  switch is what users already have.

## 4. Rules

### 4.1 Slots

- `--studio-max-models` is 1 or 2; `--studio-also-model` names the second model
  to preload. Guard-only flags sit at the end of the command line so the app's
  pid identity check still matches.
- A request for a loaded model restores its members and moves it to most
  recently used (no disk, no memory check).
- If `multi_problem` finds that the wrapped mlx_lm places moved, the server still
  starts with one model and the status file says why. Two models is not a
  security control; the request checks of ADR-009 still fail closed.
- The guard writes `preload_done` to the status file when preload ends, however it
  ends (both loaded, second skipped, or a load failed). The app compares no memory
  and shows no **Free memory is low** notice before that, so a half-done preload
  is not read as memory taken by other apps. The context shown for a two-model
  server never depends on which status event came last.

### 4.2 Memory admission (when a new model is loaded)

Inputs: `active` = measured `mx.get_active_memory()` after `gc.collect()` and
`mx.clear_cache()` (resident weights plus stored prompt caches); new weights =
safetensors bytes on disk; reply room = `3.1 x KV bytes per token x min(model
limit, T)`, only the largest counts because models take turns; margin 1.4 GB
(1 GB + 0.4 GB process overhead); budget = the smaller of `0.52 x RAM` (17.9 GB
on 32 GB, lowered by the app's own chat model) and live free memory (§4.5). T
is the context per model from §4.5. (The first version used 2 x KV, 32,768
fixed, 1 GB and 0.52 only; the real-model test showed that was too little, see
§4.5.)

`over = active + new weights + largest reply room + margin - budget`

1. If both slots are full, evict the least recently used model.
2. If `over <= 0`, load.
3. **If one model is still resident and `over <=` prompt cache bytes, trim the
   prompt cache to `cache bytes - over` and keep that model** (F9, §4.3).
4. Otherwise evict the remaining resident model and check again.
5. With nothing resident, trim the prompt cache to what fits.
6. If it still does not fit, load the new model alone (exactly the one-model
   behaviour; a single model is always the user's call) and warn.

After every evict or trim the guard measures again and re-plans. A trim that
frees less than the cache's own byte count is followed by another trim while the
remaining cache still covers the gap, and otherwise by step 4, not by an
over-budget pair. Preload at start uses the same rule with no cache to trim; a second model
that does not fit is skipped and reported, and the server still starts.

### 4.3 F9: trim before dropping the last resident model

Review finding F9 (row 656): with the first version of the rule ("evict, then
trim"), two 6 GB models resident, 3.7 GB reply room, a 2 GB prompt cache and a
17.9 GB budget, a third 6 GB model evicted **both** models although freeing
0.8 GB of cache would have kept one.

**Decided: change now, before 2.0.10.** Trimming is preferred over dropping the
last resident model whenever trimming alone closes the gap.

| | Change now (chosen) | Change later | Keep |
|---|---|---|---|
| Effect | Second model kept; up to 2 GB of cached prompts lost | Same, after release | One extra disk reload (about 4 s for 7B) each time the case happens |
| Work | One pure function, one loop, about 6 tests | Same, plus a behaviour change after release | None |
| Risk | Low: same `trim_to` call the guard already makes; reversible | Users meet the worse rule first | None new |

Why trimming at that point is safe in `mlx_lm` 0.32.2:

- `_load` runs on the generation thread only when no batch exists, and
  `_serve_single` is synchronous on the same thread, so no reply is running.
- `fetch_nearest_cache` hands out a `deepcopy`, so no request holds a stored
  entry.
- Every other use of the prompt cache is on the same generation thread, and
  mlx_lm itself calls `trim_to` on that thread.
- The guard already called `trim_to` at this exact point when nothing was
  resident. F9 only adds a condition, not a new call site.

Losing cache is cheaper than losing a model: a trimmed prompt is prefilled again
on its next use, while an evicted model needs a disk load and also loses its
warm prompts in practice.

### 4.4 Chat model beside a two-model server (goes beyond design risk R6)

The first design (row 656, risk R6) only asked for a note on the Chat tab when a
Chat model loaded after the server start was not in the budget. The 2.0.10 source
goes further, and this is **accepted**:

- `POST /api/load` answers **409** with the figures when the Chat model's weights
  plus the two-model server's larger estimate (asked pair, loaded pair, or
  measured use plus reply room and margin) exceed `0.52 x RAM`
  (`chat_load_problem`, `app.py`). It is checked before the old Chat model is
  released, so a refusal changes nothing.
- It applies only while a two-model server runs with multi on. A stopped server,
  a one-model server or `multi_off` has no limit, as in 2.0.9.
- No override: the message names the two ways out (stop the API server, or pick
  a smaller model).
- A second check follows, on live free memory (§4.5): refused if the Chat weights
  plus `max(0, need - server active_bytes)` are more than `free - 2 GB`. It is
  skipped when free memory cannot be read.
- The app also writes the lower budget to `server.budget`; the guard takes the
  smallest of the start budget, the file and the live figure (§4.5) for later
  loads, so it never goes above the start budget.

Why accept: two server models plus a Chat model in one machine is a third model
the user did not pick as a set, and the 32 GB Mac has no room for it. A note only
(R6) would leave a likely OOM kill of both server models. "A single model is
always the user's call" (§4.2 step 6) still holds: it covers one model alone,
not a third beside two.

The refusal stays on screen with its figures, in a panel with **Dismiss** that is
marked as an alert (re-check N1, coded). Its look in the real window and with
VoiceOver is UNVERIFIED.

### 4.5 Limits after the real-model test (rows 743, 751, 760; unreleased, 2.0.10)

The first real two-model run (row 743, busy Mac with Docker open) failed on
memory and cache. The server's peak was 16.98 GB, under its 17.87 GB budget, but
system swap rose from 0 to 4.60 GB. Only 11.8 to 15.1 GB was free at start.
Prompts over 34,877 tokens were never cached and emptied the cache, while the UI
offered 54,917. Two 32K conversations could not both stay cached. The estimate
was 1.08 GB short on a follow-up turn.

**Decided (row 751 option B):** a live free-memory check and a corrected formula.
Constants live in `mlx_guard.py`; the app uses the same functions.

- `free = free + speculative + purgeable + file-backed pages` from `/usr/bin/vm_stat` (no shell, 2 s timeout).
- App, at estimate and start: `budget = min(0.52 x RAM - Chat weights, running server's active + free - 2 GB)`, never below 0.
- Guard, at each later check: `budget = min(start budget, server.budget file, own active + free - 2 GB)`, never below 0.
- `T = min(32768, round down to 1024 of (budget - weights - 1.4 GB) / ((2 + 3.1) x KV per token))`, where the KV size per token is the larger of the two models' (131,072 bytes if a config cannot be read). Each model gets `min(T, its context limit)`.
- Prompt cache cap = `2 x KV x T` (one long conversation per model), in place of the earlier fixed 2 GB; the guard reserves T (`--studio-kv-tokens T`). T and the cap stay until a restart.
- T = 32,768: full. **8,192 to 32,767: shrunk**, and the UI says so. **Below 8,192: two models refused** (409 with figures). The app checks again after stopping the old server.
- Free memory is checked at start, at every non-hit model load in the guard, and at Chat load. If it drops later, nothing is unloaded at once: the UI shows "Free memory is low", and the next load evicts, trims or loads alone against the lower budget.
- Status refresh: besides load events, the guard rewrites the status figures from mlx_lm's request loop (`_next_request`) at most every 10 s, without adding an event, so cache growth is not mistaken for memory taken by other apps. A reply served one at a time holds that loop.
- `memory_low` (only after `preload_done`, with a measurement and a free figure): `(need - 0.4 GB - active) - (free - 2 GB) > 0`.
- `vm_stat` unreadable: the live term is skipped (fixed 52% rule), the UI says so, and the guard prints one line.
- Unchanged: the one-model server (no cache byte cap, no live check, no refusal), `server.budget`, the F9 rule, the switch between two loaded models.
- The oversize warning is titled **Loaded without enough memory** and says the model "does not fit in the memory the API server may use now", with room for a T-token reply, not "larger than the memory budget". "The other model was unloaded" is said only after an eviction.
- "Close other apps for more" is said only when free memory, not the 52% rule, is the limit (`free_is_limit`).

Calculated for two Qwen2.5 7B 4-bit models (4.284 GB weights and 57,344 KV bytes
per token each) on 32 GB. **None of these figures is measured:**

| Free at start | T | Cache cap | Need |
|---|---|---|---|
| 19.9 GB or more (52% rule binds, budget 17.87) | 26,624 | 3.05 GB | 17.75 GB |
| 15.11 GB | 10,240 | 1.17 GB | 12.96 GB |
| 11.78 GB (row 743 state) | refused | - | 12.36 GB at 8K, so 14.4 GB free |

One model alone keeps its larger context. Rules and tests:
`.agents/reports/row760-spec.md` (hub repo).

Left for the re-test, not built: `mx.set_cache_limit` (MLX buffer pool, about
2.3 GB seen) and a lower wired limit than mlx_lm's 26.8 GB.

## 5. Consequences

| Good | Cost |
|---|---|
| Same URL, port, pidfile and request guard; clients change nothing | Replies for different models are serial |
| Switching between the two loaded models needs no disk read | One OOM kill stops both models |
| One MLX memory pool, measured, not guessed | Wraps more mlx_lm internals; every upgrade must pass `multi_problem` and the hook tests |
| The guard can fall back to one model instead of refusing to start | A switch between two loaded models is not re-checked against the budget |
| F9 keeps a model where a small cache trim is enough | A trim can drop cached prompts of the model that stays |

Known residual: the prompt cache entries of an evicted model stay in the cache
until the LRU order pushes them out. Dropping them first on eviction would need
private `LRUPromptCache` members; left for later.

## 5a. Accepted deviations from the §4.5 spec

Differences from `.agents/reports/row760-spec.md`, accepted in review (row 776):

| No. | Deviation |
|---|---|
| 1 | `memory_low` subtracts the 0.4 GB process overhead from what is still to come, because the running process already uses it. This makes the notice fire **later**. As specified it would fire right after every shrunk start |
| 2 | In the free-memory refusal, "needed" is the need at 8K **plus the 2 GB reserve** (14.4 GB, not 12.4 GB, in the example above), less a running server's memory, so it never reads below "free". This is the original wording. `total_bytes` stays the need |
| 3 | A refusal by the fixed 52% rule (too big, or free memory unreadable) has its own sentence: "Not enough memory for two models: X GB allowed for the API server, Y GB needed for 8K each. Choose smaller models or start one model." |
| 4 | The fallback estimate has no "of X GB free" and no "Close other apps for more" |
| 5 | There is no `snapshot.server.estimate`: the new fields are on `/api/server/estimate`. The UI asks again every 10 s while stopped. A running pair is not asked again and never shown as refused |
| 6 | With a server running, the estimate and the start request add its `active_bytes` to free memory, because a restart gets it back. Otherwise a restart of a pair is wrongly refused |
| 7 | `{T/1000}K` is rounded down. An unknown running context (no status, not started by this app run) counts as 32,768 |
| 8 | The one-model estimate reads no `vm_stat` and returns `free_bytes` null; its figures move with the new factor and margin |
| 9 | The oversize text drops the budget or context figure when it has none |

## 5b. Known limits

- A killed server (SIGKILL): free memory may be read before macOS returns it.
- Screen readers may announce the live notices or estimate again on later updates
  (not checked).
- A model whose context limit is below 8,192 can make the "needed" figure too
  small.
- The guard's live budget counts the 0.4 GB overhead twice: it trims or evicts
  earlier than needed.
- A Chat swap does not count the old Chat model's memory as free, so it may be
  refused until Unload.
- If the check after stopping the old server refuses, the old server stays stopped;
  the reason is in the "API server stopped" notice.
- Status figures can be about 10 s old, or older during a reply served one at a
  time; the app caches its free-memory reading for 10 s.
- The view says **keep prompts to T tokens or fewer. The prompt cache holds about
  2T tokens in total; above that, older prompts are dropped and read again**. The
  cap is `2 x KV x T` for both models together, so by arithmetic one conversation
  up to about 2T (larger model) can still be cached, pushing out the other. The
  line is stricter than the cap; not tested with real models.
- The budget never rises above the start figure; a restart is needed for a larger
  context.
- Beside a running server, the estimate uses **free + held** whenever the server
  holds memory. The memory line says **measured by the API server, refreshed about
  every 10 s**.

## 6. Revisit trigger

- The real-model memory test (§7) shows the 0.52 budget, the reply-room formula
  or the trim/evict rule does not match measured use.
- Parallel replies on two models are wanted (go to option A).
- An `mlx_lm` upgrade changes `ModelProvider`, `ResponseGenerator` or
  `LRUPromptCache`.

## 7. Evidence and what is not verified

- Code: `mlx_guard.py` (`free_bytes`, `context_tokens`, `kv_reserve`,
  `admission_plan`, `install_multi`, `multi_problem`, `start_multi`), `app.py`
  (`server_memory_estimate`, `_launch_server`, `_server_limits`,
  `chat_load_problem`), `ui/app.js` (`multiNotice`, `estimateWords`).
- Tests: `tests/test_row656_guard.py`, `tests/test_row656_app.py`,
  `tests/test_row713_fixes.py`, `tests/test_row731_fixes.py`,
  `tests/test_row760_limits.py`, `tests/test_row815_preload.py`,
  `tests/test_guard.py`, with stand-in models. Last full run (2026-11-26,
  reported by the preload fix run, temporary `HOME`): 1503 passed, 0 skipped.
- The F9 rule change (§4.2 step 3, §4.3) is in the code (`admission_plan` and the
  `_load` loop), tested in `tests/test_row656_guard.py`, unreleased.
- Real-model test row 743 (one run, busy Mac, before the §4.5 limits): load,
  switch and memory at 32K and 48K measured; failed on swap and cache, which led
  to §4.5. The §4.5 rules are now in the code (unreleased). **The real two-model
  re-test of them has not run.**
- **UNVERIFIED:** the §4.5 constants (2 GB reserve, 3.1 reply factor from one
  data point, 0.4 GB overhead) and whether the `vm_stat` free figure is memory
  macOS gives without swapping. Also unverified: real eviction and trimming,
  whether `LRUPromptCache.nbytes` matches the memory a trim frees, a tool call
  with real models, and the screens in a real window. All §4.5 figures are
  calculated until the re-test.
