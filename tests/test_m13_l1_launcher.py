"""M13 / L1: the native launcher probes interpreters, logs to launch.log and
leaves single-instance handling to app.py.

Compiles packaging/launcher.c with stub interpreter candidates into a throwaway
bundle. HOME is temporary, so no real log is touched.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "packaging" / "launcher.c"

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "darwin" or not shutil.which("clang"),
        reason="the native launcher builds on macOS only",
    ),
]


def fake_python(path: Path, name: str, probe_exit: int) -> str:
    target = path / name
    target.write_text(
        "#!/bin/sh\n"
        f'if [ "$1" = "-c" ]; then exit {probe_exit}; fi\n'
        f'echo "ran {name} $* in $(pwd)"\n'
        f'echo "stderr from {name}" >&2\n',
        encoding="utf-8",
    )
    target.chmod(0o755)
    return str(target)


class Bundle:
    def __init__(self, base: Path):
        self.base = base
        self.home = base / "home"
        self.home.mkdir()
        contents = base / "Stub.app" / "Contents"
        self.resources = contents / "Resources"
        self.resources.mkdir(parents=True)
        (contents / "MacOS").mkdir()
        (self.resources / "app.py").write_text("print('real app ran')\n", encoding="utf-8")
        self.binary = contents / "MacOS" / "MLXStudio"
        self.logs = self.home / ".mlx_studio" / "logs"
        self.log = self.logs / "launch.log"

    def run(self, candidates: list[str]) -> subprocess.CompletedProcess:
        listed = ",".join(f'"{c}"' for c in candidates)
        subprocess.run(
            [
                "clang",
                "-Wall",
                "-Wextra",
                "-Werror",
                f"-DPYTHON_CANDIDATES={listed}",
                "-o",
                str(self.binary),
                str(SOURCE),
            ],
            check=True,
            capture_output=True,
        )
        return subprocess.run(
            [str(self.binary)],
            env={"HOME": str(self.home), "PATH": "/usr/bin:/bin"},
            cwd=str(self.base),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def text(self) -> str:
        return self.log.read_text(encoding="utf-8")


@pytest.fixture
def bundle(tmp_path):
    return Bundle(tmp_path)


def test_prefers_the_interpreter_with_mlx_lm(bundle):
    plain = fake_python(bundle.base, "plain", 20)
    ready = fake_python(bundle.base, "ready", 22)
    assert bundle.run([plain, ready]).returncode == 0
    assert "ran ready app.py" in bundle.text()
    assert "ran plain" not in bundle.text()


def test_prefers_mlx_lm_with_a_native_window(bundle):
    ready = fake_python(bundle.base, "ready", 22)
    best = fake_python(bundle.base, "best", 23)
    assert bundle.run([ready, best]).returncode == 0
    assert "ran best app.py" in bundle.text()


def test_first_candidate_wins_a_tie(bundle):
    first = fake_python(bundle.base, "first", 20)
    second = fake_python(bundle.base, "second", 20)
    assert bundle.run([first, second]).returncode == 0
    assert "ran first app.py" in bundle.text()


@pytest.mark.parametrize("code", [0, 1, 10, 127])
def test_skips_old_or_broken_interpreters(bundle, code):
    bad = fake_python(bundle.base, "bad", code)
    good = fake_python(bundle.base, "good", 20)
    assert bundle.run([bad, str(bundle.base / "absent"), good]).returncode == 0
    assert "ran good app.py" in bundle.text()
    assert "ran bad" not in bundle.text()


def test_logs_the_failure_when_nothing_is_usable(bundle):
    old = fake_python(bundle.base, "old", 10)
    done = bundle.run([old])
    assert done.returncode == 1
    assert "no Python 3.10+ interpreter found" in bundle.text()


def test_logs_a_missing_app(bundle):
    (bundle.resources / "app.py").unlink()
    good = fake_python(bundle.base, "good", 20)
    assert bundle.run([good]).returncode == 1
    assert "cannot find" in bundle.text()


def test_child_output_goes_to_an_owner_only_log(bundle):
    good = fake_python(bundle.base, "good", 20)
    done = bundle.run([good])
    assert done.stdout == ""
    assert done.stderr == ""
    text = bundle.text()
    assert "stderr from good" in text
    assert f"interpreter={good}" in text
    assert "Stub.app/Contents/Resources" in text
    assert bundle.log.stat().st_mode & 0o777 == 0o600
    assert bundle.logs.stat().st_mode & 0o777 == 0o700


def test_appends_and_rotates_a_large_log(bundle):
    bundle.logs.mkdir(parents=True)
    bundle.log.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
    good = fake_python(bundle.base, "good", 20)
    bundle.run([good])
    bundle.run([good])
    assert (bundle.logs / "launch.log.1").stat().st_size == 2 * 1024 * 1024
    assert bundle.text().count("ran good app.py") == 2


def test_rotated_log_is_owner_only(bundle):
    bundle.logs.mkdir(parents=True)
    bundle.log.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
    bundle.log.chmod(0o644)
    good = fake_python(bundle.base, "good", 20)
    assert bundle.run([good]).returncode == 0
    assert (bundle.logs / "launch.log.1").stat().st_mode & 0o777 == 0o600
    assert bundle.log.stat().st_mode & 0o777 == 0o600


def test_a_symlinked_log_is_neither_followed_nor_rotated(bundle):
    bundle.logs.mkdir(parents=True)
    target = bundle.base / "elsewhere.txt"
    target.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
    target.chmod(0o644)
    bundle.log.symlink_to(target)
    good = fake_python(bundle.base, "good", 20)
    assert bundle.run([good]).returncode == 0
    assert bundle.log.is_symlink()
    assert not (bundle.logs / "launch.log.1").exists()
    assert target.stat().st_size == 2 * 1024 * 1024
    assert target.stat().st_mode & 0o777 == 0o644


def test_keeps_logging_when_rotation_fails(bundle):
    blocker = bundle.logs / "launch.log.1"
    blocker.mkdir(parents=True)
    (blocker / "keep").write_text("keep", encoding="utf-8")
    bundle.log.write_text("x" * (2 * 1024 * 1024), encoding="utf-8")
    good = fake_python(bundle.base, "good", 20)
    assert bundle.run([good]).returncode == 0
    assert "ran good app.py" in bundle.text()
    assert (blocker / "keep").read_text(encoding="utf-8") == "keep"


def test_the_log_size_check_uses_the_open_descriptor():
    """No stat-then-act on the log path (CodeQL cpp/toctou-race-condition)."""
    text = SOURCE.read_text(encoding="utf-8")
    assert not re.search(r"(?<![A-Za-z0-9_])l?stat\(", text)
    assert "fstat(" in text


def test_the_real_probe_accepts_a_real_interpreter(bundle):
    assert bundle.run([sys.executable]).returncode == 0, bundle.text()
    assert "real app ran" in bundle.text()


def test_leaves_single_instance_handling_to_app_py():
    text = SOURCE.read_text(encoding="utf-8")
    assert "already_running" not in text
    assert "osascript" not in text
    assert "api/ping" not in text


def test_the_dead_python_launcher_is_gone():
    assert not (ROOT / "packaging" / "launcher.py").exists()
