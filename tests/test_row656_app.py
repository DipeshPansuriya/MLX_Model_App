"""Row 656: the app starts, watches and stops an API server that holds two models."""

from __future__ import annotations

import json
import os
import sys
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


def launched(app, monkeypatch, also=(), background=False):
    seen = {}

    def popen(cmd, **kwargs):
        seen.update(cmd=cmd, env=kwargs["env"], resident=app._resident_file().exists())
        raise OSError("not started in tests")

    monkeypatch.setattr(app, "stop_server", lambda *a, **k: None)
    monkeypatch.setattr(app.subprocess, "Popen", popen)
    app._resident_file().write_text("{}", "utf-8")
    app.worker_server_start(A, background, None, list(also))
    return seen


# 10. the command ------------------------------------------------------------ #
class TestLaunch:
    @pytest.mark.parametrize("background", [False, True])
    def test_two_models(self, app_module, monkeypatch, ram, background):
        cache_model(app_module, A)
        cache_model(app_module, B)
        seen = launched(app_module, monkeypatch, [B], background)
        cmd = seen["cmd"]
        assert cmd[2:4] == ["--model", A]
        assert cmd[-10:] == [
            "--prompt-cache-bytes", str(2 * 57_344 * 32768),
            "--studio-also-model", B,
            "--studio-max-models", "2",
            "--studio-budget-bytes", str(int(0.52 * RAM)),
            "--studio-kv-tokens", "32768",
        ]  # fmt: skip
        assert seen["env"]["MLX_STUDIO_RESIDENT_FILE"] == str(app_module._resident_file())
        assert seen["resident"] is False, "a stale resident file is removed before the start"
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(app_module, "_ps_field", lambda pid, field: " ".join(cmd))
        assert app_module._is_our_server(4242, A, None)

    def test_one_model_is_launched_as_before(self, app_module, monkeypatch, ram):
        cmd = launched(app_module, monkeypatch)["cmd"]
        assert not [p for p in cmd if p.startswith(("--studio", "--prompt-cache-bytes"))]
        assert cmd[cmd.index("--prompt-cache-size") + 1] == "2"
        assert cmd[-4] == "--allowed-origins"  # then only the cache size

    def test_state_and_pidfile_name_both(self, app_module, monkeypatch, ram):
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
        app_module.STATE.server_pending.extend([A, B])
        app_module.worker_server_start(A, True, None, [B])
        assert app_module.STATE.server_repos == [A, B]
        assert app_module.STATE.server_pending == []
        data = json.loads(app_module._server_pidfile().read_text("utf-8"))
        assert (data["repo"], data["repos"]) == (A, [A, B])


# 11. pidfile, restore, watch, stop ------------------------------------------ #
def adopt(app, monkeypatch, **payload):
    app.APP_DIR.mkdir(parents=True, exist_ok=True)
    app._server_pidfile().write_text(json.dumps({"pid": os.getpid(), "repo": A, **payload}))
    monkeypatch.setattr(app, "_is_our_server", lambda *a, **k: True)
    monkeypatch.setattr(app, "_is_guarded", lambda pid: True)
    monkeypatch.setattr(app, "_watch_server", lambda *a: None)
    app.restore_server_state()


def resident_file(app, pid, **data):
    app.APP_DIR.mkdir(parents=True, exist_ok=True)
    app._resident_file().write_text(json.dumps({"pid": pid, **data}), "utf-8")


class TestAdoptAndStop:
    def test_restore_reads_both(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        assert app_module.STATE.server_repos == [A, B]
        assert app_module.STATE.snapshot()["server"]["repos"] == [A, B]

    @pytest.mark.parametrize("repos", [None, [], "org/x", [1]])
    def test_a_pidfile_from_an_older_build_means_one_model(self, app_module, monkeypatch, repos):
        adopt(app_module, monkeypatch, **({} if repos is None else {"repos": repos}))
        assert app_module.STATE.server_repos == [A]

    def test_snapshot_shows_the_measured_state_of_this_server(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        events = [{"event": "load", "model": f"org/{i}"} for i in range(8)]
        resident_file(
            app_module,
            os.getpid(),
            resident=[B, A],
            budget_bytes=18,
            active_bytes=9,
            peak_bytes=12,
            multi=True,
            reason=None,
            events=events,
        )
        server = app_module.STATE.snapshot()["server"]
        assert server["resident"] == [B, A]
        assert server["memory"] == {
            "budget_bytes": 18, "active_bytes": 9, "peak_bytes": 12, "measured": True,
        }  # fmt: skip
        assert server["events"] == events[-5:]
        assert server["multi_off"] is None

    def test_multi_off_reason_is_passed_on(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        resident_file(app_module, os.getpid(), multi=False, reason="mlx_lm moved")
        server = app_module.STATE.snapshot()["server"]
        assert server["multi_off"] == "mlx_lm moved"
        assert (server["memory"], server["resident"]) == (None, [])

    @pytest.mark.parametrize("body", ["{", "[]", json.dumps({"pid": 1, "resident": [B]})])
    def test_a_stale_or_broken_resident_file_is_ignored(self, app_module, monkeypatch, body):
        adopt(app_module, monkeypatch)
        app_module._resident_file().write_text(body, "utf-8")
        server = app_module.STATE.snapshot()["server"]
        assert (server["resident"], server["memory"], server["events"]) == ([], None, [])

    def test_stop_forgets_both_and_removes_the_files(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        resident_file(app_module, os.getpid(), resident=[A, B])
        monkeypatch.setattr(app_module, "_is_our_server", lambda *a, **k: False)
        app_module.stop_server()
        assert app_module.STATE.server_repos == []
        assert not app_module._server_pidfile().exists()
        assert not app_module._resident_file().exists()

    def test_a_watcher_of_an_old_server_keeps_the_new_files(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        resident_file(app_module, os.getpid())
        app_module._clear_server_pidfile(only_pid=1)
        assert app_module._resident_file().exists()

    @pytest.mark.parametrize(("code", "memory"), [(-9, True), (1, False)])
    def test_a_crash_names_both_models(self, app_module, code, memory):
        class Dead:
            returncode = code

            def poll(self):
                return code

        s = app_module.STATE
        s.server_pid, s.server_repo, s.server_repos = 4242, A, [A, B]
        s.server_stopping = False
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module._watch_server(4242, A, Dead())
        assert "a and b" in s.server_down_notice
        assert ("out of memory" in s.server_down_notice) is memory
        assert s.server_repos == []


class TestOneModelMemory:
    def test_a_one_model_server_shows_its_memory(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch)
        resident_file(
            app_module,
            os.getpid(),
            resident=[A],
            budget_bytes=18,
            active_bytes=5,
            peak_bytes=6,
        )
        server = app_module.STATE.snapshot()["server"]
        assert server["resident"] == [A] and server["memory"]["active_bytes"] == 5
        assert server["multi_off"] is None


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
    def test_two_models(self, app_module, base_url, started):
        calls, done = started
        status, _ = post(base_url, "/api/server/start", {"repo": A, "also": [B]})
        assert status == 200 and done.wait(5)
        assert calls[0][0] == A and calls[0][3] == [B]
        assert app_module.STATE.server_pending == [A, B]

    @pytest.mark.parametrize("also", [None, []])
    def test_no_second_model(self, base_url, started, also):
        calls, done = started
        assert post(base_url, "/api/server/start", {"repo": A, "also": also})[0] == 200
        assert done.wait(5) and calls[0][3] == []

    @pytest.mark.parametrize(
        ("also", "why"),
        [
            (B, "list"),
            ([1], "list"),
            ([B, "org/c"], "one"),
            ([A], "different"),
            (["org/missing"], "installed"),
            (["--model"], "installed"),
        ],
    )
    def test_bad_second_model(self, app_module, base_url, started, also, why):
        status, body = post(base_url, "/api/server/start", {"repo": A, "also": also})
        assert status == 400 and why in body["error"]
        assert started[0] == [] and app_module.STATE.server_pending == []

    def test_the_first_model_must_be_installed_for_a_pair(self, base_url, started):
        status, body = post(base_url, "/api/server/start", {"repo": "org/no", "also": [B]})
        assert status == 400 and "installed" in body["error"]

    @pytest.mark.parametrize("repo", [A, B])
    def test_a_model_being_deleted(self, app_module, base_url, started, repo):
        app_module.STATE.deleting.add(repo)
        assert post(base_url, "/api/server/start", {"repo": A, "also": [B]})[0] == 409
        assert started[0] == [] and app_module.STATE.server_pending == []

    def test_over_budget_is_refused_with_the_numbers(self, app_module, base_url, started):
        cache_model(app_module, "org/huge", weights=20 * GB)
        status, body = post(base_url, "/api/server/start", {"repo": A, "also": ["org/huge"]})
        assert status == 409
        assert "GB" in body["error"] and body["estimate"]["fits"] is False
        assert started[0] == []

    def test_a_single_model_is_never_refused_for_size(self, app_module, base_url, started):
        cache_model(app_module, "org/huge", weights=40 * GB)
        assert post(base_url, "/api/server/start", {"repo": "org/huge"})[0] == 200

    def test_estimate_route(self, app_module, base_url, started):
        status, body = post(base_url, "/api/server/estimate", {"repo": A, "also": [B]})
        assert status == 200
        assert body == app_module.server_memory_estimate([A, B])
        assert post(base_url, "/api/server/estimate", {"repo": A, "also": [A]})[0] == 400
        assert started[0] == []

    def test_estimate_needs_the_token(self, server):
        host, port = server.server_address[:2]
        request = urllib.request.Request(
            f"http://{host}:{port}/api/server/estimate",
            data=b"{}",
            headers={"Content-Type": "application/json"},
        )
        with pytest.raises(urllib.error.HTTPError) as refused:
            urllib.request.urlopen(request, timeout=5)
        assert refused.value.code == 403


# 13. delete ------------------------------------------------------------------ #
class TestDeleteBlocked:
    def test_second_model_of_a_running_server(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch, repos=[A, B])
        with app_module.STATE.lock:
            assert app_module.STATE.in_use_locked(B) == "stop the API server first"

    def test_model_named_only_by_the_resident_file(self, app_module, monkeypatch):
        adopt(app_module, monkeypatch)
        resident_file(app_module, os.getpid(), resident=["org/c", A])
        with app_module.STATE.lock:
            assert app_module.STATE.in_use_locked("org/c") == "stop the API server first"
            assert app_module.STATE.in_use_locked("org/d") is None


# 14. one formula ------------------------------------------------------------- #
class TestEstimate:
    def test_same_numbers_as_the_guard(self, app_module, ram):
        cache_model(app_module, A, weights=4 * GB)
        cache_model(app_module, B, weights=4 * GB)
        guard = app_module._guard_module()
        est = app_module.server_memory_estimate([A, B])
        tokens = guard.context_tokens(int(0.52 * RAM), 8 * GB, 57_344)
        room, cache = guard.kv_reserve(CONFIG, tokens), 2 * 57_344 * tokens
        assert est["context_tokens"] == tokens == 28_672
        assert est["weights_bytes"] == 8 * GB
        assert est["reply_bytes"] == room
        assert est["cache_bytes"] == cache and est["margin_bytes"] == 14 * GB // 10
        assert est["budget_bytes"] == int(0.52 * RAM)
        assert est["total_bytes"] == 8 * GB + room + cache + 14 * GB // 10
        assert est["fits"] is True
        plan = guard.admission_plan(
            [guard.Resident(A, 4 * GB, room)],
            guard.Resident(B, 4 * GB, room),
            4 * GB + cache,
            0,
            est["budget_bytes"],
            2,
        )
        assert (plan.evict, plan.alone) == ([], False)

    def test_fit_flips_exactly_at_the_budget(self, app_module, ram, monkeypatch):
        cache_model(app_module, A, weights=4 * GB)
        cache_model(app_module, B, weights=4 * GB)
        monkeypatch.setattr(app_module, "_server_budget", lambda: 0)
        least = app_module.server_memory_estimate([A, B])["total_bytes"]  # at 8,192
        # Two bytes of room for rounding.
        for budget, fits in ((least + 2, True), (least - 10**6, False)):
            monkeypatch.setattr(app_module, "_server_budget", lambda b=budget: b)
            assert app_module.server_memory_estimate([A, B])["fits"] is fits

    def test_safe_context_while_both_are_loaded(self, app_module, ram):
        long = {**CONFIG, "max_position_embeddings": 1_000_000}
        cache_model(app_module, A, weights=4 * GB, config=long)
        cache_model(app_module, B, weights=4 * GB)
        est = app_module.server_memory_estimate([A, B])
        tokens = est["context_tokens"]
        assert [m["safe_context"] for m in est["models"]] == [tokens, tokens]

    def test_unreadable_config_has_no_safe_context(self, app_module, ram):
        cache_model(app_module, A, config={})
        est = app_module.server_memory_estimate([A])
        assert est["models"][0]["safe_context"] is None
        assert est["reply_bytes"] == int(6.4 * 131_072 * 32768)  # one model's factor

    def test_importing_the_guard_writes_no_bytecode_into_the_bundle(
        self, app_module, monkeypatch, tmp_path
    ):
        bundled = tmp_path / "mlx_guard.py"
        bundled.write_bytes(app_module.GUARD_SCRIPT.read_bytes())
        monkeypatch.setattr(app_module, "GUARD_SCRIPT", bundled)
        monkeypatch.setattr(sys, "dont_write_bytecode", False)
        assert app_module._guard_module().MEMORY_MARGIN == 14 * GB // 10
        assert [p.name for p in tmp_path.glob("**/*") if "pyc" in p.name] == []
        assert sys.dont_write_bytecode is False
