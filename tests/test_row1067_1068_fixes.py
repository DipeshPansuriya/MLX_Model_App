"""Row 1067: an MLX start checks its port first. Row 1068: a long Test Connection
is started and polled, so no single request outlives the window's fetch limit."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request

import pytest
from test_row892_connection import SCREEN, UI, post
from test_ui_behaviour import needs_node, run

import engine_runtime as runtime

PAGE = (UI / "index.html").read_text("utf-8")


def get_json(base_url, path="/api/server/test"):
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url + path), timeout=10) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


class TestMlxPort:
    def test_another_program_on_the_port_refuses_the_start(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module.engine_runtime, "listeners", lambda port, **k: {4242})
        why = app_module._engine_refusal("mlx", ["org/run"])
        assert "port 8080" in why

    def test_the_server_being_replaced_may_hold_it(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module.engine_runtime, "listeners", lambda port, **k: {4242})
        app_module.STATE.server_pid = 4242
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: pid == 4242)
        assert app_module._engine_refusal("mlx", ["org/run"]) is None

    def test_without_the_port_check_nothing_is_asked(self, app_module, monkeypatch):
        monkeypatch.setattr(runtime, "port_problem", lambda *a, **k: pytest.fail("asked"))
        assert app_module._engine_refusal("mlx", ["org/run"], port_too=False) is None

    def test_a_refused_start_stops_and_launches_nothing(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module.engine_runtime, "listeners", lambda port, **k: {4242})
        for name in ("stop_server", "_spawn_server"):
            monkeypatch.setattr(app_module, name, lambda *a, **k: pytest.fail("touched"))
        app_module.worker_server_start("org/run")
        assert "port 8080" in app_module.STATE.server_down_notice


class TestLongTestConnection:
    @pytest.fixture
    def slow(self, app_module, monkeypatch):
        release = threading.Event()

        def answer():
            release.wait(10)
            return 200, {"ok": True, "pid": 7}

        monkeypatch.setattr(app_module, "connection_test", answer)
        monkeypatch.setattr(app_module, "CONNECTION_TEST_HOLD_SECONDS", 0.05)
        yield release
        release.set()

    def test_one_request_is_not_held_for_the_whole_test(self, app_module, base_url, slow):
        assert app_module.CONNECTION_TEST_HOLD_SECONDS < 30
        assert post(base_url) == (202, {"running": True})
        assert get_json(base_url) == (202, {"running": True})
        assert post(base_url)[0] == 409  # one at a time
        slow.set()
        app_module.CONNECTION_TEST_RUNS[0].done.wait(5)
        assert get_json(base_url) == (200, {"ok": True, "pid": 7})

    def test_nothing_to_read_before_a_test(self, base_url):
        assert get_json(base_url)[0] == 404

    def test_the_result_needs_the_token(self, app_module, base_url, slow, monkeypatch):
        monkeypatch.setattr(app_module, "API_TOKEN", "another")
        monkeypatch.setattr(app_module.Handler, "_has_token", lambda self: False)
        assert get_json(base_url)[0] == 403

    def test_an_unexpected_error_ends_the_run(self, app_module, base_url, monkeypatch):
        def broken():
            raise KeyError("secret-detail")

        monkeypatch.setattr(app_module, "connection_test", broken)
        status, answer = post(base_url)
        assert status == 500 and "secret-detail" not in answer["error"]


@needs_node
class TestScreen:
    def test_the_screen_polls_until_the_result(self, tmp_path):
        out = run(
            tmp_path,
            SCREEN
            + """
            render(live());
            let asked = 0;
            respond = (path, opts) => {
              if (path !== "/api/server/test") return new Promise(() => {});
              const body = ++asked < 3 ? { running: true } : result;
              return Promise.resolve({ ok: true, json: async () => body });
            };
            await testConnection();
            return [asked, seen(), calls.filter((c) => c.path === "/api/server/test")
              .map((c) => c.opts?.method || "GET")];
            """,
        )
        asked, after, methods = out
        assert asked == 3 and "Passed" in after["reply"]
        assert methods[0] == "POST" and "POST" not in methods[1:]

    def test_the_busy_button_keeps_keyboard_focus(self, tmp_path):
        out = run(
            tmp_path,
            SCREEN
            + """
            render(live()); waiting(); press();
            const b = q("#testBtn");
            return [b.disabled === true, b.getAttribute("aria-disabled")];
            """,
        )
        assert out == [False, "true"]


def test_the_live_region_is_always_rendered():
    region = re.search(r'<[^>]*id="testLive"[^>]*>', PAGE).group(0)
    assert 'role="status"' in region and "hidden" not in region
    results = re.search(r'<[^>]*id="testResults"[^>]*>', PAGE).group(0)
    assert "role=" not in results
    assert PAGE.index('id="testLive"') < PAGE.index('id="testResults"')
    button = re.search(r'<[^>]*id="testBtn"[^>]*>', PAGE).group(0)
    assert 'aria-disabled="true"' in button and not re.search(r"\sdisabled\b", button)
