#!/usr/bin/env python3
"""
check_secrets.py — block credentials from entering the repository.

Runs with the Python standard library only, so it works on a fresh macOS install
with no pip step. Used by three callers:

  * .githooks/pre-commit   staged content only
  * .githooks/pre-push     commits about to leave the machine
  * .github/workflows/security.yml   full tree, every push and PR

Exit codes
  0  clean
  1  findings (blocks the commit or push)
  2  usage error

Usage
  ./scripts/check_secrets.py                 scan the working tree (tracked files)
  ./scripts/check_secrets.py --staged        scan staged content only
  ./scripts/check_secrets.py --range A..B    scan every blob a commit range adds
  ./scripts/check_secrets.py --range SHA --remote origin
                                             scan every commit of SHA that the
                                             remote does not have yet
  ./scripts/check_secrets.py --all-history   scan every blob in history
  ./scripts/check_secrets.py --list-rules    print the rule table

Findings report the rule and path:line only. The matched line is never printed,
because this output ends up in CI logs and pull request comments.
"""

from __future__ import annotations

import argparse
import codecs
import re
import subprocess
import sys
from pathlib import Path

# ── rules ────────────────────────────────────────────────────────────────────
# (id, description, pattern, severity)
# severity "block" fails the run; "warn" is reported but does not fail.
RULES: list[tuple[str, str, str, str]] = [
    # Cloud and platform tokens with recognisable prefixes
    ("SEC-001", "GitHub personal access token (classic)", r"ghp_[A-Za-z0-9]{36}", "block"),
    ("SEC-002", "GitHub OAuth token", r"gho_[A-Za-z0-9]{36}", "block"),
    ("SEC-003", "GitHub app token", r"(?:ghu|ghs)_[A-Za-z0-9]{36}", "block"),
    ("SEC-004", "GitHub fine-grained token", r"github_pat_[A-Za-z0-9_]{22,}", "block"),
    ("SEC-005", "OpenAI-style key", r"sk-(?:proj-|ant-)?[A-Za-z0-9_\-]{20,}", "block"),
    ("SEC-006", "Anthropic key", r"sk-ant-[A-Za-z0-9\-_]{20,}", "block"),
    ("SEC-007", "AWS access key id", r"AKIA[0-9A-Z]{16}", "block"),
    (
        "SEC-008",
        "AWS secret access key assignment",
        r"aws_secret_access_key\s*[=:]\s*['\"]?[A-Za-z0-9/+=]{40}",
        "block",
    ),
    ("SEC-009", "Google API key", r"AIza[0-9A-Za-z\-_]{35}", "block"),
    ("SEC-010", "Slack token", r"xox[baprs]-[0-9A-Za-z\-]{10,}", "block"),
    ("SEC-011", "Stripe live key", r"sk_live_[0-9A-Za-z]{24,}", "block"),
    ("SEC-012", "Hugging Face token", r"hf_[A-Za-z0-9]{30,}", "block"),
    (
        "SEC-013",
        "Private key block",
        r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
        "block",
    ),
    (
        "SEC-014",
        "JSON Web Token",
        r"eyJ[A-Za-z0-9_\-]{10,}\.eyJ[A-Za-z0-9_\-]{10,}\.[A-Za-z0-9_\-]{10,}",
        "block",
    ),
    # Generic assignments — higher false-positive rate, so scoped and warned
    (
        "SEC-020",
        "Hardcoded password assignment",
        r"(?i)\b(?:password|passwd|pwd)\s*[=:]\s*['\"](?P<secret>[^'\"\s]{8,})['\"]",
        "block",
    ),
    (
        "SEC-021",
        "Hardcoded secret assignment",
        (
            r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token)"
            r"\s*[=:]\s*['\"](?P<secret>[^'\"\s]{12,})['\"]"
        ),
        "block",
    ),
    (
        "SEC-022",
        "Bearer token literal",
        r"(?i)bearer\s+(?P<secret>[A-Za-z0-9\-._~+/]{20,}=*)",
        "warn",
    ),
    (
        "SEC-023",
        "Connection string with inline password",
        (
            r"(?i)(?:postgres|postgresql|mysql|mongodb(?:\+srv)?|redis|amqp)://"
            r"[^:\s]+:(?P<secret>[^@\s]+)@"
        ),
        "block",
    ),
    # A long random-looking value assigned to an uppercase name: the .env shape.
    # Requires mixed case plus a digit so shell variables and paths do not match.
    (
        "SEC-024",
        "Env-style secret assignment",
        (
            r"(?m)^[A-Z][A-Z0-9_]{2,}\s*=\s*[\"']?(?P<secret>(?=[A-Za-z0-9_\-]*[a-z])"
            r"(?=[A-Za-z0-9_\-]*[A-Z])(?=[A-Za-z0-9_\-]*\d)[A-Za-z0-9_\-]{20,})[\"']?\s*$"
        ),
        "warn",
    ),
]

COMPILED = [(rid, desc, re.compile(pat), sev) for rid, desc, pat, sev in RULES]

# ── allowlist ────────────────────────────────────────────────────────────────
# Paths never scanned: binaries, build output, vendored assets.
SKIP_PATH_RE = re.compile(
    r"(?:^|/)(?:\.git|dist|node_modules|__pycache__|\.venv|venv|build)(?:/|$)"
    r"|\.(?:png|jpg|jpeg|gif|webp|ico|icns|pdf|zip|dmg|so|dylib|pyc|woff2?|ttf)$",
    re.IGNORECASE,
)

# Placeholder shapes. Only the secret value is judged (the named `secret` group,
# else the whole token match), never a key name, user name or the rest of the line.
# A value is a placeholder when it is wholly an <angle> name or a variable, or
# when placeholder words, x-runs and ellipses cover at least half of it.
PLACEHOLDER_PART_RE = re.compile(
    r"(?i)example|placeholder|dummy|redacted|sample|fake|"
    r"your[_-]?(?:api[_-]?)?(?:token|key|password|secret)|test[_-]?key|"
    r"x{4,}|\.{3}|…"
)
ANGLE_RE = re.compile(r"<[A-Za-z0-9_\- ]+>")
# A lower-case word phrase led by a placeholder word, no digits: example-only-value.
PHRASE_RE = re.compile(r"(?:example|sample|dummy|fake|placeholder|redacted)(?:[-_.][a-z]+)*")

# A variable reference is a placeholder only when it is the whole secret value: a
# real password may hold a dollar sign. The bare form must be upper case.
VARIABLE_RE = re.compile(r"\$\{[A-Za-z0-9_]+\}|\$[A-Z_][A-Z0-9_]*")

# Inline suppression: append this comment to a line that legitimately matches.
SUPPRESS_RE = re.compile(r"(?:secret-scan|gitleaks):\s*allow", re.IGNORECASE)

MAX_FILE_BYTES = 2 * 1024 * 1024  # skip anything larger; secrets live in text


# ── git helpers ──────────────────────────────────────────────────────────────
def git(*args: str, text: bool = True) -> str:
    result = subprocess.run(["git", *args], capture_output=True, text=text, check=False)
    if result.returncode != 0:
        stderr = result.stderr if text else result.stderr.decode("utf-8", "replace")
        raise RuntimeError(f"git {' '.join(args)} failed: {stderr.strip()}")
    return result.stdout


def repo_root() -> Path:
    return Path(git("rev-parse", "--show-toplevel").strip())


def tracked_files() -> list[str]:
    out = git("ls-files", "-z")
    return [p for p in out.split("\0") if p]


def staged_files() -> list[str]:
    out = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z")
    return [p for p in out.split("\0") if p]


def decode(data: bytes) -> str:
    """Text of any file: an odd encoding must neither crash nor hide a token."""
    utf16 = data.startswith((codecs.BOM_UTF16_LE, codecs.BOM_UTF16_BE))
    return data.decode("utf-16" if utf16 else "utf-8", "replace")


def staged_content(path: str) -> str | None:
    return blob_content(f":{path}")


def outgoing_blobs(rev_range: str, remote: str | None = None) -> list[tuple[str, str]]:
    """Every (blob_sha, path) that `rev_range` would publish.

    A diff of the two ends misses a secret added in one commit and removed in a
    later one, so walk the objects. A single revision (a new branch) means all of
    its commits that no remote-tracking branch of `remote` already has.
    """
    revisions = [rev_range]
    if ".." not in rev_range:
        revisions += ["--not", f"--remotes={remote}" if remote else "--remotes"]
    objects = git("rev-list", "--objects", *revisions)
    if not objects:
        return []
    result = subprocess.run(
        ["git", "cat-file", "--batch-check=%(objecttype) %(objectname) %(rest)"],
        input=objects,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git cat-file failed: {result.stderr.strip()}")
    blobs: list[tuple[str, str]] = []
    for line in result.stdout.splitlines():
        parts = line.split(" ", 2)
        if len(parts) == 3 and parts[0] == "blob":
            blobs.append((parts[1], parts[2]))
    return blobs


def all_history_blobs() -> list[tuple[str, str]]:
    """Every (blob_sha, path) pair reachable from any ref."""
    out = git("rev-list", "--objects", "--all")
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            pairs.append((parts[0], parts[1]))
    return pairs


def blob_content(sha: str) -> str | None:
    result = subprocess.run(["git", "cat-file", "-p", sha], capture_output=True, check=False)
    if result.returncode != 0:
        return None
    return decode(result.stdout)


# ── scanning ─────────────────────────────────────────────────────────────────
class Finding:
    __slots__ = ("desc", "line", "path", "rule", "severity")

    def __init__(self, rule, desc, severity, path, line):
        self.rule = rule
        self.desc = desc
        self.severity = severity
        self.path = path
        self.line = line

    def render(self) -> str:
        colour = "\033[31m" if self.severity == "block" else "\033[33m"
        reset = "\033[0m"
        return (
            f"  {colour}{self.severity.upper():5s}{reset} {self.rule}  "
            f"{self.path}:{self.line}  {self.desc}"
        )


def is_placeholder(match: re.Match) -> bool:
    value = match.groupdict().get("secret") or match.group()
    if any(r.fullmatch(value) for r in (VARIABLE_RE, ANGLE_RE, PHRASE_RE)):
        return True
    covered = sum(len(m.group()) for m in PLACEHOLDER_PART_RE.finditer(value))
    return covered * 2 >= len(value)


def scan_text(path: str, text: str, findings: list[Finding]) -> None:
    if len(text) > MAX_FILE_BYTES:
        return
    for lineno, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        if SUPPRESS_RE.search(line):
            continue
        found = None
        for rid, desc, pattern, severity in COMPILED:
            if all(is_placeholder(m) for m in pattern.finditer(line)):
                continue  # no match, or placeholders only
            if severity == "block":
                found = Finding(rid, desc, severity, path, lineno)
                break
            found = found or Finding(rid, desc, severity, path, lineno)
        if found:
            findings.append(found)  # one per line; a block outranks a warning


def scan_paths(paths: list[str], reader, findings: list[Finding]) -> None:
    for path in paths:
        if SKIP_PATH_RE.search(path):
            continue
        text = reader(path)
        if text is None:
            continue
        scan_text(path, text, findings)


def scan_blobs(blobs: list[tuple[str, str]], findings: list[Finding]) -> None:
    for sha, path in blobs:
        if SKIP_PATH_RE.search(path):
            continue
        text = blob_content(sha)
        if text is not None:
            scan_text(path, text, findings)


def read_worktree(root: Path):
    def reader(path: str) -> str | None:
        full = root / path
        try:
            if not full.is_file() or full.stat().st_size > MAX_FILE_BYTES:
                return None
            return decode(full.read_bytes())
        except OSError:
            return None

    return reader


# ── entry point ──────────────────────────────────────────────────────────────
def main() -> int:
    parser = argparse.ArgumentParser(
        description="Block credentials from entering the repository.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--staged", action="store_true", help="scan staged content only")
    group.add_argument("--range", metavar="A..B", help="scan a commit range")
    group.add_argument("--all-history", action="store_true", help="scan every blob in history")
    group.add_argument("--list-rules", action="store_true", help="print the rule table")
    parser.add_argument("--remote", help="with --range SHA: the remote being pushed to")
    parser.add_argument("--quiet", action="store_true", help="only print on failure")
    args = parser.parse_args()

    if args.list_rules:
        print(f"{'ID':8s} {'SEVERITY':9s} DESCRIPTION")
        for rid, desc, _pat, sev in RULES:
            print(f"{rid:8s} {sev:9s} {desc}")
        return 0

    findings: list[Finding] = []
    root = repo_root()

    if args.staged:
        scan_paths(staged_files(), staged_content, findings)
        scope = "staged content"
    elif args.range:
        scan_blobs(outgoing_blobs(args.range, args.remote), findings)
        scope = f"range {args.range}"
    elif args.all_history:
        scan_blobs(all_history_blobs(), findings)
        scope = "all history"
    else:
        scan_paths(tracked_files(), read_worktree(root), findings)
        scope = "working tree"

    blocking = [f for f in findings if f.severity == "block"]
    warning = [f for f in findings if f.severity != "block"]

    if findings:
        print(f"\n\033[1mSecret scan — {scope}\033[0m")
        for f in findings:
            print(f.render())
        print()

    if blocking:
        print(f"\033[31m✗ {len(blocking)} blocking finding(s)\033[0m in {scope}.")
        print("  Remove the credential, or add `secret-scan: allow` to the line if it is a")
        print("  deliberate non-secret. Then re-run: ./scripts/check_secrets.py")
        return 1

    if warning and not args.quiet:
        print(f"\033[33m⚠ {len(warning)} warning(s)\033[0m — not blocking, review above.")

    if not args.quiet:
        print(
            f"\033[32m✓ no credentials found\033[0m in {scope} "
            f"({len(RULES)} rules, {len(blocking)} blocking, {len(warning)} warnings)"
        )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RuntimeError as exc:
        print(f"secret scan failed: {exc}", file=sys.stderr)
        sys.exit(2)
