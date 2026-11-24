---
type: ADR
title: "ADR-005 — JSON-lines activity log instead of a database"
status: "Accepted"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../ARCHITECTURE.md
  - ../PRD.md
---

# ADR-005 — JSON-lines activity log instead of a database

## 1. Decision

Activity is recorded by appending one JSON object per line to
`~/.mlx_studio/activity.log`. There is no database, no schema migration and no
index.

## 2. Context

The product must answer "what did I send to which model, and when". Entries are
written on every load, unload, chat turn, download, delete, server transition,
install and update check. The reader is a single table in the UI, filtered by
action and sorted newest-first.

The volume is small: a heavy day is a few hundred lines. The retention
requirement is "until the user clears it".

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **SQLite** | Correct for query-heavy workloads; here the only query is "last N, optionally filtered by action", which a line scan answers. Adds a schema, a migration story and a file the user cannot read with `tail` |
| **Structured log via `logging` + JSON formatter** | Interleaves with framework output, rotates on a policy the user did not choose, and is awkward to read back into the UI |
| **In-memory ring buffer only** | Loses history on quit, which defeats the requirement |
| **Remote logging** | Contradicts the local-only premise |
| **JSON lines (chosen)** | Append-only, crash-tolerant, greppable, readable by `tail`, and re-readable by the app with no parser beyond `json.loads` per line |

## 4. Consequences

**Good**

- A write is one `open(..., "a")` and one `write`. No lock, no transaction, no
  corruption window beyond a torn final line.
- The reader tolerates malformed lines by wrapping them as `raw`, so a torn write
  degrades one row instead of breaking the table.
- Users can inspect and filter with standard tools:
  `jq -r 'select(.action=="chat")' ~/.mlx_studio/activity.log`
- Clearing is `unlink`; there is no vacuum, no WAL, no sidecar file.

**Bad**

- No indexing: filtering scans the file. At the current volume this is
  microseconds; at a million lines it would not be.
- No rotation: the file grows until cleared. There is no size cap.
- No schema enforcement: a typo in an action name produces a row that the filter
  dropdown cannot select.
- Concurrent writers from multiple app instances would interleave. Only one
  instance is expected, and the second cannot bind port 8765 anyway.
- Detail strings are truncated at 300 characters on error paths, so very long
  errors lose their tail.

## 5. Revisit trigger

Adopt SQLite if the log needs full-text search, aggregation across months, or
retention policies; or if the file routinely exceeds a few hundred thousand lines.

## 6. Evidence

| Claim | Location |
|---|---|
| Append writer | `app.py:52` (`log_activity`) |
| Tolerant reader | `app.py:67` (`read_activity`) |
| Truncation on error | `app.py` `str(exc)[:300]` in worker paths |
| Clear | `POST /api/logs/clear` |
| Filter | `ui/index.html` `#logFilter`, `ui/app.js` `loadLogs` |
