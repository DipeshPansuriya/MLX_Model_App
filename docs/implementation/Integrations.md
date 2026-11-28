# Integrations

> **2.0.13 (unreleased) supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../../README.md#protections-removed-in-2013).

**Date:** 2026-11-25

## Inventory

| Integration | Used for | Direction | Credential | If it fails |
|---|---|---|---|---|
| Hugging Face Hub (`huggingface_hub`) | model search and download | out | `HF_TOKEN` if you need one | search or download does not complete; installed models stay on disk |
| PyPI | package version check, `pip install` of the runtime | out | none | version check or install fails |
| Update manifest (`MLX_STUDIO_UPDATE_URL`) | app update notice | out | none | no update notice |
| GitHub | source, CI, releases, one-line installer | out (installer) | none for a public download | install or update cannot download |
| OpenAI-compatible clients | use the local model | in, loopback | none | see [RUNBOOK.md](../RUNBOOK.md) |

`mlx_lm` can fetch image URLs found in a chat message. The guard on port 8080 passes only `data:`
URLs and answers 400 to any other ([SECURITY.md](../SECURITY.md) section 4.2).

Unreleased (2.0.10 source): for two models, the app and the guard read free memory
from the macOS tool `/usr/bin/vm_stat` (local, no network). If it fails, the fixed
52% memory rule is used.

## Details

- Chat is a client of the API server (2.0.13 source).
- The API server is started with `HF_HUB_OFFLINE=1`, so a request cannot make it download a model.
- Client settings and server rules: [README](../../README.md).
- Update manifest design: [ADR-006](../adr/ADR-006-manifest-updates.md); release steps:
  [DEPLOYMENT.md](../DEPLOYMENT.md).

## Testing

Test layout and isolation: [TESTING.md](../TESTING.md). The published 2.0.9 assets were downloaded
and matched `release/SHA256SUMS` (2026-11-26). Behaviour against the live Hub and PyPI is UNVERIFIED.
There are no sandbox credentials.

## Database

Not applicable ([DATABASE.md](../DATABASE.md)).
