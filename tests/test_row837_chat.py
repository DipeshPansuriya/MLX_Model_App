"""Chat goes through the running API server (row 837), and the row 835 fd leak.

The upstream is a fake on a free loopback port that answers like mlx_lm 0.32.2.
No model is loaded and nothing listens on 8080.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ONE, TWO = "org/one", "org/two"
PID = 4242


def chunk(**delta):
    finish = delta.pop("finish", None)
    choice = {"index": 0, "finish_reason": finish, "delta": {"role": "assistant", **delta}}
    return f"data: {json.dumps({'object': 'chat.completion.chunk', 'choices': [choice]})}\n\n"


USAGE = (
    'data: {"choices": [], "usage": {"prompt_tokens": 7, "completion_tokens": 3, '
    '"total_tokens": 10}}\n\n'
)
DONE = "data: [DONE]\n\n"
NORMAL = [chunk(content="Hel"), chunk(content="lo", finish="stop"), USAGE, DONE]


class Upstream:
    """What the fake server saw, and what it answers."""

    def __init__(self):
        self.status = 200
        self.body = b""
        self.script = list(NORMAL)
        self.requests = []
        self.hold = threading.Event()  # "WAIT" in the script blocks on this
        self.closed = threading.Event()  # the app closed the connection


@pytest.fixture
def upstream(app_module, monkeypatch):
    fake = Upstream()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            fake.requests.append((self.path, dict(self.headers), json.loads(raw)))
            if fake.status != 200:
                self.send_response(fake.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(fake.body)
                return
            script = list(fake.script)
            # Like mlx_lm: while another client is served, not even headers arrive.
            if script and script[0] == "BUSY":
                script.pop(0)
                self._until_released()
                if fake.closed.is_set():
                    return
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.end_headers()
            for part in script:
                if part == "WAIT":
                    self._until_released()
                    if fake.closed.is_set():
                        return
                    continue
                self.wfile.write(part.encode())
                self.wfile.flush()

        def _until_released(self):
            self.connection.settimeout(0.02)
            end = time.monotonic() + 10
            while time.monotonic() < end and not fake.hold.is_set():
                try:
                    if not self.connection.recv(1):
                        fake.closed.set()
                        return
                except TimeoutError:
                    pass
                except OSError:
                    fake.closed.set()
                    return
            self.connection.settimeout(None)

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(app_module, "SERVER_PORT", httpd.server_address[1])
    monkeypatch.setattr(app_module, "CHAT_BEAT_SECONDS", 0.05)
    try:
        yield fake
    finally:
        fake.hold.set()
        httpd.shutdown()
        httpd.server_close()


class Proc:
    pid = PID
    returncode = None

    def poll(self):
        return None


def running(app, repos=(ONE,), ready=True, owned=True):
    """A server this app started (``owned``) or adopted, as STATE describes it."""
    app.STATE.server_proc = Proc() if owned else None
    app.STATE.server_pid = PID
    app.STATE.server_repo = repos[0]
    app.STATE.server_repos = list(repos)
    if not owned:
        app._pid_alive = lambda pid: pid == PID
        app._is_our_server = lambda pid, *args, **kwargs: pid == PID
    if ready:
        app.STATE.server_verdict = "ready"


def post(base_url, path, body, headers=None):
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    return urllib.request.urlopen(request, timeout=10)


def ask(base_url, repo=ONE, messages=None):
    body = {"repo": repo, "messages": messages or [{"role": "user", "content": "hi"}]}
    return post(base_url, "/api/chat", body)


def refused(base_url, path="/api/chat", body=None, headers=None):
    body = body or {"repo": ONE, "messages": [{"role": "user", "content": "hi"}]}
    with pytest.raises(urllib.error.HTTPError) as caught:
        post(base_url, path, body, headers)
    return caught.value.code, json.loads(caught.value.read())["error"]


def frames(resp):
    """Yield (event, data) as the app sends them."""
    event = None
    for raw in resp:
        line = raw.decode().rstrip("\n")
        if line.startswith("event: "):
            event = line[7:]
        elif line.startswith("data: "):
            yield event, json.loads(line[6:])


def events(resp, *kinds):
    return [(k, d) for k, d in frames(resp) if not kinds or k in kinds]


def finished(app):
    end = time.monotonic() + 5
    while app.STATE.generating and time.monotonic() < end:
        time.sleep(0.01)
    return not app.STATE.generating


class TestProxy:
    def test_streams_the_reply_from_the_running_server(self, app_module, upstream, base_url):
        running(app_module)
        with ask(base_url) as resp:
            got = events(resp, "token", "done", "error")
        assert [d for k, d in got if k == "token"] == ["Hel", "lo"]
        kind, meta = got[-1]
        assert kind == "done"
        assert meta["tokens"] == 3
        assert meta["repo"] == ONE
        assert meta["stopped"] is False
        assert meta["truncated"] is False
        assert finished(app_module)

    def test_sends_only_role_and_content_with_the_chat_settings(
        self, app_module, upstream, base_url
    ):
        running(app_module)
        history = [
            {"role": "user", "content": "a", "adapters": "/x"},
            {"role": "assistant", "content": "b"},
            {"role": "user", "content": "c"},
        ]
        with ask(base_url, messages=history) as resp:
            resp.read()
        path, headers, body = upstream.requests[0]
        assert path == "/v1/chat/completions"
        assert "Origin" not in headers
        assert "X-MLX-Token" not in headers
        assert headers["Content-Type"] == "application/json"
        assert body == {
            "model": "default_model",  # mlx_lm: the loaded model, never another
            "messages": [
                {"role": "user", "content": "a"},
                {"role": "assistant", "content": "b"},
                {"role": "user", "content": "c"},
            ],
            "stream": True,
            "stream_options": {"include_usage": True},
            "max_tokens": 2048,
            "temperature": 0.7,
            "top_p": 0.95,
        }

    def test_reasoning_is_sent_apart_from_the_answer(self, app_module, upstream, base_url):
        running(app_module)
        upstream.script = [
            ": keepalive 5/10\n\n",
            chunk(reasoning="let me "),
            chunk(reasoning="see"),
            chunk(content="42", finish="length"),
            USAGE,
            DONE,
        ]
        with ask(base_url) as resp:
            got = events(resp)
        assert ("status", {"state": "prompt", "done": 5, "total": 10}) in got
        assert [d for k, d in got if k == "reasoning"] == ["let me ", "see"]
        assert [d for k, d in got if k == "token"] == ["42"]
        assert got[-1][1]["truncated"] is True

    def test_an_adopted_server_can_be_asked_once_probed(
        self, app_module, upstream, base_url, monkeypatch
    ):
        running(app_module, ready=False, owned=False)
        assert refused(base_url)[0] == 409
        runtime = app_module.engine_runtime
        monkeypatch.setattr(runtime, "listeners", lambda port, **k: {PID})
        monkeypatch.setattr(runtime, "listener_is", lambda port, pid, **k: True)
        assert app_module._probe_direct(PID, adopted=True) is None
        assert not upstream.requests, "an adopted server is not warmed again"
        with ask(base_url) as resp:
            assert events(resp, "done")


class TestRefusals:
    def test_stopped_server(self, app_module, upstream, base_url):
        code, error = refused(base_url)
        assert code == 409
        assert "Start the API server" in error
        assert not upstream.requests

    def test_a_model_the_server_does_not_serve(self, app_module, upstream, base_url):
        running(app_module)
        code, error = refused(
            base_url, body={"repo": TWO, "messages": [{"role": "user", "content": "hi"}]}
        )
        assert code == 409
        assert "does not serve" in error
        assert not upstream.requests

    def test_an_older_pair_serves_only_its_first_model(self, app_module, upstream, base_url):
        running(app_module, (ONE, TWO))
        body = {"repo": TWO, "messages": [{"role": "user", "content": "hi"}]}
        code, error = refused(base_url, body=body)
        assert code == 409 and "does not serve" in error
        assert not upstream.requests

    @pytest.mark.parametrize("pending", [False, True])
    def test_starting_or_restarting(self, app_module, upstream, base_url, pending):
        running(app_module, ready=pending)
        if pending:
            app_module.STATE.server_pending.append(ONE)
        code, error = refused(base_url)
        assert code == 409
        assert "starting" in error
        assert not upstream.requests

    def test_a_model_that_did_not_load(self, app_module, upstream, base_url):
        running(app_module, ready=False)
        app_module.STATE.server_verdict = "failed"
        code, error = refused(base_url)
        assert code == 409
        assert "could not load" in error
        assert "press Stop, then Start Server" in error

    def test_repo_is_required(self, app_module, base_url):
        running(app_module)
        code, error = refused(base_url, body={"messages": [{"role": "user", "content": "hi"}]})
        assert code == 400
        assert "repo" in error

    def test_one_reply_at_a_time(self, app_module, upstream, base_url):
        running(app_module)
        app_module.STATE.generating = True
        assert refused(base_url)[0] == 409

    def test_token_and_origin_checks_still_apply(self, app_module, upstream, base_url):
        running(app_module)
        assert refused(base_url, headers={"Origin": "http://evil.example"})[0] == 403
        body = {"repo": ONE, "messages": [{"role": "user", "content": "hi"}]}
        request = urllib.request.Request(
            base_url + "/api/chat",
            data=json.dumps(body).encode(),
            headers={"Content-Type": "application/json"},
        )
        request.add_header("X-MLX-Token", "wrong")  # after the fixture's real token
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(request, timeout=10)
        assert caught.value.code == 403
        assert not upstream.requests

    @pytest.mark.parametrize("path", ["/api/load", "/api/unload"])
    def test_the_app_no_longer_loads_a_model(self, app_module, base_url, path):
        assert refused(base_url, path, {"repo": ONE})[0] == 404
        assert not hasattr(app_module, "worker_load")


class TestFailures:
    def last(self, base_url):
        with ask(base_url) as resp:
            return events(resp, "done", "error")[-1]

    def test_the_servers_own_error_is_shown(self, app_module, upstream, base_url):
        running(app_module)
        upstream.status = 404
        upstream.body = b'{"error": "boom while loading"}'
        kind, text = self.last(base_url)
        assert kind == "error"
        assert "boom while loading" in text
        assert finished(app_module)

    def test_a_refusal_that_is_not_json(self, app_module, upstream, base_url):
        running(app_module)
        upstream.status = 500
        upstream.body = b"<html>nope</html>"
        kind, text = self.last(base_url)
        assert kind == "error"
        assert "500" in text
        assert "<html>" not in text

    def test_a_stream_cut_short(self, app_module, upstream, base_url):
        running(app_module)
        upstream.script = [chunk(content="half")]
        kind, text = self.last(base_url)
        assert kind == "error"
        assert "before the reply finished" in text

    def test_a_damaged_chunk(self, app_module, upstream, base_url):
        running(app_module)
        upstream.script = ["data: {not json\n\n"]
        assert self.last(base_url)[0] == "error"
        assert finished(app_module)

    def test_nothing_listening(self, app_module, base_url, monkeypatch):
        running(app_module)
        with socket.socket() as spare:
            spare.bind(("127.0.0.1", 0))
            free = spare.getsockname()[1]
        monkeypatch.setattr(app_module, "SERVER_PORT", free)
        kind, text = self.last(base_url)
        assert kind == "error"
        assert "could not be reached" in text
        assert finished(app_module)


class TestWaitingAndCancel:
    @pytest.mark.parametrize("script", [["BUSY", *NORMAL], ["WAIT", *NORMAL]])
    def test_a_busy_server_shows_waiting_and_then_replies(
        self, app_module, upstream, base_url, script
    ):
        running(app_module)
        upstream.script = script
        with ask(base_url) as resp:
            stream = frames(resp)
            assert next(stream) == ("status", {"state": "waiting"})
            assert next(stream) == ("status", {"state": "waiting"})
            upstream.hold.set()
            rest = list(stream)
        assert rest[-1][0] == "done"
        assert not upstream.closed.is_set()

    @pytest.mark.parametrize("script", [["BUSY", *NORMAL], [chunk(content="par"), "WAIT", *NORMAL]])
    def test_stop_closes_the_upstream_request(self, app_module, upstream, base_url, script):
        running(app_module)
        upstream.script = script
        with ask(base_url) as resp:
            stream = frames(resp)
            next(stream)
            post(base_url, "/api/chat/stop", {}).close()
            rest = list(stream)
        assert rest[-1][0] == "done"
        assert rest[-1][1]["stopped"] is True
        assert upstream.closed.wait(5)
        assert finished(app_module)

    def test_a_browser_that_goes_away_closes_the_upstream_request(
        self, app_module, upstream, base_url
    ):
        running(app_module)
        upstream.script = ["BUSY", *NORMAL]
        resp = ask(base_url)
        next(frames(resp))
        resp.close()
        assert upstream.closed.wait(5)
        assert finished(app_module)


class TestLateReader:
    def test_a_cancelled_request_is_never_opened_again(self, app_module, upstream, monkeypatch):
        real, held = app_module._read_reply, []
        monkeypatch.setattr(app_module, "_read_reply", lambda *args: held.append(args))
        stop = threading.Event()
        stop.set()
        turn = [{"role": "user", "content": "hi"}]
        assert [kind for kind, _ in app_module.stream_chat(ONE, turn, stop)] == ["done"]
        real(*held[0])  # the reader thread gets to run only now
        assert not upstream.requests


class TestSnapshot:
    def view(self, app):
        return app.STATE.snapshot()["server"]

    def test_ready_follows_the_probe(self, app_module):
        assert self.view(app_module)["ready"] is False
        running(app_module, ready=False)
        assert self.view(app_module)["ready"] is False
        app_module.STATE.server_verdict = "ready"
        assert self.view(app_module)["ready"] is True
        assert self.view(app_module)["load_failed"] is False

    def test_load_failure_is_reported(self, app_module):
        running(app_module, ready=False)
        app_module.STATE.server_verdict = "failed"
        assert self.view(app_module)["load_failed"] is True
        assert self.view(app_module)["ready"] is False

    def test_no_chat_model_in_the_snapshot(self, app_module):
        assert "loaded" not in app_module.STATE.snapshot()
        for name in ("chat_load_problem", "_publish_server_budget"):
            assert not hasattr(app_module, name)


class TestLastModelsDescriptors:
    """Row 835."""

    def open_fds(self):
        return len(os.listdir("/dev/fd"))

    @pytest.mark.parametrize("kind", ["directory", "file", "fifo"])
    def test_reading_leaves_no_descriptor_open(self, app_module, kind):
        path = app_module._last_models_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        if kind == "directory":
            path.mkdir()
        elif kind == "fifo":
            os.mkfifo(path)
        else:
            path.write_text('{"repos": ["org/one"]}', "utf-8")
        before = self.open_fds()
        for _ in range(20):
            app_module._last_models()
        assert self.open_fds() == before
