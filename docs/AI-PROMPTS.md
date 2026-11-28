# MLX Studio - AI prompt library

> **2.0.13 (unreleased) supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).

**Date:** 2026-11-25

Suggested prompts for an AI coding assistant working on this repository. They are not a record of
past sessions and their output has not been evaluated.

## Usage

Paste one prompt, fill the `<...>` parts, and check the answer against the code.

## A. Project work

| Task | Prompt |
|---|---|
| Orientation | "Read `README.md`, `docs/ARCHITECTURE.md` and `docs/API.md`. Explain how `<feature>` flows from `ui/app.js` to `app.py`, naming functions. Say what you could not verify." |
| Setup help | "Follow `docs/implementation/Setup-Initialisation.md` on this Mac. Show each command and its output. Ask before installing anything." |
| Bug fix | "Reproduce `<bug>`. Find the cause in the code. Add a failing test under `tests/`, then make the smallest fix. Run the checks in `CONTRIBUTING.md`." |
| Security change | "Change `<behaviour>` without weakening `docs/SECURITY.md` section 4. List each control you touched and the test that covers it." |
| Docs check | "Compare `<document>` with the current code. List stale lines with the code location that disproves them. Edit only those lines." |
| Release check | "Walk the release steps in `docs/DEPLOYMENT.md`. Report which are done, which are not, with evidence. Publish nothing." |

## B. Concept checks

| Question | Where to check the answer |
|---|---|
| Why a guard in front of `mlx_lm.server`? | [ADR-009](adr/ADR-009-api-server-guard.md) |
| Why only one loaded model? | [ADR-008](adr/ADR-008-single-model-slot.md) |
| Why no web framework? | [ADR-001](adr/ADR-001-no-web-framework.md) |
| Why server-sent events? | [ADR-004](adr/ADR-004-sse-streaming.md) |

## Rules for changes to this file

- Do not put credentials, private paths or chat content in a prompt.
- Update a prompt when a file or command it names changes.
