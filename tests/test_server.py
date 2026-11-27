"""API server lifecycle: pidfile, watchdog, adoption and stop."""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def spawn_fake_server(**kwargs):
    """A harmless child whose command line looks like the API server for org/model."""
    tail = ["-m", "mlx_lm", "server", "--model", "org/model"]
    tail += ["--host", "127.0.0.1", "--port", "8080"]
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", *tail],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


class TestPidfile:
    def test_write_and_read(self, app_module):
        app_module._write_server_pidfile(4242, "org/model", True)
        data = json.loads(app_module._server_pidfile().read_text(encoding="utf-8"))
        assert data["pid"] == 4242
        assert data["repo"] == "org/model"
        assert data["background"] is True
        assert data["port"] == app_module.SERVER_PORT
        assert data["started_at"]

    def test_pidfile_is_0600(self, app_module):
        import stat

        app_module._write_server_pidfile(4242, "org/model", False)
        mode = stat.S_IMODE(os.stat(app_module._server_pidfile()).st_mode)
        assert mode == 0o600

    def test_clear_removes_it(self, app_module):
        app_module._write_server_pidfile(4242, "org/model", True)
        app_module._clear_server_pidfile()
        assert not app_module._server_pidfile().exists()

    def test_clear_is_idempotent(self, app_module):
        app_module._clear_server_pidfile()
        app_module._clear_server_pidfile()

    def test_clear_with_only_pid_keeps_a_different_pid(self, app_module):
        """A watcher for a replaced server must not delete its successor's file."""
        app_module._write_server_pidfile(9999, "org/new", True)
        app_module._clear_server_pidfile(only_pid=1111)
        assert app_module._server_pidfile().exists()

    def test_clear_with_only_pid_removes_a_matching_pid(self, app_module):
        app_module._write_server_pidfile(9999, "org/new", True)
        app_module._clear_server_pidfile(only_pid=9999)
        assert not app_module._server_pidfile().exists()

    def test_clear_with_only_pid_survives_a_corrupt_file(self, app_module):
        app_module._server_pidfile().write_text("not json", encoding="utf-8")
        app_module._clear_server_pidfile(only_pid=1)
        assert app_module._server_pidfile().exists()


class TestRestoreServerState:
    def test_adopts_a_live_pid(self, app_module, monkeypatch):
        proc = spawn_fake_server()
        app_module._write_server_pidfile(proc.pid, "org/model", True)
        monkeypatch.setattr(app_module, "_watch_server", lambda *a, **k: None)
        try:
            app_module.restore_server_state()
        finally:
            proc.kill()
            proc.wait()
        assert app_module.STATE.server_pid == proc.pid
        assert app_module.STATE.server_repo == "org/model"
        assert app_module.STATE.server_background is True
        assert app_module.STATE.server_started_at

    def test_drops_a_dead_pid(self, app_module, monkeypatch):
        app_module._write_server_pidfile(4194303, "org/model", True)
        monkeypatch.setattr(app_module, "_watch_server", lambda *a, **k: None)
        app_module.restore_server_state()
        assert app_module.STATE.server_pid is None
        assert not app_module._server_pidfile().exists()

    def test_no_pidfile_is_a_noop(self, app_module):
        app_module.restore_server_state()
        assert app_module.STATE.server_pid is None

    def test_corrupt_pidfile_is_removed(self, app_module):
        app_module._server_pidfile().write_text("{", encoding="utf-8")
        app_module.restore_server_state()
        assert not app_module._server_pidfile().exists()

    def test_pidfile_without_pid_key_is_removed(self, app_module):
        app_module._server_pidfile().write_text('{"repo": "x"}', encoding="utf-8")
        app_module.restore_server_state()
        assert not app_module._server_pidfile().exists()


class TestStopServer:
    def test_stop_with_nothing_running(self, app_module):
        app_module.stop_server()
        assert app_module.STATE.server_proc is None
        assert app_module.STATE.server_pid is None

    def test_stop_terminates_a_real_child(self, app_module, monkeypatch):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        app_module.STATE.server_proc = proc
        app_module.STATE.server_pid = proc.pid
        app_module.STATE.server_repo = "org/model"
        app_module._write_server_pidfile(proc.pid, "org/model", False)

        app_module.stop_server()

        assert proc.poll() is not None
        assert app_module.STATE.server_proc is None
        assert app_module.STATE.server_pid is None
        assert app_module.STATE.server_down_notice is None
        assert not app_module._server_pidfile().exists()

    def test_stop_kills_a_detached_pid(self, app_module):
        proc = spawn_fake_server(start_new_session=True)
        app_module.STATE.server_pid = proc.pid
        app_module.STATE.server_repo = "org/model"

        app_module.stop_server()
        proc.wait(timeout=10)
        assert proc.poll() is not None


class TestWatchServer:
    def test_records_a_down_notice_when_the_child_exits(self, app_module):
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=10)
        app_module.STATE.server_pid = proc.pid
        app_module.STATE.server_proc = proc
        app_module.STATE.server_repo = "org/model"
        app_module.STATE.server_stopping = False

        app_module._watch_server(proc.pid, "org/model", proc)

        assert app_module.STATE.server_proc is None
        assert app_module.STATE.server_pid is None
        assert "stopped unexpectedly" in app_module.STATE.server_down_notice
        # The notice names the short model name, not the full repo id.
        assert "model" in app_module.STATE.server_down_notice
        assert app_module.STATE.server_last_error

    def test_does_not_act_when_stopping_deliberately(self, app_module):
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=10)
        app_module.STATE.server_stopping = True
        app_module.STATE.server_down_notice = None
        try:
            app_module._watch_server(proc.pid, "org/model", proc)
        finally:
            app_module.STATE.server_stopping = False
        assert app_module.STATE.server_down_notice is None

    def test_does_not_clobber_a_successor(self, app_module):
        """A restart installs a new pid before the old watcher wakes."""
        proc = subprocess.Popen(
            [sys.executable, "-c", "pass"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        proc.wait(timeout=10)
        app_module.STATE.server_pid = 4242  # the successor
        app_module.STATE.server_stopping = False
        app_module.STATE.server_down_notice = None

        app_module._watch_server(proc.pid, "org/model", proc)

        assert app_module.STATE.server_pid == 4242
        assert app_module.STATE.server_down_notice is None


class TestSnapshotServerBlock:
    def test_running_reflects_a_live_pid(self, app_module):
        app_module.STATE.server_pid = os.getpid()
        app_module.STATE.server_repo = "org/model"
        snapshot = app_module.STATE.snapshot()
        assert snapshot["server"]["running"] is True
        assert snapshot["server"]["pid"] == os.getpid()

    def test_not_running_without_a_pid(self, app_module):
        snapshot = app_module.STATE.snapshot()
        assert snapshot["server"]["running"] is False
        assert snapshot["server"]["pid"] is None

    def test_down_notice_is_exposed(self, app_module):
        app_module.STATE.server_down_notice = "boom"
        assert app_module.STATE.snapshot()["server"]["down_notice"] == "boom"


CODER = "mlx-community/Qwen2.5-Coder-7B-Instruct-4bit"
DISTILL = "mlx-community/DeepSeek-R1-Distill-Qwen-7B-4bit"
TOOL_MODEL = "mlx-community/Qwen2.5-7B-Instruct-1M-4bit"


class TestServerLaunch:
    @pytest.mark.parametrize("background", [False, True])
    def test_command_raises_the_default_token_limit(self, app_module, monkeypatch, background):
        """mlx_lm cuts replies at 512 tokens when a client sends no max_tokens."""
        seen = []

        def fake_popen(cmd, **kwargs):
            seen.append(cmd)
            raise OSError("not started in tests")

        monkeypatch.setattr(app_module, "stop_server", lambda *a, **k: None)
        monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
        app_module.worker_server_start("org/model", background)
        assert seen[0][seen[0].index("--max-tokens") + 1] == "8192"


class TestToolSupport:
    def test_only_proven_models_are_marked(self, app_module):
        assert app_module.tool_support() == {CODER: False, DISTILL: False, TOOL_MODEL: True}

    def test_status_carries_the_marks(self, app_module):
        server = app_module.STATE.snapshot()["server"]
        assert server["tool_support"] == app_module.tool_support()

    def test_server_view_has_a_hidden_warning(self):
        html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
        assert 'id="serverToolsWarn" hidden' in html
        # The tool warning names no client; Claude Code appears only in the client settings.
        warning = html[html.index('id="serverToolsWarn"') :].split('<div class="panel">')[0]
        assert "Claude Code" not in warning

    def test_script_drives_the_warning_from_status(self):
        script = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
        assert "tool_support" in script
        assert "#serverToolsWarn" in script


# F2: a server without the guard is shown, not hidden ------------------------ #
class TestUnguardedServer:
    TAIL = "--model org/model --host 127.0.0.1 --port 8080"
    OLD = f"/usr/bin/python3 -m mlx_lm server {TAIL}"
    NEW = f"/usr/bin/python3 /Apps/Resources/mlx_guard.py {TAIL}"

    @pytest.fixture
    def adopt(self, app_module, monkeypatch):
        """Restore from a pidfile whose process has the given command line."""
        started = []
        monkeypatch.setattr(app_module, "hardware", lambda: {"chip": "M", "ram": "32 GB"})
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: pid == 4242)
        monkeypatch.setattr(app_module, "_watch_server", lambda *a, **k: None)
        monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: started.append(a))
        monkeypatch.setattr(app_module.os, "kill", lambda *a: started.append(a))

        def run(command):
            monkeypatch.setattr(
                app_module, "_ps_field", lambda pid, f: command if f == "command" else "now"
            )
            app_module._write_server_pidfile(4242, "org/model", True)
            app_module.restore_server_state()
            return app_module.STATE.snapshot()["server"]

        run.touched = started
        return run

    def test_an_old_server_is_adopted_and_marked(self, app_module, adopt):
        server = adopt(self.OLD)
        assert (server["running"], server["unguarded"], server["repo"]) == (True, True, "org/model")
        assert adopt.touched == [], "it must not be restarted or signalled by itself"
        warned = [e for e in app_module.read_app_log() if e["level"] == "warning"]
        assert any("without the request guard" in e["message"] for e in warned)
        assert any("without the request guard" in e["detail"] for e in app_module.read_activity())

    def test_a_guarded_server_is_not_marked(self, adopt):
        server = adopt(self.NEW)
        assert (server["running"], server["unguarded"]) == (True, False)

    def test_a_server_that_is_gone_is_not_marked(self, app_module, adopt, monkeypatch):
        adopt(self.OLD)
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: False)
        assert app_module.STATE.snapshot()["server"]["unguarded"] is False

    def test_stop_clears_the_mark(self, app_module, adopt, monkeypatch):
        adopt(self.OLD)
        monkeypatch.setattr(app_module, "_is_our_server", lambda *a, **k: False)
        app_module.stop_server()
        assert app_module.STATE.server_unguarded is False

    def test_the_watcher_clears_the_mark_when_it_dies(self, app_module, monkeypatch):
        app_module.STATE.server_pid = 4194303
        app_module.STATE.server_unguarded = True
        app_module._watch_server(4194303, "org/model", None)
        assert app_module.STATE.server_unguarded is False

    def test_restart_replaces_it_with_a_guarded_server(self, app_module, adopt, monkeypatch):
        adopt(self.OLD)
        signalled, commands = [], []
        alive = {4242}

        def kill(pid, sig):
            signalled.append((pid, sig))
            alive.discard(pid)

        class Child:
            pid, returncode, stdout = 4343, None, None

            def poll(self):
                return None

        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: pid in alive or pid == 4343)
        monkeypatch.setattr(app_module.os, "kill", kill)
        monkeypatch.setattr(
            app_module.subprocess, "Popen", lambda cmd, **k: commands.append(cmd) or Child()
        )
        app_module.worker_server_start("org/model", True)
        server = app_module.STATE.snapshot()["server"]
        assert signalled == [(4242, signal.SIGTERM)]
        assert commands[0][1] == str(app_module.GUARD_SCRIPT)
        assert (server["pid"], server["unguarded"]) == (4343, False)
