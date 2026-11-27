"""Row 874: Load Test with a chosen engine. Fakes only: no process, engine or model."""

import io
import json
import threading

import pytest
from test_row874_app import PORT, REPO, SECRETS, Rig
from test_ui_behaviour import needs_node, run

import engine_runtime as runtime
import mlx_loadtest as loadtest

KEY = "sk-optiq-" + "k" * 43


@pytest.fixture
def rig(app_module, monkeypatch):
    return Rig(app_module, monkeypatch)


# ---- the app --------------------------------------------------------------------
def test_the_job_has_no_key_and_the_runner_gets_it_by_environment(rig):
    app = rig.app
    job, env = app._loadtest_job(REPO, [4096], "r1", app.APP_DIR, "optiq")
    key = env["OPTIQ_API_KEY"]
    assert key not in json.dumps(job) and job["key_env"] == "OPTIQ_API_KEY"
    assert "OPTIQ_API_KEY" not in job["server_env"]
    assert "inherited-value" not in env.values() and not set(SECRETS) & set(env)
    command = job["command"]
    assert "optiq.cli" in command and command[command.index("--port") + 1] == "0"
    assert "--no-anthropic" in command and job["request_model"] == "default_model"
    assert command[command.index("--max-context") + 1] == str(4096 + job["reply_tokens"])


def test_an_mlx_job_is_unchanged(rig):
    job, env = rig.app._loadtest_job(REPO, [4096], "r1", rig.app.APP_DIR)
    assert not {"engine", "key_env", "server_env", "request_model"} & set(job)
    assert job["command"][1] == str(rig.app.GUARD_SCRIPT) and "OPTIQ_API_KEY" not in env
    fingerprint = rig.app._loadtest_fingerprint(REPO)
    assert not {"engine", "engine_version"} & set(fingerprint)
    other = rig.app._loadtest_fingerprint(REPO, "optiq")
    assert (other["engine"], other["engine_version"]) == ("optiq", "0.8.6")


def test_an_engine_that_cannot_serve_the_model_starts_no_test(rig):
    (rig.snapshot / "config.json").write_text('{"vision_config": {}}')
    code, answer = rig.app.start_loadtest(REPO, [4096], "optiq")
    assert code == 409 and "image" in answer["error"] and rig.started == []


def test_apply_is_refused_for_another_engines_result(rig):
    record = {"id": "r1", "repo": REPO, "engine": "optiq",
              "rows": [{"size": 4096, "status": "passed"}]}  # fmt: skip
    rig.app._save_loadtest(record)
    code, answer = rig.app.apply_loadtest(REPO, "r1", 4096)
    assert code == 400 and "MLX" in answer["error"]


def test_a_server_left_by_the_runner_is_ended(rig, monkeypatch):
    app, killed = rig.app, []
    engine_mark = f"x -m optiq.cli serve --model {rig.snapshot} --host 127.0.0.1"
    monkeypatch.setattr(app, "_pid_alive", lambda pid: pid == 55)
    monkeypatch.setattr(app, "_ps_field", lambda pid, f: engine_mark if f == "command" else "t")
    monkeypatch.setattr(app.os, "getpgid", lambda pid: pid)
    monkeypatch.setattr(app.os, "killpg", lambda pid, sig: killed.append(pid))
    pids = {"repo": REPO, "engine": "optiq", "snapshot": str(rig.snapshot),
            "server": 55, "server_started": "t"}  # fmt: skip
    app._end_test_server(pids)
    assert killed == [55]
    app._loadtest_pidfile().write_text(json.dumps(pids))
    app.restore_loadtest()
    assert killed == [55, 55]


# ---- the runner -----------------------------------------------------------------
class Proc:
    pid = 77

    def poll(self):
        return None


def _runner(monkeypatch, direct=True):
    monkeypatch.setenv("OPTIQ_API_KEY", KEY)
    job = {"repo": REPO, "run_id": "r", "reply_tokens": 32, "rounds": 1, "timeout_seconds": 60}
    if direct:
        job.update(key_env="OPTIQ_API_KEY", server_env={"A": "1"}, request_model="default_model")
    return loadtest.Runner(
        job, lambda event: None, spawn=None, probe=None, clock=lambda: 0.0,
        count_tokens=len, cancel=threading.Event(),
    )  # fmt: skip


def test_the_runner_builds_the_servers_environment(monkeypatch):
    runner = _runner(monkeypatch)
    assert runner.server_environment() == {"A": "1", "OPTIQ_API_KEY": KEY}
    assert runner.headers() == {"Authorization": f"Bearer {KEY}"}
    plain = _runner(monkeypatch, direct=False)
    assert plain.headers() is None and plain.server_environment()["OPTIQ_API_KEY"] == KEY


@pytest.mark.parametrize(
    ("holders", "auth", "warm", "outcome"),
    [
        ({77}, "ready", True, True),
        ({77, 9}, "ready", True, False),
        ({77}, "open", True, "without the key"),
        ({77}, "ready", False, "could not be loaded"),
    ],
)
def test_readiness_of_a_keyed_server(monkeypatch, holders, auth, warm, outcome):
    sent = []
    monkeypatch.setattr(runtime, "listeners", lambda port, **k: holders)
    monkeypatch.setattr(runtime, "auth_state", lambda *a, **k: sent.append(a) or auth)
    monkeypatch.setattr(runtime, "warm_up", lambda *a, **k: warm)
    runner = _runner(monkeypatch)
    monitor = type("M", (), {"reason": None})()
    if isinstance(outcome, str):
        with pytest.raises(loadtest.Stop) as stopped:
            runner.wait_ready(Proc(), PORT, monitor)
        assert outcome in stopped.value.reason
    else:
        assert runner.wait_ready(Proc(), PORT, monitor) is outcome
        assert bool(sent) is outcome


def test_the_request_carries_the_key_and_reads_reasoning_content(monkeypatch):
    seen = {}

    class Conn:
        def __init__(self, *a, **k):
            pass

        def request(self, method, path, body, headers):
            seen.update(headers)

        def getresponse(self):
            lines = b'data: {"choices":[{"delta":{"reasoning_content":"hm"}}]}\n'
            lines += b'data: {"choices":[],"usage":{"prompt_tokens":1}}\ndata: [DONE]\n'
            reply = io.BytesIO(lines)
            reply.status = 200
            return reply

        def close(self):
            pass

    monkeypatch.setattr(loadtest.http.client, "HTTPConnection", Conn)
    got = loadtest.ask(
        PORT, {}, lambda: 1.0, lambda: None, lambda *a: None, {"Authorization": "Bearer k"}
    )
    assert seen["Authorization"] == "Bearer k" and got["text"] == "hm"


# ---- the page -------------------------------------------------------------------
@needs_node
def test_the_page_names_the_engine_and_offers_apply_only_for_mlx(tmp_path):
    out = run(
        tmp_path,
        """
        const row = { size: 4096, status: "passed" };
        const base = { id: "r", time: "t", status: "done", rows: [row], values: {}, flags: [] };
        return [resultRun(base, false), resultRun({ ...base, engine: "optiq" }, false)];
        """,
    )
    assert "data-apply" in out[0] and "data-apply" not in out[1] and "optiq" in out[1]
