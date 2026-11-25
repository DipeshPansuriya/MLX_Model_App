"""Row 562/564: a placeholder marker excuses a match only through the secret value."""

from __future__ import annotations

import importlib.util
import uuid
from pathlib import Path

import pytest

SCANNER_PY = Path(__file__).resolve().parent.parent / "scripts" / "check_secrets.py"


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"m_{uuid.uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


SCANNER = load(SCANNER_PY)


def scan(line: str) -> list:
    findings: list = []
    SCANNER.scan_text("config.txt", line + "\n", findings)
    return findings


def assignment(key: str, value: str) -> str:
    return f"{key} = '{value}'"


# Every value is made up and assembled at runtime, so no line here matches a rule.
FILLER = "Zq7Lm2Vt" + "9Kp4Wx8N" + "s3Bd6Hj1" + "Fr5Gc0Ty"
TAIL = "Qz7Lm2" + "Vt9"
GH = "gh" + "p_"
HF = "h" + "f_"
AWS = "AK" + "IA"
PW_KEY = "pass" + "word"
PWD_KEY = "p" + "wd"
API_KEY = "api" + "_key"
AUTH_KEY = "auth" + "_token"
SEC_KEY = "secret" + "_key"
POSTGRES = "postgres:" + "//"
MYSQL = "mysql:" + "//"
BEARER = "Bear" + "er "


def db_url(scheme: str, user: str, password: str) -> str:
    return f"DB = '{scheme}{user}:{password}@db.local/app'"


BYPASSES = {
    "sample-in-token": (
        "SEC-001",
        f"tok = '{GH}{FILLER[:16]}Sample{FILLER[16:30]}'",
    ),
    "fake-in-token": ("SEC-001", f"tok = '{GH}{FILLER[:20]}fake{FILLER[20:32]}'"),
    "example-in-key-id": ("SEC-007", f"KEY = '{AWS}EXAMPLE" + "7Q2LM9VT4'"),
    "x-run-in-token": ("SEC-012", f"tok = '{HF}{FILLER[:12]}xXxX{FILLER[12:30]}'"),
    "dummy-in-password": ("SEC-020", assignment(PW_KEY, "Dummy#" + "Lion2026")),
    "sample-in-password": ("SEC-020", assignment(PW_KEY, "MySample!" + "77qz")),
    "dots-in-password": ("SEC-020", assignment(PW_KEY, "p...w0rd" + "!Kx9")),
    "ellipsis-in-password": ("SEC-020", f"{PWD_KEY}: '" + "Gr8…" + "Pass2026'"),
    "angle-in-password": ("SEC-020", assignment(PW_KEY, "<admin>" + "Pw9zQ")),
    "test-key-in-api-key": ("SEC-021", assignment(API_KEY, "mytestkey" + "9QzLm2Vt")),
    "redacted-in-token": ("SEC-021", assignment(AUTH_KEY, "unredacted" + TAIL)),
    "your-key-in-secret": ("SEC-021", assignment(SEC_KEY, "Your-Key-Is-" + TAIL)),
    "fake-in-db-user": ("SEC-023", db_url(POSTGRES, "fake_user", TAIL + "Kp")),
    "sample-in-db-user": ("SEC-023", db_url(MYSQL, "sample", "Hunter2" + "Kp9")),
    "example-in-name": ("SEC-024", "EXAMPLE_API_TOKEN=" + FILLER),
    "placeholder-in-name": ("SEC-024", "PLACEHOLDER_OVERRIDE_KEY=" + FILLER),
    "fake-in-bearer": ("SEC-022", BEARER + FILLER[:16] + "fake" + FILLER[16:]),
}


@pytest.mark.parametrize("rule, line", BYPASSES.values(), ids=BYPASSES.keys())
def test_a_marker_inside_a_real_value_is_reported(rule, line):
    findings = scan(line)
    assert [f.rule for f in findings] == [rule]
    rendered = findings[0].render()
    assert line not in rendered
    assert FILLER[:12] not in rendered and TAIL not in rendered


@pytest.mark.parametrize(
    "rule, line",
    [
        ("SEC-024", "EXAMPLE_API_TOKEN=" + FILLER[::-1] + "Aa1"),
        ("SEC-023", db_url(POSTGRES, "sample", TAIL)),
    ],
    ids=["name", "user"],
)
def test_a_marker_in_a_name_or_user_does_not_excuse_the_value(rule, line):
    assert [f.rule for f in scan(line)] == [rule]


PLACEHOLDERS = {
    "words-token": f"tok = '{GH}" + "example" * 5 + "1'",
    "angle": assignment(PW_KEY, "<your-password>"),
    "your-api-key": assignment(API_KEY, "your_api_key_here"),
    "x-run": assignment(API_KEY, "sk-" + "x" * 16),
    "variable": assignment(PW_KEY, "${DB_PASSWORD}"),
    "phrase": assignment(PW_KEY, "example-only-value"),
    "bearer-x-run": BEARER + "x" * 24,
}


@pytest.mark.parametrize("line", PLACEHOLDERS.values(), ids=PLACEHOLDERS.keys())
def test_whole_value_placeholders_stay_allowed(line):
    # Not vacuous: a rule does match, and only the placeholder check excuses it.
    assert any(pattern.search(line) for _, _, pattern, _ in SCANNER.COMPILED)
    assert scan(line) == []


def test_a_short_placeholder_token_stays_allowed():
    assert scan(f"tok = '{GH}" + "example" * 4 + "12'") == []


def test_dead_markers_are_gone():
    assert not hasattr(SCANNER, "ALLOW_MATCH_RE")
    patterns = " ".join(
        r.pattern for r in (SCANNER.PLACEHOLDER_PART_RE, SCANNER.ANGLE_RE, SCANNER.PHRASE_RE)
    )
    for dead in ("environ", "getenv", "process", "not a real", "SEC-", "scan", "check_secrets"):
        assert dead not in patterns
