"""H1/H2: the local API only serves its own UI. H3: only our server is signalled."""

from __future__ import annotations

import http.client
import inspect
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def request(server, method, path, body=None, headers=None, host=None):
    port = server.server_address[1]
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path, skip_host=True)
        conn.putheader("Host", host or f"127.0.0.1:{port}")
        for key, value in (headers or {}).items():
            conn.putheader(key, value)
        if body is not None and "Content-Length" not in (headers or {}):
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        return resp.status, resp.headers, resp.read()
    finally:
        conn.close()


def good_headers(app_module):
    return {"Content-Type": "application/json", "X-MLX-Token": app_module.API_TOKEN}


class TestHost:
    @pytest.mark.parametrize("path", ["/api/logs", "/api/logs/export", "/api/environment", "/"])
    def test_foreign_host_is_rejected(self, server, path):
        port = server.server_address[1]
        status, _, body = request(server, "GET", path, host=f"evil.example:{port}")
        assert status == 403
        assert b"entries" not in body

    def test_wrong_port_is_rejected(self, server):
        assert request(server, "GET", "/api/ping", host="127.0.0.1:1")[0] == 403

    def test_localhost_is_allowed(self, server):
        port = server.server_address[1]
        assert request(server, "GET", "/api/ping", host=f"localhost:{port}")[0] == 200

    def test_foreign_host_post_is_rejected_even_with_the_token(self, server, app_module):
        status = request(
            server, "POST", "/api/unload", b"{}", good_headers(app_module), host="evil.example"
        )[0]
        assert status == 403


class TestCrossSitePost:
    def test_text_plain_cross_site_delete_keeps_the_model(self, server, app_module):
        model = app_module.CACHE_DIR / "models--org--model"
        model.mkdir(parents=True)
        status = request(
            server,
            "POST",
            "/api/delete",
            json.dumps({"repo": "org/model"}).encode(),
            {"Origin": "https://evil.example", "Content-Type": "text/plain"},
        )[0]
        assert status == 403
        assert model.exists()

    @pytest.mark.parametrize("origin", ["https://evil.example", "null", "http://127.0.0.1:1"])
    def test_foreign_origin_is_rejected_even_with_the_token(self, server, app_module, origin):
        headers = {**good_headers(app_module), "Origin": origin}
        assert request(server, "POST", "/api/unload", b"{}", headers)[0] == 403

    def test_non_json_content_type_is_415(self, server, app_module):
        headers = {**good_headers(app_module), "Content-Type": "text/plain"}
        assert request(server, "POST", "/api/unload", b"{}", headers)[0] == 415

    @pytest.mark.parametrize("token", [None, "", "wrong"])
    def test_post_without_the_launch_token_is_rejected(self, server, token):
        headers = {"Content-Type": "application/json"}
        if token is not None:
            headers["X-MLX-Token"] = token
        assert request(server, "POST", "/api/logs/clear", b"{}", headers)[0] == 403

    def test_own_ui_post_is_accepted(self, server, app_module):
        port = server.server_address[1]
        headers = {**good_headers(app_module), "Origin": f"http://127.0.0.1:{port}"}
        assert request(server, "POST", "/api/unload", b"{}", headers)[0] == 200

    def test_focus_needs_no_token_but_stays_same_origin(self, server):
        json_type = {"Content-Type": "application/json"}
        assert request(server, "POST", "/api/focus", b"{}", json_type)[0] == 200
        evil = {**json_type, "Origin": "https://evil.example"}
        assert request(server, "POST", "/api/focus", b"{}", evil)[0] == 403
        assert (
            request(server, "POST", "/api/focus", b"{}", {"Content-Type": "text/plain"})[0] == 415
        )


def routes(app_module, method):
    """Every /api path a handler method names, so a new route is covered too."""
    source = inspect.getsource(getattr(app_module.Handler, method))
    return sorted(set(re.findall(r'"(/api/[a-z0-9/_-]+)"', source)))


class TestTokenDelivery:
    @pytest.mark.parametrize("path", ["/", "/index.html", "/app.js", "/style.css"])
    def test_no_served_file_carries_the_token(self, server, app_module, path):
        status, headers, body = request(server, "GET", path)
        assert status == 200
        for secret in (app_module.API_TOKEN.encode(), b"__MLX_TOKEN__"):
            assert secret not in body
        assert b'name="mlx-token"' not in body
        assert headers["X-Frame-Options"] == "DENY"
        assert "frame-ancestors 'none'" in headers["Content-Security-Policy"]

    def test_without_the_token_every_api_get_is_refused(self, server, app_module):
        paths = [p for p in routes(app_module, "do_GET") if p != "/api/ping"]
        assert "/api/state" in paths and len(paths) >= 8
        for path in paths:
            assert request(server, "GET", path)[0] == 403, path

    def test_without_the_token_every_api_post_is_refused(self, server, app_module):
        paths = [p for p in routes(app_module, "do_POST") if p != "/api/focus"]
        assert "/api/delete" in paths and len(paths) >= 10
        json_type = {"Content-Type": "application/json"}
        for path in paths:
            assert request(server, "POST", path, b"{}", json_type)[0] == 403, path

    def test_token_is_long_and_new_each_launch(self, app_module):
        assert len(app_module.API_TOKEN) >= 32

    def test_the_refusal_says_how_to_get_a_new_session(self, server):
        body = request(server, "GET", "/api/state")[2]
        assert json.loads(body)["error"] == (
            "invalid session token; open MLX Studio again from its icon"
        )


def with_origins(server, app_module, method, path, origins):
    """A request with the token and one Origin header per value, repeats included."""
    port = server.server_address[1]
    own = f"http://127.0.0.1:{port}"
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, path)
        for key, value in good_headers(app_module).items():
            conn.putheader(key, value)
        for origin in origins:
            conn.putheader("Origin", own if origin == "own" else origin)
        body = b"{}" if method == "POST" else None
        if body:
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        resp = conn.getresponse()
        return resp.status, resp.read()
    finally:
        conn.close()


CALLS = [("GET", "/api/logs"), ("POST", "/api/unload")]
EVIL = "https://evil.example"


class TestOrigin:
    @pytest.mark.parametrize(("method", "path"), CALLS)
    @pytest.mark.parametrize(
        "origins",
        [["own", EVIL], [EVIL, "own"], ["own", "null"], ["own", ""], ["null"], [""], [EVIL]],
    )
    def test_one_foreign_value_refuses_the_request(self, server, app_module, method, path, origins):
        status, body = with_origins(server, app_module, method, path, origins)
        assert status == 403
        assert json.loads(body) == {"error": "forbidden origin"}

    @pytest.mark.parametrize(("method", "path"), CALLS)
    @pytest.mark.parametrize("origins", [["own", "own"], ["own"], []])
    def test_own_or_no_origin_is_allowed(self, server, app_module, method, path, origins):
        assert with_origins(server, app_module, method, path, origins)[0] == 200


def fake_server(repo="org/model", port=8080):
    """A harmless child whose command line looks like our API server."""
    tail = ["-m", "mlx_lm", "server", "--model", repo]
    tail += ["--host", "127.0.0.1", "--port", str(port)]
    return subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)", *tail],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


@pytest.fixture
def child():
    procs = []

    def start(proc):
        procs.append(proc)
        return proc

    yield start
    for proc in procs:
        proc.kill()
        proc.wait()


def sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])


class TestPidIdentity:
    def test_stale_pidfile_does_not_adopt_or_kill_an_unrelated_process(self, app_module, child):
        proc = child(sleeper())
        app_module._server_pidfile().write_text(
            json.dumps({"pid": proc.pid, "repo": "org/model", "background": False}),
            encoding="utf-8",
        )
        app_module.restore_server_state()
        app_module.stop_server()
        assert app_module.STATE.server_pid is None
        assert not app_module._server_pidfile().exists()
        assert proc.poll() is None

    def test_stop_never_signals_an_unrelated_pid(self, app_module, child):
        proc = child(sleeper())
        app_module.STATE.server_pid = proc.pid
        app_module.STATE.server_repo = "org/model"
        app_module.stop_server()
        assert proc.poll() is None
        assert app_module.STATE.server_pid is None

    def test_a_different_start_time_is_not_ours(self, app_module, child):
        proc = child(fake_server())
        app_module._write_server_pidfile(proc.pid, "org/model", True)
        data = json.loads(app_module._server_pidfile().read_text(encoding="utf-8"))
        assert data["started"]
        data["started"] = "Mon Jan  1 00:00:00 2001"
        app_module._server_pidfile().write_text(json.dumps(data), encoding="utf-8")
        app_module.restore_server_state()
        assert app_module.STATE.server_pid is None
        assert proc.poll() is None

    def test_a_different_model_is_not_ours(self, app_module, child):
        proc = child(fake_server("org/other"))
        app_module._write_server_pidfile(proc.pid, "org/model", True)
        app_module.restore_server_state()
        assert app_module.STATE.server_pid is None

    def test_matching_server_is_adopted_and_stopped(self, app_module, monkeypatch, child):
        proc = child(fake_server())
        app_module._write_server_pidfile(proc.pid, "org/model", True)
        monkeypatch.setattr(app_module, "_watch_server", lambda *a, **k: None)
        app_module.restore_server_state()
        assert app_module.STATE.server_pid == proc.pid
        app_module.stop_server()
        proc.wait(timeout=10)

    def test_pidfile_from_an_older_release_is_matched_by_command_line(
        self, app_module, monkeypatch, child
    ):
        proc = child(fake_server())
        app_module._server_pidfile().write_text(
            json.dumps({"pid": proc.pid, "repo": "org/model", "background": True, "port": 8080}),
            encoding="utf-8",
        )
        monkeypatch.setattr(app_module, "_watch_server", lambda *a, **k: None)
        app_module.restore_server_state()
        assert app_module.STATE.server_pid == proc.pid


class TestVersion:
    def test_release_version(self, app_module):
        assert app_module.APP_VERSION == "2.0.12"


# F3: exactly one Host header ------------------------------------------------ #
@pytest.mark.parametrize("method", ["GET", "POST"])
@pytest.mark.parametrize("order", [("good", "evil"), ("evil", "good"), ("good", "good")])
def test_two_host_headers_are_refused(server, app_module, method, order):
    port = server.server_address[1]
    hosts = {"good": f"127.0.0.1:{port}", "evil": "evil.example"}
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.putrequest(method, "/api/ping" if method == "GET" else "/api/unload", skip_host=True)
        for name in order:
            conn.putheader("Host", hosts[name])
        body = b"{}" if method == "POST" else None
        if body:
            for key, value in good_headers(app_module).items():
                conn.putheader(key, value)
            conn.putheader("Content-Length", str(len(body)))
        conn.endheaders(body)
        assert conn.getresponse().status == 403
    finally:
        conn.close()


# F4: every /api GET needs the token ----------------------------------------- #
API_GETS = [
    "/api/state",
    "/api/models",
    "/api/search?q=qwen",
    "/api/logs",
    "/api/logs/files",
    "/api/logs/app?limit=3",
    "/api/logs/export",
    "/api/environment",
    "/api/catalog",
    "/api/versions",
    "/api/not-a-route",
]


@pytest.fixture
def side_effects(app_module, monkeypatch):
    """Records the GET handlers that write a file or call out; none really do."""
    ran = []
    monkeypatch.setattr(app_module, "search_hf", lambda q: ran.append("search") or [])
    monkeypatch.setattr(app_module, "worker_check_versions", lambda: ran.append("versions"))
    monkeypatch.setattr(
        app_module,
        "build_diagnostic_bundle",
        lambda: ran.append("export") or (b"zip", "d.zip"),
    )
    return ran


class TestGetNeedsTheToken:
    @pytest.mark.parametrize("sent", [{}, {"X-MLX-Token": ""}, {"X-MLX-Token": "wrong"}])
    @pytest.mark.parametrize("path", API_GETS)
    def test_refused_without_it_and_nothing_runs(self, server, side_effects, path, sent):
        status, _, body = request(server, "GET", path, headers=sent)
        assert (status, list(json.loads(body))) == (403, ["error"])
        assert side_effects == []

    @pytest.mark.parametrize("path", API_GETS[:-1])
    def test_answered_with_it(self, server, app_module, side_effects, path):
        sent = {"X-MLX-Token": app_module.API_TOKEN}
        assert request(server, "GET", path, headers=sent)[0] == 200

    def test_ping_stays_open_and_says_nothing_private(self, server, app_module):
        status, _, body = request(server, "GET", "/api/ping")
        assert status == 200
        assert json.loads(body) == {"ok": True, "app": app_module.APP_VERSION}

    @pytest.mark.parametrize("path", ["/", "/app.js", "/style.css"])
    def test_static_files_stay_open(self, server, path):
        assert request(server, "GET", path)[0] == 200

    def test_a_second_launch_still_finds_and_focuses_this_one(
        self, server, app_module, monkeypatch
    ):
        class Window:
            raised = False

            def restore(self):
                pass

            def show(self):
                self.raised = True

        window = Window()
        monkeypatch.setattr(app_module, "PORT", server.server_address[1])
        monkeypatch.setattr(app_module.STATE, "window", window)
        assert app_module.instance_is_running() is True
        assert app_module.request_focus_from_running_instance() is True
        assert window.raised


class TestNoSniff:
    @pytest.mark.parametrize(
        ("method", "path", "status"),
        [
            ("GET", "/", 200),
            ("GET", "/app.js", 200),
            ("GET", "/missing", 404),
            ("GET", "/api/ping", 200),
            ("GET", "/api/logs", 403),
            ("PUT", "/api/logs", 501),
        ],
    )
    def test_every_reply(self, server, method, path, status):
        got, headers, _ = request(server, method, path)
        assert (got, headers.get_all("X-Content-Type-Options")) == (status, ["nosniff"])

    def test_the_chat_stream(self, server, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "stream_chat", lambda *a: iter([("done", {})]))
        app_module.STATE.model, app_module.STATE.loaded_repo = object(), "org/model"
        body = json.dumps({"messages": [{"role": "user", "content": "hi"}]}).encode()
        got, headers, _ = request(server, "POST", "/api/chat", body, good_headers(app_module))
        assert (got, headers["X-Content-Type-Options"]) == (200, "nosniff")


def test_log_wording_does_not_claim_prompts_are_recorded():
    app_source = (ROOT / "app.py").read_text("utf-8")
    page = " ".join((ROOT / "ui" / "index.html").read_text("utf-8").split())
    assert "logs record prompts" not in app_source
    assert "Prompts live in activity.log" not in app_source
    assert (
        "It includes model names, file paths, error messages and server output."
        " Review before sharing." in page
    )
    assert "Prompts are included" not in page
    assert ">Download installer</button>" in page and "Download DMG" not in page


def test_chat_text_is_not_written_to_the_logs(server, app_module, monkeypatch):
    done = [("token", "secret-reply"), ("done", {"tokens": 1, "tps": 2})]
    monkeypatch.setattr(app_module, "stream_chat", lambda *a: iter(done))
    app_module.STATE.model, app_module.STATE.loaded_repo = object(), "org/model"
    body = json.dumps({"messages": [{"role": "user", "content": "secret-prompt"}]}).encode()
    assert request(server, "POST", "/api/chat", body, good_headers(app_module))[0] == 200
    written = "".join(p.read_text("utf-8") for p in app_module.LOG_DIR.iterdir() if p.is_file())
    assert "1 tokens" in written
    assert "secret-prompt" not in written and "secret-reply" not in written
