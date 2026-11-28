---
type: Policy
title: "Branch and repository policy — everything in force, everything planned"
status: "Consolidated 2026-11-24; supersedes the split policy notes"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
version: "2.0"
related:
  - GITHUB-POLICY.md
  - PUSH-POLICY.md
  - SECURITY-AUDIT.md
  - REPO-HARDENING-CHECKLIST.md
  - ../SECURITY.md
  - ../CONTRIBUTING.md
---

# Branch and repository policy

> **2.0.13 (unreleased) supersedes parts of this file.** `mlx_guard.py` and the 80%
> memory rule are removed: MLX is `mlx_lm`'s own server with one model, no memory or
> prompt limit and no request checks beyond `--allowed-origins`. Text, figures and
> diagrams about the guard, memory limits or two models apply to 2.0.12 and earlier.
> [What is no longer protected](../README.md#protections-removed-in-2013).

One document for every control on this repository: what is **in force today**,
what is **planned**, what it costs, and how each was verified. Nothing here is
described as done unless a bypass was attempted and failed.

## 1. Summary

| Area | In force | Planned | Cost |
|---|---|---|---|
| Branch protection | 6 rules, 8 required checks in the script (7 before 2026-11-25; the eighth not applied), no bypass | approval gate (needs a 2nd account) | free |
| Tag protection | delete and move blocked on `refs/tags/v*` | — | free |
| Repository settings | squash/merge only, branch deleted on merge | — | free |
| Actions hardening | SHA pinning required, read-only token | — | free |
| Secret scanning | push protection + 19 local rules | 2 features plan-limited | free |
| Code scanning | **CodeQL default setup, 3 languages, weekly** | — | free for public |
| Dependency updates | Dependabot actions + pip, weekly | — | free |
| CI gates | 8 required checks listed, including automated review | — | free |
| Release integrity | signature, manifest schema, bundle freshness | — | free |

**Nothing in force is paid.** Every control runs on the GitHub free tier for a
public repository. §9 lists each one with its licence and cost.

---

## 2. Branch protection — ruleset `main-protection`

Ruleset id `28005254`, target `~DEFAULT_BRANCH`, enforcement `active`, bypass
actors `[]`, `current_user_can_bypass: never`.

| Rule | Effect |
|---|---|
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | no force-push, no history rewrite |
| `required_linear_history` | squash or merge only, no merge bubbles |
| `pull_request` | every change arrives through a PR |
| `required_status_checks` | seven checks must pass |
| `strict_required_status_checks_policy` | the branch must be current |

### 2.1 Pull request parameters

| Parameter | Value | Reason |
|---|---|---|
| `required_approving_review_count` | `0` | **Planned: 1.** See §3 |
| `dismiss_stale_reviews_on_push` | `true` | a push invalidates earlier review state |
| `require_code_owner_review` | `false` | enable with the approval gate |
| `require_last_push_approval` | `false` | enable with the approval gate |
| `required_review_thread_resolution` | `true` | every comment resolved before merge |
| `allowed_merge_methods` | `squash`, `merge` | rebase would break linear history |

### 2.2 Required status checks

| Check | Workflow | What it proves |
|---|---|---|
| `Secret scan` | `security.yml` | no credential in the tree or the history |
| `Security audit` | `security.yml` | 15 audit areas pass |
| `Lint and format` | `quality.yml` | ruff clean, formatting clean |
| `Syntax gates` | `quality.yml` | Python, JS and shell parse |
| `Documentation links` | `quality.yml` | every relative link resolves |
| `Hook installer` | `security.yml` | hooks install and are executable |
| `Automated review` | `pr-review.yml` | the report comment was produced |
| `Release consistency` | `quality.yml` | version, manifest, checksum list and tags agree |

`Release consistency` is in `scripts/set_approval_gate.sh` since 2026-11-25 but
was not applied to GitHub; the live ruleset is UNVERIFIED. See
[DEPLOYMENT.md](DEPLOYMENT.md) section 5, step 5.

Verified: a direct push to `main` is rejected with
`GH013: Repository rule violations`.

---

## 3. Approval gate — planned

**Today: 0 approvals. Every PR merged so far has `reviews=NONE`.**

That is a configuration state, not an approval. GitHub forbids approving your own
pull request:

```
POST /repos/.../pulls/8/reviews  {"event":"APPROVE"}
HTTP 422  "Review Can not approve your own pull request"
```

So a single-account repository cannot require an approval without freezing
`main`. The switch is scripted so the order is safe:

```bash
./scripts/set_approval_gate.sh --check                  # report state
./scripts/set_approval_gate.sh --reviewer <username>    # add account, then raise to 1
./scripts/set_approval_gate.sh --disable                # back to 0
```

`--reviewer` adds the collaborator **first**, confirms the invitation, and only
then sets `required_approving_review_count: 1` with
`require_last_push_approval: true`. Raising the count first would block every
merge, including urgent fixes.

| Collaborators | Approvals | `require_last_push_approval` |
|---|---|---|
| 1 | `0` | `false` |
| 2+ | `1` | `true` |

---

## 4. Tag protection — ruleset `release-tags`

Id `28006578`, target `tag`, `refs/tags/v*`, enforcement `active`, no bypass.

| Rule | Effect |
|---|---|
| `deletion` | a published release tag cannot be deleted |
| `non_fast_forward` | a tag cannot be moved |

Verified: `git push origin :refs/tags/v2.0.0` is rejected with
`GH013: Cannot delete this tag`. Tag **creation** is still open by design; gate it
with a release workflow if needed.

---

## 5. Repository settings

| Setting | Value |
|---|---|
| `allow_squash_merge` | `true` |
| `allow_merge_commit` | `true` |
| `allow_rebase_merge` | `false` |
| `delete_branch_on_merge` | `true` |
| `allow_auto_merge` | `true` |
| `allow_update_branch` | `true` |

---

## 6. Actions hardening

| Setting | Value | Effect |
|---|---|---|
| `sha_pinning_required` | `true` | a tag reference is rejected; `uses:` must be a SHA |
| `allowed_actions` | `all` | any action, but pinned |
| `default_workflow_permissions` | `read` | a workflow must ask for write |
| `can_approve_pull_request_reviews` | `false` | `GITHUB_TOKEN` cannot approve |

All 17 `uses:` across three workflows are pinned:

| Action | SHA | Tag |
|---|---|---|
| `actions/checkout` | `11d5960…` | v4 |
| `actions/setup-python` | `a26af69…` | v5 |
| `actions/setup-node` | `49933ea…` | v4 |
| `actions/github-script` | `f28e40c…` | v7 |

Dependabot keeps these current and preserves the trailing tag comment.

---

## 7. Scanning

### 7.1 Secret scanning

| Feature | State | Cost |
|---|---|---|
| Secret scanning | enabled | free for public |
| Push protection | enabled | free for public |
| Non-provider patterns | disabled | plan-limited |
| Validity checks | disabled | plan-limited |
| Open alerts | 0 | — |

The two disabled rows cannot be enabled through the API on this plan: both
`PATCH` payloads and the dedicated endpoints return 404. The local scanner covers
the gap — 19 rules at pre-commit, pre-push and in CI.

### 7.2 Code scanning — CodeQL

Enabled on 2026-11-24 through default setup:

| Property | Value |
|---|---|
| State | `configured` |
| Query suite | `default` |
| Languages | `actions`, `python`, `javascript-typescript` |
| Schedule | weekly, plus on push and PR |
| Cost | free for public repositories |

The first analysis produced **three `py/path-injection` alerts** on the static
file handler. They were real: the handler built a filesystem path from the
request. Fixed by construction — the request path is now a dictionary key into an
allowlist built by scanning `ui/` at startup, so no path is ever constructed from
input:

```python
target = STATIC_FILES.get(rel)
if target is None:
    self._send(404, b"not found", "text/plain")
    return
```

Verified after the fix:

```
/../app.py             -> 404
/../../etc/passwd      -> 404
/../ui-evil/leak.txt   -> 404
/index.html/../app.py  -> 404
/index.html            -> 200, 11081 bytes
/style.css             -> 200, 14382 bytes
/app.js                -> 200, 21235 bytes
```

### 7.3 Malware and dependency scanning

| Feature | State | Cost |
|---|---|---|
| Dependabot alerts | enabled, 0 open | free |
| Dependabot security updates | enabled | free |
| Dependabot version updates | `.github/dependabot.yml`, weekly | free |
| `pip-audit` in CI | no known vulnerabilities | free |
| Malware scanning of release assets | **not available** | — |

GitHub does not offer malware scanning of release assets on the free tier.
Release integrity is covered instead by a signature check, a manifest schema
check, a SHA-256 checksum and a bundle-freshness comparison (§8).

---

## 8. Release integrity

| Control | Mechanism |
|---|---|
| Version in one place | `APP_VERSION` in `app.py`, read by the builder and the manifest |
| Signature | `codesign --verify --deep --strict`, ad-hoc |
| Manifest schema | version, https url, 64-hex sha256, positive size |
| Checksum | SHA-256 of the pkg in `release/latest.json`; published assets listed in `release/SHA256SUMS` (starts at 2.0.9; earlier releases not backfilled) |
| Bundle freshness | `cmp` of every bundled file in `BUNDLED` (`app.py`, `mlx_guard.py`, `ui/index.html`, `ui/style.css`, `ui/app.js`) against source |
| Asset re-verification | download and compare to the manifest |

`SA-15` (bundle freshness) exists because the released `2.0.0` DMG was built
before the security fixes and shipped the vulnerable code while the source looked
clean. It now fails the audit until the bundle is rebuilt.

**Done:** the security fixes shipped in `2.0.1`
([CHANGELOG.md](../CHANGELOG.md)). The latest release is `v2.0.9` (2026-11-26).

---

## 9. Everything is free — itemised

| Control | What provides it | Licence | Cost |
|---|---|---|---|
| Rulesets, branch and tag protection | GitHub | proprietary service, free tier | free |
| Required status checks | GitHub Actions | free for public repos | free |
| `ubuntu-latest` runners | GitHub Actions | free for public repos | free |
| `macos-15` runner (`macOS build` job only) | GitHub Actions | hosted runner | billing not checked |
| Secret scanning + push protection | GitHub | free for public repos | free |
| CodeQL code scanning | GitHub + CodeQL | CodeQL CLI is free for open source | free |
| Dependabot alerts, security and version updates | GitHub | free | free |
| Private vulnerability reporting | GitHub | free | free |
| Automated review comment | this repo, `pr-review.yml` | MIT | free |
| Secret scanner, audit, hooks, gates | this repo, `scripts/` | MIT | free |
| `ruff` | Astral | MIT | free |
| `pip-audit` | Python Packaging Authority | Apache-2.0 | free |
| `pywebview`, `mlx`, `mlx-lm` | upstream | BSD-3 / MIT / MIT | free |

**Not used, because they are paid or unavailable on this plan:** Copilot code
review, GitHub Advanced Security for private repos, Windows hosted
runners, malware scanning of release assets, non-provider secret patterns and
validity checks.

---

## 10. Verification log

| Attempt | Expected | Result |
|---|---|---|
| `git push origin HEAD:main` | rejected | `GH013` rule violation |
| `git push --force origin main` | rejected | `non_fast_forward` |
| `git push origin :refs/tags/v2.0.0` | rejected | `GH013: Cannot delete this tag` |
| Author approves own PR | rejected | `HTTP 422` |
| Commit a fake `ghp_…` token | blocked | pre-commit |
| Commit with `--no-verify`, then push | blocked | pre-push |
| Unpinned `uses: action@v1` | rejected | SHA pinning required |
| `/../app.py` on the static handler | 404 | 404 |
| CodeQL `py/path-injection` ×3 | fixed | allowlist, 404 on every probe |
| Rebuild bundle, run audit | SA-15 passes | passes |

---

## 11. Planned work

| # | Item | Blocker | Owner |
|---|---|---|---|
| P-1 | Approval gate at 1 | needs a second GitHub account | Dipesh |
| P-2 | `2.0.1` release with the security fixes | done, shipped in `2.0.1` | Dipesh |
| P-3 | Tag creation gated by a release workflow | none | Dipesh |
| P-4 | `require_code_owner_review` on | after P-1 | Dipesh |
| P-5 | Non-provider secret patterns, validity checks | plan limitation | GitHub plan |
| P-6 | Malware scanning of release assets | not offered | — |

---

## 12. Sources read for this file

| Source | Used for |
|---|---|
| Rulesets `28005254`, `28006578` via the API | §2, §3, §4 |
| `GET /actions/permissions`, `/permissions/workflow` | §6 |
| `GET /code-scanning/default-setup`, `/code-scanning/alerts` | §7.2 |
| `security_and_analysis`, `/secret-scanning/alerts` | §7.1 |
| `/dependabot/alerts`, `pip-audit` run | §7.3 |
| `scripts/security_audit.sh` SA-01…SA-16 | §8, §10 |
| Bypass attempts, 2026-11-24 | §10 |
