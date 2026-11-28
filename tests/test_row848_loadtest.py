"""The Load Test runner and its API (row 848, ADR-012).

No model is loaded and no real server runs: ``loadtest_fake.py`` is a small SSE
stand-in on a free port, and the memory figures are the ones each test sets.
"""

from __future__ import annotations

import json
import os
import signal
import stat
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import loadtest_fake as fake
import portguard
import pytest

ROOT = Path(__file__).resolve().parent.parent
GB = 10**9
K = 1024
RAM = 32 * 2**30
REPO, OTHER = "org/seven", "org/other"


def cache_repo(cache: Path, repo: str, files=("config.json", "model.safetensors")) -> Path:
    folder = cache / f"models--{repo.replace('/', '--')}"
    snapshot = folder / "snapshots" / ("a" * 40)
    snapshot.mkdir(parents=True)
    (folder / "refs").mkdir()
    (folder / "refs" / "main").write_text("a" * 40, encoding="utf-8")
    for name in files:
        (snapshot / name).write_text("{}", encoding="utf-8")
    return snapshot


@pytest.fixture
def lt():
    return fake.load_runner()


def sample(lt, percent=40.0, footprint=0, peak=0, pressure=1, swap=0):
    return lt.Sample(percent, footprint, peak, pressure, swap)


class TestSafetyConstants:
    def test_the_stops_that_stay(self, lt):
        assert (lt.PRESSURE_CRITICAL, lt.SWAP_GROWTH_BYTES) == (4, 500_000_000)
        assert (lt.SAMPLE_SECONDS, lt.MAX_GAP_SECONDS) == (0.1, 1.0)


class TestTrip:
    @pytest.mark.parametrize(
        ("change", "word"),
        [({"pressure": 4}, "pressure"), ({"swap": 6 * 10**8}, "swap")],
    )
    def test_each_memory_trigger(self, lt, change, word):
        assert word in lt.trip(sample(lt, **change), 0)

    def test_no_share_of_memory_stops_a_test(self, lt):
        assert lt.trip(sample(lt, percent=99.0, pressure=2, swap=5 * 10**8), 0) is None


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class Proc:
    pid = 999_999

    def __init__(self):
        self.signals = []

    def poll(self):
        return None

    def terminate(self):
        self.signals.append("TERM")

    def kill(self):
        self.signals.append("KILL")


class TestMonitor:
    def monitor(self, lt, probe, **extra):
        clock, proc = Clock(), Proc()
        cancel, abandon = threading.Event(), threading.Event()
        watch = lt.Monitor(
            proc, probe, clock, base_swap=0, deadline=clock.now + 900,
            cancel=cancel, abandon=abandon, killer=lambda p: p.kill(), **extra,
        )  # fmt: skip
        return watch, clock, proc, cancel, abandon

    def test_critical_pressure_kills_at_once(self, lt):
        watch, _, proc, _, _ = self.monitor(lt, lambda pid: sample(lt, 78.0, pressure=4))
        watch.tick()
        assert proc.signals == ["KILL"] and watch.reason[0] == "memory"
        assert watch.peak_percent == 78.0

    def test_an_unreadable_probe_kills(self, lt):
        def broken(pid):
            raise OSError("no")

        watch, _, proc, _, _ = self.monitor(lt, broken)
        watch.tick()
        assert proc.signals == ["KILL"] and watch.reason[0] == "memory"

    def test_a_sample_gap_over_a_second_is_an_interruption(self, lt):
        watch, clock, proc, _, _ = self.monitor(lt, lambda pid: sample(lt))
        watch.tick()
        clock.now += 1.0
        watch.tick()
        assert proc.signals == []
        clock.now += 1.5
        watch.tick()
        assert proc.signals == ["KILL"] and watch.reason[0] == "interrupted"

    @pytest.mark.parametrize(("stalled", "seconds"), [(False, 901), (True, 121)])
    def test_the_size_timeout_and_a_silent_stream(self, lt, stalled, seconds):
        watch, clock, proc, _, _ = self.monitor(lt, lambda pid: sample(lt))
        if stalled:
            watch.begin_step()
        for _ in range(seconds):
            clock.now += 1.0
            watch.tick()
        assert proc.signals[0] == "KILL" and watch.reason == ("failed", "timeout")

    def test_cancel_terminates_then_kills_after_five_seconds(self, lt):
        watch, clock, proc, cancel, _ = self.monitor(lt, lambda pid: sample(lt))
        cancel.set()
        watch.tick()
        assert proc.signals == ["TERM"] and watch.reason[0] == "cancelled"
        for _ in range(6):
            clock.now += 1.0
            watch.tick()
        assert proc.signals[:2] == ["TERM", "KILL"]

    def test_a_closed_app_pipe_kills_at_once(self, lt):
        watch, _, proc, _, abandon = self.monitor(lt, lambda pid: sample(lt))
        abandon.set()
        watch.tick()
        assert proc.signals == ["KILL"]


class TestPrompts:
    def test_close_to_the_size_and_never_above_it(self, lt):
        text = lt.make_prompt(2000, "run1", "a", fake.count_words)
        assert 1900 <= fake.count_words(text) <= 2000

    def test_a_run_id_goes_first_so_nothing_is_reused(self, lt):
        first = lt.make_prompt(200, "run1", "a", fake.count_words)
        second = lt.make_prompt(200, "run1", "b", fake.count_words)
        assert first.splitlines()[0] != second.splitlines()[0]
        assert "run1" in first.splitlines()[0]


def job(tmp_path, sizes=(4096, 8192), **extra):
    return {
        "run_id": "run1",
        "repo": REPO,
        "sizes": list(sizes),
        "reply_tokens": 32,
        "rounds": 2,
        "timeout_seconds": 60,
        "request_model": REPO,
        "snapshot": str(tmp_path),
        "command": ["python", "fake", "--model", REPO, "--port", "0"],
        "workdir": str(tmp_path),
        **extra,
    }


def run_job(lt, tmp_path, probe=None, mode="ok", cancel=None, **extra):
    events, spawned = [], []
    spawn = fake.spawner(mode, spawned)
    runner = lt.Runner(
        job(tmp_path, **extra),
        events.append,
        spawn=spawn,
        probe=probe or (lambda pid: sample(lt)),
        clock=time.monotonic,
        count_tokens=fake.count_words,
        cancel=cancel,
    )
    status, reason = runner.run()
    assert [p.pid for p in spawned if p.poll() is None] == []
    rows = {e["row"]["size"]: e["row"] for e in events if e["event"] == "row"}
    return status, reason, rows, events, spawned


class TestRunner:
    def test_every_size_passes_with_its_figures(self, lt, tmp_path):
        status, _, rows, events, spawned = run_job(lt, tmp_path, sizes=(8192, 4096))
        assert status == "done"
        assert list(rows) == [4096, 8192]
        row = rows[4096]
        assert row["status"] == "passed" and row["steps_done"] == 4
        assert row["prompt_tokens"] > 3000
        assert row["read_tps"] > 0 and row["write_tps"] > 0
        assert row["first_token_s"] >= 0 and row["follow_up_first_token_s"] >= 0
        assert row["peak_percent"] == 40.0
        assert len(spawned) == 2  # a fresh server for each size
        assert any(e["event"] == "progress" and e.get("total") for e in events)
        assert any(e["event"] == "server" and e["pid"] == spawned[0].pid for e in events)

    def test_the_server_gets_a_free_port(self, lt, tmp_path):
        *_, spawned = run_job(lt, tmp_path, sizes=(4096,))
        port = int(spawned[0].args[spawned[0].args.index("--port") + 1])
        assert port > 0 and port not in portguard.RESERVED_PORTS

    def test_no_prompt_or_reply_text_is_reported(self, lt, tmp_path):
        *_, events, _ = run_job(lt, tmp_path, sizes=(4096,))
        text = json.dumps(events)
        assert "def block" not in text and fake.REPLY_WORD not in text

    def test_few_reply_tokens_give_no_write_speed(self, lt, tmp_path):
        _, _, rows, *_ = run_job(lt, tmp_path, mode="short", sizes=(4096,))
        assert rows[4096]["write_tps"] is None

    def test_a_high_share_of_memory_alone_stops_nothing(self, lt, tmp_path):
        status, _, rows, *_ = run_job(lt, tmp_path, probe=lambda pid: sample(lt, 95.0))
        assert status == "done"
        assert [r["status"] for r in rows.values()] == ["passed", "passed"]

    def test_critical_pressure_stops_this_size_and_all_larger(self, lt, tmp_path):
        def probe(pid):
            return sample(lt, 78.0, pressure=4 if pid else 1)

        status, _, rows, _, spawned = run_job(lt, tmp_path, probe=probe)
        assert status == "memory" and len(spawned) == 1
        assert rows[4096]["status"] == "stopped" and rows[4096]["peak_percent"] == 78.0
        assert rows[8192]["status"] == "skipped"

    def test_another_program_on_the_port_is_never_asked(self, lt, tmp_path, monkeypatch):
        import engine_runtime

        monkeypatch.setattr(engine_runtime, "listeners", lambda port, **_: {1})
        status, reason, *_, spawned = run_job(lt, tmp_path, sizes=(4096,))
        assert status == "failed" and "another program" in reason
        assert len(spawned) == 3

    @pytest.mark.parametrize(
        ("mode", "word"),
        [("too-long", "maximum context"), ("cut", "cut short"), ("load-failed", "load")],
    )
    def test_a_failure_stops_the_run(self, lt, tmp_path, mode, word):
        status, reason, rows, *_ = run_job(lt, tmp_path, mode=mode)
        assert status == "failed" and word in reason
        assert [r["status"] for r in rows.values()] == ["failed", "skipped"]

    def test_a_server_that_dies_while_starting_is_tried_three_times(self, lt, tmp_path):
        status, _, _, _, spawned = run_job(lt, tmp_path, mode="die")
        assert status == "failed" and len(spawned) == 3

    @pytest.mark.parametrize("mode", ["slow-load", "slow-reply"])
    def test_cancel_at_load_and_in_a_reply(self, lt, tmp_path, mode):
        cancel = threading.Event()
        threading.Timer(1.0, cancel.set).start()
        status, _, rows, *_ = run_job(lt, tmp_path, mode=mode, cancel=cancel)
        assert status == "cancelled" and rows[4096]["status"] == "cancelled"


class TestMacProbe:
    def test_it_reads_this_mac_or_says_it_cannot(self, lt):
        probe = lt.mac_probe
        if sys.platform != "darwin":
            with pytest.raises(OSError):
                probe(None)
            return
        got = probe(os.getpid())
        assert 0 < got.percent <= 100
        assert 10**6 < got.footprint <= got.peak < 10**11
        assert got.pressure in (1, 2, 4) and got.swap >= 0
        assert probe(None).footprint == 0


def alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    state = subprocess.run(
        ["ps", "-p", str(pid), "-o", "stat="], capture_output=True, text=True, check=False
    ).stdout.strip()
    return bool(state) and not state.startswith("Z")


def until(check, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        if value := check():
            return value
        time.sleep(0.05)
    raise AssertionError("timed out")


class TestNoOrphans:
    def start(self, tmp_path):
        proc = subprocess.Popen(
            [sys.executable, str(fake.__file__), "runner", "slow-reply"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
        )
        proc.stdin.write(json.dumps(job(tmp_path, sizes=(4096,))) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:
            event = json.loads(line)
            if event["event"] == "server":
                return proc, event["pid"]
        raise AssertionError("no server started")

    def test_a_closed_pipe_ends_the_runner_and_its_server(self, tmp_path):
        proc, server = self.start(tmp_path)
        proc.stdin.close()
        proc.wait(timeout=15)
        until(lambda: not alive(server))

    def test_cancel_on_the_pipe(self, tmp_path):
        proc, server = self.start(tmp_path)
        proc.stdin.write("cancel\n")
        proc.stdin.flush()
        out = proc.stdout.read()
        proc.wait(timeout=15)
        assert json.loads(out.splitlines()[-1])["status"] == "cancelled"
        until(lambda: not alive(server))

    def test_a_killed_runner_closes_its_servers_pipe(self, tmp_path):
        """Only a server that reads the pipe (the stand-in) ends by this alone."""
        proc, server = self.start(tmp_path)
        proc.kill()
        proc.wait(timeout=15)
        proc.stdin.close()
        until(lambda: not alive(server))


class TestPortGuard:
    def test_a_real_runner_is_refused(self):
        assert portguard.real_launch([sys.executable, str(ROOT / "mlx_loadtest.py")])
        assert not portguard.real_launch([sys.executable, str(fake.__file__), "runner", "ok"])


# -- the API ----------------------------------------------------------------- #
def call(base, path, body=None, headers=None):
    request = urllib.request.Request(
        f"{base}{path}",
        data=None if body is None else json.dumps(body).encode(),
        headers=headers or ({} if body is None else {"Content-Type": "application/json"}),
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def api(app_module, base_url, monkeypatch, tmp_path):
    """The app with one installed model and a fake runner (``api.mode``)."""
    app = app_module
    folder = cache_repo(app.CACHE_DIR, REPO)
    config = {"num_hidden_layers": 2, "num_attention_heads": 1, "hidden_size": 250}
    (folder / "config.json").write_text(
        json.dumps({**config, "max_position_embeddings": 16384}), "utf-8"
    )
    app.APP_DIR.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(app, "_complete_models", lambda: {REPO})
    monkeypatch.setattr(app, "hardware", lambda: {"ram_bytes": RAM})

    class Api:
        mode = "ok"
        module = app

        def get(self, path):
            return call(base_url, path)

        def post(self, path, body, **kw):
            return call(base_url, path, body, **kw)

        def start(self, sizes=(4096, 8192)):
            return self.post("/api/loadtest/start", {"repo": REPO, "sizes": list(sizes)})

        def finished(self):
            return until(lambda: not app.STATE.loadtest["active"] and app.STATE.loadtest)

    handle = Api()
    monkeypatch.setattr(
        app,
        "_loadtest_command",
        lambda: [sys.executable, str(fake.__file__), "runner", handle.mode],
    )
    yield handle
    app.shutdown_loadtest()


ROUTES = ("/api/loadtest/start", "/api/loadtest/cancel", "/api/loadtest/apply")


class TestRouteChecks:
    @pytest.mark.parametrize("path", ROUTES)
    def test_token_origin_and_content_type(self, api, path):
        assert path not in api.module.TOKEN_EXEMPT
        assert api.post(path, {}, headers={"Content-Type": "text/plain"})[0] == 415
        foreign = {"Content-Type": "application/json", "Origin": "http://evil.example"}
        assert api.post(path, {}, headers=foreign)[0] == 403

    @pytest.mark.parametrize("path", [*ROUTES, "/api/loadtest?repo=a", "/api/config"])
    def test_no_token_no_answer(self, server, path):
        url = f"http://127.0.0.1:{server.server_address[1]}{path}"
        body = b"{}" if path in ROUTES else None
        request = urllib.request.Request(url, body, {"Content-Type": "application/json"})
        with pytest.raises(urllib.error.HTTPError) as failed:
            urllib.request.urlopen(request, timeout=10)
        assert failed.value.code == 403

    @pytest.mark.parametrize(
        "sizes",
        [None, [], "4096", [4095], [32768], [4096, 4096], [4096.0], [True], [4096] * 9,
         list(range(4096, 4109))],
    )  # fmt: skip
    def test_bad_sizes(self, api, sizes):
        assert api.post("/api/loadtest/start", {"repo": REPO, "sizes": sizes})[0] == 400
        assert api.module.STATE.loadtest is None

    def test_a_model_that_is_not_installed(self, api):
        body = {"repo": OTHER, "sizes": [4096]}
        assert api.post("/api/loadtest/start", body)[0] == 400

    def test_cancel_when_idle(self, api):
        assert api.post("/api/loadtest/cancel", {})[0] == 404


class TestApiRun:
    def test_a_run_end_to_end(self, api):
        app = api.module
        status, body = api.start()
        assert status == 200
        run = api.finished()
        assert run["status"] == "done"
        status, shown = api.get(f"/api/loadtest?repo={REPO}")
        assert status == 200
        result = shown["results"][-1]
        assert result["id"] == body["run_id"] and result["stale"] is False
        assert [r["status"] for r in result["rows"]] == ["passed", "passed"]
        assert result["fingerprint"]["ram_bytes"] == RAM
        saved = app._loadtest_file()
        assert stat.S_IMODE(saved.stat().st_mode) == 0o600
        assert "def block" not in saved.read_text() and fake.REPLY_WORD not in saved.read_text()
        assert list(app.APP_DIR.glob("loadtest-run-*")) == []
        assert not app._loadtest_pidfile().exists()
        assert app.STATE.snapshot()["loadtest"]["status"] == "done"
        # The "apply this size" route is gone: a result changes no setting.
        mine = {"repo": REPO, "run_id": body["run_id"], "size": 8192}
        assert api.post("/api/loadtest/apply", mine)[0] == 404
        assert app.user_settings(REPO)["max_context_tokens"] is None

    def test_a_result_made_with_other_settings_is_stale(self, api):
        api.start((4096,))
        api.finished()
        api.module.save_settings({"prompt_cache_size": 2}, REPO)
        assert api.get(f"/api/loadtest?repo={REPO}")[1]["results"][-1]["stale"] is True

    def test_the_command_is_the_servers_with_its_own_port_and_files(self, api, monkeypatch):
        app = api.module
        app.save_settings({"max_context_tokens": 5000, "prompt_cache_size": 2}, REPO)
        seen = {}
        real = app._loadtest_job

        def spy(*args, **kwargs):
            job_, env = real(*args, **kwargs)
            seen.update(job=job_, env=env)
            return job_, env

        monkeypatch.setattr(app, "_loadtest_job", spy)
        api.start((4096, 8192))
        api.finished()
        cmd = seen["job"]["command"]
        # mlx_lm has no context flag, so the sizes change nothing in its command.
        asked = app.server_settings([REPO])
        assert cmd == app._server_command(REPO, asked, 0, {})
        assert cmd[1:4] == ["-m", "mlx_lm", "server"]
        assert cmd[cmd.index("--host") + 1] == "127.0.0.1"
        assert cmd[cmd.index("--prompt-cache-size") + 1] == "2"
        assert "--kv-bits" not in cmd
        env = seen["env"]
        assert not [name for name in env if name.startswith("MLX_STUDIO")]
        assert env["HF_HUB_OFFLINE"] == "1" and env["PYTHONDONTWRITEBYTECODE"] == "1"

    def test_busy_while_it_runs_and_cancel(self, api):
        app = api.module
        api.mode = "slow-reply"
        assert api.start((4096,))[0] == 200
        assert api.start((4096,))[0] == 409
        for path, body in (
            ("/api/server/start", {"repo": REPO}),
            ("/api/install", {}),
            ("/api/download", {"repo": REPO}),
            ("/api/delete", {"repo": REPO}),
        ):
            status, answer = api.post(path, body)
            assert status == 409 and "load test" in answer["error"], path
        assert app.STATE.reserve_download(REPO) is None
        assert app._launch_server(REPO, False) is None
        assert api.get(f"/api/loadtest?repo={REPO}")[1]["status"]["active"] is True
        pid = until(lambda: app.STATE.loadtest.get("server_pid"))
        assert api.post("/api/loadtest/cancel", {})[0] == 200
        assert api.finished()["status"] == "cancelled"
        until(lambda: not alive(pid))

    def test_quitting_the_app_ends_the_run(self, api):
        api.mode = "slow-reply"
        api.start((4096,))
        pid = until(lambda: api.module.STATE.loadtest.get("server_pid"))
        api.module.shutdown_loadtest()
        until(lambda: not alive(pid))
        assert api.module.STATE.loadtest["active"] is False
        assert "shutdown_loadtest" in api.module.main.__code__.co_names
        assert "restore_loadtest" in api.module.main.__code__.co_names

    @pytest.mark.parametrize("busy", ["running", "pending", "adopted", "install", "download"])
    def test_refused_while_something_else_runs(self, api, busy):
        app = api.module
        if busy == "running":
            app.STATE.server_proc = Proc()
        elif busy == "pending":
            app.STATE.server_pending.append(OTHER)
        elif busy == "adopted":
            app.STATE.server_pid = os.getpid()
        elif busy == "install":
            app.reserve = app.STATE.reserve_install("install")
        else:
            app.STATE.reserve_download(REPO)
        assert api.start((4096,))[0] == 409
        assert app.STATE.loadtest is None
        app.STATE.server_proc = app.STATE.server_pid = None

    def test_a_failed_run_logs_the_tail_and_removes_its_files(self, api):
        api.mode = "load-failed"
        api.start((4096,))
        assert api.finished()["status"] == "failed"
        assert fake.LOG_LINE in api.module.APP_LOG.read_text("utf-8")
        assert list(api.module.APP_DIR.glob("loadtest-run-*")) == []

    def test_a_runner_that_vanishes_is_a_failed_run(self, api):
        api.mode = "vanish"
        api.start((4096,))
        run = api.finished()
        assert run["status"] == "failed" and "unexpectedly" in run["record"]["reason"]

    @pytest.mark.parametrize("broken", ["not json", "[1]", '{"runs": 5}'])
    def test_a_broken_result_file(self, api, broken):
        app = api.module
        app._loadtest_file().write_text(broken, "utf-8")
        assert api.get(f"/api/loadtest?repo={REPO}")[1]["results"] == []
        api.get(f"/api/loadtest?repo={REPO}")
        assert app.APP_LOG.read_text("utf-8").count("loadtest.json") == 1

    def test_results_stay_in_memory_when_the_file_cannot_be_written(self, api):
        app = api.module
        app._loadtest_file().mkdir()
        api.start((4096,))
        assert api.finished()["status"] == "done"
        shown = api.get(f"/api/loadtest?repo={REPO}")[1]["results"]
        assert shown[-1]["rows"][0]["status"] == "passed"

    def test_five_runs_are_kept_for_a_model(self, api):
        app = api.module
        for number in range(7):
            app._save_loadtest({"id": str(number), "repo": REPO, "status": "done", "rows": []})
        app._save_loadtest({"id": "o", "repo": OTHER, "status": "done", "rows": []})
        assert [r["id"] for r in app._read_loadtest()] == ["2", "3", "4", "5", "6", "o"]

    def test_the_page_lists_the_sizes_the_model_allows(self, api):
        shown = api.get(f"/api/loadtest?repo={REPO}")[1]
        assert shown["sizes"]["max"] == 16384 and shown["sizes"]["min"] == 4096
        assert {"size": 32768, "allowed": False} in shown["sizes"]["offered"]
        assert shown["status"] is None and shown["blocked"] is None


class TestLeftovers:
    def sleeper(self, *words):
        return subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)", *words],
            start_new_session=True,
        )

    # The second is the server of a build before 2.0.13, left behind by it.
    @pytest.mark.parametrize("start", [["-m", "mlx_lm", "server"], ["/x/mlx_guard.py"]])
    @pytest.mark.parametrize("ours", [True, False])
    def test_only_a_process_with_our_identity_is_signalled(self, app_module, ours, start):
        app = app_module
        app.APP_DIR.mkdir(parents=True, exist_ok=True)
        served = ["--model", REPO if ours else OTHER, "--host", "127.0.0.1"]
        words = [*start, *served]
        proc = self.sleeper(*words)
        try:
            until(lambda: app._ps_field(proc.pid, "lstart"))
            (app.APP_DIR / "loadtest-run-old").mkdir()
            app._save_loadtest({"id": "1", "repo": REPO, "status": "running", "rows": []})
            app._loadtest_pidfile().write_text(
                json.dumps(
                    {
                        "repo": REPO,
                        "server": proc.pid,
                        "server_started": app._ps_field(proc.pid, "lstart"),
                    }
                )
            )
            app.restore_loadtest()
            if ours:
                assert proc.wait(timeout=10) == -signal.SIGKILL
            else:
                time.sleep(0.3)
                assert proc.poll() is None
            assert app._read_loadtest()[0]["status"] == "interrupted"
            assert not app._loadtest_pidfile().exists()
            assert list(app.APP_DIR.glob("loadtest-run-*")) == []
        finally:
            proc.kill()
            proc.wait(timeout=10)

    def test_the_app_ends_the_server_of_a_runner_that_is_gone(self, app_module, tmp_path):
        """mlx_lm's server does not follow its parent, so the app's watcher ends it."""
        app = app_module
        app.APP_DIR.mkdir(parents=True, exist_ok=True)
        server = self.sleeper("-m", "mlx_lm", "server", "--model", REPO, "--host", "127.0.0.1")
        try:
            until(lambda: app._ps_field(server.pid, "lstart"))
            runner = subprocess.Popen([sys.executable, "-c", "pass"], stdout=subprocess.PIPE)
            record = {"id": "1", "repo": REPO, "status": "running", "rows": []}
            run = {"proc": runner, "record": record, "repo": REPO, "cancel": False, "active": True}
            app._write_loadtest_pidfile(run, server=server.pid)
            app._watch_loadtest(run, tmp_path / "work")
            assert server.wait(timeout=10) == -signal.SIGKILL
            assert record["status"] == "failed" and "unexpectedly" in record["reason"]
        finally:
            server.kill()
            server.wait(timeout=10)
