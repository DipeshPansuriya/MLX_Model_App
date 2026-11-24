"""Single-instance behaviour: the bug that produced a dead second launch.

Regression cover for the 2.0.3 defect where a second double-click started a
process that died on a busy port, and for the 2.0.4 defect where a 2.0.3
launcher met a 2.0.2 instance that has no /api/focus endpoint and exited 0 with
nothing on screen.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
LAUNCHER = ROOT / "packaging" / "launcher.py"


def load_launcher(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    spec = importlib.util.spec_from_file_location("mlxlauncher", LAUNCHER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TestAlreadyRunning:
    def test_false_when_nothing_listens(self, tmp_path, monkeypatch):
        launcher = load_launcher(tmp_path, monkeypatch)
        monkeypatch.setattr(launcher, "PORT", 1)  # nothing can be listening
        assert launcher.already_running() is False

    def test_true_against_the_live_server(self, tmp_path, monkeypatch, base_url, server):
        launcher = load_launcher(tmp_path, monkeypatch)
        host, port = server.server_address[:2]
        monkeypatch.setattr(launcher, "HOST", host)
        monkeypatch.setattr(launcher, "PORT", port)
        assert launcher.already_running() is True


class TestAskFocus:
    def test_true_when_endpoint_answers_ok(self, tmp_path, monkeypatch, server):
        launcher = load_launcher(tmp_path, monkeypatch)
        host, port = server.server_address[:2]
        monkeypatch.setattr(launcher, "HOST", host)
        monkeypatch.setattr(launcher, "PORT", port)

        import threading
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        class Ok(BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.dumps({"ok": True}).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *args):
                pass

        httpd = ThreadingHTTPServer((host, 0), Ok)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            monkeypatch.setattr(launcher, "PORT", httpd.server_address[1])
            assert launcher.ask_focus() is True
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_false_on_404_from_an_older_instance(self, tmp_path, monkeypatch, server):
        """2.0.2 answers /api/ping but 404s /api/focus — the 2.0.3 silent failure."""
        launcher = load_launcher(tmp_path, monkeypatch)
        host, port = server.server_address[:2]
        monkeypatch.setattr(launcher, "HOST", host)
        monkeypatch.setattr(launcher, "PORT", port)
        assert launcher.ask_focus() is False

    def test_false_when_nothing_listens(self, tmp_path, monkeypatch):
        launcher = load_launcher(tmp_path, monkeypatch)
        monkeypatch.setattr(launcher, "PORT", 1)
        assert launcher.ask_focus() is False


class TestAppleScriptFallback:
    def test_returns_bool_and_never_raises(self, tmp_path, monkeypatch):
        launcher = load_launcher(tmp_path, monkeypatch)
        result = launcher.raise_by_applescript()
        assert isinstance(result, bool)

    def test_false_when_osascript_is_missing(self, tmp_path, monkeypatch):
        launcher = load_launcher(tmp_path, monkeypatch)

        def boom(*args, **kwargs):
            raise FileNotFoundError("osascript")

        monkeypatch.setattr(launcher.subprocess, "run", boom)
        assert launcher.raise_by_applescript() is False

    def test_false_on_nonzero_exit(self, tmp_path, monkeypatch):
        launcher = load_launcher(tmp_path, monkeypatch)

        class Result:
            returncode = 1

        monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: Result())
        assert launcher.raise_by_applescript() is False

    def test_true_on_zero_exit(self, tmp_path, monkeypatch):
        launcher = load_launcher(tmp_path, monkeypatch)

        class Result:
            returncode = 0

        monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: Result())
        assert launcher.raise_by_applescript() is True


class TestMainSingleInstance:
    def test_second_launch_focuses_and_returns_zero(self, tmp_path, monkeypatch, server):
        launcher = load_launcher(tmp_path, monkeypatch)
        host, port = server.server_address[:2]
        monkeypatch.setattr(launcher, "HOST", host)
        monkeypatch.setattr(launcher, "PORT", port)
        monkeypatch.setattr(launcher, "ask_focus", lambda: True)

        spawned = []
        monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: spawned.append(a))

        assert launcher.main() == 0
        assert spawned == [], "must not spawn a rival process"

    def test_second_launch_uses_fallback_when_focus_fails(self, tmp_path, monkeypatch, server):
        launcher = load_launcher(tmp_path, monkeypatch)
        host, port = server.server_address[:2]
        monkeypatch.setattr(launcher, "HOST", host)
        monkeypatch.setattr(launcher, "PORT", port)
        monkeypatch.setattr(launcher, "ask_focus", lambda: False)

        called = []
        monkeypatch.setattr(launcher, "raise_by_applescript", lambda: called.append(True) or True)
        spawned = []
        monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: spawned.append(a))

        assert launcher.main() == 0
        assert called == [True], "fallback must run when /api/focus is unavailable"
        assert spawned == []

    def test_second_launch_still_returns_zero_when_everything_fails(
        self, tmp_path, monkeypatch, server
    ):
        launcher = load_launcher(tmp_path, monkeypatch)
        host, port = server.server_address[:2]
        monkeypatch.setattr(launcher, "HOST", host)
        monkeypatch.setattr(launcher, "PORT", port)
        monkeypatch.setattr(launcher, "ask_focus", lambda: False)
        monkeypatch.setattr(launcher, "raise_by_applescript", lambda: False)
        monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: pytest.fail("spawned"))

        assert launcher.main() == 0

    def test_launch_log_records_the_fallback(self, tmp_path, monkeypatch, server):
        launcher = load_launcher(tmp_path, monkeypatch)
        host, port = server.server_address[:2]
        monkeypatch.setattr(launcher, "HOST", host)
        monkeypatch.setattr(launcher, "PORT", port)
        monkeypatch.setattr(launcher, "ask_focus", lambda: False)
        monkeypatch.setattr(launcher, "raise_by_applescript", lambda: True)

        launcher.main()
        text = launcher.LOG.read_text(encoding="utf-8")
        assert "focus endpoint unavailable; applescript fallback ok=True" in text
        assert "already running; focus requested (ok=True)" in text


class TestAppSideGuards:
    def test_instance_is_running_true_against_live_server(self, app_module, server, monkeypatch):
        host, port = server.server_address[:2]
        monkeypatch.setattr(app_module, "HOST", host)
        monkeypatch.setattr(app_module, "PORT", port)
        assert app_module.instance_is_running() is True

    def test_instance_is_running_false_when_port_is_dead(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "PORT", 1)
        assert app_module.instance_is_running() is False

    def test_request_focus_true_against_live_server(self, app_module, server, monkeypatch):
        host, port = server.server_address[:2]
        monkeypatch.setattr(app_module, "HOST", host)
        monkeypatch.setattr(app_module, "PORT", port)

        class StubWindow:
            def restore(self):
                pass

            def show(self):
                pass

        app_module.STATE.window = StubWindow()
        assert app_module.request_focus_from_running_instance() is True

    def test_request_focus_false_without_a_window(self, app_module, server, monkeypatch):
        host, port = server.server_address[:2]
        monkeypatch.setattr(app_module, "HOST", host)
        monkeypatch.setattr(app_module, "PORT", port)
        app_module.STATE.window = None
        assert app_module.request_focus_from_running_instance() is False

    def test_request_focus_false_when_port_is_dead(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "PORT", 1)
        assert app_module.request_focus_from_running_instance() is False

    def test_focus_window_false_without_a_window(self, app_module):
        app_module.STATE.window = None
        assert app_module.focus_window() is False

    def test_focus_window_survives_a_broken_window(self, app_module):
        class Broken:
            def restore(self):
                raise RuntimeError("no window")

            def show(self):
                raise RuntimeError("no window")

        app_module.STATE.window = Broken()
        assert app_module.focus_window() is False
