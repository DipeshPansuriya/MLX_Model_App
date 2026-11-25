---
type: Diagram
title: "Update decision — how the app decides to show an update"
date: 2026-11-24
project: mlx-studio
related:
  - ../DEPLOYMENT.md
  - release-flow.md
  - ../adr/ADR-006-manifest-updates.md
---

# Update decision

Two independent channels feed one badge. This diagram shows exactly what the app
compares and what it renders.

```mermaid
flowchart TD
    START["app launch<br/>or Check for updates"] --> PKG["worker_check_versions"]

    PKG --> P1["importlib.metadata<br/>mlx · mlx-lm · huggingface-hub"]
    PKG --> P2["PyPI JSON API<br/>pypi.org/pypi/PACKAGE/json"]
    PKG --> P3{"MLX_STUDIO_UPDATE_URL<br/>set?"}

    P3 -->|no| P4["app.channel = local build<br/>app.latest = null"]
    P3 -->|yes| P5["fetch manifest<br/>truststore TLS"]
    P5 -->|ok| P6["app.latest = manifest.version<br/>app.release = url, notes, sha256"]
    P5 -->|fail| P7["app.latest = null<br/>no false update"]

    P1 --> CMP["compare with vt() / is_newer()"]
    P2 --> CMP
    P6 --> CMP
    P4 --> CMP
    P7 --> CMP

    CMP --> D1{"mlx or mlx-lm<br/>missing?"}
    D1 -->|yes| R1["pill: missing (red)<br/>button: Install MLX"]
    D1 -->|no| D2{"latest &gt; installed?"}
    D2 -->|yes| R2["pill: update → x.y.z (amber)<br/>button: Install / Update"]
    D2 -->|no| R3["pill: up to date (green)"]

    CMP --> D3{"manifest.version<br/>&gt; APP_VERSION?"}
    D3 -->|yes| R4["app pill: update → x.y.z<br/>panel: notes + Download installer<br/>nav ! badge"]
    D3 -->|no| R5["app pill: local build or up to date"]

    R1 --> BADGE
    R2 --> BADGE
    R4 --> BADGE
    BADGE["nav Setup badge =<br/>any of the above"]
```

## Comparison rules

| Rule | Implementation | Example |
|---|---|---|
| Numeric, not lexical | `vt()` splits on `.`, `-`, `+`; digit runs become ints | `0.32.10 > 0.32.9` |
| Missing installed version | Treated as older than any candidate | `None` → update |
| Missing candidate | No update claimed | PyPI unreachable → no pill change |
| Equal versions | No update | `0.32.4` vs `0.32.4` → up to date |
| No channel | Never claims an app update | `local build` |

## Rendered outcomes

| Condition | App pill | Package pill | Nav badge | Panel |
|---|---|---|---|---|
| All current, no channel | `local build` | `up to date` | hidden | hidden |
| All current, channel set | `up to date` | `up to date` | hidden | hidden |
| Package behind | `up to date` | `update → x.y.z` | shown | hidden |
| App behind | `update → x.y.z` | `up to date` | shown | shown |
| Package missing | `local build` | `missing` | shown | hidden |
| Both behind | `update → x.y.z` | `update → x.y.z` | shown | shown |

## Observed on this machine

| Field | Value |
|---|---|
| MLX installed / latest | `0.32.4` / `0.32.4` |
| MLX-LM installed / latest | `0.32.2` / `0.32.2` |
| huggingface-hub | `2.4.0` |
| App version | `2.0.0` |
| Channel without env var | `local build` |
| Channel with 2.1.0 manifest | `update → 2.1.0`, panel visible |
| Last check | `2026-11-24 22:10:17` |

## Failure behaviour

| Failure | Effect |
|---|---|
| PyPI unreachable | Pills show no comparison; no error dialog; app keeps working |
| Manifest unreachable | `app.latest` stays null; no update claimed |
| TLS chain missing | `truststore` injected first; without it the fetch fails and is treated as unreachable |
| Malformed manifest JSON | Treated as unreachable |
