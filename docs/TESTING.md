---
type: Testing
title: "Testing — MLX Studio"
status: "2.0.9 released 2026-11-26; also covers unreleased changes in the working source"
date: 2026-11-25
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.9"
related:
  - PRD.md
  - ARCHITECTURE.md
  - RUNBOOK.md
---

# Testing — MLX Studio

## 1. Approach

| Layer | Method |
|---|---|
| Backend and HTTP contract | `pytest`; the real request handler on a free loopback port |
| API server guard | `pytest`; policy functions and the real `mlx_lm` handler (skipped without `mlx_lm`; the unreleased macOS job fails on such a skip) |
| UI behaviour | `ui/app.js` run under Node against a stub DOM (`tests/ui_harness.js`); UI tests skip without Node |
| Packaging, installer, release | `pytest` on the scripts; macOS-only tests skip elsewhere |
| Live window, real models | Manual (§6) |

The app code is not tested against a real browser or native window in `pytest`.

## 2. Run

```bash
python3 -m pip install pytest ruff
PYTHONDONTWRITEBYTECODE=1 python3 -m pytest -p no:cacheprovider
ruff check --no-cache . && ruff format --check --no-cache .
node --check ui/app.js
python3 scripts/check_docs.py
python3 scripts/check_secrets.py
./scripts/security_audit.sh --quick
```

`security_audit.sh` also reads the real `~/.mlx_studio` permissions. Without
`--quick` it runs `pip-audit` if installed.

## 3. Isolation

The `app_module` fixture imports a fresh `app.py` with a temporary `HOME` and
cleared Hugging Face cache variables, and asserts that the cache path is inside
it. Tests marked `integration` touch macOS tooling or the filesystem outside the
temporary folder.

An automatic fixture in `tests/conftest.py` fakes `vm_stat`, so tests never read
this Mac's free memory (unreleased).

The build, release and scanner tests replace `pip_audit` with a stub, so the
security audit test does not call the real one. If `HTTP_PROXY` or `HTTPS_PROXY`
is set, also set `NO_PROXY=127.0.0.1,localhost`; without it some loopback tests
in `test_row553.py` fail.

## 4. Suite

Working tree on 2026-11-26 (unreleased changes included): 38 test files and 1503
collected tests (`pytest --collect-only`). The released 2.0.9 had 28 files, and
its latest reported full run had 1059 tests, all passed.

| File in `tests/` | Covers |
|---|---|
| `test_api.py` | Every endpoint, status codes, JSON shape |
| `test_app_robustness.py` | Concurrency, in-use guards, cache handling, request validation |
| `test_security.py` | Local API origin rules; only the app's own server is signalled |
| `test_row536_secrets_release.py` | Secret scanner allow-list; release checksum rules and `--backfill` |
| `test_guard.py`, `test_m6_guard_lists.py` | API server guard; every file list includes it |
| `test_guard_null_args.py` | `null` tool argument rule: policy, real `mlx_lm` handler with and without streaming, concurrent requests |
| `test_row573_ui_low.py` | Button focus, stale Installed, downloads toggle name, Background box, favicon |
| `test_row553.py` | Download completeness, served-model delete guard, start cancel, install slot, UI fixes (32 tests) |
| `test_row555_scanner_audit.py` | Secret scanner, security audit and release checksum behaviour |
| `test_row557.py` | Search cards, collapsed downloads, model search field (32 tests) |
| `test_row562_scanner_markers.py` | Placeholder rules judge only the secret value (28 tests) |
| `test_server.py` | Pidfile, watchdog, adoption, stop |
| `test_downloads.py` | Progress, cache location, model discovery |
| `test_helpers.py` | Version compare, size formatting, fit rating, pid probes |
| `test_logs.py` | Rotation, permissions, migration, reader |
| `test_single_instance.py` | One running window |
| `test_ui_behaviour.py`, `test_ui_features.py` | Front-end behaviour, copy name, type-to-filter dropdowns |
| `test_ui_buttons.py` | Front-end button behaviour |
| `test_packaging.py` | Runs the build and installer scripts: bundle and pkg structure, version line, signing failure |
| `test_m15_ci.py` | CI build and release gates, and the no-skip checker (`.github/scripts/require_no_skips.py`) |
| `test_row550_launch.py` | Token in the URL fragment, browser mode, second launch (unreleased) |
| `test_row603_csp.py` | Content-Security-Policy on every reply, no inline script, `pywebview` style hash (unreleased) |
| `test_row652_icon.py` | Bundle name and icon at start, with stubbed AppKit (unreleased) |
| `test_row656_guard.py`, `test_row656_app.py`, `test_row656_ui.py` | Two models: admission, cache trim before eviction, start and estimate routes, pickers and notices; fake loader, no real model (unreleased) |
| `test_row713_fixes.py` | Two-model review and security fixes: Chat load refusal, budget file, cache byte limit, notices, browser fallback; stand-in models (unreleased) |
| `test_row731_fixes.py` | Budget file safety, `/api/load` installed check, load-failure panel, screen-reader markup, budget on server adoption (unreleased) |
| `test_row760_limits.py` | Two-model limits: free bytes, context tokens, guard budget, estimate, start, Chat load, `memory_low`, UI words; fake `vm_stat` (unreleased) |
| `test_row815_preload.py` | `preload_done` however preload ends; context shown whatever the last status event was; no `memory_low` before preload ends; guard-to-app check (unreleased) |
| `test_row817_review.py` | Pre-release review fixes: running-pair estimate, restart body, status refresh hook, words for oversize, cache hint, memory line; fake request loop (unreleased) |
| `test_c1_h4_build.py`, `test_m13_l1_launcher.py` | Minimum macOS, clean-clone build, native launcher (unreleased: unwritable log, parallel launches) |
| `test_c2_l3_installer.py`, `test_c3_h5_release.py` | Checksum verification, release manifest |
| `test_h8_m14_secrets.py`, `test_l8_token.py` | Secret scanner, token handling |

The IDs in file names (C1, H4, M6 and so on) are findings from the 2.0.9 review.

## 5. CI

| Job | Runs |
|---|---|
| `tests` | Suite on Ubuntu, without MLX |
| `macos-build` | Build, then the suite on macOS. Unreleased workflow: installs `mlx`, `mlx-lm` and `huggingface-hub` pinned by `requirements.txt`, then fails if a test in `test_guard.py`, `test_guard_null_args.py`, `test_app_robustness.py` or `test_packaging.py` was skipped or not run. Not yet run on GitHub |

Other jobs are listed in [DEPLOYMENT.md](DEPLOYMENT.md) §8.

## 6. Manual checks before a release

| Check | Pass means |
|---|---|
| Install the built `.pkg`, launch | `curl -s http://127.0.0.1:8765/api/ping` shows the new version; one app entry in the Dock |
| Load a model, chat, stop, unload | Tokens stream; stop works |
| Download and cancel a model | Progress shows; cancel leaves it not installed |
| Start the API server, send a request | Reply from `/v1/chat/completions` |
| Send a `text/plain` POST to port 8080 | 415 |
| Name a model that is not downloaded | 400, nothing downloaded |
| Tool call from your client on each model | A structured tool call comes back |
| Keyboard and VoiceOver pass | No unreachable control |

## 7. Recorded results

| Date | Result | Limit |
|---|---|---|
| 2026-11-26 | Latest unreleased tree, with the memory limits and the preload fix: 1503 passed, 0 skipped; `ruff check`, format check, `node --check` and `git diff --check` clean | Reported by the preload fix run, temporary `HOME`; one new test file was reformatted during the run and its 23 cases passed again; no real model loaded |
| 2026-11-26 | Earlier unreleased tree: 1406 passed, no skips; `ruff check`, format check, `node --check` and `git diff --check` clean | Reported by the latest fix run, temporary `HOME`; no real model loaded |
| 2026-11-26 | Earlier unreleased tree: 1313 passed, 0 failed; `ruff check`, format check and `node --check` passed | Reported by the two-model build, temporary `HOME`; no real model loaded |
| 2026-11-26 | 1059 passed, 0 failed, 0 skipped; `ruff check` and format check passed | Reported by the guard fix run (temporary `HOME`, offline); a security check repeated the suite, exit 0 |
| 2026-11-26 | 909 passed | Reported by the UI change run; test files changed afterwards, so current totals are UNVERIFIED |
| 2026-11-25 | 714 passed, 0 failed, 0 skipped; ruff and `node --check` passed | Reported by the fix run on this tree; temporary `HOME`, offline |
| 2026-11-25 | 545 passed; ruff and `node --check` passed | Earlier tree |
| 2026-11-25 | Guard end to end with tiny random-weight models | No real model loaded |
| 2026-11-24 | Black-box run of the packaged 2.0.0 app | Historical; does not cover later changes |

The suite was not re-run for this document.

## 8. Gaps

| Gap | Risk |
|---|---|
| No automated test of the native window | Layout, focus and dialogs break unnoticed |
| No test with real model weights | `mlx_lm` behaviour differences missed |
| One hardware profile used for manual checks | Other chips, memory sizes and older macOS untested |
| No load or soak test | Memory growth over long sessions unknown |
| Guard tests skip without `mlx_lm` (Ubuntu) | The real-handler part runs only where MLX is installed. The unreleased macOS job runs it, but is not yet proven on GitHub and is not a required check |
| Two models on real hardware | The re-test of the new memory limits has not run: memory peak, context, eviction, cache trim, tool calls, switch time and swap with these limits are unmeasured. All limit figures are calculated |
| Real window for the unreleased changes | Dock icon, confirm boxes, token handoff and browser mode not run live |

## 9. Sources read for this file

`pytest.ini`, `tests/conftest.py`, test file docstrings,
`.github/workflows/quality.yml`, `scripts/security_audit.sh`, test run records
of 2026-11-24 and 2026-11-25.
