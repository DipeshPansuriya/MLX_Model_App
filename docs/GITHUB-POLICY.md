---
type: Policy
title: "GitHub policy — branch protection, review and required checks"
status: "Enforced by ruleset main-protection since 2026-11-24"
date: 2026-11-24
author: "Dipesh Pansuriya"
project: mlx-studio
related:
  - PUSH-POLICY.md
  - SECURITY.md
  - ../.github/workflows/security.yml
  - ../.github/workflows/quality.yml
  - ../.github/workflows/pr-review.yml
  - ../.github/CODEOWNERS
---

# GitHub policy — branch protection, review and required checks

## 1. Rule

`main` is protected. Nobody — including the repository owner — can commit to it
directly, force-push it, delete it, or merge into it without a pull request and
seven green status checks.

Enforcement is a **repository ruleset**, not classic branch protection, because
rulesets support required status checks and bypass control in one object and are
visible at `/rules/`.

| Property | Value |
|---|---|
| Ruleset | `main-protection` (id `28005254`) |
| Target | branch, `~DEFAULT_BRANCH` |
| Enforcement | active |
| Bypass actors | none |
| `current_user_can_bypass` | `never` |

## 2. Rules in force

| Rule | Effect |
|---|---|
| `deletion` | `main` cannot be deleted |
| `non_fast_forward` | no force-push, no history rewrite |
| `required_linear_history` | squash or merge commits only; no merge bubbles |
| `pull_request` | every change arrives through a PR |
| `required_status_checks` | seven checks must pass, and the branch must be up to date |

### 2.1 Pull request parameters

| Parameter | Value | Why |
|---|---|---|
| `required_approving_review_count` | `0` | Single maintainer; GitHub forbids self-approval, so a non-zero count would deadlock every PR. See §5 |
| `dismiss_stale_reviews_on_push` | `true` | A new push invalidates earlier review state |
| `require_code_owner_review` | `false` | Would also deadlock; `.github/CODEOWNERS` is in place to switch it on later |
| `require_last_push_approval` | `false` | Same reason |
| `required_review_thread_resolution` | `true` | Every review comment must be resolved before merge |
| `allowed_merge_methods` | `squash`, `merge` | Rebase merge would produce non-linear history |

### 2.2 Required status checks

| Check | Workflow | Job |
|---|---|---|
| `Secret scan` | `security.yml` | `secrets` |
| `Security audit` | `security.yml` | `audit` |
| `Lint and format` | `quality.yml` | `lint` |
| `Syntax gates` | `quality.yml` | `syntax` |
| `Documentation links` | `quality.yml` | `docs` |
| `Hook installer` | `security.yml` | `hooks` |
| `Automated review` | `pr-review.yml` | `review` |

`strict_required_status_checks_policy` is `true`, so a PR must be rebased or
merged with `main` before it can land — a green run on a stale base does not count.

## 3. Workflows

| Workflow | Trigger | Jobs | Cost |
|---|---|---|---|
| `security.yml` | push to `main`, PR, dispatch | secret scan (tree + history), audit, hook installer | free |
| `quality.yml` | push to `main`, PR, dispatch | lint and format, syntax gates, docs links | free |
| `pr-review.yml` | PR opened, synchronize, reopened, ready_for_review | one report job | free |

All jobs run on `ubuntu-latest`. `macos-14` was tried and never starts on this
repository — jobs are cancelled with zero steps and no runner assigned, while an
`ubuntu-latest` job in the same workflow completes. The audit script is portable
so it runs on Linux; `codesign` and `spctl` checks report `INFO` off macOS.

### 3.1 Automated review

`pr-review.yml` collects ruff, formatting, secret scan, documentation link and
syntax results plus a changed-file table, then posts them as a single sticky
comment identified by `<!-- mlx-studio-auto-review -->`. A re-run updates that
comment instead of adding another.

It is a **report, not an approval**. The job never calls the review API, never
approves, and never blocks on its own — merge is gated by the required checks.

## 4. Cost

Everything here is on the free tier.

| Item | Cost |
|---|---|
| GitHub Actions on `ubuntu-latest` for a public repository | free, unlimited |
| Rulesets and branch protection on a public repository | free |
| Secret scanning and push protection | free for public repositories |
| Dependabot alerts and security updates | free |
| `pr-review.yml` | free — a comment, not a paid review product |
| CodeQL / Copilot code review | **not used** — paid or unavailable on this plan |

The workflows use `actions/checkout`, `actions/setup-python`,
`actions/setup-node` and `actions/github-script`, all free for public repos. No
job uses a macOS or Windows runner, which would consume paid minutes.

## 5. Why zero approvals

GitHub does not allow a pull request author to approve their own PR. With one
maintainer, `required_approving_review_count: 1` would make every PR
unmergeable — including this one. The policy therefore requires the pull request
and all checks, but not an approval.

The moment a second collaborator exists:

1. Set `required_approving_review_count` to `1`.
2. Set `require_code_owner_review` to `true`.
3. Keep `require_last_push_approval` as needed.

`.github/CODEOWNERS` already declares ownership per path, so step 2 needs no file
change.

## 6. What a contributor experiences

```
fork or branch
   ↓
commit            pre-commit: secret scan, Python and JS syntax
   ↓
push              pre-push: range scan + HEAD tree scan
   ↓
open PR           pr-review.yml posts the report comment
                  security.yml + quality.yml run seven checks
   ↓
fix and push      checks re-run; the review comment is updated in place
   ↓
merge             blocked until all seven are green and the branch is current
   ↓
squash merge      linear history preserved
```

A direct push to `main` is rejected by the server:

```
remote: error: GH013: Repository rule violations found for refs/heads/main.
remote: - Changes must be made through a pull request.
remote: - 6 of 6 required status checks are expected.
```

## 7. Verification performed

| Test | Expected | Result |
|---|---|---|
| Direct push to `main` | rejected | `GH013` rule violation |
| Ruleset present and active | yes | `main-protection`, enforcement `active` |
| Bypass actors | none | `[]`, `current_user_can_bypass: never` |
| PR opened | created | [#1](https://github.com/DipeshPansuriya/MLX_Model_App/pull/1) |
| Checks on PR head | all green | 7/7 success |
| Automated review comment | posted once | comment `7139473031` |
| Merge via PR | succeeds | squash merge `ada454a` |
| Local `main` after merge | fast-forward, clean | `ada454a`, tree clean |

## 8. Limits

| Limit | Consequence |
|---|---|
| Zero required approvals | No human sign-off is enforced; the checks are the gate |
| `Automated review` is required but advisory | It reports; it does not judge correctness |
| Rulesets apply to branches, not tags | A tag can be pushed directly; releases are still verified by checksum |
| No signed commits required | Commit authorship is not cryptographically proven |
| Single maintainer | Bus factor of one; CODEOWNERS documents the intended split |

## 9. Sources read for this file

| Source | Used for |
|---|---|
| Ruleset `28005254` via the GitHub API | §2 rules and parameters |
| `.github/workflows/security.yml`, `quality.yml`, `pr-review.yml` | §3 job names and triggers |
| `.github/CODEOWNERS` | §5 ownership |
| PR #1 run, 2026-11-24 | §7 results |
