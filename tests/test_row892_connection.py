"""Row 892: Test Connection, and the port-owner check before anything is sent (F6).

Plan: row1045-2.0.14 WP2. A fake server on a free port stands in for the engines;
no model and no real server is started.
"""

from __future__ import annotations

import json
import re
import socket
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_ui_behaviour import needs_node, run

import engine_runtime as runtime

UI = Path(__file__).resolve().parent.parent / "ui"
PAGE = (UI / "index.html").read_text("utf-8")
KEY = runtime.new_key()  # made for this run; no real key is read
REPO = "org/model"


def said(content="", **extra):
    message = {"role": "assistant", "content": content, **extra}
    return {"choices": [{"index": 0, "message": message, "finish_reason": "stop"}]}


def called(arguments, name="get_weather"):
    if not isinstance(arguments, str):
        arguments = json.dumps(arguments)
    call = {"id": "1", "type": "function", "function": {"name": name, "arguments": arguments}}
    return said(None, tool_calls=[call])


GOOD_REPLY = said("ok")
GOOD_CALL = called({"city": "Paris"})


class Fake:
    def __init__(self):
        self.requests = []
        self.connections = 0
        self.answers = {"reply": GOOD_REPLY, "tool": GOOD_CALL}
        self.status = 200
        self.delay = 0.0
        self.trickle = False
        self.release = threading.Event()


@pytest.fixture
def fake():
    seen = Fake()

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def setup(self):
            seen.connections += 1
            super().setup()

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            seen.requests.append((self.path, dict(self.headers), body))
            answer = seen.answers["tool" if "tools" in body else "reply"]
            raw = answer if isinstance(answer, bytes) else json.dumps(answer).encode()
            seen.release.wait(seen.delay)
            try:
                self.send_response(seen.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                if seen.trickle:  # always something within the socket's own time limit
                    while not seen.release.wait(0.05):
                        self.wfile.write(b" ")
                        self.wfile.flush()
                self.wfile.write(raw)
            except OSError:
                pass

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    seen.port = httpd.server_address[1]
    try:
        yield seen
    finally:
        seen.release.set()
        httpd.shutdown()
        httpd.server_close()


def check(fake, headers=dict, timeout=5.0):
    return runtime.check_connection(fake.port, "default_model", headers, timeout)


# ─────────────────────────────── the two checks ───────────────────────────────


class TestChecks:
    def test_both_pass_and_exactly_two_small_requests_are_sent(self, fake):
        result = check(fake)
        assert [result[name]["status"] for name in ("reply", "tool")] == ["pass", "pass"]
        assert "one pass" in result["tool"]["detail"].lower()
        assert [path for path, _, _ in fake.requests] == ["/v1/chat/completions"] * 2
        (_, _, reply), (_, _, tool) = fake.requests
        for body in (reply, tool):
            assert body["model"] == "default_model"
            assert body["stream"] is False and body["max_tokens"] <= 512
        assert "tools" not in reply
        [offered] = tool["tools"]
        parameters = offered["function"]["parameters"]
        assert parameters["required"] == ["city"]
        assert "unit" in parameters["properties"]  # the optional value

    def test_a_null_optional_value_is_reported(self, fake):
        fake.answers["tool"] = called({"city": "Paris", "unit": None})
        tool = check(fake)["tool"]
        assert tool["status"] == "warn"
        assert "null" in tool["detail"] and "unit" in tool["detail"]
        assert "does not change replies" in tool["detail"]

    @pytest.mark.parametrize(
        "text",
        [
            '{"name": "get_weather", "arguments": {"city": "Paris"}}',
            "<tool_call>\n{}\n</tool_call>",
        ],
    )
    def test_a_tool_call_written_as_text_fails(self, fake, text):
        fake.answers["tool"] = said(text)
        tool = check(fake)["tool"]
        assert tool["status"] == "fail" and "as text" in tool["detail"]

    @pytest.mark.parametrize(
        ("answer", "words"),
        [
            (said("It is sunny."), "did not call"),
            (called({"city": "Paris"}, name="delete_files"), "not offered"),
            (called("{city: Paris"), "not valid JSON"),
            (called("[1]"), "not valid JSON"),
            (called({}), "city"),
            (called({"city": None}), "city"),
            (called({"city": 7}), "city"),
            (said(None, tool_calls=["nonsense"]), "could not be read"),
            (said("", reasoning="thinking about it"), "thinking"),
            ({"choices": []}, "could not be read"),
            (b"<html>not json</html>", "could not be read"),
        ],
    )
    def test_every_other_tool_answer_fails_in_plain_words(self, fake, answer, words):
        fake.answers["tool"] = answer
        tool = check(fake)["tool"]
        assert tool["status"] == "fail", tool
        assert words in tool["detail"]

    @pytest.mark.parametrize(
        ("answer", "status", "words"),
        [
            (said(""), "fail", "empty"),
            (said(None), "fail", "empty"),
            (said("", reasoning_content="hmm"), "warn", "thinking"),
            ({"nothing": 1}, "fail", "could not be read"),
            (b"\xff\xfe", "fail", "could not be read"),
        ],
    )
    def test_the_short_reply_is_judged(self, fake, answer, status, words):
        fake.answers["reply"] = answer
        reply = check(fake)["reply"]
        assert reply["status"] == status and words in reply["detail"]

    def test_a_huge_reply_is_not_read_to_the_end(self, fake):
        fake.answers["reply"] = said("x" * (runtime.TEST_REPLY_BYTES + 10))
        assert check(fake)["reply"]["status"] == "fail"

    def test_a_refusal_shows_the_status_and_never_the_key(self, fake):
        fake.status = 401
        fake.answers["reply"] = {"error": {"message": f"bad key {KEY} sent as Bearer {KEY}"}}
        result = check(fake, headers=lambda: {"Authorization": f"Bearer {KEY}"})
        assert result["reply"]["status"] == "fail"
        assert "401" in result["reply"]["detail"]
        assert KEY not in json.dumps(result)

    def test_a_model_that_echoes_the_key_does_not_show_it(self, fake):
        fake.answers["tool"] = called({"city": "Paris", KEY: None})
        result = check(fake, headers=lambda: {"Authorization": f"Bearer {KEY}"})
        assert KEY not in json.dumps(result)

    def test_the_key_is_sent_in_the_header(self, fake):
        check(fake, headers=lambda: {"Authorization": f"Bearer {KEY}"})
        assert all(h["Authorization"] == f"Bearer {KEY}" for _, h, _ in fake.requests)

    @pytest.mark.parametrize("trickle", [False, True])
    def test_a_slow_server_times_out_and_the_tool_call_is_not_tried(self, fake, trickle):
        if trickle:
            fake.trickle = True
        else:
            fake.delay = 10
        started = time.monotonic()
        result = check(fake, timeout=0.4)
        assert time.monotonic() - started < 3
        assert result["reply"]["status"] == "fail"
        assert "No answer within" in result["reply"]["detail"]
        assert result["tool"]["status"] == "skipped"
        assert len(fake.requests) == 1

    def test_nothing_listening_fails_clearly(self):
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            port = probe.getsockname()[1]
        result = runtime.check_connection(port, "m", dict, 2)
        assert result["reply"]["status"] == "fail"
        assert "could not be reached" in result["reply"]["detail"]
        assert result["tool"]["status"] == "skipped"

    def test_a_refused_owner_check_sends_nothing(self, fake):
        def headers():
            raise runtime.Refused("another program")

        with pytest.raises(runtime.Refused):
            check(fake, headers=headers)
        assert fake.requests == []


# ─────────────────────────── the app: route and owner ───────────────────────────


class Proc:
    pid = 4242
    returncode = None

    def poll(self):
        return None


def serving(app, fake, engine="mlx", ready=True):
    state = app.STATE
    state.server_proc, state.server_pid = Proc(), Proc.pid
    state.server_repo, state.server_repos = REPO, [REPO]
    state.server_engine, state.server_port = engine, fake.port
    app.SERVER_PORT = fake.port
    state.server_verdict = "ready" if ready else None
    if engine == "optiq":
        app.APP_DIR.mkdir(parents=True, exist_ok=True)
        runtime.write_key(app._key_file(), KEY)


@pytest.fixture
def holders(app_module, monkeypatch):
    """The pids that listen on the server's port."""
    seen = {Proc.pid}
    monkeypatch.setattr(app_module.engine_runtime, "listeners", lambda port, **k: set(seen))
    return seen


def post(base_url, path="/api/server/test", body=None, headers=None):
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body or {}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


def logs(app) -> str:
    return "".join(p.read_text() for p in app.LOG_DIR.iterdir() if p.is_file())


ENGINES = ["mlx", "optiq"]


@pytest.mark.parametrize("engine", ENGINES)
class TestOwner:
    """Both engines: only our own server process is ever sent anything."""

    @pytest.mark.parametrize("others", [{99}, {Proc.pid, 99}, set()])
    def test_chat_headers_refuse_a_wrong_owner(self, app_module, fake, holders, engine, others):
        serving(app_module, fake, engine)
        holders.clear()
        holders.update(others)
        with pytest.raises(runtime.Refused, match=r"not sent|Nothing was sent"):
            app_module._chat_headers()

    def test_chat_sends_nothing_to_a_wrong_owner(self, app_module, fake, holders, base_url, engine):
        serving(app_module, fake, engine)
        holders.clear()
        holders.add(99)
        events = list(
            app_module.stream_chat(
                REPO, [{"role": "user", "content": "private"}], threading.Event()
            )
        )
        assert [kind for kind, _ in events] == ["error"]
        time.sleep(0.2)
        assert fake.requests == []

    def test_the_test_sends_nothing_to_a_wrong_owner(
        self, app_module, fake, holders, base_url, engine
    ):
        serving(app_module, fake, engine)
        holders.clear()
        holders.add(99)
        status, answer = post(base_url)
        assert status == 409 and "another program" in answer["error"]
        assert fake.requests == []
        assert KEY not in json.dumps(answer) + logs(app_module)

    def test_an_unreadable_port_sends_nothing(
        self, app_module, fake, base_url, monkeypatch, engine
    ):
        serving(app_module, fake, engine)
        monkeypatch.setattr(app_module.engine_runtime, "listeners", lambda port, **k: None)
        assert post(base_url)[0] == 409
        assert fake.requests == []

    def test_a_good_server_is_tested_and_the_key_never_shown(
        self, app_module, fake, holders, base_url, engine
    ):
        serving(app_module, fake, engine)
        status, answer = post(base_url)
        assert status == 200, answer
        assert answer["reply"]["status"] == answer["tool"]["status"] == "pass"
        assert (answer["model"], answer["engine"], answer["pid"]) == (REPO, engine, Proc.pid)
        sent = [headers.get("Authorization") for _, headers, _ in fake.requests]
        assert sent == [f"Bearer {KEY}" if engine == "optiq" else None] * 2
        assert {body["model"] for _, _, body in fake.requests} == {
            app_module.engines.get(engine).request_model
        }
        assert KEY not in json.dumps(answer) + logs(app_module)
        assert "connection test" in logs(app_module)

    def test_the_owner_is_checked_before_each_request(
        self, app_module, fake, base_url, monkeypatch, engine
    ):
        serving(app_module, fake, engine)
        looks = iter([{Proc.pid}, {99}])
        monkeypatch.setattr(app_module.engine_runtime, "listeners", lambda port, **k: next(looks))
        assert post(base_url)[0] == 409
        assert len(fake.requests) == 1


@pytest.mark.parametrize("change", [{"server_pid": 5151}, {"server_port": 1}])
def test_a_server_replaced_during_the_test_gets_nothing_more(
    app_module, fake, base_url, monkeypatch, change
):
    serving(app_module, fake, "optiq")

    def listeners(port, **kwargs):
        # The first look is for the first request; then another server takes over.
        pid = app_module.STATE.server_pid
        if fake.requests:
            return {pid}
        return {Proc.pid}

    monkeypatch.setattr(app_module.engine_runtime, "listeners", listeners)
    judged = app_module.engine_runtime._judge_reply

    def replaced(message):
        for name, value in change.items():
            setattr(app_module.STATE, name, value)
        return judged(message)

    monkeypatch.setattr(app_module.engine_runtime, "_judge_reply", replaced)
    status, answer = post(base_url)
    assert status == 409 and "changed during the test" in answer["error"]
    assert len(fake.requests) == 1


class TestRoute:
    @pytest.mark.parametrize(
        "headers",
        [
            {"X-MLX-Token": "wrong"},
            {"Origin": "http://evil.example"},
            {"Host": "evil.example"},
        ],
    )
    def test_only_this_app_may_start_it(self, app_module, fake, holders, base_url, headers):
        serving(app_module, fake)
        assert post(base_url, headers=headers)[0] == 403
        assert fake.requests == []

    def test_a_stopped_server_is_refused(self, app_module, fake, holders, base_url):
        status, answer = post(base_url)
        assert status == 409 and "Start the API server" in answer["error"]
        assert fake.requests == []

    @pytest.mark.parametrize("verdict", [None, "failed"])
    def test_a_server_that_is_not_ready_is_refused(
        self, app_module, fake, holders, base_url, verdict
    ):
        serving(app_module, fake, ready=False)
        app_module.STATE.server_verdict = verdict
        assert post(base_url)[0] == 409
        assert fake.requests == []

    def test_nothing_is_sent_during_a_load_test(
        self, app_module, fake, holders, base_url, monkeypatch
    ):
        serving(app_module, fake)
        monkeypatch.setattr(app_module.STATE, "testing", lambda: True)
        status, answer = post(base_url)
        assert status == 409 and answer["error"] == app_module.LOADTEST_BUSY
        assert fake.requests == []

    def test_only_one_test_runs_at_a_time(self, app_module, fake, holders, base_url):
        serving(app_module, fake)
        fake.delay = 10
        first = []
        thread = threading.Thread(target=lambda: first.append(post(base_url)))
        thread.start()
        end = time.monotonic() + 5
        while not fake.requests and time.monotonic() < end:
            time.sleep(0.01)
        status, answer = post(base_url)
        assert status == 409 and "already running" in answer["error"]
        fake.release.set()
        thread.join(timeout=10)
        assert first[0][0] == 200
        assert post(base_url)[0] == 200  # free again

    def test_the_request_chooses_nothing(self, app_module, fake, holders, base_url):
        serving(app_module, fake)
        post(base_url, body={"port": 1, "model": "org/other", "prompt": "mine"})
        assert [b["model"] for _, _, b in fake.requests] == ["default_model"] * 2
        assert "mine" not in json.dumps([b for _, _, b in fake.requests])


# ───────────────────────────────── the screen ─────────────────────────────────


def test_the_api_server_page_has_the_button_and_both_results():
    start = PAGE.index('id="view-server"')
    server = PAGE[start : PAGE.index('<section class="view', start)]
    assert 'id="testBtn"' in server and "Test Connection" in server
    for name in ("testResults", "testReply", "testTool", "testError"):
        assert f'id="{name}"' in server
    assert "Nothing is run" in server


def test_the_hidden_results_are_not_drawn():
    # A class that sets display beats the hidden attribute, unless a rule says otherwise.
    style = (UI / "style.css").read_text("utf-8")
    tag = re.search(r'<[^>]*id="testResults"[^>]*>', PAGE).group(0)
    assert " hidden" in tag
    for name in re.search(r'class="([^"]*)"', tag).group(1).split():
        if re.search(rf"(?m)^\.{name} \{{[^}}]*display:", style):
            assert f".{name}[hidden] {{ display: none; }}" in style, name


SCREEN = """
const live = (over = {}) => snap({ server: { running: true, ready: true, pid: 7,
  endpoint: "e", lines: [], tool_support: {}, repos: ["org/run"], repo: "org/run", ...over } });
const q = (s) => document.querySelector(s);
const press = () => q("#testBtn").listeners.click({ target: q("#testBtn") });
const until = () => new Promise((done) => setTimeout(done, 20));
const seen = () => ({ disabled: q("#testBtn").disabled, hidden: q("#testResults").hidden,
  reply: html("#testReply"), tool: html("#testTool"), error: q("#testError").textContent });
let finish;
const waiting = () => { respond = (path) => path === "/api/server/test"
  ? new Promise((done) => { finish = done; }) : new Promise(() => {}); };
const answer = (body, ok = true) => finish({ ok, statusText: "bad", json: async () => body });
const result = { ok: true, pid: 7, reply: { status: "pass", detail: "The model answered." },
  tool: { status: "fail", detail: "<img src=x> as text" } };
"""


@needs_node
class TestScreen:
    def test_the_button_needs_a_ready_server(self, tmp_path):
        out = run(
            tmp_path,
            SCREEN
            + """
            return [{ running: false }, { ready: false }, {}].map((over) => {
              render(live(over)); return q("#testBtn").disabled;
            });
            """,
        )
        assert out == [True, True, False]

    def test_results_are_shown_in_plain_words_and_escaped(self, tmp_path):
        out = run(
            tmp_path,
            SCREEN
            + """
            render(live()); waiting();
            press(); const during = seen();
            press();  // a second press while it runs sends nothing
            answer(result); await until();
            return [during, seen(), calls.filter((c) => c.path === "/api/server/test").length];
            """,
        )
        during, after, sent = out
        assert during["disabled"] is True and "Testing" in during["reply"]
        assert after["disabled"] is False and after["hidden"] is False
        assert "Passed" in after["reply"] and "The model answered." in after["reply"]
        assert "Failed" in after["tool"] and "&lt;img" in after["tool"]
        assert "<img" not in after["tool"]
        assert sent == 1

    @pytest.mark.parametrize("change", ["{ pid: 8 }", "{ running: false }"])
    def test_another_server_clears_the_results(self, tmp_path, change):
        out = run(
            tmp_path,
            SCREEN
            + f"""
            render(live()); waiting(); press(); answer(result); await until();
            render(live()); const kept = seen().hidden;
            render(live({change}));
            return [kept, seen().hidden];
            """,
        )
        assert out == [False, True]

    def test_a_late_answer_for_an_old_server_is_dropped(self, tmp_path):
        out = run(
            tmp_path,
            SCREEN
            + """
            render(live()); waiting(); press();
            render(live({ pid: 8 }));
            answer(result); await until();
            return seen();
            """,
        )
        assert out["hidden"] is True and out["disabled"] is False

    def test_a_refusal_is_shown(self, tmp_path):
        out = run(
            tmp_path,
            SCREEN
            + """
            render(live()); waiting(); press();
            answer({ error: "Nothing was sent: another program" }, false); await until();
            return seen();
            """,
        )
        assert "Nothing was sent" in out["error"]
        assert out["hidden"] is True and out["disabled"] is False
