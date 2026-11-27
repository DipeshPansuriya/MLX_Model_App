# Engines

The API Server runs one engine at a time. MLX is the default. Checked with fakes only:
no OptiQ server was started and nothing was installed through the app.

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
- Refused while that engine serves, during a load test, or if the folder is a link.

## Running OptiQ

- Same address as MLX: `http://127.0.0.1:8080` (use `127.0.0.1`, not `localhost`).
- A new key at every start, kept in `~/.mlx_studio/server.key` (mode 0600) and the
  server's environment. **Copy key** puts it on the clipboard.
- The key is used only while every listener on port 8080 is the started server.
  Any other listener stops the start, or the running server.
- Ready means: calls without the key get 401 and one short reply with the key works.
- One text model from the model cache. Image models and unreviewed model code are refused.
- If the start fails, the previous server is started again with its own engine.

## Client settings

| Client | Base URL | Model | Key |
|---|---|---|---|
| OpenAI compatible (OptiQ) | `http://127.0.0.1:8080/v1` | `default_model` | Copy key |
| Claude Code (OptiQ, Anthropic route on) | `ANTHROPIC_BASE_URL=http://127.0.0.1:8080` | `ANTHROPIC_MODEL=default_model` | `ANTHROPIC_AUTH_TOKEN` |
| MLX | `http://127.0.0.1:8080/v1` | the model id | none; no Anthropic route |

A live connection from Claude Code or another client has not been tried.

## Protections OptiQ does not have

The API Server card lists them (`engines.py`, `Optiq.missing`): no 80% memory limit or
memory stop, long prompts drop older text, the reply cap is a default only, no web page
checks beyond the key, a key holder may send draft model or file paths, and `/health`,
`/v1/optiq/context` and `OPTIONS` answer without the key.

## Load Test

Choose the engine before **Start test**. The OptiQ test uses a spare port, without the
Anthropic route. A run fails a size if the server drops part of the prompt.
**Apply** is offered for MLX results only.

## Adding an engine

1. Review it (flags, auth, telemetry, offline, pinned hashed packages).
2. Add a class in `engines.py` with the methods `Optiq` has, then one `REGISTRY` entry.
3. Add it to the Load Test engine list in `ui/index.html`, and any new file to the
   build, release and CI lists.
