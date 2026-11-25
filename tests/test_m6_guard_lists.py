"""M6 follow-up: every file list that covers app.py also covers mlx_guard.py.

The app starts the API server through mlx_guard.py, so a build, release or hook
list that skips it lets a stale, broken or tampered guard ship unnoticed.
Scripts run from throwaway trees with a throwaway HOME.
"""

from __future__ import annotations

import importlib.util
import os
import re
import shutil
import subprocess
import sys
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
GUARD = "mlx_guard.py"
# Derived from the tree: a new top-level source must be added to every list too.
SOURCES = sorted(p.name for p in ROOT.glob("*.py"))


def read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def load_manifest_module(script: Path):
    spec = importlib.util.spec_from_file_location(f"relman_{uuid.uuid4().hex}", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_guard_is_a_top_level_source():
    assert GUARD in SOURCES


@pytest.mark.parametrize("name", SOURCES)
def test_build_script_copies_every_source(name):
    assert f'cp "$ROOT/{name}" "$APP/Contents/Resources/{name}"' in read("packaging/build_app.sh")


@pytest.mark.parametrize("name", SOURCES)
def test_manifest_lists_every_source(name):
    module = load_manifest_module(ROOT / "packaging" / "release_manifest.py")
    assert name in module.SHIPPED
    assert name in module.BUNDLED


@pytest.mark.parametrize("name", SOURCES)
@pytest.mark.parametrize(
    "path",
    [
        ".github/workflows/quality.yml",
        ".github/workflows/pr-review.yml",
        ".githooks/pre-commit",
        ".github/pull_request_template.md",
    ],
)
def test_compile_gates_cover_every_source(path, name):
    gates = [line for line in read(path).splitlines() if "py_compile" in line]
    assert any(re.search(rf"(?<![\w/]){re.escape(name)}\b", line) for line in gates), gates


@pytest.mark.parametrize("name", SOURCES)
def test_codeowners_names_every_source(name):
    assert re.search(rf"^/{re.escape(name)}\s+@", read(".github/CODEOWNERS"), re.MULTILINE)


# ── release_manifest.py behaviour ────────────────────────────────────────────
class Tree:
    def __init__(self, base: Path, monkeypatch):
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            monkeypatch.delenv(key)
        monkeypatch.setenv("HOME", str(base))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for who in ("AUTHOR", "COMMITTER"):
            monkeypatch.setenv(f"GIT_{who}_NAME", "t")
            monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@invalid")
        self.root = base / "project"
        (self.root / "packaging").mkdir(parents=True)
        (self.root / "ui").mkdir()
        shutil.copy(ROOT / "packaging" / "release_manifest.py", self.root / "packaging")
        for name in ("app.py", GUARD, "ui/index.html", "ui/style.css", "ui/app.js"):
            (self.root / name).write_text(f"# {name}\n", encoding="utf-8")
        (self.root / "app.py").write_text('APP_VERSION = "3.1.0"\n', encoding="utf-8")
        self.mod = load_manifest_module(self.root / "packaging" / "release_manifest.py")

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)


@pytest.fixture
def tree(tmp_path, monkeypatch):
    return Tree(tmp_path, monkeypatch)


def fake_pkgutil(tree: Tree, monkeypatch, change) -> list[str]:
    """Run check_payload with pkgutil replaced by a copy of the sources."""
    real_run = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[0] != "pkgutil":
            return real_run(argv, *args, **kwargs)
        contents = Path(argv[-1]) / "Payload" / "Applications" / "MLX Studio.app" / "Contents"
        resources = contents / "Resources"
        (resources / "ui").mkdir(parents=True)
        for name in ("app.py", GUARD, "ui/index.html", "ui/style.css", "ui/app.js"):
            shutil.copy(tree.root / name, resources / name)
        (contents / "Info.plist").write_text(
            '<plist version="1.0"><dict><key>CFBundleShortVersionString</key>'
            "<string>3.1.0</string></dict></plist>",
            encoding="utf-8",
        )
        change(resources / GUARD)
        return subprocess.CompletedProcess(argv, 0, "", "")

    monkeypatch.setattr(tree.mod.shutil, "which", lambda name: f"/usr/sbin/{name}")
    monkeypatch.setattr(tree.mod.subprocess, "run", run)
    return tree.mod.check_payload(tree.root / "stub.pkg")


def test_payload_with_the_current_guard_passes(tree, monkeypatch):
    assert fake_pkgutil(tree, monkeypatch, lambda guard: None) == []


@pytest.mark.parametrize(
    "change",
    [Path.unlink, lambda guard: guard.write_text("# stale\n", encoding="utf-8")],
    ids=["missing", "stale"],
)
def test_payload_with_a_wrong_guard_is_refused(tree, monkeypatch, change):
    problems = fake_pkgutil(tree, monkeypatch, change)
    assert len(problems) == 1
    assert problems[0].startswith(f"{GUARD} in stub.pkg differs")


def test_a_guard_change_after_the_tag_is_reported(tree):
    tree.git("init", "-q", "-b", "main")
    tree.git("add", "-A")
    tree.git("commit", "-q", "-m", "release")
    tree.git("tag", "v3.1.0")
    assert tree.mod.tag_source_problems("3.1.0") == []
    (tree.root / GUARD).write_text("# changed\n", encoding="utf-8")
    assert tree.mod.tag_source_problems("3.1.0")


# ── security_audit.sh behaviour ──────────────────────────────────────────────
def audit(tmp_path: Path, guard: str | None, bundled: str | None = "same") -> str:
    """Run a copy of the audit over a minimal tree and return its output."""
    root = tmp_path / "audit"
    home = tmp_path / "audit-home"
    home.mkdir()
    for folder in ("scripts", "packaging", "ui"):
        (root / folder).mkdir(parents=True)
    shutil.copy(ROOT / "scripts" / "security_audit.sh", root / "scripts")
    (root / "app.py").write_text('HOST = "127.0.0.1"\n', encoding="utf-8")
    (root / "ui" / "app.js").write_text("// ui\n", encoding="utf-8")
    if guard is not None:
        (root / GUARD).write_text(guard, encoding="utf-8")
    resources = root / "dist" / "MLX Studio.app" / "Contents" / "Resources"
    (resources / "ui").mkdir(parents=True)
    shutil.copy(root / "app.py", resources / "app.py")
    shutil.copy(root / "ui" / "app.js", resources / "ui" / "app.js")
    if bundled == "same" and guard is not None:
        shutil.copy(root / GUARD, resources / GUARD)
    elif bundled not in (None, "same"):
        (resources / GUARD).write_text(bundled, encoding="utf-8")
    env = {
        "HOME": str(home),
        "PATH": os.pathsep.join([str(Path(sys.executable).parent), os.environ["PATH"]]),
        # Skips codesign and spctl on the stub bundle.
        "MLX_AUDIT_PLATFORM": "linux",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    done = subprocess.run(
        ["bash", str(root / "scripts" / "security_audit.sh"), "--quick"],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    return re.sub(r"\x1b\[[0-9;]*m", "", done.stdout)


def results(output: str, check: str) -> list[str]:
    return [" ".join(line.split()) for line in output.splitlines() if check in line.split()[:2]]


def test_audit_passes_a_clean_guard(tmp_path):
    output = audit(tmp_path, "x = 1\n")
    for check in ("SA-01", "SA-02", "SA-14", "SA-15"):
        assert not [line for line in results(output, check) if line.startswith("FAIL")], output
    assert f"PASS SA-14 {GUARD} compiles" in results(output, "SA-14")
    assert f"PASS SA-15 bundled {GUARD} matches the working tree" in results(output, "SA-15")


@pytest.mark.parametrize(
    ("check", "guard"),
    [
        ("SA-01", "import os\nos.system('true')\n"),
        ("SA-02", 'BIND = "0.0.0.0"\n'),
        ("SA-14", "def (:\n"),
        ("SA-14", None),
    ],
    ids=["dangerous-pattern", "wildcard-bind", "syntax-error", "guard-missing"],
)
def test_audit_fails_on_a_bad_guard(tmp_path, check, guard):
    output = audit(tmp_path, guard)
    assert any(line.startswith("FAIL") for line in results(output, check)), output


@pytest.mark.parametrize("bundled", [None, "# stale\n"], ids=["missing", "stale"])
def test_audit_fails_on_a_wrong_bundled_guard(tmp_path, bundled):
    output = audit(tmp_path, "x = 1\n", bundled)
    assert any(GUARD in line and line.startswith("FAIL") for line in results(output, "SA-15"))
