# Contributing

Thanks for looking at MLX Studio. This repository is gated: `main` is protected,
every change arrives through a pull request, and its status checks must pass.

## Quick start

```bash
git clone https://github.com/DipeshPansuriya/MLX_Model_App.git
cd MLX_Model_App
./scripts/install_hooks.sh      # activate pre-commit and pre-push
./run.sh                        # launch the app
```

## Before you open a pull request

```bash
ruff check .                    # lint
ruff format --check .           # formatting
python3 scripts/check_secrets.py
python3 scripts/check_docs.py
python3 -m py_compile app.py mlx_guard.py mlx_loadtest.py
node --check ui/app.js
./scripts/security_audit.sh --quick
```

All of these also run in CI. A pull request that fails any of them cannot merge.

## Rules

| Rule | Detail |
|---|---|
| No direct commits to `main` | The ruleset rejects them |
| No credentials | Enforced at commit, push and CI — see [docs/PUSH-POLICY.md](docs/PUSH-POLICY.md) |
| Conventional commit prefixes | `feat:`, `fix:`, `docs:`, `ci:`, `security:`, `deps:`, `test:`, `refactor:` |
| Squash or merge only | Rebase merge is disabled |
| Linear history | No merge bubbles |
| Resolve review threads | Every comment must be resolved before merge |
| Docs ship with code | A behaviour change updates the affected document in the same PR |

## What a pull request needs

1. A title in the conventional-commit form.
2. A body stating **what** changed and **how it was verified** — commands and
   their output, not adjectives.
3. Green checks: `Secret scan`, `Security audit`, `Lint and format`,
   `Syntax gates`, `Documentation links`, `Hook installer`, `Automated review`,
   `Release consistency` (listed in `scripts/set_approval_gate.sh`; not yet applied
   to the live ruleset on 2026-11-25).
4. No unresolved review threads.

The automated review comment is a report, not an approval. It lists lint,
formatting, secret, link and syntax results plus the changed files.

## Code standards

| Area | Standard |
|---|---|
| Python | `ruff check` and `ruff format`, config in `ruff.toml`, target 3.10 |
| JavaScript | No build step; `node --check ui/app.js` must pass |
| Shell | `bash -n` on every script; `set -uo pipefail` |
| Docs | Front matter on `docs/**/*.md`; relative links must resolve |
| Logging | No secrets, no environment dumps, truncate error detail |
| Errors | Every failure becomes a JSON `{error}` with a status code, or an SSE `error` event |

## Adding a dependency

Third-party GitHub Actions must be pinned to a commit SHA — SHA pinning is
required at the repository level. Add the tag as a trailing comment:

```yaml
- uses: actions/checkout@11d5960a326750d5838078e36cf38b85af677262  # v4
```

Python dependencies are not vendored; the app installs `mlx` and `mlx-lm` from
the Setup tab. Report a vulnerability in those upstream.

## Reporting a security issue

Do not open a public issue. Use
[private reporting](https://github.com/DipeshPansuriya/MLX_Model_App/security/advisories/new)
— see [SECURITY.md](SECURITY.md).

## Documentation map

| Document | Contents |
|---|---|
| [docs/README.md](docs/README.md) | Index and conventions |
| [docs/PRD.md](docs/PRD.md) | Requirements and acceptance criteria |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | Processes, state, request paths |
| [docs/DESIGN.md](docs/DESIGN.md) | Interface, tokens, states |
| [docs/TESTING.md](docs/TESTING.md) | Verification and gaps |
| [docs/GITHUB-POLICY.md](docs/GITHUB-POLICY.md) | Branch, tag and Actions policy |
