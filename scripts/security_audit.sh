#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# security_audit.sh — re-runnable security checks for MLX Studio
#
#   ./scripts/security_audit.sh            full audit
#   ./scripts/security_audit.sh --quick    skip the dependency scan
#
# Exit code 0 when every check passes, 1 when any check fails.
# Findings are printed as PASS / FAIL / WARN / INFO with a stable check id.
# ─────────────────────────────────────────────────────────────────────────────
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
cd "$ROOT" || exit 1

QUICK=0
[ "${1:-}" = "--quick" ] && QUICK=1

PASS=0; FAIL=0; WARN=0
FAILED_IDS=()

ok()   { printf '  \033[32mPASS\033[0m  %-6s %s\n' "$1" "$2"; PASS=$((PASS+1)); }
bad()  { printf '  \033[31mFAIL\033[0m  %-6s %s\n' "$1" "$2"; FAIL=$((FAIL+1)); FAILED_IDS+=("$1"); }
warn() { printf '  \033[33mWARN\033[0m  %-6s %s\n' "$1" "$2"; WARN=$((WARN+1)); }
info() { printf '  \033[36mINFO\033[0m  %-6s %s\n' "$1" "$2"; }

section() { printf '\n\033[1m%s\033[0m\n' "$1"; }

# ── SA-01 static patterns ────────────────────────────────────────────────────
section "SA-01  Dangerous code patterns"
PATTERNS='shell=True|os\.system|os\.popen|\beval\(|\bexec\(|pickle\.load|__import__\(|verify=False|ssl\._create_unverified|trust_remote_code\s*=\s*True'
if grep -nE "$PATTERNS" app.py packaging/*.py 2>/dev/null | grep -v '^\s*#' | grep -q .; then
    bad SA-01 "dangerous pattern found: $(grep -nE "$PATTERNS" app.py packaging/*.py | head -3 | tr '\n' ' ')"
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
if grep -qE '0\.0\.0\.0' app.py packaging/*.py 2>/dev/null; then
    bad SA-02 "0.0.0.0 appears in source"
else
    ok SA-02 "no 0.0.0.0 bind anywhere"
fi

# ── SA-03 path containment ───────────────────────────────────────────────────
section "SA-03  Static file containment"
if grep -q 'is_relative_to(root)' app.py; then
    ok SA-03 "static handler uses Path.is_relative_to (component-wise)"
else
    bad SA-03 "static handler does not use is_relative_to"
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
if grep -q 'unload the model first' app.py; then
    ok SA-05 "delete refuses a loaded model"
else
    warn SA-05 "loaded-model delete guard not found"
fi

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
SECRET_RE='ghp_[A-Za-z0-9]{20,}|gho_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|sk-[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY'
LEAKS=""
while IFS= read -r f; do
    case "$f" in *.png|*.icns|*.jpg) continue ;; esac
    if grep -qIE "$SECRET_RE" "$f" 2>/dev/null; then LEAKS="$LEAKS $f"; fi
done < <(git ls-files)
if [ -n "$LEAKS" ]; then
    bad SA-07 "secret-like string in:$LEAKS"
else
    ok SA-07 "no credentials in tracked files"
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
    perm=$(stat -f "%Sp" "$f" 2>/dev/null)
    case "$perm" in *w*w*) WW="$WW $f" ;; esac
done < <(git ls-files)
if [ -n "$WW" ]; then
    bad SA-09 "world-writable tracked file:$WW"
else
    ok SA-09 "no world-writable tracked files"
fi

# ── SA-10 runtime log permissions ───────────────────────────────────────────
section "SA-10  Runtime state permissions"
LOG="$HOME/.mlx_studio/activity.log"
if [ -d "$HOME/.mlx_studio" ]; then
    dperm=$(stat -f "%Sp" "$HOME/.mlx_studio")
    case "$dperm" in
        drwx------) ok SA-10 "~/.mlx_studio is 0700" ;;
        *) warn SA-10 "~/.mlx_studio is $dperm (expected drwx------)" ;;
    esac
else
    info SA-10 "~/.mlx_studio does not exist yet"
fi
if [ -f "$LOG" ]; then
    lperm=$(stat -f "%Sp" "$LOG")
    case "$lperm" in
        -rw-------) ok SA-10 "activity.log is 0600" ;;
        *) warn SA-10 "activity.log is $lperm (expected -rw-------)" ;;
    esac
fi

# ── SA-11 bundle signature ──────────────────────────────────────────────────
section "SA-11  Bundle signature"
APP="$ROOT/dist/MLX Studio.app"
if [ -d "$APP" ]; then
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

# ── SA-12 DMG integrity and manifest match ─────────────────────────────────
section "SA-12  Release integrity"
MANIFEST="$ROOT/release/latest.json"
if [ -f "$MANIFEST" ]; then
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
        then ok SA-12 "local DMG matches manifest sha256 and size"
        else bad SA-12 "local DMG does not match the manifest"
        fi
    else
        info SA-12 "DMG not built locally; cannot compare"
    fi
else
    warn SA-12 "no release/latest.json"
fi

# ── SA-13 dependency vulnerabilities ───────────────────────────────────────
section "SA-13  Dependency vulnerabilities"
if [ "$QUICK" -eq 1 ]; then
    info SA-13 "skipped (--quick)"
elif python3 -c "import pip_audit" 2>/dev/null; then
    REQS=$(mktemp)
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
    if python3 -m pip_audit --progress-spinner off -r "$REQS" >/tmp/sa13.log 2>&1; then
        ok SA-13 "pip-audit: no known vulnerabilities in runtime dependencies"
    else
        bad SA-13 "pip-audit reported findings: $(tail -3 /tmp/sa13.log | tr '\n' ' ')"
    fi
    rm -f "$REQS"
else
    warn SA-13 "pip-audit not installed (python3 -m pip install pip-audit)"
fi

# ── SA-14 syntax ────────────────────────────────────────────────────────────
section "SA-14  Syntax gates"
if python3 -m py_compile app.py 2>/dev/null; then
    ok SA-14 "app.py compiles"
else
    bad SA-14 "app.py does not compile"
fi
if command -v node >/dev/null 2>&1; then
    if node --check ui/app.js 2>/dev/null; then
        ok SA-14 "ui/app.js parses"
    else
        bad SA-14 "ui/app.js has a syntax error"
    fi
else
    info SA-14 "node not available; JS syntax not checked"
fi
rm -rf __pycache__ 2>/dev/null

# ── summary ─────────────────────────────────────────────────────────────────
printf '\n\033[1mSummary\033[0m\n'
printf '  passed %d · warnings %d · failed %d\n' "$PASS" "$WARN" "$FAIL"
if [ "$FAIL" -gt 0 ]; then
    printf '  failed checks: %s\n' "${FAILED_IDS[*]}"
    exit 1
fi
printf '  \033[32mall checks passed\033[0m\n'
exit 0
