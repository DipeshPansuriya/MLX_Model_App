# Pull request

## What

<!-- One or two sentences. What changes, and why. -->

## How it was verified

<!-- Commands and their real output. Not adjectives. -->

```
ruff check .
ruff format --check .
python3 scripts/check_secrets.py
python3 scripts/check_docs.py
```

## Checklist

- [ ] Conventional commit prefix in the title (`feat:`, `fix:`, `docs:`, `ci:`, `security:`, `deps:`)
- [ ] `ruff check .` passes
- [ ] `ruff format --check .` passes
- [ ] `python3 scripts/check_secrets.py` finds nothing
- [ ] `python3 scripts/check_docs.py` resolves every link
- [ ] `python3 -m py_compile app.py mlx_guard.py` and `node --check ui/app.js` pass
- [ ] Documentation updated for any behaviour change
- [ ] No credential, token or key in the diff
- [ ] Third-party actions pinned to a commit SHA, if any were added

## Security

- [ ] This change does not widen the attack surface (new endpoint, new bind, new subprocess, new file write)
- [ ] If it does, the threat model in `docs/SECURITY.md` is updated in this PR
