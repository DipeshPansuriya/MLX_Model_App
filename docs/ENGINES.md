# Engines

The API Server runs one engine at a time. MLX is the default.

A live check of commit `ee4cbc6` over the app's HTTP routes passed with a real model on a
copied engine folder: OptiQ start, key, Chat, OpenAI and Anthropic tool calls, Stop, then
MLX. Still untried: the Setup install button, clicks in the screens, a Claude Code client
and the load test. Changes made after that commit are checked with fakes only.

| Engine | State |
|---|---|
| MLX (built in, `mlx_guard.py`) | default; command unchanged |
| OptiQ 0.8.6 | install on Setup, then choose it on the API Server card |
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
- Memory: when the app looks at the server (every 2 seconds until ready, then about every
  30) it compares the whole Mac's memory use with the memory limit (80%, or your lower
  value) and stops the server with the reason. This is sampling: memory can pass the limit
  between two looks, so it cannot be guaranteed. There is no look while the first request,
  which loads the model, is running. Nothing watches while the app is closed.
- Keys are replaced by `[key hidden]` in the server output, also in the background
  `server.log`.
- Only the max reply tokens, max context, prompt cache and performance settings are passed
  to OptiQ; only a change to those asks for a restart. OptiQ reports no performance mode
  back, so the saved one is shown as "not reported by OptiQ".
- If the start fails, the previous server is started again with its own engine.

## Client settings

| Client | Base URL | Model | Key |
|---|---|---|---|
| OpenAI compatible (OptiQ) | `http://127.0.0.1:8080/v1` | `default_model` | Copy key |
| Claude Code (OptiQ, Anthropic route on) | `ANTHROPIC_BASE_URL=http://127.0.0.1:8080` | `ANTHROPIC_MODEL=default_model` | `ANTHROPIC_AUTH_TOKEN` |
| MLX | `http://127.0.0.1:8080/v1` | the model id | none; no Anthropic route |

The OpenAI and Anthropic routes answered plain HTTP requests in the live check. A
connection from Claude Code or another client program has not been tried.

## Protections OptiQ does not have

The API Server card lists them (`engines.py`, `Optiq.missing`): no memory limit inside the
server (only the sampled stop above), long prompts drop older text, the reply cap is a default only, no web page
checks beyond the key, a key holder may send draft model or file paths, and `/health`,
`/v1/optiq/context` and `OPTIONS` answer without the key.

## Load Test

Choose the engine before **Start test**. The OptiQ test uses a spare port, without the
Anthropic route. A run fails a size if the server drops part of the prompt.
**Apply** is offered for MLX results only.
**Thinking** is sent to both engines as `chat_template_kwargs.enable_thinking` (read
in the installed mlx_lm and OptiQ source; not tried with a running engine).

## Adding an engine

1. Review it (flags, auth, telemetry, offline, pinned hashed packages).
2. Add a class in `engines.py` with the methods `Optiq` has, then one `REGISTRY` entry.
3. Add it to the Load Test engine list in `ui/index.html`, and any new file to the
   build, release and CI lists.
