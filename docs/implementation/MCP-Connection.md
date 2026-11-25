# MCP connection

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
