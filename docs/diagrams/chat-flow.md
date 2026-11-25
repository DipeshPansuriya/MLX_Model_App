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

One turn, from keystroke to metrics line.

```mermaid
sequenceDiagram
    autonumber
    actor U as User
    participant UI as ui/app.js
    participant H as Handler<br/>127.0.0.1:8765
    participant S as State
    participant G as stream_chat
    participant M as mlx_lm
    participant L as activity.log

    U->>UI: Enter
    UI->>UI: push user message, render bubble
    UI->>H: POST /api/chat {messages}
    H->>S: lock: model present? generating = true
    alt no model loaded
        S-->>H: model is None
        H-->>UI: 409 {error: "no model loaded"}
        UI-->>U: toast "Load a model first"
    else model loaded
        H-->>UI: 200 text/event-stream
        H->>G: stream_chat(messages)
        G->>M: apply_chat_template(add_generation_prompt=True)
        G->>M: make_sampler(temp=0.7, top_p=0.95)
        loop per token
            M-->>G: GenerationResponse
            G-->>H: ("token", text)
            H-->>UI: event: token
            UI-->>U: append to bubble, scroll
            Note over G: stop_event checked each token
        end
        G-->>H: ("done", meta)
        H-->>UI: event: done {tokens, tps, peak_gb}
        UI-->>U: metrics line under answer
        H->>L: append chat/success
        H->>S: generating = false
    end

    U->>UI: Stop (optional)
    UI->>H: POST /api/chat/stop
    H->>S: stop_event.set()
    Note over G: loop exits, partial answer kept
```

## Timing observed on M6 / 32 GB, 7B 4-bit

| Segment | Observed |
|---|---|
| Request to first token | 0.4–0.9 s |
| Steady-state throughput | 42–53 tok/s |
| Peak memory | 4.37 GB |
| 3-token answer, total | 0.42 s |

## Rules the diagram encodes

| Rule | Where |
|---|---|
| Chat refuses without a loaded model | `409`, `Handler._stream_chat` |
| Tokens are flushed per emission | `emit` writes and flushes, `Handler._stream_chat` |
| Stop is an event, not a kill | A new event per reply in `State.stop_event`, checked per token |
| The partial answer survives a stop | `stream_chat` leaves the loop and still sends `done`; `ui/app.js` keeps the text |
| Every completed turn is logged | `log_activity("chat", …)` in `_stream_chat` |
| The client disconnecting is not an error | `BrokenPipeError` caught, logged as failed |

## Failure paths

| Failure | Response | UI |
|---|---|---|
| No model, model loading or reply running | `409` | Toast and red bubble; the turn is dropped from history |
| Generation raises | SSE `event: error` | Red bubble with the message |
| Client disconnects | connection closed | Log entry `client disconnected` |
| Stop pressed | partial `done` | Partial answer plus metrics |
