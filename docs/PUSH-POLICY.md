---
type: Policy
title: "Push policy — secrets and credentials"
status: "Enforced by git hooks and CI since 2026-11-24"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
related:
  - ../scripts/check_secrets.py
  - ../scripts/install_hooks.sh
  - SECURITY.md
  - SECURITY-AUDIT.md
---

# Push policy — secrets and credentials

## 1. Rule

**No credential may enter the repository.** Not in a commit, not in history, not
in a DMG, not in a manifest, not in a screenshot, not in a test fixture.

A credential means anything that grants access: API keys, access tokens, private
keys, passwords, connection strings with inline passwords, session tokens, or a
`.env` file carrying any of those.

This is enforced at three points, so a single mistake cannot reach the remote:

| Point | Trigger | Scope | Bypass |
|---|---|---|---|
| **pre-commit hook** | `git commit` | staged content | `git commit --no-verify` |
| **pre-push hook** | `git push` | commits leaving the machine + HEAD tree | `git push --no-verify` |
| **CI workflow** | push to `main`, PR | working tree + full history | none |

A `--no-verify` bypass on commit is still caught by the pre-push hook, and a
bypass on both is still caught by CI. The bypasses exist for genuine emergencies;
using one is a decision to be recorded in the commit message.

## 2. Activation

Hooks are version-controlled in `.githooks/` and activated per clone:

```bash
./scripts/install_hooks.sh          # set core.hooksPath=.githooks
./scripts/install_hooks.sh --check  # verify
./scripts/install_hooks.sh --remove # restore git defaults
```

Because `core.hooksPath` is a local git config value, it is not cloned. Every
clone runs the installer once. CI runs it too, so the scripts are proven to work
on a clean checkout.

## 3. Rules

19 rules across four groups. `--list-rules` prints the live table.

### 3.1 Platform tokens (block)

| ID | Detects |
|---|---|
| SEC-001 | GitHub personal access token (classic), `ghp_…` |
| SEC-002 | GitHub OAuth token, `gho_…` |
| SEC-003 | GitHub app token, `ghu_…` / `ghs_…` |
| SEC-004 | GitHub fine-grained token, `github_pat_…` |
| SEC-005 | OpenAI-style key, `sk-…` / `sk-proj-…` |
| SEC-006 | Anthropic key, `sk-ant-…` |
| SEC-007 | AWS access key id, `AKIA…` |
| SEC-008 | AWS secret access key assignment |
| SEC-009 | Google API key, `AIza…` |
| SEC-010 | Slack token, `xox[baprs]-…` |
| SEC-011 | Stripe live key, `sk_live_…` |
| SEC-012 | Hugging Face token, `hf_…` |

### 3.2 Key material and tokens (block)

| ID | Detects |
|---|---|
| SEC-013 | `-----BEGIN … PRIVATE KEY-----` blocks |
| SEC-014 | JSON Web Tokens, `eyJ…eyJ…` |
| SEC-023 | Connection strings with an inline password |

### 3.3 Generic assignments (block)

| ID | Detects |
|---|---|
| SEC-020 | `password = "…"` / `passwd:` / `pwd=` with 8+ chars |
| SEC-021 | `api_key`, `apikey`, `secret_key`, `access_token`, `auth_token` with 12+ chars |

### 3.4 Lower-confidence patterns (warn)

| ID | Detects | Why not blocking |
|---|---|---|
| SEC-022 | `Bearer <token>` literal | Common in documentation examples |
| SEC-024 | `.env`-shaped `NAME=<random>` | Requires mixed case plus a digit to reduce noise; still heuristic |

Warnings are printed and do not fail the run. They exist so a reviewer looks.

## 4. Suppressing a false positive

Append `secret-scan: allow` to the line:

```python
PLACEHOLDER_TOKEN = "ghp_exampleexampleexampleexample12"  # secret-scan: allow
```

The marker suppresses every rule on that line. Use it only for values that are
provably not credentials — documentation placeholders, test fixtures with obvious
fake values, or a regex inside the scanner itself.

Lines are also auto-allowed when they contain an obvious placeholder marker:
`example`, `placeholder`, `dummy`, `redacted`, `your_token`, `<...>`, `${VAR}`,
`os.environ`, `getenv`, `process.env`, `xxxx`, `...`, `fake`, `sample`,
`test_key`, or a `SEC-NNN` reference.

## 5. What is never scanned

| Skipped | Reason |
|---|---|
| `.git/`, `dist/`, `node_modules/`, `__pycache__/`, `.venv/`, `build/` | Not source; build output is git-ignored anyway |
| Binary extensions (`.png`, `.icns`, `.dmg`, `.so`, `.woff2`, …) | No text secrets; would produce noise |
| Files over 2 MiB | Secrets live in small text files |

Model weights are excluded by `.gitignore` (`*.safetensors`, `*.gguf`, `*.bin`),
so they never reach a commit in the first place.

## 6. What to do when a secret is found

| Situation | Action |
|---|---|
| Found before commit | Remove it, re-stage, commit |
| Found by pre-push, not yet pushed | `git rebase -i` or `git commit --amend` to remove it, then push |
| Already pushed | **Rotate the credential first.** Then rewrite history (`git filter-repo`), force-push, and tell anyone who pulled |
| Found in a DMG or manifest | Rebuild the artifact, regenerate the checksum, republish the release |
| Found in a screenshot | Recapture, replace the file, amend the commit |

Rotation comes first because a pushed secret must be assumed compromised the
moment it is visible, regardless of how quickly it is removed.

## 7. CI enforcement

`.github/workflows/security.yml` runs three jobs on every push to `main`, every
pull request, and on demand:

| Job | Command | Fails when |
|---|---|---|
| `secrets` | `check_secrets.py` then `--all-history` | any blocking finding |
| `audit` | `security_audit.sh` | any SA check fails |
| `hooks` | `install_hooks.sh` then `--check` | hooks are missing or not executable |

`fetch-depth: 0` is set so the history scan sees every commit.

## 8. Verification performed

| Test | Expected | Result |
|---|---|---|
| Fake GitHub token staged | commit blocked, exit 1 | blocked |
| Fake OpenAI key staged | commit blocked | blocked |
| Fake AWS key pair staged | commit blocked (SEC-007, SEC-008) | blocked |
| Fake Hugging Face token staged | commit blocked | blocked |
| `password = "…"` staged | commit blocked | blocked |
| `api_key = "…"` staged | commit blocked | blocked |
| `secret-scan: allow` on a fake key | not flagged | not flagged |
| Clean tree staged | commit succeeds, exit 0 | succeeded |
| Clean history pushed | push succeeds | succeeded |
| Key committed with `--no-verify`, then pushed | push blocked | blocked |
| `--all-history` after a bypassed commit | finds it, exit 1 | found |
| Working tree on this repo | clean | 0 blocking, 0 warnings |

## 9. Limits

| Limit | Consequence |
|---|---|
| Pattern matching, not entropy analysis | A high-entropy secret with no recognisable shape can pass |
| No live verification against providers | A revoked key is still reported; a valid key with an unusual prefix may not be |
| Hooks are local config | A clone that skips `install_hooks.sh` has no local protection; CI still catches it |
| `--no-verify` exists | Deliberate bypass is possible; CI is the backstop |
| History scan is O(blobs) | Slow on a very large repository; acceptable here |
| Screenshots are not OCR-scanned | A key visible in an image is not detected |

## 10. Sources read for this file

| Source | Used for |
|---|---|
| `scripts/check_secrets.py` | §3 rule table, §4 allowlist, §5 skips |
| `.githooks/pre-commit`, `.githooks/pre-push` | §1 enforcement points |
| `scripts/install_hooks.sh` | §2 activation |
| `.github/workflows/security.yml` | §7 CI jobs |
| Hook test session, 2026-11-24 | §8 results |
