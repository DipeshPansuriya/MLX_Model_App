# Engines

The API Server runs one engine at a time. On the API Server and Load Test screens OptiQ
is the default; the engine of the last ready server is kept. A start request that names
no engine still means MLX. If the chosen engine is not installed, the Start area offers
the same install as Setup, or MLX.

A live check of commit `ee4cbc6` over the app's HTTP routes passed with a real model on a
copied engine folder: OptiQ start, key, Chat, OpenAI and Anthropic tool calls, Stop, then
MLX. A later check on 2026-11-28 (commit `5630379`, outgoing network blocked) started both
engines with real models: chat and tool calls on both, Claude Code 2.1.332 on OptiQ, and
one 4096-token load test on OptiQ. Still untried: the Setup install button, DeepSeek
Harness, the native window, **Apply changes** and **Restart**.

| Engine | State |
|---|---|
| MLX (built in) | 2.0.13 starts `mlx_lm server` itself, without the earlier request guard |
| OptiQ 0.8.6 | default; install on Setup or from the Start area |
| Splash, Rapid-MLX | shown as "Not available yet: security review pending"; no install, no command |

## Install, update, remove (Setup, "Other engines")

- Installs only `packaging/optiq-lock.txt` (hashed, wheels only, from pypi.org) into
  `~/.mlx_studio/engines/optiq/venv`. The lock's own hash is checked first.
- Needs the app to run on CPython 3.15, Apple silicon, not free-threaded.
- A newer engine version comes only with a newer MLX Studio.
- A failed or stopped install shows "broken": remove it or install again.
- An installed engine is never installed over: **Install / Update** is off. An update or a
  repair asks first, because the old folder is removed before the download.
- Refused while that engine serves, during a load test, or if the folder is a link.

## Running OptiQ

- Same address as MLX: `http://127.0.0.1:8080` (use `127.0.0.1`, not `localhost`).
- A new key at every start, kept in `~/.mlx_studio/server.key` (mode 0600) and the
  server's environment. **Copy key** puts it on the clipboard.
- The key is used only while every listener on port 8080 is the started server.
  Any other listener stops the start, or the running server.
- Ready means: calls without the key get 401 and one short reply with the key works.
- One text model from the model cache. Image models and unreviewed model code are refused.
- At each start the engine folder is checked again: no linked folder, the install marker
  matches the shipped lock, and no package (pip aside) that the lock does not name at that
  version. Files inside a package are not checked.
- Memory: no limit. The app does not stop the server for memory (removed in 2.0.13).
- `/v1/models` (key needed) lists the served model by its full folder path, which includes
  your user name, plus `:think` and `:no-think` forms. OptiQ 0.8.6 `serve` takes the name
  from `--model` and has no option to change it. Send `default_model`.
- Keys are replaced by `[key hidden]` in the server output, also in the background
  `server.log`.
- Only the max reply tokens, max context, prompt cache and performance settings are passed
  to OptiQ; only a change to those asks for a restart. OptiQ reports no performance mode
  back, so the saved one is shown as "not reported by OptiQ".
- If the start fails, the previous server is started again with its own engine.

**Start watch, both engines.** A server that has not reported ready within 600 seconds
is stopped, and the one it replaced is started again. Ready means that only the
server's own process listens on its port and also: for MLX started by this run of the
app, `/health` says 200 and one first request is answered; for OptiQ, the key check
passes and one first request is answered. `/health` 503, a failed first request or an
answer without the key stops it at once. A server kept from an earlier run is stopped
too when its port owner cannot be read for 600 seconds.

## Starting and connecting

- **Start Server** and **Apply changes** first show a check panel: engine, model, context
  limit, reasoning, vision, prompt cache, KV bits, reply tokens and background. Edits are
  saved as that model's settings; **Cancel** saves and starts nothing. MLX has no context
  limit and neither engine serves images, so those are off. Reasoning and the other flags
  were read in the installed engine source, not tried with a running engine.
- **Run in background** is on by default for OptiQ and off for MLX, and follows the engine
  picked. The choice of the last ready server is kept for its engine. A background MLX
  server has no password and keeps running until **Stop**.
- **Connect your tool** appears while a server runs: Claude Code (OptiQ with the Anthropic
  route), DeepSeek Harness (you choose the context window) and other OpenAI clients. The
  app writes no other app's settings file and shows no key.

## Client settings

| Client | Base URL | Model | Key |
|---|---|---|---|
| OpenAI compatible (OptiQ) | `http://127.0.0.1:8080/v1` | `default_model` | Copy key |
| Claude Code (OptiQ, Anthropic route on) | `ANTHROPIC_BASE_URL=http://127.0.0.1:8080` | `ANTHROPIC_MODEL=default_model` | `ANTHROPIC_AUTH_TOKEN` |
| MLX | `http://127.0.0.1:8080/v1` | `default_model` (or the exact started id; another name loads that model) | none; no Anthropic route |

The OpenAI and Anthropic routes answered plain HTTP requests in the live check.
Claude Code 2.1.332 was tried once with OptiQ (commit `5630379`). DeepSeek Harness
and other client programs have not been tried.

## Protections the engines do not have

The API Server card lists them for the chosen engine (`engines.py`, `missing`).

- **MLX:** no API key, memory limit, prompt limit, reply cap, request size limit, web
  page checks beyond `--allowed-origins`, or refusal of other models, folders, draft
  models and adapters. [Full list](../README.md#protections-removed-in-2013).
- **OptiQ:** no memory limit, long prompts drop older text, the reply cap is a default
  only, no web page checks beyond the key, a key holder may send draft model or file
  paths, and `/health`, `/v1/optiq/context` and `OPTIONS` answer without the key.

## Load Test

Choose the engine before **Start test**. The OptiQ test uses a spare port, without the
Anthropic route. A run fails a size if the server drops part of the prompt.
No percentage memory limit applies; the test server is still stopped on critical
memory pressure, swap growth, unreadable memory or a sampling gap.
**Thinking** is sent to both engines as `chat_template_kwargs.enable_thinking` (read
in the installed mlx_lm and OptiQ source; not tried with a running engine).

## Adding an engine

1. Review it (flags, auth, telemetry, offline, pinned hashed packages).
2. Add a class in `engines.py` with the methods `Optiq` has, then one `REGISTRY` entry.
3. Add it to the Load Test engine list in `ui/index.html`, and any new file to the
   build, release and CI lists.
