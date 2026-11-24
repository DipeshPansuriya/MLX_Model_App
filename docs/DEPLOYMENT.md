---
type: Deployment
title: "Deployment — MLX Studio"
status: "Built at tag v2.0.0 (2026-11-24)"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.0"
related:
  - ARCHITECTURE.md
  - SECURITY.md
  - RUNBOOK.md
  - ../packaging/
---

# Deployment — MLX Studio

## 1. Two ways to run it

| Mode | Command | Audience |
|---|---|---|
| **Packaged app** | Double-click `MLX Studio.app`, or open the DMG and drag it to Applications | End users |
| **From source** | `./run.sh` or `Run_MLX_Manager.command` | Development |

Both execute the same `app.py`. The bundle is a wrapper: `Info.plist`, an icon, a
shell stub and the Python sources under `Contents/Resources`.

## 2. What the bundle contains

```
MLX Studio.app/
└── Contents/
    ├── Info.plist                 # identity, version, icon, minimum OS
    ├── MacOS/
    │   └── MLXStudio              # bash stub: find Python, exec launcher.py
    ├── Resources/
    │   ├── app.py                 # backend
    │   ├── launcher.py            # interpreter discovery, log redirect
    │   ├── AppIcon.icns           # compiled icon
    │   └── ui/
    │       ├── index.html
    │       ├── style.css
    │       └── app.js
    └── _CodeSignature/            # ad-hoc signature
```

| `Info.plist` key | Value |
|---|---|
| `CFBundleName` / `CFBundleDisplayName` | `MLX Studio` |
| `CFBundleIdentifier` | `com.mlxstudio.app` |
| `CFBundleExecutable` | `MLXStudio` |
| `CFBundleShortVersionString` / `CFBundleVersion` | read from `APP_VERSION` |
| `CFBundleIconFile` | `AppIcon` |
| `LSMinimumSystemVersion` | `12.0` |
| `LSApplicationCategoryType` | `public.app-category.developer-tools` |
| `NSHighResolutionCapable` | `true` |
| `MLXStudioBuildDate` | UTC build timestamp |

The version is never duplicated by hand: `build_app.sh` greps `APP_VERSION` from
`app.py` and writes it into the plist.

## 3. Build

```bash
cd packaging
./build_app.sh              # app + dmg
./build_app.sh --app-only   # app only
./build_app.sh --no-sign    # skip ad-hoc signing
```

Steps performed:

1. Read `APP_VERSION` from `app.py`; abort if absent.
2. Create `dist/MLX Studio.app/Contents/{MacOS,Resources,Frameworks}`.
3. Copy `app.py`, `ui/`, `launcher.py`, `AppIcon.icns`.
4. Generate `Info.plist`.
5. Write the `MLXStudio` stub, `chmod +x`.
6. `codesign --force --deep --sign -` (ad-hoc).
7. Stage a DMG folder: the app, an `Applications` symlink, `Read Me.txt`.
8. `hdiutil create -format UDZO -fs HFS+`.
9. Print the DMG path, size and SHA-256.

Output:

```
dist/MLX Studio.app
dist/MLX.Studio-2.0.0.dmg      # URL-safe asset name
```

The asset name uses dots rather than spaces so release download URLs need no
escaping.

## 4. Interpreter discovery

The bundle does not embed Python. Two layers resolve an interpreter:

**Stub** (`Contents/MacOS/MLXStudio`) tries, in order:

1. `/Library/Frameworks/Python.framework/Versions/Current/bin/python3`
2. `/opt/homebrew/bin/python3`
3. `/usr/local/bin/python3`
4. `/usr/bin/python3`

Each must satisfy `sys.version_info >= (3, 10)`. If none does, an `osascript`
alert names the requirement and the fix.

**Launcher** (`launcher.py`) repeats the search, then `Popen`s `app.py` with
`start_new_session=True` and redirects stdout/stderr to
`~/.mlx_studio/launch.log`. The log is the first place to look when a
double-click produces nothing.

## 5. Release

```bash
# 1. bump APP_VERSION in app.py and update CHANGELOG.md
# 2. build
cd packaging && ./build_app.sh
# 3. manifest
./release_manifest.py \
  --dmg "../dist/MLX.Studio-2.1.0.dmg" \
  --url "https://github.com/DipeshPansuriya/MLX_Model_App/releases/download/v2.1.0/MLX.Studio-2.1.0.dmg" \
  --notes "…" \
  --out ../release/latest.json
# 4. commit, tag, push
git commit -am "release: 2.1.0"
git tag -a v2.1.0 -m "MLX Studio 2.1.0"
git push origin main --tags
# 5. create the GitHub release and upload the DMG
```

`release_manifest.py` reads the version from `app.py` unless `--version` is given,
computes the DMG's SHA-256 and size, and writes the manifest.

The manifest is committed at `release/latest.json` and served from
`raw.githubusercontent.com`, which is what installed copies poll.

## 6. Update channels

| Channel | Mechanism | Cadence |
|---|---|---|
| Runtime packages | `importlib.metadata` vs. PyPI JSON API | On demand and at launch |
| Application | `release/latest.json` via `MLX_STUDIO_UPDATE_URL` | On demand and at launch |

The app compares versions numerically (`vt()` splits on `.`, `-`, `+` and coerces
digit runs to integers) so `0.32.10 > 0.32.9`. A manifest older than or equal to
the running build produces no update panel.

Without `MLX_STUDIO_UPDATE_URL` the app reports `local build` and never claims an
update — the safe default for a source checkout.

## 7. CI (not built)

There is no automated build. The intended workflow, if added:

```yaml
on:
  push:
    tags: ["v*"]
jobs:
  release:
    runs-on: macos-14
    steps:
      - uses: actions/checkout@v4
      - run: cd packaging && ./build_app.sh
      - run: cd packaging && ./release_manifest.py --dmg "../dist/MLX.Studio-${VERSION}.dmg" --url "…" --out ../release/latest.json
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*.dmg
```

Marked **Open** in [PRD.md](PRD.md) §9 (OD-2).

## 8. Verification performed for 2.0.0

| Check | Result |
|---|---|
| `codesign --verify --deep --strict` | valid (ad-hoc) |
| `hdiutil verify` on the DMG | CRC32 valid |
| Mount and inspect layout | app, `Applications` symlink, `Read Me.txt` present |
| Copy out and launch | `/api/ping` → `{"ok": true, "app": "2.0.0"}` |
| Native window present | 1360×900 window owned by the Python process |
| Release asset download | HTTP 200, 979129 bytes |
| SHA-256 of download vs manifest | identical (`e4236659…`) |
| Live manifest fetch from `raw.githubusercontent.com` | `latest: 2.0.0`, `update: false` |

## 9. Rollback

| Situation | Action |
|---|---|
| New version misbehaves | Reinstall the previous DMG from Releases; the app is self-contained |
| Manifest points at a bad build | Revert `release/latest.json`, push; installed copies see the older version next check |
| Bad tag published | Delete the release and tag, re-tag the corrected commit |

Model weights and the activity log are untouched by app rollback: they live in
`~/.cache/huggingface` and `~/.mlx_studio`.

## 10. Sources read for this file

| Source | Used for |
|---|---|
| `packaging/build_app.sh` | Build steps, plist keys, DMG layout, asset naming |
| `packaging/launcher.py` | Interpreter order, log path |
| `packaging/release_manifest.py` | Manifest fields and checksum |
| `release/latest.json` | Published manifest values |
| Release verification run, 2026-11-24 | §8 table |
