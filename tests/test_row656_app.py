"""Row 656: the app starts, watches and stops the API server.

The server holds one model (row 887); a pidfile from an older build may name two.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

import pytest

A, B = "org/a", "org/b"
GB = 10**9
RAM = 32 * 1024**3
CONFIG = {
    "num_hidden_layers": 28,
    "num_key_value_heads": 4,
    "num_attention_heads": 28,
    "hidden_size": 3584,
    "max_position_embeddings": 32768,
}


def cache_model(app, repo, weights=1000, config=CONFIG):
    """A model folder shaped like the Hub cache, with a sparse stand-in for weights."""
    entry = app.CACHE_DIR / f"models--{repo.replace('/', '--')}"
    snapshot = entry / "snapshots" / "abc"
    snapshot.mkdir(parents=True)
    (entry / "refs").mkdir()
    (entry / "refs" / "main").write_text("abc", "utf-8")
    (snapshot / "config.json").write_text(json.dumps(config), "utf-8")
    with open(snapshot / "model.safetensors", "wb") as handle:
        handle.truncate(weights)


@pytest.fixture
def ram(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "hardware", lambda: {"ram_bytes": RAM, "ram": "32 GB"})
    app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
    app_module.LOG_DIR.mkdir(parents=True, exist_ok=True)


def launched(app, monkeypatch, background=False):
    seen = {}

    def popen(cmd, **kwargs):
        seen.update(cmd=cmd, env=kwargs["env"])
        raise OSError("not started in tests")

    monkeypatch.setattr(app.subprocess, "Popen", popen)
    app.worker_server_start(A, background)
    return seen


# 10. the command ------------------------------------------------------------ #
class TestLaunch:
    @pytest.mark.parametrize("background", [False, True])
    def test_the_command_is_recognised_as_ours(self, app_module, monkeypatch, ram, background):
        cache_model(app_module, A)
        cmd = launched(app_module, monkeypatch, background)["cmd"]
        assert cmd[cmd.index("--model") + 1] == A
        assert not [p for p in cmd if p.startswith(("--studio", "--prompt-cache"))]
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(app_module, "_ps_field", lambda pid, field: " ".join(cmd))
        assert app_module._is_our_server(4242, A, None)
        assert not app_module._is_our_server(4242, B, None)

    def test_state_and_pidfile_name_the_model(self, app_module, monkeypatch, ram):
        class Proc:
            pid = os.getpid()
            stdout = iter(())
            returncode = 0

            def wait(self, timeout=None):
                return 0

        monkeypatch.setattr(app_module, "stop_server", lambda *a, **k: None)
        monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: Proc())
        monkeypatch.setattr(app_module, "_watch_server", lambda *a: None)
        monkeypatch.setattr(app_module, "_ps_field", lambda pid, field: "")
        app_module.STATE.server_pending.append(A)
        app_module.worker_server_start(A, True)
        assert app_module.STATE.server_repos == [A]
        assert app_module.STATE.server_pending == []
        data = json.loads(app_module._server_pidfile().read_text("utf-8"))
        assert (data["repo"], data["repos"]) == (A, [A])


# 11. pidfile, restore, watch, stop ------------------------------------------ #
def adopt(app, monkeypatch, **payload):
    app.APP_DIR.mkdir(parents=True, exist_ok=True)
    app._server_pidfile().write_text(json.dumps({"pid": os.getpid(), "repo": A, **payload}))
    monkeypatch.setattr(app, "_is_our_server", lambda *a, **k: True)
    monkeypatch.setattr(app, "_watch_server", lambda *a: None)
    app.restore_server_state()


class TestAdoptAndStop:
    def test_restore_reads_both(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        assert app_module.STATE.server_repos == [A, B]
        assert app_module.STATE.snapshot()["server"]["repos"] == [A, B]

    @pytest.mark.parametrize("repos", [None, [], "org/x", [1]])
    def test_a_pidfile_from_an_older_build_means_one_model(self, app_module, monkeypatch, repos):
        adopt(app_module, monkeypatch, **({} if repos is None else {"repos": repos}))
        assert app_module.STATE.server_repos == [A]

    def test_stop_forgets_both_and_removes_the_pidfile(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        monkeypatch.setattr(app_module, "_is_our_server", lambda *a, **k: False)
        app_module.stop_server()
        assert app_module.STATE.server_repos == []
        assert not app_module._server_pidfile().exists()

    def test_a_watcher_of_an_old_server_keeps_the_new_pidfile(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch)
        app_module._clear_server_pidfile(only_pid=1)
        assert app_module._server_pidfile().exists()

    @pytest.mark.parametrize(("code", "memory"), [(-9, True), (1, False)])
    def test_a_crash_names_the_model(self, app_module, code, memory):
        class Dead:
            returncode = code

            def poll(self):
                return code

        s = app_module.STATE
        s.server_pid, s.server_repo, s.server_repos = 4242, A, [A]
        s.server_stopping = False
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module._watch_server(4242, A, Dead())
        assert "API server for a stopped unexpectedly" in s.server_down_notice
        assert ("out of memory" in s.server_down_notice) is memory
        assert s.server_repos == []


# 12. the start request ------------------------------------------------------ #
def post(base_url, path, body):
    request = urllib.request.Request(
        base_url + path,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def started(app_module, monkeypatch, ram):
    calls = []
    done = __import__("threading").Event()

    def worker(*args):
        calls.append(args)
        done.set()

    monkeypatch.setattr(app_module, "worker_server_start", worker)
    cache_model(app_module, A)
    cache_model(app_module, B)
    return calls, done


class TestStartRequest:
    @pytest.mark.parametrize("also", [None, []])
    def test_no_second_model(self, app_module, base_url, started, also):
        calls, done = started
        assert post(base_url, "/api/server/start", {"repo": A, "also": also})[0] == 200
        assert done.wait(5) and calls[0][:2] == (A, False) and len(calls[0]) == 3
        assert app_module.STATE.server_pending == [A]

    @pytest.mark.parametrize("also", [[B], B, [1], [B, "org/c"], [A], ["--model"]])
    def test_a_second_model_is_refused(self, app_module, base_url, started, also):
        status, body = post(base_url, "/api/server/start", {"repo": A, "also": also})
        assert status == 400 and "one model" in body["error"]
        assert started[0] == [] and app_module.STATE.server_pending == []

    @pytest.mark.parametrize("repo", ["org/no", "--model"])
    def test_the_model_must_be_installed(self, app_module, base_url, started, repo):
        status, _ = post(base_url, "/api/server/start", {"repo": repo})
        assert status == 400
        assert started[0] == [] and app_module.STATE.server_pending == []

    def test_a_model_being_deleted(self, app_module, base_url, started):
        app_module.STATE.deleting.add(A)
        assert post(base_url, "/api/server/start", {"repo": A})[0] == 409
        assert started[0] == [] and app_module.STATE.server_pending == []

    def test_a_large_model_is_never_refused_for_size(self, app_module, base_url, started):
        cache_model(app_module, "org/huge", weights=40 * GB)
        assert post(base_url, "/api/server/start", {"repo": "org/huge"})[0] == 200

    def test_start_needs_the_token(self, server, started):
        host, port = server.server_address[:2]
        request = urllib.request.Request(
            f"http://{host}:{port}/api/server/start",
            data=json.dumps({"repo": A}).encode(),
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)
        assert refused.value.code == 403
        assert started[0] == []


# 13. delete ------------------------------------------------------------------ #
class TestDeleteBlocked:
    def test_every_model_of_an_adopted_server(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        with app_module.STATE.lock:
            for repo in (A, B):
                assert app_module.STATE.in_use_locked(repo) == "stop the API server first"
            assert app_module.STATE.in_use_locked("org/d") is None
