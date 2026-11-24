---
type: Diagram
title: "Release flow — version bump to installed copies"
date: 2026-11-24
project: mlx-studio
related:
  - ../DEPLOYMENT.md
  - update-decision.md
---

# Release flow

How a change becomes an installed app, and how installed copies learn about the
next one.

```mermaid
flowchart TD
    A["edit app.py<br/>APP_VERSION = x.y.z"] --> B["update CHANGELOG.md"]
    B --> C{"py_compile<br/>node --check"}
    C -->|fail| A
    C -->|pass| D["regression run<br/>backend + CDP UI"]
    D -->|fail| A
    D -->|pass| E["./packaging/build_app.sh"]

    E --> F["dist/MLX Studio.app<br/>Info.plist · icon · sources"]
    F --> G["codesign --sign -<br/>ad-hoc"]
    G --> H["stage: app + Applications symlink + Read Me"]
    H --> I["hdiutil create -format UDZO"]
    I --> J["dist/MLX.Studio-x.y.z.dmg<br/>+ sha256 printed"]

    J --> K["./packaging/release_manifest.py<br/>--dmg --url --notes"]
    K --> L["release/latest.json<br/>version · url · sha256 · size"]

    L --> M["git commit · tag vx.y.z<br/>push main --tags"]
    M --> N["GitHub Release<br/>upload DMG asset"]

    N --> O["user downloads DMG"]
    O --> P["drag to Applications"]
    P --> Q["right-click Open<br/>Gatekeeper"]
    Q --> R["app runs"]

    L -.->|"MLX_STUDIO_UPDATE_URL"| S["installed copy polls manifest"]
    S --> T{"manifest.version<br/>&gt; APP_VERSION?"}
    T -->|yes| U["Setup shows update panel<br/>+ nav ! badge"]
    T -->|no| V["local build / up to date"]
    U --> O
```

## Artefacts produced

| Artefact | Path | Contains |
|---|---|---|
| App bundle | `dist/MLX Studio.app` | plist, icon, `app.py`, `launcher.py`, `ui/` |
| Installer | `dist/MLX.Studio-x.y.z.dmg` | app, `Applications` symlink, `Read Me.txt` |
| Manifest | `release/latest.json` | version, url, notes, published, sha256, size |
| Release asset | GitHub Releases | the DMG |

## Gates before a tag

| Gate | Command | Blocks release |
|---|---|---|
| Python syntax | `python3 -m py_compile app.py` | yes |
| JS syntax | `node --check ui/app.js` | yes |
| Backend smoke | `/api/ping`, `/api/state`, SSE chat | yes |
| UI regression | CDP run, `RESULT: PASS` | yes |
| Signature | `codesign --verify --deep --strict` | yes |
| DMG integrity | `hdiutil verify` | yes |
| Install and launch | copy out, `open`, `/api/ping` | yes |
| Checksum match | download vs manifest `sha256` | yes |

## Verified for 2.0.0

| Step | Result |
|---|---|
| DMG built | 979129 bytes |
| Signature | valid (ad-hoc) |
| `hdiutil verify` | CRC32 valid |
| Launch from DMG copy | `/api/ping` → `{"ok": true, "app": "2.0.0"}` |
| Asset download | HTTP 200, size matched |
| SHA-256 | `e4236659…` matched the manifest |
| Live manifest fetch | `latest 2.0.0`, `update false` |
