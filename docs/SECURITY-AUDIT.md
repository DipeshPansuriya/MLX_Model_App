---
type: SecurityAudit
title: "Security audit — MLX Studio 2.0.0"
status: "Audit complete 2026-11-24; three findings remediated in the same change"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0.0"
related:
  - SECURITY.md
  - TESTING.md
  - RUNBOOK.md
  - ../scripts/security_audit.sh
---

# Security audit — MLX Studio 2.0.0

## 1. Scope and method

An audit of the shipped 2.0.0 code plus the packaging and release path, performed
on 2026-11-24 against `app.py`, `ui/`, `packaging/` and the built bundle. Method:
static pattern review, live probing of the running server, dependency scanning,
filesystem permission review, and signature/release integrity checks.

Every check is encoded in [`scripts/security_audit.sh`](../scripts/security_audit.sh)
so it can be re-run on demand and in CI.

```bash
./scripts/security_audit.sh          # full, including pip-audit
./scripts/security_audit.sh --quick  # skip the dependency scan
```

Exit code 0 means every check passed; 1 means at least one failed.

## 2. Result

| | Count |
|---|---|
| Checks executed | 21 |
| Passed | 18 |
| Warnings (accepted risk) | 2 |
| **Failed** | **0** |
| Findings remediated during the audit | **3** |

Final run:

```
Summary
  passed 18 · warnings 2 · failed 0
  all checks passed
```

## 3. Findings remediated

### F-01 — Static handler prefix bypass (Medium)

**Check:** SA-03 · **Status:** Fixed

The static file handler confined requests with a string prefix test:

```python
if not str(target).startswith(str(UI_DIR.resolve())) or not target.is_file():
```

A sibling directory whose name shares the prefix passes that test. With
`UI_DIR = /tmp/audittest/ui`, a request for `/../ui-evil/leak.txt` resolved to
`/tmp/audittest/ui-evil/leak.txt`, and
`"/tmp/audittest/ui-evil/leak.txt".startswith("/tmp/audittest/ui")` is `True`.

Reproduced live before the fix:

```
/../ui-evil/leak.txt  -> (200, b'SECRET-OUTSIDE-UI\n')
```

Impact is limited in the shipped layout — `ui/` has no sibling sharing its prefix,
so no file outside `ui/` was reachable in practice — but the control was wrong and
would have become exploitable the moment a directory such as `ui-backup/` or
`ui-old/` appeared next to it.

**Fix:** component-wise containment.

```python
root = UI_DIR.resolve()
target = (root / rel).resolve()
if not target.is_relative_to(root) or not target.is_file():
```

After the fix:

```
/../ui-evil/leak.txt  -> (404, b'not found')
/../app.py            -> (404, b'not found')
/../../etc/passwd     -> (404, b'not found')
/index.html           -> (200, 11081)
```

### F-02 — Unauthenticated `/api/quit` endpoint (Medium)

**Check:** SA-05 · **Status:** Removed

`POST /api/quit` returned `200` and then terminated the process:

```python
if path == "/api/quit":
    self._json({"ok": True})
    threading.Thread(target=lambda: (time.sleep(0.3), stop_server(), sys.exit(0)), daemon=True).start()
```

Nothing in the UI or the launchers called it. On a loopback API with no
authentication, any local process — or any web page able to issue a
`fetch` to `127.0.0.1:8765` — could terminate the app and drop a loaded model.
A shutdown primitive with no caller is pure attack surface.

**Fix:** the route is deleted; it now falls through to `404 unknown endpoint`.
Window close remains the supported shutdown path, and it also stops the child
server.

```
quit -> 404 {"error": "unknown endpoint"}
alive -> 200 {"ok": true, "app": "2.0.0"}
```

### F-03 — Unvalidated `Content-Length` and unbounded body read (Low)

**Check:** SA-04 · **Status:** Fixed

The body reader parsed the header without validation and read whatever length it
was told:

```python
length = int(self.headers.get("Content-Length") or 0)
...
return json.loads(self.rfile.read(length).decode("utf-8"))
```

A non-numeric value raised `ValueError` inside the handler, producing a traceback
per request. A very large value would have attempted a correspondingly large read.

**Fix:** defensive parse plus an explicit ceiling.

```python
MAX_BODY = 8 * 1024 * 1024  # 8 MiB: chat history is the largest legitimate body

raw = self.headers.get("Content-Length") or "0"
try:
    length = int(raw)
except (TypeError, ValueError):
    return {}
if length <= 0 or length > self.MAX_BODY:
    return {}
```

After the fix, malformed, negative and oversized lengths all produce a clean
`400 Bad Request` with no traceback.

### F-04 — Activity log world-readable (Low)

**Check:** SA-10 · **Status:** Fixed

`~/.mlx_studio` was created `0755` and `activity.log` `0644`, so any local user
could read recorded prompts. The log intentionally stores prompt text (see
[SECURITY.md](SECURITY.md) §4.6), which makes its permissions matter.

**Fix:** the directory is `0700`, the file is `0600`, both enforced on every write
so a pre-existing loose file is corrected.

```
drwx------  ~/.mlx_studio
-rw-------  ~/.mlx_studio/activity.log
```

### F-05 — Client disconnects logged as server errors (Informational)

**Check:** SA-14 · **Status:** Fixed

A client closing a connection mid-request produced a `ConnectionResetError`
traceback from `socketserver`. Not a vulnerability, but it polluted output and
made genuine errors harder to spot.

**Fix:** `handle_one_request` is overridden to treat `ConnectionResetError` and
`BrokenPipeError` as a normal close.

## 4. Checks that passed

| Check | What it asserts | Result |
|---|---|---|
| SA-01 | No `shell=True`, `os.system`, `eval`, `exec`, `pickle.load`, `__import__`, `verify=False`, or `trust_remote_code=True` | PASS |
| SA-02 | `HOST` is `127.0.0.1`; no `0.0.0.0` anywhere | PASS |
| SA-03 | Static handler uses component-wise containment | PASS |
| SA-04 | `Content-Length` bounded and parsed defensively | PASS |
| SA-05 | No unauthenticated quit; delete refuses a loaded model | PASS |
| SA-06 | `truststore` injected; verification never disabled | PASS |
| SA-07 | No credential-shaped string in any tracked file | PASS |
| SA-08 | `.gitignore` covers build output, caches, env files, weights | PASS |
| SA-09 | No world-writable tracked file | PASS |
| SA-10 | Runtime state is `0700`/`0600` | PASS |
| SA-11 | `codesign --verify --deep --strict` passes | PASS |
| SA-12 | Local DMG matches manifest `sha256` and `size` | PASS |
| SA-13 | `pip-audit`: no known vulnerabilities in runtime dependencies | PASS |
| SA-14 | `app.py` compiles; `ui/app.js` parses | PASS |

## 5. Accepted risks (warnings)

| Check | Warning | Why accepted |
|---|---|---|
| SA-11 | Ad-hoc signature; Gatekeeper warns on first launch | No Apple Developer account. Documented right-click → Open. [ADR-007](adr/ADR-007-adhoc-signing.md); PRD OD-1 |
| SA-11 | `spctl` rejects the bundle | Consequence of the above: not notarised |

Both clear the moment a Developer ID certificate and a notarisation step are
added; nothing else in the audit depends on them.

## 6. Dependency audit detail

`pip-audit` against the pinned runtime set:

```
mlx==0.32.4              mlx-lm==0.32.2           huggingface-hub==2.4.0
pywebview==6.2.1         numpy==2.5.5             transformers==5.24.0
tokenizers==0.23.3       safetensors==0.8.0       truststore==0.10.5
bottle==0.13.4
```

Result: **no known vulnerabilities**. This is a point-in-time result against the
advisory database at audit time; re-run SA-13 on every release.

## 7. Live probes performed

| Probe | Expected | Observed |
|---|---|---|
| `GET /../ui-evil/leak.txt` | 404 | 404 after fix (200 before) |
| `GET /../app.py` | 404 | 404 |
| `GET /../../etc/passwd` | 404 | 404 |
| `GET /` | 200 | 200, 11081 bytes |
| `POST` with `Content-Length: abc` | 400, no traceback | 400 |
| `POST` with `Content-Length: 999999999999` | 400 | 400 |
| `POST` with `Content-Length: -5` | 400 | 400 |
| `POST /api/quit` | 404 | 404 |
| Listener address | `127.0.0.1` only | `127.0.0.1:8765` |
| CRLF in request headers | no injected header | none |
| Client disconnect | no traceback | no traceback after fix |

## 8. What this audit does not cover

| Not covered | Why | Would need |
|---|---|---|
| Remote attack surface | Nothing binds beyond loopback | A deployment change first |
| Model weight provenance | Weights are downloaded data | Hash pinning per model |
| Tokenizer code execution | `trust_remote_code` is off | Enabling it would need review |
| Notarisation and hardened runtime | No Developer ID | PRD OD-1 |
| Fuzzing of the HTTP parser | Time-boxed audit | A fuzz harness |
| Multi-user macOS scenarios | Single-user product | A threat model for shared Macs |
| Supply chain of `mlx` itself | Third-party binary wheels | Reproducible builds |

## 9. Re-audit procedure

```bash
# 1. full audit, including dependency scan
./scripts/security_audit.sh

# 2. rebuild and re-verify the bundle
cd packaging && ./build_app.sh && cd ..
./scripts/security_audit.sh --quick

# 3. refresh the manifest checksum for a release
cd packaging && ./release_manifest.py --dmg "../dist/MLX.Studio-<v>.dmg" \
  --url "https://github.com/…/releases/download/v<v>/MLX.Studio-<v>.dmg" \
  --notes "…" --out ../release/latest.json
```

Run the full audit before every tag; see [RUNBOOK.md](RUNBOOK.md) §7.

## 10. Sources read for this file

| Source | Used for |
|---|---|
| `app.py` at the audited revision | F-01 to F-05, all SA checks |
| `scripts/security_audit.sh` | The check definitions and final run |
| `dist/MLX Studio.app`, `dist/MLX.Studio-2.0.0.dmg` | SA-11, SA-12 |
| `release/latest.json` | SA-12 checksum comparison |
| `pip-audit` run, 2026-11-24 | §6 |
| Live probe session, 2026-11-24 | §7 |
