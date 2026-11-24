---
type: ADR
title: "ADR-006 — Manifest-based app updates, PyPI for packages"
status: "Accepted"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
supersedes: none
related:
  - ../DEPLOYMENT.md
  - ../PRD.md
---

# ADR-006 — Manifest-based app updates, PyPI for packages

## 1. Decision

Two independent update channels:

- **Runtime packages** (MLX, MLX-LM, huggingface-hub): compare the installed
  version from `importlib.metadata` against the latest release on the PyPI JSON
  API.
- **The application itself**: fetch a JSON manifest from
  `MLX_STUDIO_UPDATE_URL` and compare its `version` field against `APP_VERSION`.

Neither channel installs anything automatically. The package channel offers an
**Install / Update** button that runs pip; the app channel offers a **Download
DMG** button that opens the release URL.

## 2. Context

MLX moves quickly, and a stale runtime is the most likely cause of a broken
experience. The app must therefore be able to say "you are behind" without a
backend of its own. It also must not lie: reporting an update that does not exist
is worse than reporting nothing.

There is no update server, no Sparkle framework, no notarised distribution and no
Apple Developer account.

## 3. Alternatives considered

| Alternative | Why rejected |
|---|---|
| **Sparkle framework** | The macOS standard, but requires a signed app, an appcast and a bundled framework; unavailable without a Developer ID |
| **GitHub Releases API directly** | Workable, but couples the app to one host and to GitHub's rate limits and JSON shape; a manifest is host-agnostic and lets the release notes be curated |
| **Auto-download and install** | Turns the manifest into a code-execution channel; unacceptable without notarisation and signature verification |
| **No update check at all** | Leaves the user on a stale runtime with no signal; the original complaint that prompted this work |
| **Manifest + PyPI (chosen)** | Each channel uses the source of truth that already exists for it: PyPI for packages, a small file for the app |

## 4. Consequences

**Good**

- The package channel needs no infrastructure: PyPI already publishes versions.
- The app channel is a static file that can be served from `raw.githubusercontent.com`,
  an S3 bucket or a local path (`file://`) for testing.
- Version comparison is numeric (`vt()` splits on `.`, `-`, `+`), so `0.32.10`
  correctly outranks `0.32.9`.
- With no channel configured the app reports `local build` and never claims an
  update — the right default for a source checkout.
- The manifest carries `sha256` and `size`, so a user can verify a download
  manually.

**Bad**

- The manifest is trusted input. A compromised host could point at a malicious
  DMG; the user still has to download and open it, and Gatekeeper still applies,
  but the app does not verify the checksum itself.
- No automatic install means users can ignore the notice indefinitely.
- The PyPI check makes a network request at launch; offline it fails silently and
  the pills show no comparison rather than an error.
- Two channels means two places to look; the nav `!` badge aggregates them, which
  hides which one fired until Setup is opened.
- `truststore` must be injected for TLS to work on python.org builds; without it
  the check fails with `CERTIFICATE_VERIFY_FAILED` (fixed during 2.0.0
  verification).

## 5. Revisit trigger

Adopt signature verification inside the app if auto-install is ever added. Move
the manifest to a first-party host if `raw.githubusercontent.com` rate limits
become a problem.

## 6. Evidence

| Claim | Location |
|---|---|
| PyPI lookup | `app.py:120` (`latest_pypi_version`) |
| Version compare | `app.py:134` (`vt`), `app.py:140` (`is_newer`) |
| Manifest fetch | `app.py:437` (`latest_app_version`) |
| Channel default | `app.py:34` (`APP_UPDATE_URL`) |
| Manifest generator | `packaging/release_manifest.py` |
| Published manifest | `release/latest.json` |
