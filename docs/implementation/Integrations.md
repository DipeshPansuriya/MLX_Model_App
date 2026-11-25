# Integrations

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

## Details

- The API server is started with `HF_HUB_OFFLINE=1`, so a request cannot make it download a model.
- Client settings and server rules: [README](../../README.md).
- Update manifest design: [ADR-006](../adr/ADR-006-manifest-updates.md); release steps:
  [DEPLOYMENT.md](../DEPLOYMENT.md).

## Testing

Test layout and isolation: [TESTING.md](../TESTING.md). Behaviour against the live Hub, PyPI and
published release assets is UNVERIFIED for 2.0.9. There are no sandbox credentials.

## Database

Not applicable ([DATABASE.md](../DATABASE.md)).
