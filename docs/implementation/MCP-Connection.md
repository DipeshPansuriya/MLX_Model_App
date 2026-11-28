# MCP connection

> **2.0.13 (unreleased) supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../../README.md#protections-removed-in-2013).

**Date:** 2026-11-25

**Not applicable.** MLX Studio configures no Model Context Protocol (MCP) server and does not act as
one: the repository has no `.mcp.json`, and a search of `app.py`, `mlx_guard.py` and `ui/app.js`
found no MCP code.

| Question | Answer |
|---|---|
| Servers configured | none |
| Credentials for MCP | none |
| How other tools connect | through the OpenAI-compatible server on port 8080 ([API.md](../API.md)) |

If MCP support is added, record the decision as an [ADR](../adr/README.md), then document servers,
transport, tools and secrets here.
