---
type: ADR
title: "ADR-004 — SSE for chat, polling for state"
status: "Accepted"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../ARCHITECTURE.md
  - ADR-001-no-web-framework.md
---

# ADR-004 — SSE for chat, polling for state

## 1. Decision

Chat tokens are delivered as **Server-Sent Events** on a dedicated `POST
/api/chat` connection. Everything else the UI needs is delivered by **polling
`GET /api/state` every 2 seconds**.

## 2. Context

Two different update patterns exist in the app:

- **Chat** produces a high-frequency, ordered, one-shot stream that ends when the
  answer ends.
- **State** (loaded model, server status, downloads, install progress, versions)
  changes rarely and is small.

Using one mechanism for both would be simpler to describe and worse for at least
one of them.

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **WebSocket for everything** | Bidirectional capability is unused; adds a handshake, a frame protocol and reconnect logic for a loopback client |
| **SSE for everything** | A state stream would need diffing, reconnection and ordering guarantees; the payload is a few KB and idempotent, so a full snapshot is cheaper to reason about |
| **Polling for chat** | Would either lag or hammer the server; token-level polling is wasteful and visibly janky |
| **Long-polling** | Reinvents SSE with worse semantics |
| **SSE + polling (chosen)** | Each pattern matches its data: a stream for a stream, a snapshot for a snapshot |

## 4. Consequences

**Good**

- Chat latency is one `write` + `flush` per token; no buffering layer.
- State rendering is idempotent: the UI re-renders from a full snapshot, so a
  missed poll self-heals on the next one. There is no incremental state to corrupt.
- The SSE reader in `ui/app.js` is ~40 lines: buffer, split on `\n\n`, read
  `event:` and `data:` lines.
- No reconnect machinery is needed: a chat stream that dies is a chat turn that
  failed, and the user retries.

**Bad**

- Up to 2 seconds of staleness on state changes. Acceptable for a load that takes
  tens of seconds, noticeable for a button that flips instantly — which is why
  button state is also updated optimistically in the handler.
- Polling costs a request every 2 seconds for the lifetime of the window. On
  loopback this is negligible; it would not be acceptable over a network.
- SSE over `POST` is not the classic `EventSource` shape (which is GET-only), so
  the client uses `fetch` + a stream reader rather than `EventSource`. This is
  deliberate: the message history belongs in the request body, not a query string.
- One long-lived connection per generation; a proxy in between would need
  buffering disabled. There is no proxy.

## 5. Revisit trigger

Reconsider if state changes need sub-second latency (then push state over the same
SSE channel), or if the app is ever served beyond loopback (then polling must go).

## 6. Evidence

| Claim | Location |
|---|---|
| SSE response headers | `app.py:710` (`_stream_chat`) |
| Event emission | `app.py:724` (`emit`) |
| Poll interval | `ui/app.js` `setInterval(refreshState, 2000)` |
| SSE reader | `ui/app.js` `sendMessage` stream loop |
