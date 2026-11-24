#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# install_hooks.sh — activate the repository's git hooks.
#
#   ./scripts/install_hooks.sh            install (sets core.hooksPath)
#   ./scripts/install_hooks.sh --check    report status without changing anything
#   ./scripts/install_hooks.sh --remove   restore git's default hooks path
#
# Using core.hooksPath means the hooks are version-controlled in .githooks/ and
# every clone gets the same rules after running this once.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT" || exit 1

HOOKS_DIR=".githooks"
MODE="${1:-install}"

case "$MODE" in
    --check)
        current="$(git config --get core.hooksPath || true)"
        if [ "$current" = "$HOOKS_DIR" ]; then
            echo "hooks: active (core.hooksPath=$current)"
        else
            echo "hooks: NOT active (core.hooksPath=${current:-<unset>})"
            echo "       run ./scripts/install_hooks.sh to enable"
            exit 1
        fi
        for h in pre-commit pre-push; do
            if [ -x "$HOOKS_DIR/$h" ]; then
                echo "  $h: executable"
            else
                echo "  $h: MISSING or not executable"
                exit 1
            fi
        done
        exit 0
        ;;
    --remove)
        git config --unset core.hooksPath 2>/dev/null || true
        echo "hooks: removed (core.hooksPath unset)"
        exit 0
        ;;
    install) ;;
    *)
        echo "usage: $0 [--check|--remove]" >&2
        exit 2
        ;;
esac

chmod +x "$HOOKS_DIR/pre-commit" "$HOOKS_DIR/pre-push" scripts/check_secrets.py
git config core.hooksPath "$HOOKS_DIR"

echo "hooks: installed"
echo "  core.hooksPath = $(git config --get core.hooksPath)"
echo "  pre-commit     → scripts/check_secrets.py --staged, then syntax gates"
echo "  pre-push       → scripts/check_secrets.py --range and HEAD tree"
echo
echo "verify with: ./scripts/install_hooks.sh --check"
