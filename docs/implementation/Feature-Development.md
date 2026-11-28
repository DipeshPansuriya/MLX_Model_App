# Feature development

> **2.0.13 supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../../README.md#protections-removed-in-2013).
>
> In this file **unreleased** means not in 2.0.9: those changes shipped in 2.0.10 to 2.0.13 (latest release, 2026-11-28).

**Date:** 2026-11-25 | Rules for contributors: [CONTRIBUTING.md](../../CONTRIBUTING.md).

## Lifecycle

1. Agree the scope ([PRD.md](../PRD.md)); record a design decision as an [ADR](../adr/README.md) if one is made.
2. Write a short plan: files and approach.
3. Branch, change, add tests.
4. Run the checks in [TESTING.md](../TESTING.md).
5. Update the documents below.
6. Open a pull request ([BRANCH-POLICY.md](../BRANCH-POLICY.md)).

## Where code goes

| Change | Place |
|---|---|
| Endpoint or background worker | `app.py` (`do_GET`, `do_POST`, `worker_*`) |
| API server request rule | `mlx_guard.py` |
| Interface | `ui/index.html`, `ui/app.js`, `ui/style.css` (no build step), see [DESIGN.md](../DESIGN.md) |
| Packaging | `packaging/` |

## Standards

- Lint and format with Ruff (`ruff.toml`).
- A new POST route goes through the existing request checks; if it needs `repo`, add it to `REPO_ROUTES`.
- A new top-level Python module must also be added to the build, release and compile-check lists
  (`packaging/`, workflows, hooks), as was needed for `mlx_guard.py`.
- Do not log prompt or reply text.
- Unreleased source: no inline script, event handler or `eval` in the UI (the
  Content-Security-Policy blocks them). Recheck the style hash when `pywebview` changes.

## Feature flags

None.

## Documents to update

| If you change | Update |
|---|---|
| Endpoints | [API.md](../API.md) |
| Stored files | [DATABASE.md](../DATABASE.md) |
| Network, auth, logs | [SECURITY.md](../SECURITY.md) |
| Build or release | [DEPLOYMENT.md](../DEPLOYMENT.md) |
| Anything a user sees | [README](../../README.md), [CHANGELOG](../../CHANGELOG.md) |
