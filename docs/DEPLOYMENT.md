---
type: Deployment
title: "Deployment — MLX Studio"
status: "2.0.9 released 2026-11-26; lines marked unreleased describe the working source"
date: 2026-11-25
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.9"
related:
  - ARCHITECTURE.md
  - SECURITY.md
  - RUNBOOK.md
  - ../packaging/
---

# Deployment — MLX Studio

MLX Studio is a desktop app. Deployment means building the `.pkg` and `.dmg`,
publishing them as GitHub release assets and updating the two files in
`release/`.

## 1. Ways to run it

| Mode | How | Audience |
|---|---|---|
| Packaged app | Install with the one-line installer, `.pkg` or DMG ([README](../README.md)) | Users |
| From source | `./run.sh` or `Run_MLX_Manager.command` | Development |

## 2. What the bundle contains

```
MLX Studio.app/
└── Contents/
    ├── Info.plist
    ├── MacOS/MLXStudio          # compiled arm64 launcher (packaging/launcher.c)
    ├── Resources/
    │   ├── app.py               # backend
    │   ├── mlx_guard.py         # API server request guard
    │   ├── mlx_loadtest.py      # Load Test runner
    │   ├── AppIcon.icns
    │   └── ui/                  # index.html, style.css, app.js
    └── _CodeSignature/          # ad-hoc signature
```

| `Info.plist` key | Value |
|---|---|
| `CFBundleIdentifier` | `com.mlxstudio.app` |
| `CFBundleExecutable` | `MLXStudio` |
| `CFBundleShortVersionString` / `CFBundleVersion` | read from `APP_VERSION` in `app.py` |
| `LSMinimumSystemVersion` | `12.0` |

Running on macOS 12 or any older macOS is UNVERIFIED, as is the minimum macOS
that MLX itself needs.

## 3. Build

Needs `clang` (Xcode command line tools), on a Mac.

```bash
./packaging/build_app.sh              # app, dmg, pkg, dist/SHA256SUMS
./packaging/build_app.sh --app-only   # app only
./packaging/build_app.sh --no-sign    # skip ad-hoc signing
```

| Step | Detail |
|---|---|
| 1 | Read `APP_VERSION`; abort if absent (unreleased: needs exactly one line `APP_VERSION = "X.Y.Z"`, else it stops before writing anything). Remove the old app and build record |
| 2 | Copy `app.py`, `mlx_guard.py`, `mlx_loadtest.py`, the three UI files and the icon; write `Info.plist` |
| 3 | Compile the launcher for arm64; fail if its `minos` differs from `LSMinimumSystemVersion` |
| 4 | Ad-hoc `codesign`. In 2.0.9 the script does not stop if this fails; the unreleased script exits with `error: codesign failed` and packages nothing |
| 5 | `dist/MLX.Studio-<version>.dmg` with an `Applications` shortcut and `Read Me.txt` |
| 6 | `dist/MLX.Studio-<version>.pkg` with `pkgbuild` (unsigned), installs into `/Applications` |
| 7 | `dist/SHA256SUMS`, the build record for both files |

## 4. Interpreter discovery

The bundle does not embed Python. The launcher tries, in order:

1. `/Library/Frameworks/Python.framework/Versions/Current/bin/python3`
2. `/opt/homebrew/bin/python3`
3. `/usr/local/bin/python3`
4. `/usr/bin/python3` (skipped once another interpreter works)

It needs Python 3.10 or newer and prefers one that has `mlx_lm`, then
`pywebview`. It then replaces itself with `python3 app.py`. Output goes to
`~/.mlx_studio/logs/launch.log`, the first place to look when a double-click
does nothing.

Unreleased launcher: a log it may not write is moved to `launch.log.1` (replacing
the older one) and a new file is opened; a symlink or a folder is left alone.
Launches started together take turns at this, waiting about 5 seconds at most.

## 5. Release

### 5.1 What GitHub has (after the 2.0.9 release, 2026-11-26)

| Item | State |
|---|---|
| Latest release | `v2.0.9`, published, dmg and pkg |
| `v2.0.9` tag | On `a954b91`, the merged 2.0.9 source |
| `release/latest.json` on `main` (`20f9768`) | Says 2.0.9; `sha256` is that of the 2.0.9 pkg |
| `release/SHA256SUMS` on `main` (`20f9768`) | Lists the 2.0.9 dmg and pkg |
| `v2.0.8` tag | Holds the 2.0.7 source. Which source built its files is UNVERIFIED |
| `v2.0.7` | No tag, no release; both asset URLs returned 404 when checked before the 2.0.9 release |

After promotion, both assets were downloaded again and matched
`release/SHA256SUMS`, and the pkg matched `release/latest.json`. Before this
release the one-line installer was very likely broken: it compared the 2.0.8 dmg
with the 2.0.7 manifest checksum (read from code and published metadata; not run
live).

### 5.2 Checksum list: no backfill (decided 2026-11-26)

The owner chose option A: `release/SHA256SUMS` starts with the built and
verified 2.0.9. No earlier release is added.

| Effect | Detail |
|---|---|
| No download of unknown files | Nothing published before 2.0.9 is vouched for |
| Default install | Passed the staging test after 2.0.9 was promoted (2026-11-26) |
| `--version` 2.0.8 or older | Refused: no checksum line |

The release tool still has `--backfill`; this release does not use it.

### 5.3 Release order (option A)

Run from the project root, in this order. Steps marked **Yes** publish, download
or change GitHub and need the owner's approval first. They were run for 2.0.9 on
2026-11-26, except the gate script in step 4; the result is recorded after the
step 12 commands. `gh` was not available, so the release was created through the
GitHub REST API.

| Step | Action | Approval |
|---|---|---|
| 1 | All changes finished, full suite green. Re-check GitHub (read only): latest is `v2.0.8`, no `v2.0.9` | No |
| 2 | Branch `release/2.0.9`; commit the source **without** `release/latest.json`, then park that edit: `git stash push -m "2.0.8 manifest, not in 2.0.9 source" -- release/latest.json` | No |
| 3 | Push the branch and open the pull request | Yes |
| 4 | `Release consistency` is green and prints the "2.0.7 was never published" note. Then `./scripts/set_approval_gate.sh --check`, then `--disable` if approvals are 0, or `--reviewer <same user>` if the approval gate is on | Yes |
| 5 | Merge only with every check green. No install test here: the new installer refuses until step 10 is merged | Yes |
| 6 | Build and generate in a clean worktree of the merged commit (block below) | No |
| 7 | `git tag -a v2.0.9 <merged-sha> -m "release: 2.0.9" && git push origin v2.0.9`; wait for the tag run to pass | Yes |
| 8 | Publish as a **pre-release** from the same worktree. Do not rebuild (command below) | Yes |
| 9 | Branch `release/2.0.9-manifest` with `release/latest.json`, `release/SHA256SUMS` and `CHANGELOG.md`; open the pull request | Yes |
| 10 | CI downloads and verifies the published assets. Merge when green | Yes |
| 11 | Wait about 5 minutes (raw file cache), then run the staging test with `--version 2.0.9` added | Yes |
| 12 | Promote: `gh release edit v2.0.9 --prerelease=false --latest`, then run the staging test unchanged | Yes |

Step 6 (the CHANGELOG entry carries no checksum block before this):

```bash
git fetch origin && W="$(mktemp -d)/rel" && git worktree add "$W" <merged-sha> && cd "$W"
./packaging/build_app.sh
python3 packaging/release_manifest.py --dist dist --notes "<notes>"
# paste the printed checksum block into the CHANGELOG 2.0.9 entry
python3 packaging/release_manifest.py --verify --release --dist dist
./scripts/security_audit.sh --release    # reads the real ~/.mlx_studio
```

If a rebuild changes a checksum already recorded for 2.0.9, the tool refuses.
`--replace` overrides this. It cannot know whether a version was published, so
never use it after `gh release create`.

Step 8:

```bash
gh release create v2.0.9 --verify-tag --prerelease --title "MLX Studio 2.0.9" --notes "<notes>" \
  dist/MLX.Studio-2.0.9.dmg dist/MLX.Studio-2.0.9.pkg
```

Staging test (steps 11 and 12):

```bash
curl -fsSL https://raw.githubusercontent.com/DipeshPansuriya/MLX_Model_App/main/scripts/install.sh \
  | bash -s -- --dir "$(mktemp -d)" --no-launch
```

| Written by step 6 | Content |
|---|---|
| `release/latest.json` | `version`, `url` and `sha256` and `size` of the **pkg**, `notes`, `published` |
| `release/SHA256SUMS` | Checksum of every asset it lists: new lines added, earlier ones kept |

Never edit either by hand. Until step 10 is merged, the installer refuses 2.0.9,
because `main` has no checksum for it. CI cannot stop someone marking a release
as latest before that; the pre-release flag in step 8 covers it.

**Result for 2.0.9 (2026-11-26):**

| Steps | Result |
|---|---|
| 1 to 3, 5 | A test fix and a `packaging/launcher.c` fix were added to the pull request after the first CI run failed. All 15 checks then passed; pull request #26 was squash merged as `a954b91` |
| 4 | `set_approval_gate.sh` was not run; no ruleset change |
| 6 | Clean worktree build; full suite green; `--verify --release` and `--check --tag` passed; audit `--release`: 37 pass, 2 expected signing warnings, 0 fail |
| 7 | Tag `v2.0.9` pushed on `a954b91`; tag run passed 6 of 6 jobs |
| 8 | Published as a pre-release without a rebuild |
| 9, 10 | Pull request #27: 15 of 15 checks passed, including download and verification of the published assets; squash merged as `20f9768` |
| 11, 12 | Staging test passed with `--version 2.0.9`, then unchanged after promotion to latest |

## 6. Installer

`scripts/install.sh` (options: see the [README](../README.md)) installs from the
DMG:

1. Apple Silicon and `curl` required; resolves the latest release tag if no
   version is given.
2. Downloads `MLX.Studio-<version>.dmg`, then `release/SHA256SUMS` from `main`.
3. Refuses to install without exactly one checksum line for that dmg, or on a
   mismatch. A pre-release is not picked up unless `--version` names it
   (GitHub's "latest release" behaviour; UNVERIFIED live).
4. Copies the app next to the target and verifies its signature.
5. Swaps it in; the previous app is restored if the swap fails.

Trust limits are listed in [SECURITY.md](SECURITY.md) §5.

## 7. Update channels

| Channel | Mechanism |
|---|---|
| MLX packages | Installed versions compared with PyPI; install uses the pins in `app.py` (`mlx` 0.32.4, `mlx-lm` 0.32.2) and does not downgrade a newer one |
| Application | A manifest at `MLX_STUDIO_UPDATE_URL`, for example the raw URL of `release/latest.json` |

Without `MLX_STUDIO_UPDATE_URL` the app does not look for app updates. The app
shows a link; it does not install the update itself.

## 8. CI

`.github/workflows/quality.yml` runs on pushes, pull requests and `v*` tags.
For the 2.0.9 release it passed on GitHub, including the 6 of 6 jobs of the
`v2.0.9` tag run (2026-11-26, section 5.3).

| Job | Checks |
|---|---|
| `lint`, `syntax`, `docs` | Lint, syntax of Python, JavaScript and shell, documentation links |
| `tests` | `pytest` on Ubuntu without MLX |
| `macos-build` | Build, built pkg matches the source, tests on macOS |
| `release` (check name `Release consistency`) | Version, manifest, `SHA256SUMS` and tags agree, including one valid line for the manifest version's dmg (except the never-published 2.0.7 manifest); a pushed tag matches `APP_VERSION`; a change under `release/` must match the published assets |

CI does not publish a release.

Unreleased workflow change (not yet run on GitHub): `macos-build` installs `mlx`,
`mlx-lm` and `huggingface-hub` pinned by `requirements.txt` and fails if a test in
`test_guard.py`, `test_guard_null_args.py`, `test_app_robustness.py` or
`test_packaging.py` was skipped or not run
(`.github/scripts/require_no_skips.py`, which must be committed with the
workflow). `macos-build` is not in the required checks that
`scripts/set_approval_gate.sh` lists, so a failure there does not block a merge.

## 9. After an update

Quit the app and open the new one. A background server from an older build is
adopted as it is and stays without the new guard. If the app shows **server
unprotected**, open API Server and press **Restart now** (same model, same mode).

## 10. Rollback

| Situation | Action |
|---|---|
| Before the step 5 merge | Close the pull request; `git stash pop` only if the 2.0.8 edit is wanted back |
| The source merge is bad | `git revert <merge-sha>` through a pull request. This restores the old installer, which compares the dmg with the manifest checksum (the pkg's since 2.0.9), so it would very likely fail. Prefer a forward fix |
| `Release consistency` blocks wrongly | Delete the two `Release consistency` lines in `scripts/set_approval_gate.sh` and rerun step 4 (needs approval) |
| 2.0.9 bad before promotion | Leave it as a pre-release; `git revert <release-pr-sha>` through a pull request |
| 2.0.9 bad after promotion | `gh release edit v2.0.9 --prerelease` (latest returns to 2.0.8, which the new installer refuses: no checksum line), then the same revert |
| A user needs an earlier version | Reinstall its release asset, or `scripts/install.sh --version <v>` if its checksum is in `release/SHA256SUMS` |
| Never | Move or reuse a tag; publish a new version number instead |

Model weights stay in the Hugging Face cache and logs in `~/.mlx_studio`.

## 11. Verification

| Check | Result |
|---|---|
| 2.0.9 rehearsal in a scratch clone: build, generate, `--verify --release`, audit `--quick --release`; `minos 12.0` | Passed on 2026-11-25, before the guard was added |
| Installer with a real dmg, stubbed download, temporary target, run twice | Exit 0 |
| `release_manifest.py --check` and `--check --tag v2.0.9` on the working tree, 2026-11-26 after the release | Passed |
| Section 5 steps for 2.0.9, with real release assets | Run on 2026-11-26; see section 5.3 |
| Released app installed over `/Applications` (equals the released dmg), 2026-11-26 | `/api/ping` shows `2.0.9`; server through the guard with the 1M model answered a chat; foreign `Origin` and `Host` got 403 |
| App window and UI clicks, pkg install, DeepSeek Harness end to end | UNVERIFIED |
| `--backfill` against real assets (not used for 2.0.9) | UNVERIFIED |

## 12. Sources read for this file

`packaging/build_app.sh`, `packaging/launcher.c`,
`packaging/release_manifest.py`, `scripts/install.sh`, `app.py`,
`.github/workflows/quality.yml`, `scripts/set_approval_gate.sh`,
`release/latest.json`.
