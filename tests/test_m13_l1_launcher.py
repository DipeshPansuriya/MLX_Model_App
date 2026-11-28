"""M13 / L1: the native launcher probes interpreters, logs to launch.log and
leaves single-instance handling to app.py.

Compiles packaging/launcher.c with stub interpreter candidates into a throwaway
bundle. HOME is temporary, so no real log is touched.
"""

from __future__ import annotations

import fcntl
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
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

    def run(self, candidates: list[str], args: tuple = ()) -> subprocess.CompletedProcess:
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
            [str(self.binary), *args],
            **self.surroundings(),
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )

    def surroundings(self) -> dict:
        return {"env": {"HOME": str(self.home), "PATH": "/usr/bin:/bin"}, "cwd": str(self.base)}

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


@pytest.mark.parametrize(
    ("args", "passed"),
    [
        (("--start-server-at-login",), " --start-server-at-login"),
        (("--other",), ""),
        (("--start-server-at-login", "--other"), ""),
        (("-c", "print(1)"), ""),
    ],
)
def test_only_the_login_argument_reaches_the_app(bundle, args, passed):
    good = fake_python(bundle.base, "good", 20)
    assert bundle.run([good], args).returncode == 0
    assert f"ran good app.py{passed} in " in bundle.text()


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


@pytest.mark.parametrize("size", [2 * 1024 * 1024, 10], ids=["large", "small"])
@pytest.mark.parametrize("mode", [0o400, 0o000], ids=["read-only", "no-access"])
def test_a_log_that_cannot_be_written_is_moved_aside(bundle, size, mode):
    bundle.logs.mkdir(parents=True)
    bundle.log.write_text("x" * size, encoding="utf-8")
    bundle.log.chmod(mode)
    good = fake_python(bundle.base, "good", 20)
    done = bundle.run([good])
    assert done.returncode == 0
    assert done.stdout == ""
    assert done.stderr == ""
    assert "ran good app.py" in bundle.text()
    assert bundle.log.stat().st_mode & 0o777 == 0o600
    assert (bundle.logs / "launch.log.1").stat().st_size == size


def test_starts_when_an_unwritable_log_cannot_be_moved(bundle):
    bundle.logs.mkdir(parents=True)
    bundle.log.write_text("old", encoding="utf-8")
    bundle.log.chmod(0o400)
    good = fake_python(bundle.base, "good", 20)
    bundle.logs.chmod(0o500)
    try:
        done = bundle.run([good])
    finally:
        bundle.logs.chmod(0o700)
    assert done.returncode == 0
    assert "ran good app.py" in done.stdout
    assert bundle.text() == "old"
    assert not (bundle.logs / "launch.log.1").exists()


def test_a_directory_at_the_log_path_is_left_alone(bundle):
    bundle.log.mkdir(parents=True)
    good = fake_python(bundle.base, "good", 20)
    done = bundle.run([good])
    assert done.returncode == 0
    assert "ran good app.py" in done.stdout
    assert bundle.log.is_dir()
    assert not (bundle.logs / "launch.log.1").exists()


def test_a_symlink_to_an_unwritable_file_is_not_moved(bundle):
    bundle.logs.mkdir(parents=True)
    target = bundle.base / "elsewhere.txt"
    target.write_text("keep", encoding="utf-8")
    target.chmod(0o400)
    bundle.log.symlink_to(target)
    good = fake_python(bundle.base, "good", 20)
    assert bundle.run([good]).returncode == 0
    assert bundle.log.is_symlink()
    assert not (bundle.logs / "launch.log.1").exists()
    assert target.read_text(encoding="utf-8") == "keep"
    assert target.stat().st_mode & 0o777 == 0o400


@contextmanager
def locked(folder: Path):
    """Hold the folder lock a launch takes while it opens and moves the logs."""
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


@pytest.mark.parametrize(
    ("size", "mode"), [(10, 0o400), (2 * 1024 * 1024, 0o600)], ids=["unwritable", "large"]
)
def test_a_second_launch_waits_and_keeps_the_log_the_first_moved_aside(bundle, size, mode):
    good = fake_python(bundle.base, "good", 20)
    bundle.run([good])  # builds the binary; its first start is the slow one
    old = bundle.logs / "launch.log.1"
    bundle.log.write_text("h" * size, encoding="utf-8")
    bundle.log.chmod(mode)
    with locked(bundle.logs):  # stands in for the first launch
        second = subprocess.Popen([str(bundle.binary)], **bundle.surroundings())
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline and not old.exists():
            time.sleep(0.02)
        moved_early = old.exists()
        if not moved_early:
            # What the first launch does before it lets go of the lock.
            bundle.log.rename(old)
            bundle.log.write_text("first launch\n", encoding="utf-8")
    assert second.wait(timeout=60) == 0
    assert not moved_early, "the log was moved while another launch held the lock"
    assert old.read_text(encoding="utf-8") == "h" * size
    assert bundle.text().startswith("first launch\n")
    assert "ran good app.py" in bundle.text()


def lock_is_taken(folder: Path) -> bool:
    fd = os.open(folder, os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        return False
    except BlockingIOError:
        return True
    finally:
        os.close(fd)


def test_the_lock_is_held_while_the_log_is_opened(bundle):
    good = fake_python(bundle.base, "good", 20)
    bundle.run([good])
    bundle.log.unlink()
    os.mkfifo(bundle.log)  # opening it for writing blocks until someone reads
    first = subprocess.Popen([str(bundle.binary)], **bundle.surroundings())
    try:
        deadline = time.monotonic() + 3
        seen = 0
        while time.monotonic() < deadline and seen < 3:
            time.sleep(0.1)
            seen = seen + 1 if lock_is_taken(bundle.logs) else 0
    finally:
        reader = os.open(bundle.log, os.O_RDONLY | os.O_NONBLOCK)
        try:
            code = first.wait(timeout=60)
        finally:
            os.close(reader)
    assert seen == 3, "the log was opened without holding the lock"
    assert code == 0


def test_a_lock_that_is_never_released_does_not_stop_the_start(bundle):
    bundle.logs.mkdir(parents=True)
    good = fake_python(bundle.base, "good", 20)
    with locked(bundle.logs):
        done = bundle.run([good])
    assert done.returncode == 0
    assert "ran good app.py" in bundle.text()


def test_the_app_does_not_inherit_the_log_lock(bundle):
    (bundle.resources / "app.py").write_text(
        "import fcntl, os\n"
        'fd = os.open(os.path.expanduser("~/.mlx_studio/logs"), os.O_RDONLY)\n'
        "fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
        'print("log lock is free")\n',
        encoding="utf-8",
    )
    assert bundle.run([sys.executable]).returncode == 0, bundle.text()
    assert "log lock is free" in bundle.text()


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
