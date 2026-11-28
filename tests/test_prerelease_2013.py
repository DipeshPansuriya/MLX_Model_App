"""Defects found before the 2.0.13 release that still apply to the direct server."""

# ruff: noqa: F811 - the imported fixtures are used by name
from __future__ import annotations

import io
import os
import re
import socket
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_row837_chat import ONE, PID, running
from test_row837_ui import ui as chat_ui
from test_row848_loadtest import lt, run_job  # noqa: F401
from test_ui_behaviour import needs_node

import engine_runtime

ROOT = Path(__file__).resolve().parent.parent


# L2. The test server's port belongs to the child ------------------------------- #
class TestPortOwner:
    def test_a_listener_is_recognised_by_its_pid(self):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            port = sock.getsockname()[1]
            assert engine_runtime.listener_is(port, os.getpid()) is True
            assert engine_runtime.listener_is(port, os.getppid()) is False

    def test_another_programs_answer_is_not_trusted(self, lt, tmp_path, monkeypatch):
        posts = []

        class Intruder(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                posts.append(self.path)
                self.send_error(500)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Intruder)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            monkeypatch.setattr(lt, "free_port", lambda: httpd.server_address[1])
            status, reason, _, _, spawned = run_job(lt, tmp_path, mode="no-listen", sizes=(4096,))
        finally:
            httpd.shutdown()
            httpd.server_close()
        assert posts == []
        assert status == "failed" and "another program" in reason
        assert len(spawned) == lt.PORT_ATTEMPTS


# I1 and I2. The chat proxy ---------------------------------------------------- #
class Stream(io.BytesIO):
    status = 200


class TestChatProxy:
    @pytest.mark.parametrize("bad", ["x/y", "5", "", "1.5/3", "²/3"])
    def test_a_bad_keepalive_does_not_end_the_reply(self, app_module, bad):
        lines = (
            f": keepalive {bad}\n\n"
            'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        conn = types.SimpleNamespace(
            request=lambda *a: None, getresponse=lambda: Stream(lines.encode())
        )
        said = []
        app_module._read_reply(conn, b"{}", said.append)
        assert [kind for kind, _ in said] == ["token", "end"]

    def test_an_adopted_pid_that_is_not_our_server_is_not_asked(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "_pid_alive", app_module._pid_alive)
        running(app_module, owned=False)
        asked = []
        monkeypatch.setattr(app_module, "_is_our_server", lambda *a, **k: asked.append(a) or False)
        assert "Start the API server first" in app_module._chat_problem(ONE)
        assert asked and asked[0][:2] == (PID, ONE)

    def test_our_adopted_server_is_asked(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "_pid_alive", app_module._pid_alive)
        running(app_module, owned=False)
        monkeypatch.setattr(app_module, "_is_our_server", lambda *a, **k: True)
        assert app_module._chat_problem(ONE) is None

    def test_an_ended_child_is_not_asked(self, app_module):
        running(app_module)
        app_module.STATE.server_proc.poll = lambda: 1
        assert "Start the API server first" in app_module._chat_problem(ONE)


# 7. The card and the page ------------------------------------------------------ #
class TestCard:
    def test_chat_only_hints_are_described(self):
        html = (ROOT / "ui" / "index.html").read_text("utf-8")
        hints = re.findall(r'<span class="hint"( id="([\w-]+)")?>Chat only', html)
        assert len(hints) >= 3
        for _, name in hints:
            assert name, "a Chat only hint has no id"
            assert re.search(rf'aria-describedby="[^"]*\b{name}\b', html), name


# Browser test defects D1 to D7 and D9 ------------------------------------------ #
STOPPED = '["done", { tokens: 1, tps: 1, stopped: true }]'


@needs_node
class TestBrowserDefects:
    def test_d1_the_second_click_of_send_does_not_stop(self, tmp_path):
        out = chat_ui(
            tmp_path,
            """
            show(srv()); respond = sse([]);
            wire(); const stop = el("#stopBtn").listeners.click;
            const stops = () => calls.filter((c) => c.path === "/api/chat/stop").length;
            await stop({ detail: 2 }); const second = stops();
            await stop({ detail: 1 }); await stop({ detail: 0 });
            return [second, stops()];
            """,
        )
        assert out == [0, 2]

    def test_d2_a_stopped_reply_is_said_as_stopped(self, tmp_path):
        out = chat_ui(tmp_path, f'return say([["token", "a"], {STOPPED}]);')
        assert out["announced"] == "Reply stopped"

    def test_d3_focus_leaves_the_chats_stop_button_for_the_input(self, tmp_path):
        out = chat_ui(
            tmp_path,
            f"""
            show(srv()); respond = sse([["token", "a"], {STOPPED}]);
            el("#input").value = "hi"; const sent = sendMessage();
            el("#stopBtn").focus(); await sent;
            return document.activeElement === el("#input");
            """,
        )
        assert out is True

    def test_d3_focus_leaves_the_load_tests_stop_button(self, tmp_path):
        out = chat_ui(
            tmp_path,
            """
            const paint = (s) => { state.snapshot = snap({ server: s }); render(state.snapshot); };
            paint(srv()); const stop = el("#ltStopServerBtn"); stop.focus();
            paint(off());
            return [stop.hidden, document.activeElement !== stop,
              document.activeElement === el("#ltStartBtn")
              || document.activeElement === el("#ltModelSelectSearch")];
            """,
        )
        assert out == [True, True, True]

    def test_d7_a_failed_load_is_said_on_both_pages(self, tmp_path):
        out = chat_ui(
            tmp_path,
            """
            show(srv({ load_failed: true, ready: false }));
            return [el("#input").placeholder, html("#serverState"),
              el("#navServerText").textContent, el("#navServerDot").className];
            """,
        )
        assert "Start the API server first" not in out[0] and "Stop, then Start" in out[0]
        assert "dot-bad" in out[1] and "Model not loaded" in out[1]
        assert out[2] == "model not loaded" and "dot-bad" in out[3]


class TestBrowserDefectsInPython:
    def test_d4_a_cancel_marks_the_remaining_sizes_cancelled(self, lt, tmp_path):
        cancel = threading.Event()
        cancel.set()
        status, _, rows, _, _ = run_job(lt, tmp_path, cancel=cancel, sizes=(4096, 8192))
        assert status == "cancelled"
        for row in rows.values():
            assert (row["status"], row["reason"]) == ("cancelled", "cancelled by you")
