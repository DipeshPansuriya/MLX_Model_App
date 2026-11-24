"""API server lifecycle: pidfile, watchdog, adoption and stop."""

from __future__ import annotations

import json
import os
import subprocess
import sys


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
        app_module._write_server_pidfile(os.getpid(), "org/model", True)
        monkeypatch.setattr(app_module, "_watch_server", lambda *a, **k: None)
        app_module.restore_server_state()
        assert app_module.STATE.server_pid == os.getpid()
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
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )
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
