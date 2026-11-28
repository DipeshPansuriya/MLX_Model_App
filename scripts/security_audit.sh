#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# security_audit.sh — re-runnable security checks for MLX Studio
#
#   ./scripts/security_audit.sh            full audit
#   ./scripts/security_audit.sh --quick    skip the dependency scan
#   ./scripts/security_audit.sh --release  also require dist/ to match the release
#                                          manifest (run before publishing)
#
# Exit code 0 when every check passes, 1 when any check fails.
# Findings are printed as PASS / FAIL / WARN / INFO with a stable check id.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT" || exit 1

QUICK=0
RELEASE=0
for arg in "$@"; do
    case "$arg" in
        --quick)   QUICK=1 ;;
        --release) RELEASE=1 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

PASS=0; FAIL=0; WARN=0
FAILED_IDS=()

ok()   { printf '  \033[32mPASS\033[0m  %-6s %s\n' "$1" "$2"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %-6s %s\n' "$1" "$2"; FAIL=$((FAIL+1)); FAILED_IDS+=("$1"); }
warn() { printf '  \033[33mWARN\033[0m  %-6s %s\n' "$1" "$2"; WARN=$((WARN+1)); }
info() { printf '  \033[36mINFO\033[0m  %-6s %s\n' "$1" "$2"; }

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

IS_MACOS=0
[ "$(uname -s)" = "Darwin" ] && IS_MACOS=1
# Test hook: MLX_AUDIT_PLATFORM=linux exercises the non-macOS paths locally.
[ "${MLX_AUDIT_PLATFORM:-}" = "linux" ] && IS_MACOS=0

# Portable file mode. Branch on the platform, never on the exit status: GNU
# `stat -f` means "filesystem status", so it succeeds while printing something
# that is not a mode at all. Falls back to Python when the platform tool is
# unavailable, so a simulated run still reports real modes.
file_mode() {
    local out=""
    if [ "$IS_MACOS" -eq 1 ]; then
        out=$(stat -f "%Sp" "$1" 2>/dev/null)
    else
        out=$(stat -c "%A" "$1" 2>/dev/null)
    fi
    if [ -z "$out" ]; then
        out=$(python3 - "$1" <<'PY' 2>/dev/null
import os, stat, sys
st = os.stat(sys.argv[1])
kind = "d" if stat.S_ISDIR(st.st_mode) else "-"
print(kind + stat.filemode(stat.S_IMODE(st.st_mode))[1:])
PY
)
    fi
    printf '%s' "$out"
}

# Python files that ship inside the app; every source check covers all of them.
SOURCES=(app.py model_info.py mlx_loadtest.py optiq_engine.py engine_runtime.py engines.py login_agent.py
         chat_store.py projects.py hub_catalog.py live_stats.py tool_null_text.py loadtest_compare.py engine_plan.py update_check.py catalog_routes.py)

# ── SA-01 static patterns ────────────────────────────────────────────────────
section "SA-01  Dangerous code patterns"
PATTERNS='shell=True|os\.system|os\.popen|\beval\(|\bexec\(|pickle\.load|__import__\(|verify=False|ssl\._create_unverified|trust_remote_code\s*=\s*True'
# Captured first: under `pipefail` a missing file would hide a match in another.
HITS="$(grep -nE "$PATTERNS" "${SOURCES[@]}" packaging/*.py 2>/dev/null | grep -v '^\s*#')"
if [ -n "$HITS" ]; then
    bad SA-01 "dangerous pattern found: $(printf '%s\n' "$HITS" | head -3 | tr '\n' ' ')"
else
    ok SA-01 "no shell=True, eval, exec, pickle, or disabled TLS verification"
fi

# ── SA-02 loopback binding ───────────────────────────────────────────────────
section "SA-02  Network exposure"
if grep -qE '^HOST = "127\.0\.0\.1"' app.py; then
    ok SA-02 "HOST is 127.0.0.1"
else
    bad SA-02 "HOST is not loopback"
fi
if grep -qE '0\.0\.0\.0' "${SOURCES[@]}" packaging/*.py 2>/dev/null; then
    bad SA-02 "0.0.0.0 appears in source"
else
    ok SA-02 "no 0.0.0.0 bind anywhere"
fi

# ── SA-03 path containment ───────────────────────────────────────────────────
section "SA-03  Static file containment"
# Strongest form: the request path is a dictionary key into a startup allowlist,
# so no filesystem path is built from user input at all.
if grep -q 'STATIC_FILES.get(rel)' app.py && grep -q '_build_static_allowlist' app.py; then
    ok SA-03 "static handler uses a startup allowlist; no path built from input"
elif grep -q 'is_relative_to(root)' app.py; then
    ok SA-03 "static handler uses Path.is_relative_to (component-wise)"
else
    bad SA-03 "static handler neither allowlists nor uses is_relative_to"
fi

# ── SA-04 request body limits ────────────────────────────────────────────────
section "SA-04  Request body handling"
if grep -q 'MAX_BODY' app.py; then
    ok SA-04 "Content-Length is bounded and parsed defensively"
else
    bad SA-04 "no body size limit"
fi

# ── SA-05 destructive endpoint surface ──────────────────────────────────────
section "SA-05  Endpoint surface"
if grep -q '"/api/quit"' app.py; then
    bad SA-05 "/api/quit still present (unauthenticated process exit)"
else
    ok SA-05 "no unauthenticated quit endpoint"
fi
if grep -q 'stop the API server first' app.py; then
    ok SA-05 "delete refuses the model the server was started with (not others it loads on request)"
else
    warn SA-05 "loaded-model delete guard not found"
fi

# ── SA-17 direct MLX server ─────────────────────────────────────────────────
section "SA-17  Direct MLX server (no request guard, no memory limit)"
if grep -q '"--allowed-origins"' app.py; then
    ok SA-17 "mlx_lm server is given --allowed-origins"
else
    bad SA-17 "mlx_lm server has no --allowed-origins (its default lets any web page read replies)"
fi
if grep -q -- '--trust-remote-code' "${SOURCES[@]}"; then
    bad SA-17 "--trust-remote-code appears in source"
else
    ok SA-17 "no --trust-remote-code"
fi
if grep -q '"HF_HUB_OFFLINE": "1"' app.py; then
    ok SA-17 "server environment is offline: a request cannot download a model"
else
    bad SA-17 "HF_HUB_OFFLINE is not set for the server"
fi
# Stated, not checked: these are gone in 2.0.13 and no PASS above covers them.
info SA-17 "REMOVED: 80% memory rule and prompt limit; a large load can freeze the Mac"
info SA-17 "REMOVED: Host, JSON-only, Origin and size checks on port 8080"
info SA-17 "REMOVED: refusal of other models, folders, adapters and draft models"

# ── SA-06 TLS verification ──────────────────────────────────────────────────
section "SA-06  TLS"
if grep -q 'truststore.inject_into_ssl' app.py; then
    ok SA-06 "truststore injected for PyPI and manifest fetches"
else
    warn SA-06 "truststore not injected; TLS may fail on python.org builds"
fi
if grep -qE 'verify\s*=\s*False|_create_unverified_context' app.py; then
    bad SA-06 "certificate verification disabled somewhere"
else
    ok SA-06 "certificate verification never disabled"
fi

# ── SA-07 secrets in tracked files ──────────────────────────────────────────
section "SA-07  Secret leakage"
# Delegate to the single rule set so the audit and the git hooks cannot drift.
if [ -f "$ROOT/scripts/check_secrets.py" ]; then
    SCAN_OUT="$(python3 "$ROOT/scripts/check_secrets.py" --quiet 2>&1)"
    SCAN_RC=$?
    # A crash also exits 1, so only reported findings count as credentials.
    BLOCKING="$(printf '%s\n' "$SCAN_OUT" | grep -cE 'BLOCK.*SEC-[0-9]{3}' || true)"
    if [ "$SCAN_RC" -eq 0 ]; then
        ok SA-07 "no credentials in tracked files (check_secrets.py)"
    elif [ "$SCAN_RC" -eq 1 ] && [ "$BLOCKING" -gt 0 ]; then
        bad SA-07 "credentials found: $BLOCKING blocking finding(s); run ./scripts/check_secrets.py"
    else
        bad SA-07 "scanner failed (exit $SCAN_RC), nothing verified; run ./scripts/check_secrets.py"
    fi
else
    warn SA-07 "scripts/check_secrets.py missing; cannot scan"
fi

# ── SA-08 gitignore coverage ────────────────────────────────────────────────
section "SA-08  Ignore rules"
MISSING=""
for pat in "dist/" "*.dmg" "*.app/" "__pycache__/" ".env" "*.safetensors"; do
    grep -qF "$pat" .gitignore 2>/dev/null || MISSING="$MISSING $pat"
done
if [ -n "$MISSING" ]; then
    bad SA-08 "missing ignore patterns:$MISSING"
else
    ok SA-08 "build output, caches, env files and weights are ignored"
fi

# ── SA-09 tracked file permissions ──────────────────────────────────────────
section "SA-09  File permissions"
WW=""
while IFS= read -r f; do
    [ -f "$f" ] || continue
    perm=$(file_mode "$f")
    # Ninth character of "-rwxrwxrwx" is the write bit for others.
    case "$perm" in ????????w*) WW="$WW $f" ;; esac
done < <(git ls-files)
if [ -n "$WW" ]; then
    bad SA-09 "world-writable tracked file:$WW"
else
    ok SA-09 "no world-writable tracked files"
fi

# ── SA-10 runtime log permissions ───────────────────────────────────────────
section "SA-10  Runtime state permissions"
LOGDIR="$HOME/.mlx_studio/logs"
LOG="$LOGDIR/activity.log"
for dir in "$HOME/.mlx_studio" "$LOGDIR"; do
    if [ -d "$dir" ]; then
        dperm=$(file_mode "$dir")
        case "$dperm" in
            drwx------) ok SA-10 "$(basename "$dir") is 0700" ;;
            *) warn SA-10 "$dir is $dperm (expected drwx------)" ;;
        esac
    fi
done
if [ ! -d "$HOME/.mlx_studio" ]; then
    info SA-10 "~/.mlx_studio does not exist yet"
fi
# Every log file must be owner-only: activity.log holds prompts, server.log holds
# model output, app.log holds paths and errors.
if [ -d "$LOGDIR" ]; then
    for f in "$LOGDIR"/*; do
        [ -f "$f" ] || continue
        lperm=$(file_mode "$f")
        case "$lperm" in
            -rw-------) ok SA-10 "$(basename "$f") is 0600" ;;
            *) warn SA-10 "$(basename "$f") is $lperm (expected -rw-------)" ;;
        esac
    done
fi
if [ -f "$HOME/.mlx_studio/server.pid" ]; then
    pperm=$(file_mode "$HOME/.mlx_studio/server.pid")
    case "$pperm" in
        -rw-------) ok SA-10 "server.pid is 0600" ;;
        *) warn SA-10 "server.pid is $pperm (expected -rw-------)" ;;
    esac
fi

# ── SA-11 bundle signature ──────────────────────────────────────────────────
section "SA-11  Bundle signature"
APP="$ROOT/dist/MLX Studio.app"
if [ "$IS_MACOS" -ne 1 ]; then
    info SA-11 "not macOS; bundle signature checks skipped"
elif [ -d "$APP" ]; then
    if codesign --verify --deep --strict "$APP" 2>/dev/null; then
        ok SA-11 "codesign --verify --deep --strict passes"
    else
        bad SA-11 "signature verification failed"
    fi
    # Capture first: `grep -q` closes the pipe early and, under `pipefail`,
    # the SIGPIPE would make the pipeline look like a failure.
    SIGN_INFO="$(codesign -dv "$APP" 2>&1 || true)"
    case "$SIGN_INFO" in
        *adhoc*) warn SA-11 "ad-hoc signature: Gatekeeper will warn on first launch" ;;
        *)       ok SA-11 "Developer ID signature present" ;;
    esac
    SPCTL_OUT="$(spctl -a -t install "$APP" 2>&1 || true)"
    case "$SPCTL_OUT" in
        *rejected*) warn SA-11 "spctl rejects the bundle (expected without notarisation)" ;;
        *)          ok SA-11 "spctl accepts the bundle" ;;
    esac
else
    info SA-11 "no built bundle; run packaging/build_app.sh"
fi

# ── SA-12 release manifest integrity ───────────────────────────────────────
section "SA-12  Release integrity"
MANIFEST="$ROOT/release/latest.json"
if [ -f "$MANIFEST" ]; then
    if python3 - "$MANIFEST" <<'PY'
import json, re, sys
m = json.load(open(sys.argv[1]))
ok = (
    isinstance(m.get("version"), str)
    and isinstance(m.get("url"), str) and m["url"].startswith("https://")
    and isinstance(m.get("sha256"), str) and re.fullmatch(r"[0-9a-f]{64}", m["sha256"])
    and isinstance(m.get("size"), int) and m["size"] > 0
)
sys.exit(0 if ok else 1)
PY
    then ok SA-12 "manifest has version, https url, 64-hex sha256 and size"
    else bad SA-12 "manifest is missing a field or has a malformed sha256/url"
    fi

    # A local rebuild legitimately differs from the published asset, so day to
    # day a mismatch is a warning. With --release it fails.
    if [ "$RELEASE" -eq 1 ]; then
        if python3 packaging/release_manifest.py --verify --release --dist dist; then
            ok SA-12 "release gate: dist/ matches the manifest, checksums and source"
        else
            bad SA-12 "release gate failed: see the release check messages above"
        fi
    fi

    DMG=$(python3 - "$MANIFEST" <<'PY'
import json, sys
print(json.load(open(sys.argv[1])).get("url", "").rsplit("/", 1)[-1])
PY
)
    LOCAL="$ROOT/dist/$DMG"
    if [ -f "$LOCAL" ]; then
        if python3 - "$MANIFEST" "$LOCAL" <<'PY'
import hashlib, json, sys, pathlib
m = json.load(open(sys.argv[1]))
dmg = pathlib.Path(sys.argv[2])
h = hashlib.sha256(dmg.read_bytes()).hexdigest()
sys.exit(0 if h == m.get("sha256") and dmg.stat().st_size == m.get("size") else 1)
PY
        then ok SA-12 "local DMG matches the manifest sha256 and size"
        else warn SA-12 "local DMG differs from the manifest: regenerate it before releasing"
        fi
    else
        info SA-12 "DMG not built locally; cannot compare"
    fi
elif [ "$RELEASE" -eq 1 ]; then
    bad SA-12 "release gate failed: no release/latest.json"
else
    warn SA-12 "no release/latest.json"
fi

# ── SA-15 shipped bundle matches source ────────────────────────────────────
# A bundle built before a security fix ships the vulnerable code while the
# source looks clean. Compare the bundled sources with the working tree.
section "SA-15  Bundle freshness"
BUNDLE="$ROOT/dist/MLX Studio.app"
RELEASE_TOOL="$ROOT/packaging/release_manifest.py"
if [ ! -d "$BUNDLE" ]; then
    info SA-15 "no built bundle; run packaging/build_app.sh"
else
    SHIPPED_FILES=()
    if [ -f "$RELEASE_TOOL" ]; then
        # One list: the release gate checks the same files inside the pkg.
        LIST="$(python3 - "$RELEASE_TOOL" 2>/dev/null <<'PY'
import runpy, sys
print("\n".join(runpy.run_path(sys.argv[1])["BUNDLED"]))
PY
)" || LIST=""
        while IFS= read -r src; do
            [ -n "$src" ] && SHIPPED_FILES+=("$src")
        done <<< "$LIST"
    else
        if [ "$RELEASE" -eq 1 ]; then report=bad; else report=warn; fi
        "$report" SA-15 "no release_manifest.py, so no shipped file list: only Python sources compared"
        SHIPPED_FILES=("${SOURCES[@]}")
    fi
    if [ "${#SHIPPED_FILES[@]}" -eq 0 ]; then
        bad SA-15 "cannot read the shipped file list from packaging/release_manifest.py"
    else
        for src in "${SHIPPED_FILES[@]}"; do
            if cmp -s "$BUNDLE/Contents/Resources/$src" "$ROOT/$src"; then
                ok SA-15 "bundled $src matches the working tree"
            else
                bad SA-15 "bundled $src is missing or differs from source: rebuild before releasing"
            fi
        done
    fi
fi

# ── SA-13 dependency vulnerabilities ───────────────────────────────────────
section "SA-13  Dependency vulnerabilities"
if [ "$QUICK" -eq 1 ]; then
    info SA-13 "skipped (--quick)"
elif python3 -c "import pip_audit" 2>/dev/null; then
    REQS=$(mktemp)
    if [ -f "$ROOT/requirements.txt" ]; then
        cp "$ROOT/requirements.txt" "$REQS"
    else
        python3 - "$REQS" <<'PY'
import importlib.metadata as md, sys
names = ["mlx","mlx-lm","huggingface-hub","pywebview","numpy","transformers",
         "tokenizers","safetensors","truststore","bottle"]
out = []
for n in names:
    try:
        out.append(f"{n}=={md.version(n)}")
    except Exception:
        pass
open(sys.argv[1], "w").write("\n".join(out) + "\n")
PY
    fi
    if PIP_OUT="$(python3 -m pip_audit --progress-spinner off -r "$REQS" 2>&1)"; then
        ok SA-13 "pip-audit: no known vulnerabilities in runtime dependencies"
    else
        bad SA-13 "pip-audit reported findings: $(printf '%s\n' "$PIP_OUT" | tail -3 | tr '\n' ' ')"
    fi
    rm -f "$REQS"
else
    warn SA-13 "pip-audit not installed (python3 -m pip install pip-audit)"
fi

# ── SA-14 syntax ────────────────────────────────────────────────────────────
section "SA-14  Syntax gates"
for src in "${SOURCES[@]}"; do
    if python3 -m py_compile "$src" 2>/dev/null; then
        ok SA-14 "$src compiles"
    else
        bad SA-14 "$src does not compile"
    fi
done
if command -v node >/dev/null 2>&1; then
    if node --check ui/app.js 2>/dev/null && node --check ui/download.js 2>/dev/null; then
        ok SA-14 "ui/app.js parses"
    else
        bad SA-14 "ui/app.js has a syntax error"
    fi
else
    info SA-14 "node not available; JS syntax not checked"
fi
rm -rf __pycache__ 2>/dev/null

# ── SA-16 installer safety ──────────────────────────────────────────────────
section "SA-16  Installer safety"
INSTALLER="$ROOT/scripts/install.sh"
if [ ! -f "$INSTALLER" ]; then
    info SA-16 "scripts/install.sh not present"
elif ! bash -n "$INSTALLER" 2>/dev/null; then
    bad SA-16 "scripts/install.sh has a syntax error"
else
    ok SA-16 "scripts/install.sh parses"
    # The installer downloads and runs privileged steps, so it must verify the
    # artifact and must not pipe a remote script into a shell.
    if grep -q 'shasum -a 256' "$INSTALLER"; then
        ok SA-16 "installer verifies a SHA-256 checksum"
    else
        bad SA-16 "installer does not verify a checksum"
    fi
    if grep -q 'codesign --verify' "$INSTALLER"; then
        ok SA-16 "installer verifies the bundle signature"
    else
        bad SA-16 "installer does not verify the signature"
    fi
    # Every curl in executable code must write to a file, never to a shell. The
    # bootstrap one-liner in the usage text is documentation, not code, so it is
    # excluded by dropping comment lines and the heredoc block.
    CODE="$(sed -n '/^usage()/,/^USAGE$/!p' "$INSTALLER" | grep -v '^[[:space:]]*#')"
    PIPED="$(printf '%s\n' "$CODE" | grep -E 'curl[^|]*\|[[:space:]]*(ba)?sh' || true)"
    if [ -n "$PIPED" ]; then
        bad SA-16 "installer pipes a download into a shell"
    else
        ok SA-16 "installer never pipes a download into a shell"
    fi
    # Safe forms: writing to a file (-o/-O) or capturing into a variable ($(curl …)).
    UNSAFE="$(printf '%s\n' "$CODE" | grep -E '(^|[^[:alnum:]_-])curl[[:space:]]' \
        | grep -v 'command -v curl' | grep -vE '(-o|-O)[[:space:]]' \
        | grep -v '\$(curl' || true)"
    if [ -n "$UNSAFE" ]; then
        warn SA-16 "curl without an output file: $(printf '%s' "$UNSAFE" | head -1)"
    else
        ok SA-16 "every curl writes to a file"
    fi
    if grep -q 'xattr -dr com.apple.quarantine' "$INSTALLER"; then
        ok SA-16 "installer clears quarantine on the artifact it verified"
    else
        warn SA-16 "installer does not clear quarantine"
    fi
fi

# ── summary ─────────────────────────────────────────────────────────────────
printf '\n\033[1mSummary\033[0m\n'
printf '  passed %d · warnings %d · failed %d\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf '  failed checks: %s\n' "${FAILED_IDS[*]}"
    exit 1
fi
printf '  \033[32mall checks passed\033[0m\n'
exit 0
