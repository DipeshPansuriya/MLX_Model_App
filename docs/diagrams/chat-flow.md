---
type: Diagram
title: "Chat request path — send to metrics"
date: 2026-11-24
project: mlx-studio
related:
  - ../ARCHITECTURE.md
  - runtime.md
  - model-lifecycle.md
---

# Chat request path

One turn in the 2.0.13 source (unreleased). Released builds infer in the app.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as ui/app.js
    participant H as Handler<br/>127.0.0.1:8765
    participant A as API server<br/>127.0.0.1:8080
    participant L as activity.log

    U->>UI: Enter (only when the server is ready)
    UI->>H: POST /api/chat {repo, messages}
    alt stopped, starting, not served or reply running
        H-->>UI: 409 {error}
        UI-->>U: error in the bubble, text given back
    else ready
        H-->>UI: 200 text/event-stream
        H->>A: POST /v1/chat/completions (stream)
        loop while silent
            H-->>UI: event: status (waiting)
        end
        alt prompt over the limit
            A-->>H: 400 context_length_exceeded
            H-->>UI: event: error
        else generating
            A-->>H: delta.reasoning / delta.content
            H-->>UI: event: reasoning / token
            A-->>H: usage, [DONE]
            H-->>UI: event: done
            H->>L: append chat/success
        end
    end
    U->>UI: Stop (optional)
    UI->>H: POST /api/chat/stop
    H->>A: socket shut down
    H-->>UI: event: done {stopped}
```

## Failure paths

| Failure | Response | UI |
|---|---|---|
| Server not ready or model not served | `409` | Red bubble; typed text is returned |
| Server refuses (for example a long prompt) | SSE `error` | Red bubble with the server's words |
| Server stops mid-reply | SSE `error` | Partial reply kept with the reason; turn dropped from history |
| Reply ends or is stopped with thinking only | SSE `done` | Thinking shown as the reply; turn dropped from history |
| Browser goes away | upstream closed | Log entry `client disconnected` |
