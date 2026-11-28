"""Row 831: Apply restarts the server, and a failed start brings the previous set back.

No process is started and no model is loaded: Popen is a stand-in.
"""

from __future__ import annotations

import importlib.util
import signal
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "row656_app_for831", ROOT / "tests/test_row656_app.py"
)
apps = importlib.util.module_from_spec(spec)
spec.loader.exec_module(apps)
cache_model, post, ram = apps.cache_model, apps.post, apps.ram

A, B, C = "org/a", "org/b", "org/c"


class Proc:
    def __init__(self, pid, cmd):
        self.pid, self.cmd = pid, cmd
        self.returncode = None
        self.stdout = iter(())

    def poll(self):
        return self.returncode

    def wait(self, timeout=None):
        if self.returncode is None:
            self.returncode = 0
        return self.returncode

    def terminate(self):
        self.returncode = -15

    kill = terminate


@pytest.fixture
def rig(app_module, monkeypatch, ram):
    """A server is running ``org/a`` in the background; later starts are recorded."""
    app = app_module
    for repo in (A, B, C):
        cache_model(app, repo)
    made, watched, broken = [], [], set()

    def popen(cmd, **kwargs):
        repo = cmd[cmd.index("--model") + 1]
        if repo in broken:
            raise OSError(f"cannot start {repo}")
        made.append(Proc(5000 + len(made), cmd))
        return made[-1]

    monkeypatch.setattr(app.subprocess, "Popen", popen)
    monkeypatch.setattr(app, "_ps_field", lambda pid, field: "")
    real_watch = app._watch_server
    monkeypatch.setattr(app, "_watch_server", lambda *args: watched.append(args))
    # A real foreground start reads output until the server exits.
    monkeypatch.setattr(app, "_pump_output", lambda proc: None)
    # What the probe of the direct server sees: /health of each pid, None: no answer.
    health = {}
    probe = app.engine_runtime
    monkeypatch.setattr(probe, "listeners", lambda port, **k: {app.STATE.server_pid})
    monkeypatch.setattr(probe, "listener_is", lambda port, pid, **k: True)
    monkeypatch.setattr(probe, "health", lambda port: health.get(app.STATE.server_pid))
    monkeypatch.setattr(probe, "warm_up", lambda *args: True)
    app.worker_server_start(A, True)
    watched.clear()

    class Rig:
        pass

    r = Rig()
    r.app, r.made, r.watched, r.broken, r.watch = app, made, watched, broken, real_watch
    r.health = health
    return r


def models(proc):
    return [proc.cmd[proc.cmd.index("--model") + 1]]


# refused or not spawned: the caller's own thread brings the old set back -------- #
class TestFailsAtOnce:
    def test_a_spawn_error_brings_the_old_set_back(self, rig):
        rig.broken.add(B)
        rig.app.worker_server_start(B, False)
        s = rig.app.STATE
        assert s.server_repos == [A] and s.server_background is True
        assert "cannot start org/b" in s.server_apply_notice

    def test_both_failing_is_never_silent(self, rig):
        rig.broken.update({A, B})
        rig.app.worker_server_start(B, False)
        s = rig.app.STATE
        assert s.server_pid is None and s.server_apply_notice is None
        notice = s.server_down_notice
        assert "cannot start org/b" in notice and "cannot start org/a" in notice
        assert "The API server is stopped." in notice

    def test_stop_pressed_meanwhile_keeps_it_stopped(self, rig, monkeypatch):
        def popen(cmd, **kwargs):
            rig.app.STATE.cancel_pending_starts()
            raise OSError("boom")

        monkeypatch.setattr(rig.app.subprocess, "Popen", popen)
        rig.app.worker_server_start(B, False)
        assert rig.app.STATE.server_pid is None and len(rig.made) == 1
        assert rig.app.STATE.server_apply_notice is None

    def test_a_first_start_that_fails_starts_nothing_else(self, rig):
        rig.app.stop_server()
        rig.broken.add(B)
        rig.app.worker_server_start(B, False)
        assert len(rig.made) == 1 and rig.app.STATE.server_apply_notice is None

    def test_a_server_that_already_died_is_not_brought_back(self, rig):
        rig.made[0].returncode = 1
        rig.broken.add(B)
        rig.app.worker_server_start(B, False)
        assert len(rig.made) == 1 and rig.app.STATE.server_pid is None

    def test_pending_covers_the_whole_replacement(self, rig, monkeypatch):
        seen = []
        real = rig.app.subprocess.Popen

        def popen(cmd, **kwargs):
            seen.append(rig.app.STATE.snapshot()["server"]["starting"])
            return real(cmd, **kwargs)

        rig.broken.add(B)
        monkeypatch.setattr(rig.app.subprocess, "Popen", popen)
        rig.app.STATE.server_pending.append(B)
        rig.app.worker_server_start(B, False)
        assert seen == [True, True]
        assert rig.app.STATE.snapshot()["server"]["starting"] is False

    def test_the_next_start_and_stop_drop_the_notice(self, rig):
        rig.broken.add(B)
        rig.app.worker_server_start(B, False)
        assert rig.app.STATE.snapshot()["server"]["apply_notice"]
        rig.app.worker_server_start(C, False)
        assert rig.app.STATE.snapshot()["server"]["apply_notice"] is None
        rig.broken.add(A)
        rig.app.STATE.server_apply_notice = "x"
        rig.app.stop_server()
        assert rig.app.STATE.server_apply_notice is None


# it starts, then fails: the watcher brings the old set back ---------------------- #
def replaced(rig, repo=B):
    """Replace the running server; returns the new process and its watcher's arguments."""
    rig.app.worker_server_start(repo, True)
    return rig.made[-1], rig.watched.pop()


def ready(rig, proc):
    """The server answers, and the probe has seen it."""
    rig.health[proc.pid] = 200
    rig.app._probe_direct(proc.pid)


def run_watch(rig, monkeypatch, args, steps):
    """Run the real watcher; each sleep runs the next step."""
    todo = list(steps)

    def sleep(_seconds):
        assert todo, "the watcher is still waiting"
        todo.pop(0)()

    monkeypatch.setattr(rig.app.time, "sleep", sleep)
    rig.watch(*args)


class TestFailsAfterTheStart:
    def test_an_early_exit_brings_the_old_set_back(self, rig, monkeypatch):
        proc, args = replaced(rig)
        rig.app.STATE.server_lines.append("mlx_lm: refusing to start")
        run_watch(rig, monkeypatch, args, [lambda: setattr(proc, "returncode", 1)])
        s = rig.app.STATE
        assert models(rig.made[-1]) == [A] and s.server_repos == [A]
        assert s.server_down_notice is None
        assert "exited with status 1" in s.server_apply_notice
        assert "mlx_lm: refusing to start" in s.server_apply_notice
        # The old set is watched as an ordinary start: one retry, never a loop.
        assert len(rig.watched[-1]) == 3

    def test_a_model_that_cannot_load_is_replaced_although_alive(self, rig, monkeypatch):
        proc, args = replaced(rig)
        run_watch(rig, monkeypatch, args, [lambda: rig.health.update({proc.pid: 503})])
        assert proc.returncode == -15, "the useless process is stopped"
        assert rig.app.STATE.server_repos == [A]
        assert "could not be loaded" in rig.app.STATE.server_apply_notice

    def test_a_death_after_it_was_ready_is_reported_as_before(self, rig, monkeypatch):
        proc, args = replaced(rig)
        steps = [
            lambda: rig.health.update({proc.pid: 200}),
            lambda: None,
            lambda: setattr(proc, "returncode", 1),
        ]
        run_watch(rig, monkeypatch, args, steps)
        assert rig.app.STATE.server_pid is None and len(rig.made) == 2
        assert "stopped unexpectedly" in rig.app.STATE.server_down_notice

    def test_one_that_never_answers_is_stopped_and_the_old_set_comes_back(self, rig, monkeypatch):
        proc, args = replaced(rig)
        now = [0.0]
        monkeypatch.setattr(rig.app.time, "monotonic", lambda: now[0])
        late = rig.app.START_WATCH_SECONDS + 100.0
        run_watch(rig, monkeypatch, args, [lambda: now.__setitem__(0, late)])
        assert proc.returncode == -15 and rig.app.STATE.server_repos == [A]
        assert "did not answer in time" in rig.app.STATE.server_apply_notice

    def late(self, rig, monkeypatch):
        """The clock, and the step that moves it past the start watch."""
        now = [0.0]
        monkeypatch.setattr(rig.app.time, "monotonic", lambda: now[0])
        later = rig.app.START_WATCH_SECONDS + 100.0
        return lambda: now.__setitem__(0, later)

    def test_a_first_start_that_never_answers_is_stopped(self, rig, monkeypatch):
        rig.app.stop_server()
        proc, args = replaced(rig)
        run_watch(rig, monkeypatch, args, [self.late(rig, monkeypatch)])
        s = rig.app.STATE
        assert proc.returncode == -15 and s.server_pid is None and len(rig.made) == 2
        assert "did not answer in time" in s.server_down_notice

    def test_one_that_is_ready_is_left_running_after_the_watch(self, rig, monkeypatch):
        proc, args = replaced(rig)
        steps = [
            lambda: rig.health.update({proc.pid: 200}),
            self.late(rig, monkeypatch),
            lambda: None,
            lambda: setattr(proc, "returncode", 1),
        ]
        run_watch(rig, monkeypatch, args, steps)
        assert proc.returncode == 1  # it exited by itself; the app did not stop it
        assert "did not answer" not in rig.app.STATE.server_down_notice

    def test_an_adopted_one_whose_port_cannot_be_read_is_stopped(self, rig, monkeypatch):
        app, killed = rig.app, []
        proc, args = replaced(rig)
        app.STATE.server_proc, app.STATE.server_fallback = None, None
        monkeypatch.setattr(app.engine_runtime, "listener_is", lambda *a, **k: False)
        monkeypatch.setattr(app, "_pid_alive", lambda pid: not killed)
        monkeypatch.setattr(app, "_still_our_server", lambda *a: True)
        monkeypatch.setattr(app.os, "kill", lambda pid, number: killed.append((pid, number)))
        run_watch(rig, monkeypatch, (proc.pid, args[1], None), [self.late(rig, monkeypatch)])
        assert killed == [(proc.pid, signal.SIGKILL)]
        assert "did not answer in time" in app.STATE.server_down_notice

    def test_stop_meanwhile_keeps_it_stopped(self, rig, monkeypatch):
        proc, args = replaced(rig)

        def stop():
            rig.app.STATE.cancel_pending_starts()
            proc.returncode = 1

        run_watch(rig, monkeypatch, args, [stop])
        assert len(rig.made) == 2

    def test_a_newer_start_waiting_wins(self, rig, monkeypatch):
        proc, args = replaced(rig)

        def queue():
            rig.app.STATE.server_pending.append(C)
            proc.returncode = 1

        run_watch(rig, monkeypatch, args, [queue])
        assert len(rig.made) == 2

    def test_a_first_start_has_nothing_to_watch_for(self, rig):
        rig.app.stop_server()
        rig.app.worker_server_start(B, True)
        assert len(rig.watched[-1]) == 3


# a second Apply before the new server is ready -------------------------------- #
def stop(rig, base_url):
    assert post(base_url, "/api/server/stop", {})[0] == 200


class TestLastGoodSet:
    def test_the_same_apply_twice_still_falls_back_to_the_old_set(self, rig, monkeypatch):
        replaced(rig)
        proc, args = replaced(rig)
        run_watch(rig, monkeypatch, args, [lambda: setattr(proc, "returncode", 1)])
        assert models(rig.made[-1]) == [A] and rig.app.STATE.server_repos == [A]
        assert rig.app.STATE.server_apply_notice.endswith("a was started again.")

    def test_a_failed_apply_while_loading_brings_the_old_set_back(self, rig):
        replaced(rig)
        rig.broken.add(C)
        rig.app.worker_server_start(C, False)
        s = rig.app.STATE
        assert models(rig.made[-1]) == [A] and s.server_background is True
        assert s.server_apply_notice.endswith("a was started again.")

    def test_a_server_that_reported_ready_is_the_next_fallback(self, rig, monkeypatch):
        proc, args = replaced(rig)
        steps = [
            lambda: rig.health.update({proc.pid: 200}),
            lambda: setattr(proc, "returncode", 1),
        ]
        run_watch(rig, monkeypatch, args, steps)
        assert rig.app.STATE.server_fallback is None

    def test_ready_counts_before_the_watcher_has_looked(self, rig):
        proc, _ = replaced(rig)
        ready(rig, proc)
        rig.broken.add(C)
        rig.app.worker_server_start(C, True)
        assert rig.app.STATE.server_repos == [B]

    def test_an_old_watcher_does_not_drop_the_fallback_of_a_newer_start(self, rig, monkeypatch):
        _, old = replaced(rig)
        replaced(rig, C)
        times = [0.0]
        later = rig.app.START_WATCH_SECONDS + 100.0
        monkeypatch.setattr(rig.app.time, "monotonic", lambda: times.pop() if times else later)
        run_watch(rig, monkeypatch, old, [])
        assert rig.app.STATE.server_fallback == ([A], True)

    def test_it_dies_while_the_next_apply_waits_and_that_one_fails(self, rig, monkeypatch):
        proc, args = replaced(rig)

        def queue():
            rig.app.STATE.server_pending.append(C)
            proc.returncode = 1

        run_watch(rig, monkeypatch, args, [queue])
        rig.broken.add(C)
        rig.app.worker_server_start(C, False)
        assert rig.app.STATE.server_repos == [A]

    def test_one_try_only(self, rig):
        rig.broken.add(B)
        rig.app.worker_server_start(B, False)
        assert rig.app.STATE.server_fallback is None

    def test_both_failing_leaves_no_fallback(self, rig):
        rig.broken.update({A, B})
        rig.app.worker_server_start(B, False)
        assert rig.app.STATE.server_fallback is None

    def test_stop_clears_the_fallback(self, rig, base_url):
        replaced(rig)
        assert rig.app.STATE.server_fallback == ([A], True)
        stop(rig, base_url)
        assert rig.app.STATE.server_fallback is None
        rig.broken.add(C)
        rig.app.worker_server_start(C, False)
        assert len(rig.made) == 2 and rig.app.STATE.server_pid is None

    def test_stop_while_the_start_is_being_prepared_keeps_none(self, rig, monkeypatch):
        real = rig.app._running_server

        def running():
            found = real()
            rig.app.STATE.cancel_pending_starts()
            return found

        monkeypatch.setattr(rig.app, "_running_server", running)
        rig.app.worker_server_start(B, True)
        assert rig.app.STATE.server_fallback is None


# the restored server dies as well ---------------------------------------------- #
class TestRestoredServerDies:
    def check(self, rig):
        s = rig.app.STATE.snapshot()["server"]
        assert s["running"] is False and s["apply_notice"] is None
        assert "a was started again." in s["down_notice"]
        assert "API server for a stopped unexpectedly (exited with status 1)" in s["down_notice"]

    def test_later_there_is_one_notice(self, rig, monkeypatch):
        rig.broken.add(B)
        rig.app.worker_server_start(B, False)
        proc = rig.made[-1]
        run_watch(rig, monkeypatch, rig.watched[-1], [lambda: setattr(proc, "returncode", 1)])
        self.check(rig)

    def test_before_the_notice_is_written_there_is_still_one(self, rig, monkeypatch):
        real = rig.app._launch_server

        def launch(*args):
            proc = real(*args)
            if proc is not None:
                proc.returncode = 1
                rig.watch(proc.pid, args[0], proc)
            return proc

        rig.broken.add(B)
        monkeypatch.setattr(rig.app, "_launch_server", launch)
        rig.app.worker_server_start(B, False)
        self.check(rig)


# an unexpected error while starting -------------------------------------------- #
def fail_pidfile(rig, monkeypatch, times):
    """The step after the spawn raises, the first ``times`` times."""
    real, left = rig.app._write_server_pidfile, [times]

    def write(*args):
        if left[0]:
            left[0] -= 1
            raise RuntimeError("disk gone")
        real(*args)

    monkeypatch.setattr(rig.app, "_write_server_pidfile", write)


class TestUnexpectedError:
    def test_before_the_spawn_brings_the_old_set_back(self, rig, monkeypatch):
        real = rig.app._server_command

        def broken(repo, *args):
            if repo == B:
                raise RuntimeError("no settings")
            return real(repo, *args)

        monkeypatch.setattr(rig.app, "_server_command", broken)
        rig.app.worker_server_start(B, False)
        s = rig.app.STATE
        assert s.server_repos == [A] and "no settings" in s.server_apply_notice
        assert s.server_pending == []

    def test_after_the_spawn_stops_the_half_started_one(self, rig, monkeypatch):
        fail_pidfile(rig, monkeypatch, 1)
        rig.app.worker_server_start(B, False)
        s = rig.app.STATE
        assert [models(p) for p in rig.made] == [[A], [B], [A]]
        assert rig.made[1].returncode == -15
        assert s.server_repos == [A] and "disk gone" in s.server_apply_notice

    def test_in_the_restore_too_is_never_silent(self, rig, monkeypatch):
        fail_pidfile(rig, monkeypatch, 2)
        rig.app.worker_server_start(B, False)
        s = rig.app.STATE
        assert s.server_pid is None and all(p.returncode is not None for p in rig.made)
        assert s.server_down_notice.count("disk gone") == 2
        assert "The API server is stopped." in s.server_down_notice

    def test_in_a_restore_made_by_the_watcher(self, rig, monkeypatch):
        proc, args = replaced(rig)
        fail_pidfile(rig, monkeypatch, 1)
        run_watch(rig, monkeypatch, args, [lambda: setattr(proc, "returncode", 1)])
        assert "disk gone" in rig.app.STATE.server_down_notice

    def test_on_a_first_start_is_recorded(self, rig, monkeypatch):
        rig.app.stop_server()
        fail_pidfile(rig, monkeypatch, 1)
        rig.app.worker_server_start(B, False)
        s = rig.app.STATE
        assert len(rig.made) == 2 and s.server_pid is None
        assert s.server_last_error == "disk gone"


# the request ------------------------------------------------------------------- #
class TestRequest:
    def test_a_second_model_is_refused_and_the_old_server_stays(self, rig, base_url):
        status, body = post(base_url, "/api/server/start", {"repo": B, "also": [C]})
        assert status == 400 and "one model" in body["error"]
        assert rig.made[0].returncode is None and rig.app.STATE.server_pending == []

    def test_a_missing_model_is_refused_before_the_old_server_stops(self, rig, base_url):
        status, body = post(base_url, "/api/server/start", {"repo": "org/gone"})
        assert status == 400 and "not installed completely" in body["error"]
        assert rig.app.STATE.server_repos == [A] and rig.made[0].returncode is None

    def test_dismiss_clears_both_notices(self, rig, base_url):
        rig.app.STATE.server_apply_notice = "x"
        rig.app.STATE.server_down_notice = "y"
        assert post(base_url, "/api/server/notice/clear", {})[0] == 200
        server = rig.app.STATE.snapshot()["server"]
        assert (server["apply_notice"], server["down_notice"]) == (None, None)
