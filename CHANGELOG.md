# Changelog

All notable changes to MLX Studio are documented here.
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [2.0.2] — 2026-11-25

Documentation and packaging fix. `2.0.1` shipped a `Read Me.txt` that told users
to drag the app and then allow it afterwards, which is the step that fails on
macOS 15+. The DMG now leads with clearing quarantine **before** installing.

### Changed

- **DMG `Read Me.txt` rewritten.** It now opens with an *IMPORTANT — read this
  before double-clicking* block giving the `xattr -dr com.apple.quarantine` step
  on the DMG, the same step on the installed app if it was already dragged, the
  Open Anyway fallback, and the `curl` installer as the route that avoids the
  dialog entirely.
- **Install docs lead with the prompt-free route.** README, RUNBOOK §4.7 and
  ADR-007 put the installer first, document clearing quarantine on the DMG before
  installing, and record what does *not* work: Homebrew casks still apply
  quarantine, and a self-signed certificate is still rejected by `spctl` even
  when trusted.

### Added

- **One-line installer** — `scripts/install.sh` downloads the latest DMG with
  `curl`, verifies its SHA-256 against `release/latest.json`, installs to
  `/Applications`, checks the signature and clears quarantine. `curl` sets no
  quarantine flag, so macOS 15+ never shows the "Not Opened" dialog and no Apple
  Developer account is needed. Supports `--version`, `--dir` and `--no-launch`.
- **SA-16 installer safety** — the audit now checks that the installer parses,
  verifies a checksum and the signature, clears quarantine on the artifact it
  verified, and never pipes a download into a shell.

## [2.0.1] — 2026-11-25

Security release. The published `2.0.0` DMG was built from `c421803`, before the
security audit fixes landed, so it still shipped the unauthenticated `/api/quit`
endpoint and the prefix-based static path check. This release republishes the
hardened tree and adds the log folder, the model fit catalogue and the background
API server.

### Security

- **`/api/quit` removed.** It was unauthenticated and shut the app down; it now
  returns `404`. Fixed in `799b22e`, shipped here for the first time.
- **Static path traversal closed by construction.** The request path is a key into
  a map built by scanning `ui/` at startup, so no filesystem path is derived from
  user input. Replaces the `str.startswith` check that allowed
  `/../ui-evil/leak.txt` to return `200`. Clears CodeQL alerts #1–#3
  (`py/path-injection`). Fixed in `799b22e`.
- **Body size limit.** `Content-Length` is parsed defensively and capped at 8 MiB,
  so an oversized or malformed header no longer raises a traceback.
- **Client disconnects** are treated as normal rather than server errors.

### Added

- **Log folder** — everything now lives in `~/.mlx_studio/logs/`
  (`activity.log`, `app.log`, `launch.log`, `server.log`), rotated at 2 MB with
  three backups. Logs from 2.0.0 are migrated on first start.
- **Export diagnostics** — Activity → Export diagnostics downloads a zip of the
  log folder plus `environment.json` (versions, paths, hardware, installed model
  ids) so an end user can attach one file to a GitHub issue.
- **Application log** — `app.log` records startup, shutdown, HTTP errors, model
  load failures, download failures, install results and API server deaths.
- **Best for your Mac** — a curated 25-model catalogue in the Download view,
  each rated `great fit` / `good fit` / `tight fit` / `too large` against this
  machine's unified memory using measured 4-bit weight sizes. Filter by fit band
  or category.
- **Background API server** — a **Background** checkbox detaches `mlx_lm server`
  into its own session so it keeps serving after the window closes. The pid is
  written to `~/.mlx_studio/server.pid` and adopted on the next launch.
- **Server down notification** — a 2 s watchdog detects the server dying, shows a
  red banner with the exit status and a **Restart** button, fires a desktop
  notification, and logs `api server died`.

### Changed

- **Install instructions corrected for macOS 15+.** Apple removed the
  Control-click → Open bypass in Sequoia, so the documented step did nothing and
  the app looked broken. The README, runbook, ADR-007, PRD, security docs and the
  DMG `Read Me.txt` now lead with **System Settings → Privacy & Security → Open
  Anyway**, keeping right-click → Open for macOS 14 and older, and document the
  `xattr -dr com.apple.quarantine` alternative.

### Fixed

- A server restart no longer lets the old watcher delete the new pidfile or
  raise a false "stopped unexpectedly" banner.
- HTTP error logging now coerces the status code, which
  `BaseHTTPRequestHandler` passes as a string.
- The watchdog polls the `Popen` handle rather than the pid, because a detached
  child stays a zombie and `os.kill(pid, 0)` still succeeds on one.

## [2.0.0] — 2026-11-24

First packaged release. Replaces the earlier Tk prototype with a web-technology
interface hosted in a native macOS window.

### Added

- **Native desktop app** — pywebview window (1360×900) with browser fallback.
- **Chat** — in-process model load/unload, token streaming over SSE, per-turn
  metrics (`tokens · tok/s · GB peak`), Stop and New chat controls.
- **Models** — cards per cached model with size, Load/Unload, Reveal, Delete
  (blocked while loaded).
- **Download** — search the `mlx-community` org on Hugging Face, live per-model
  download status.
- **API Server** — one-click `mlx_lm server` on `http://127.0.0.1:8080/v1`, live
  output pane, copyable endpoint, `curl` example.
- **Activity** — filterable, colour-coded log persisted to
  `~/.mlx_studio/activity.log`.
- **Setup** — installed versions of MLX / MLX-LM / huggingface-hub with
  `up to date` / `update → x.y.z` / `missing` pills, app version, hardware,
  last-check time, and a nav `!` badge when anything is outdated.
- **App update channel** — `MLX_STUDIO_UPDATE_URL` manifest support with an
  in-app *App update available* panel and DMG download button.
- **Packaging** — `packaging/build_app.sh` builds a signed `.app` and a
  compressed `.dmg`; `packaging/release_manifest.py` generates the update
  manifest with SHA-256.
- **App icon** — rendered 1024 px source compiled to `AppIcon.icns`.
- **Docs** — README with screenshots, LICENSE, CHANGELOG, `.gitignore`.

### Changed

- Model sizes now follow symlinked blobs and de-duplicate by inode, so a 4 GB
  model reports 4.0 GB instead of 8.0 GB.
- Server stop is logged exactly once, by the output pump that observes exit.

### Fixed

- Cross-thread Tk callbacks crashed with `RuntimeError: main thread is not in
  main loop`; worker updates now go through a queue drained on the main thread.
- PyPI update checks failed with `CERTIFICATE_VERIFY_FAILED` on python.org
  builds; `truststore` is now injected before the request.

### Removed

- `MLX_Model_Manager.py` (Tk prototype) — superseded by `app.py` + `ui/`.

## [1.0.0] — 2026-11-24

- Initial Tkinter prototype: install MLX, download/delete models, launch
  `mlx_lm chat` in Terminal, run the API server, basic activity log.

[2.0.2]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.2
[2.0.1]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.1
[2.0.0]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.0
[1.0.0]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v1.0.0
