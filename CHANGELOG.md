# Changelog

All notable changes to MLX Studio are documented here.
This project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

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

[2.0.0]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v2.0.0
[1.0.0]: https://github.com/DipeshPansuriya/MLX_Model_App/releases/tag/v1.0.0
