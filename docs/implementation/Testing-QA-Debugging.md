# Testing, QA and debugging

> **2.0.13 supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../../README.md#protections-removed-in-2013).
>
> In this file **unreleased** means not in 2.0.9: those changes shipped in 2.0.10 to 2.0.13 (latest release, 2026-11-28).

**Date:** 2026-11-25 | Suite, commands, isolation, CI and recorded results: [TESTING.md](../TESTING.md).

## Quick run

```bash
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider
node --check ui/app.js
python3 -m py_compile app.py mlx_guard.py
./scripts/security_audit.sh --quick
```

`py_compile` writes bytecode; delete `__pycache__` afterwards if you need a clean tree.

## QA before a release

Follow the manual checks in [TESTING.md](../TESTING.md) section 6. Record each result; mark anything
not run as UNVERIFIED.

## Debugging

| Symptom | Look at |
|---|---|
| App does not start | `~/.mlx_studio/logs/launch.log` |
| Request or worker error | `app.log` |
| Model action failed | `activity.log` |
| API server stopped or refuses to start | `server.log`, `app.log`; message starting `mlx_guard: refusing to start` |
| Need to share evidence | export diagnostics, review the zip, then share |

Recovery procedures: [RUNBOOK.md](../RUNBOOK.md).

## Performance and test data

No performance test or benchmark is recorded for 2.0.9. Tests use a temporary home folder
([TESTING.md](../TESTING.md) section 3).
