"""The guard that keeps the suite off 8765 and 8080. See ``portguard``."""

from __future__ import annotations

import shutil
import socket
import subprocess
import sys
from pathlib import Path

import portguard
import pytest

TESTS = Path(__file__).resolve().parent
GUARD = str(TESTS.parent / "mlx_guard.py")


@pytest.fixture
def expected():
    """The refusals this test causes on purpose are taken off the record again."""
    before = len(portguard.VIOLATIONS)
    yield lambda: portguard.VIOLATIONS[before:]
    del portguard.VIOLATIONS[before:]


@pytest.mark.parametrize("port", [8765, 8080])
@pytest.mark.parametrize("action", ["bind", "connect"])
def test_the_users_ports_cannot_be_used(expected, port, action):
    with socket.socket() as sock, pytest.raises(OSError, match=str(port)):
        getattr(sock, action)(("127.0.0.1", port))
    assert len(expected()) == 1


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, GUARD, "--model", "org/model", "--port", "8080"],
        [sys.executable, GUARD, "--model", "org/model"],
        [sys.executable, "-m", "mlx_lm", "server"],
        [sys.executable, str(TESTS.parent / "app.py")],
    ],
)
def test_the_real_server_cannot_be_started(expected, command):
    with pytest.raises(OSError, match="tried to start"):
        subprocess.Popen(command)
    assert expected()


# Asked of ``real_launch`` only: none of these is ever really started.
@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, "-u", GUARD],
        [sys.executable, "-I", GUARD, "--port", "8080"],
        [sys.executable, "-B", "-X", "utf8", "-W", "error", GUARD],
        [sys.executable, "-Xutf8", "app.py"],
        [sys.executable, "-m", "mlx_lm.server"],
        [sys.executable, "-u", "-m", "mlx_lm.server", "--port", "8080"],
        [sys.executable, "-um", "mlx_lm", "server"],
        [sys.executable, "-mmlx_lm.server"],
        [sys.executable, "app.py"],
        ["bash", "-c", f"exec python3 '{GUARD}' --port 8080"],
        ["/bin/sh", "-ec", "cd /tmp && python3 -u app.py"],
        ["zsh", "-c", "true; python3 -m mlx_lm.server"],
        ["bash", "-c", "echo one\npython3 app.py\n"],
    ],
)
def test_other_spellings_of_the_real_server_are_seen(command):
    assert "tried to start" in (portguard.real_launch(command) or ""), command


@pytest.mark.parametrize(
    "command",
    [
        [sys.executable, "-u", "-c", "pass", GUARD],
        [sys.executable, "-m", "pytest", "app.py"],
        [sys.executable, "-u", GUARD, "--port", "50123"],
        [sys.executable, "-m", "mlx_lm.server", "--port", "50123"],
        [sys.executable, "scripts/myapp.py"],
        [sys.executable],
        ["bash", "-n", "app.py"],
        ["bash", "-c", "xattr app.py; echo python3"],
        ["bash", "-c", "python3 -c 'pass'; ls app.py"],
        ["bash", "-c", "echo 'unbalanced"],
    ],
)
def test_other_commands_are_left_alone(command):
    assert portguard.real_launch(command) is None, command


def test_a_shell_command_line_is_read_too():
    assert portguard.real_launch("python3 app.py", in_shell=True)
    assert portguard.real_launch(["python3 app.py"], in_shell=True)
    assert portguard.real_launch("ls app.py", in_shell=True) is None
    assert portguard.real_launch("python3 app.py") is None  # a program of that name


def test_a_stand_in_does_not_count_when_the_interpreter_ignores_it(tmp_path):
    (tmp_path / "mlx_lm").mkdir()
    (tmp_path / "mlx_lm" / "server.py").write_text("", "utf-8")
    env = {"PYTHONPATH": str(tmp_path)}
    for flag in ("-I", "-E"):
        assert portguard.real_launch([sys.executable, flag, GUARD], env), flag
    assert portguard.real_launch([sys.executable, "-u", GUARD], env) is None


def test_popen_says_when_the_command_is_for_a_shell(expected, monkeypatch):
    asked = []

    def refuse(command, env=None, in_shell=False):
        asked.append(in_shell)
        return "refused on purpose: tried to start nothing"  # before any process

    monkeypatch.setattr(portguard, "real_launch", refuse)
    with pytest.raises(OSError, match="on purpose"):
        subprocess.Popen("true", shell=True)  # noqa: S602 - refused before it runs
    assert asked == [True]
    assert expected()


def test_stand_ins_and_free_ports_are_allowed(tmp_path):
    assert portguard.real_launch([sys.executable, "-c", "pass", GUARD, "--port", "8080"]) is None
    assert portguard.real_launch(["/usr/bin/xattr", GUARD]) is None
    assert portguard.real_launch([sys.executable, GUARD, "--port", "50123"]) is None
    (tmp_path / "mlx_lm").mkdir()
    (tmp_path / "mlx_lm" / "server.py").write_text("", "utf-8")
    env = {"PYTHONPATH": str(tmp_path)}
    assert portguard.real_launch([sys.executable, GUARD], env) is None


def test_a_swallowed_refusal_still_fails_the_run(tmp_path):
    shutil.copy(TESTS / "portguard.py", tmp_path)
    shutil.copy(TESTS / "conftest.py", tmp_path)
    (tmp_path / "test_sample.py").write_text(
        "import contextlib, socket\n"
        "def test_quietly():\n"
        "    with socket.socket() as s, contextlib.suppress(OSError):\n"
        "        s.bind(('127.0.0.1', 8080))\n",
        "utf-8",
    )
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", "--rootdir", ".", "."],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    assert done.returncode != 0, done.stdout
    assert "tried to bind port 8080" in done.stdout


def test_no_server_is_brought_back_after_a_test_has_ended(app_module, monkeypatch):
    # The 2.0.12 fallback: a watcher saw the killed fake server die after the test's
    # fake Popen was gone, and started the real one on 8080.
    stops = app_module.STATE.server_stops
    launched = []
    monkeypatch.setattr(app_module, "_launch_server", lambda *args: launched.append(args))
    portguard.shut_down(app_module)
    assert app_module._bring_back((["org/model"], True), "it exited", stops) is None
    app_module.STATE.server_pid = 4242
    app_module._watch_server(4242, "org/model", None, (["org/model"], True), stops)
    assert not launched
