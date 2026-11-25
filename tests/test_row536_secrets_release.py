"""F5: the allow-list excuses the matched text only. F6: install.sh and SHA256SUMS ship together."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
VERSION = "3.1.0"
PKG = f"MLX.Studio-{VERSION}.pkg"
DMG = f"MLX.Studio-{VERSION}.dmg"


def load(path: Path):
    spec = importlib.util.spec_from_file_location(f"m_{uuid.uuid4().hex}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ── F5 ───────────────────────────────────────────────────────────────────────
# Every credential shape is assembled at runtime, so no line here matches a rule.
TOKEN = "ghp_" + "Qw7" * 12
PLACEHOLDER = "ghp_" + "x" * 36
DB_URL = "postgres:" + "//app:" + "Tr0ub4dor" + "@db.internal/app"

SCANNER = load(ROOT / "scripts" / "check_secrets.py")


def scan(line: str) -> list:
    findings: list = []
    SCANNER.scan_text("config.txt", line + "\n", findings)
    return findings


@pytest.mark.parametrize(
    "line",
    [
        f"token = {TOKEN}  # example",
        f"token = {TOKEN}  # was $OLD_TOKEN",
        f"token = {TOKEN}  # ${{OLD_TOKEN}}",
        f'token = os.getenv("T", "{TOKEN}")',
        f"fake sample placeholder ... <token> {TOKEN}",
        f"token = {TOKEN}  # see SEC-001, check_secrets",
        f"old {PLACEHOLDER} new {TOKEN}",
    ],
    ids=["example", "dollar", "braces", "getenv", "many", "rule-id", "placeholder-first"],
)
def test_an_allow_word_elsewhere_on_the_line_does_not_hide_a_token(line):
    findings = scan(line)
    assert [(f.rule, f.line) for f in findings] == [("SEC-001", 1)]
    assert TOKEN not in findings[0].render()


def test_the_example_host_does_not_hide_a_connection_password():
    line = DB_URL.replace("db.internal", "db.example.com")
    assert [f.rule for f in scan(line)] == ["SEC-023"]


def test_an_allowed_match_does_not_hide_a_later_rule():
    findings = scan(f"{PLACEHOLDER} {DB_URL}")
    assert [f.rule for f in findings] == ["SEC-023"]


@pytest.mark.parametrize(
    "line",
    [
        f"token = {PLACEHOLDER}",
        "postgres:" + "//app:" + "${DB_PASS}" + "@db/app",
        "postgres:" + "//app:" + "<password>" + "@db/app",
        "pass" + 'word = "example-only-value"',
        f"token = {TOKEN}  # secret-scan: allow",
    ],
    ids=["x-token", "env-var", "angle", "example-value", "pragma"],
)
def test_placeholders_and_the_explicit_pragma_stay_allowed(line):
    assert scan(line) == []


# ── F6 ───────────────────────────────────────────────────────────────────────
def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Project:
    """A throwaway tree holding a published 3.1.0 manifest and a newer source."""

    def __init__(self, base: Path, monkeypatch):
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            monkeypatch.delenv(key)
        monkeypatch.setenv("HOME", str(base))
        monkeypatch.setenv("GIT_CEILING_DIRECTORIES", str(base))
        self.root = base / "project"
        for folder in ("packaging", "release", "scripts", "assets"):
            (self.root / folder).mkdir(parents=True)
        shutil.copy(ROOT / "packaging" / "release_manifest.py", self.root / "packaging")
        shutil.copy(ROOT / "scripts" / "install.sh", self.root / "scripts")
        (self.root / "app.py").write_text('APP_VERSION = "3.2.0"\n', encoding="utf-8")
        self.mod = load(self.root / "packaging" / "release_manifest.py")
        self.assets = self.root / "assets"
        (self.assets / PKG).write_bytes(b"pkg-bytes")
        (self.assets / DMG).write_bytes(b"dmg-bytes")
        manifest = {
            "version": VERSION,
            "url": self.mod.release_url(VERSION),
            "sha256": sha(b"pkg-bytes"),
            "size": len(b"pkg-bytes"),
        }
        self.latest = self.root / "release" / "latest.json"
        self.latest.write_text(json.dumps(manifest), encoding="utf-8")
        self.sums = self.root / "release" / "SHA256SUMS"

    def write_sums(self, *lines: str) -> None:
        self.sums.write_text("".join(f"{x}\n" for x in lines), encoding="utf-8")

    def backfill(self) -> int:
        argv = ["--backfill", "--dist", str(self.assets), "--dmg-sha256", sha(b"dmg-bytes")]
        return self.mod.main(argv)


@pytest.fixture
def project(tmp_path, monkeypatch):
    return Project(tmp_path, monkeypatch)


PKG_LINE = f"{sha(b'pkg-bytes')}  {PKG}"
DMG_LINE = f"{sha(b'dmg-bytes')}  {DMG}"


def test_check_fails_while_the_installer_has_no_checksum_list(project, capsys):
    assert project.mod.main(["--check"]) == 1
    assert "install.sh" in capsys.readouterr().err


@pytest.mark.parametrize(
    "lines",
    [
        [PKG_LINE],
        [PKG_LINE, DMG_LINE, DMG_LINE],
        [PKG_LINE, f"{'0' * 64}  {DMG}", DMG_LINE],
        [PKG_LINE, f"not-a-hash  {DMG}"],
    ],
    ids=["missing", "repeated", "conflicting", "malformed"],
)
def test_check_needs_exactly_one_installer_line(project, capsys, lines):
    project.write_sums(*lines)
    assert project.mod.main(["--check"]) == 1
    assert "install.sh needs exactly one" in capsys.readouterr().err


def test_check_passes_with_the_published_lines(project):
    project.write_sums(PKG_LINE, DMG_LINE)
    assert project.mod.main(["--check"]) == 0


def test_check_does_not_require_the_list_for_an_installer_that_ignores_it(project):
    (project.root / "scripts" / "install.sh").write_text("#!/bin/bash\n", encoding="utf-8")
    assert project.mod.main(["--check"]) == 0


def test_backfill_lists_the_published_release_and_leaves_the_manifest(project):
    older = f"{'a' * 64}  MLX.Studio-3.0.0.dmg"
    project.write_sums(older)
    before = project.latest.read_bytes()
    assert project.backfill() == 0
    assert sorted(project.sums.read_text(encoding="utf-8").splitlines()) == sorted(
        [older, PKG_LINE, DMG_LINE]
    )
    assert project.latest.read_bytes() == before
    assert project.backfill() == 0
    assert project.sums.read_text(encoding="utf-8").count(DMG) == 1


@pytest.mark.parametrize("fault", ["other-pkg", "no-dmg", "conflict"])
def test_backfill_writes_nothing_when_the_assets_are_not_the_published_ones(project, fault):
    if fault == "other-pkg":
        (project.assets / PKG).write_bytes(b"local rebuild")
    elif fault == "no-dmg":
        (project.assets / DMG).unlink()
    else:
        project.write_sums(f"{'0' * 64}  {DMG}")
    before = project.sums.read_bytes() if project.sums.exists() else None
    assert project.backfill() == 1
    assert (project.sums.read_bytes() if project.sums.exists() else None) == before


def test_release_consistency_is_a_required_check_in_both_gate_modes():
    gate = (ROOT / "scripts" / "set_approval_gate.sh").read_text(encoding="utf-8")
    quality = (ROOT / ".github" / "workflows" / "quality.yml").read_text(encoding="utf-8")
    assert re.search(r"^ {4}name: Release consistency$", quality, re.MULTILINE)
    assert gate.count('{"context": "Release consistency"}') == 2


# ── Row 554: a manifest whose release was never published ────────────────────
def never_published(project, monkeypatch, digest: str | None = None) -> None:
    pinned = {VERSION: digest or sha(b"pkg-bytes")}
    monkeypatch.setattr(project.mod, "NEVER_PUBLISHED", pinned, raising=False)


def test_check_passes_without_a_list_for_a_never_published_manifest(project, monkeypatch, capsys):
    never_published(project, monkeypatch)
    assert project.mod.main(["--check"]) == 0
    assert "never published" in capsys.readouterr().out
    assert not project.sums.exists()


def test_the_next_version_tag_passes_with_a_never_published_manifest(project, monkeypatch):
    never_published(project, monkeypatch)
    assert project.mod.main(["--check", "--tag", "v3.2.0"]) == 0


def test_the_exception_is_pinned_to_the_exact_manifest_hash(project, monkeypatch, capsys):
    never_published(project, monkeypatch, "0" * 64)
    assert project.mod.main(["--check"]) == 1
    assert "install.sh needs exactly one" in capsys.readouterr().err


@pytest.mark.parametrize("source", [VERSION, "3.0.9"], ids=["same", "older"])
def test_the_exception_ends_unless_the_source_is_newer(project, monkeypatch, capsys, source):
    never_published(project, monkeypatch)
    (project.root / "app.py").write_text(f'APP_VERSION = "{source}"\n', encoding="utf-8")
    assert project.mod.main(["--check"]) == 1
    assert "install.sh needs exactly one" in capsys.readouterr().err


@pytest.mark.parametrize("lines", [[PKG_LINE, DMG_LINE], [DMG_LINE], [PKG_LINE]])
def test_lines_for_a_never_published_version_are_refused(project, monkeypatch, capsys, lines):
    never_published(project, monkeypatch)
    project.write_sums(*lines)
    assert project.mod.main(["--check"]) == 1
    assert "never published" in capsys.readouterr().err


def test_backfill_refuses_a_never_published_version(project, monkeypatch, capsys):
    never_published(project, monkeypatch)
    assert project.backfill() == 1
    assert "never published" in capsys.readouterr().err
    assert not project.sums.exists()


def test_only_2_0_7_is_recorded_as_never_published(project):
    assert list(project.mod.NEVER_PUBLISHED) == ["2.0.7"]
    assert re.fullmatch(r"[0-9a-f]{64}", project.mod.NEVER_PUBLISHED["2.0.7"])
