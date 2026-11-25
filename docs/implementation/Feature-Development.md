# Feature development

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
