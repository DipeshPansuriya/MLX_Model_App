"""Row 1105: routes and app glue for the finished 2.0.14 modules. No network, no pip."""

from __future__ import annotations

import json
import subprocess
from types import SimpleNamespace

import pytest

import feature_routes as routes
import mlx_loadtest

REPO = "org/model"


class FakeEngine:
    id, name, version, installable = "optiq", "OptiQ", "0.8.6", True

    def __init__(self, state="not_installed", error=None):
        self.state_now, self.error, self.installs = state, error, 0

    def view(self, app_dir):
        return {"id": self.id, "state": self.state_now, "version": self.version}

    def install(self, app_dir, emit):
        self.installs += 1
        if self.error:
            raise self.error
        emit("engine line")
        self.state_now = "installed"


@pytest.fixture
def app(app_module, monkeypatch):
    """The real app with pip, the version lookup, the engines and the threads faked."""
    ran, versions = [], {}
    world = SimpleNamespace(code=0, ran=ran, versions=versions, checked=0, engine=FakeEngine())

    def popen(cmd, **kwargs):
        ran.append(cmd)
        if world.code == 0:
            versions.update(app_module.PINNED_PACKAGES)
        return SimpleNamespace(stdout=iter(["pip line\n"]), wait=lambda: world.code)

    def checked():
        world.checked += 1

    fake = SimpleNamespace(**{**vars(subprocess), "Popen": popen})
    monkeypatch.setattr(app_module, "subprocess", fake)
    monkeypatch.setattr(app_module, "pkg_version", versions.get)
    monkeypatch.setattr(app_module, "worker_check_versions", checked)
    known = {"mlx": app_module.engines.get("mlx"), "optiq": world.engine}
    monkeypatch.setattr(app_module.engines, "REGISTRY", [world.engine])
    monkeypatch.setattr(app_module.engines, "get", known.get)
    monkeypatch.setattr(
        routes,
        "threading",
        SimpleNamespace(
            Thread=lambda target, args, daemon: SimpleNamespace(start=lambda: target(*args))
        ),
    )
    app_module.world = world
    return app_module


def _post(app, path="/api/install/all"):
    return routes.handle_post(app, path, {})


def test_the_route_lists_name_what_is_handled_and_other_paths_are_left_alone(app):
    assert routes.GET_ROUTES == ("/api/install/plan",)
    assert routes.POST_ROUTES == ("/api/install/all", "/api/update/check")
    assert routes.handle_get(app, "/api/state") is None
    assert routes.handle_post(app, "/api/install", {}) is None


def test_plan_lists_the_pins_and_engines_and_changes_nothing(app):
    code, plan = routes.handle_get(app, "/api/install/plan")
    assert code == 200 and plan["needed"] and plan["blocked"] is None
    assert [i["id"] for i in plan["items"]] == [*app.PINNED_PACKAGES, "optiq"]
    assert not app.world.ran and not app.world.engine.installs


@pytest.mark.parametrize(("field", "value"), [("server_pending", ["org/m"]), ("server_proc", 1)])
def test_a_starting_or_running_server_blocks_the_install(app, field, value):
    setattr(app.STATE, field, value)
    code, answer = _post(app)
    setattr(app.STATE, field, None if field == "server_proc" else type(value)())
    assert (code, answer) == (409, {"error": routes.SERVER_BUSY})
    assert not app.world.ran and not app.STATE.install["running"]


def test_a_load_test_blocks_the_install(app):
    app.STATE.loadtest = {"active": True, "repo": REPO}
    assert _post(app) == (409, {"error": app.LOADTEST_BUSY})
    app.STATE.loadtest = None


def test_a_second_install_is_refused(app):
    assert app.STATE.reserve_install("install")
    assert _post(app) == (409, {"error": routes.INSTALL_BUSY})
    assert app.STATE.install["kind"] == "install" and not app.world.ran


def test_install_all_runs_pip_then_the_engine_then_releases_once(app):
    assert _post(app) == (200, {"ok": True})
    install = app.STATE.install
    assert install["kind"] == "all" and install["running"] is False
    (cmd,) = app.world.ran
    assert cmd[1:5] == ["-m", "pip", "install", "--upgrade"] and cmd[5:] == app.install_specs()
    assert app.world.engine.installs == 1 and app.world.checked == 1
    text = "\n".join(install["lines"])
    assert "pip line" in text and "engine line" in text
    assert install["lines"][-1] == "Everything is installed and verified."


def test_the_reservation_is_held_between_the_package_and_engine_steps(app):
    held = []
    app.world.engine.install = lambda app_dir, emit: held.append(app.STATE.install["running"])
    _post(app)
    assert held == [True]
    assert app.STATE.install["lines"][-1].startswith("Verify failed: optiq")


def test_a_failed_pip_stops_before_the_engine(app):
    app.world.code = 1
    _post(app)
    assert app.world.engine.installs == 0 and app.STATE.install["running"] is False
    assert app.STATE.install["lines"][-1] == "The install step failed. See the lines above."


def test_a_refused_engine_install_ends_the_run_with_its_reason(app):
    app.world.engine.error = app.engine_runtime.Refused("No disk space.")
    _post(app)
    assert "No disk space." in app.STATE.install["lines"]
    assert app.STATE.install["running"] is False


def test_nothing_to_do_is_said_without_reserving(app):
    app.world.versions.update(app.PINNED_PACKAGES)
    app.world.engine.state_now = "installed"
    assert _post(app) == (409, {"error": routes.UP_TO_DATE})
    assert app.STATE.install.get("kind") != "all"


def test_the_release_is_freed_even_if_the_run_raises(app, monkeypatch):
    def boom(*args):
        raise KeyboardInterrupt

    monkeypatch.setattr(routes.engine_plan, "run_plan", boom)
    with pytest.raises(KeyboardInterrupt):
        _post(app)
    assert app.STATE.install["running"] is False


def test_pip_helper_leaves_the_reservation_alone_and_honours_reinstall(app):
    app.STATE.install = {"running": True, "kind": "reinstall", "lines": ["kept"]}
    assert routes.pip_install(app, "reinstall", app.STATE.install["lines"]) is True
    assert app.world.ran[0][4] == "--force-reinstall"
    assert app.STATE.install["running"] and app.STATE.install["lines"][0] == "kept"


def test_a_pip_without_output_is_a_failure_not_a_stuck_install(app):
    app.subprocess.Popen = lambda cmd, **kw: SimpleNamespace(stdout=None, kill=lambda: None)
    lines = []
    assert routes.pip_install(app, "install", lines) is False
    assert lines[-1] == "error: no stdout stream"


def test_the_update_route_asks_only_when_called_with_the_app_version(app, monkeypatch):
    asked = []
    monkeypatch.setattr(
        routes.update_check, "check", lambda current: asked.append(current) or {"status": "x"}
    )
    routes.handle_get(app, "/api/install/plan")
    assert not asked
    assert _post(app, "/api/update/check") == (200, {"status": "x"})
    assert asked == [app.APP_VERSION]


# -- live memory and speed -------------------------------------------------- #
def _probe(pid):
    return mlx_loadtest.Sample(40.0, 2000 if pid else 0, 3000 if pid else 0, 1, 0)


def _serve(app, monkeypatch, pid=4321):
    monkeypatch.setattr(app, "_pid_alive", lambda value: value == pid)
    app.STATE.server_pid, app.STATE.server_repo, app.STATE.server_engine = pid, REPO, "mlx"


def _store(app, write_tps, engine="mlx"):
    row = {"size": 4096, "status": "passed", "write_tps": write_tps}
    record = {"id": f"r{write_tps}", "repo": REPO, "rows": [row], "engine": engine}
    app._save_loadtest(record)


# The RAM probe answers on macOS and reads 0 elsewhere, so both answers are given here.
@pytest.mark.parametrize(("ram_bytes", "total"), [(64 * 2**30, 64 * 2**30), (0, None)])
def test_a_stopped_server_shows_the_mac_only(app, monkeypatch, ram_bytes, total):
    monkeypatch.setattr(app, "hardware", lambda: {"ram_bytes": ram_bytes})
    app.STATE.server_pid = None
    stats = routes.server_stats(app, _probe)
    assert stats["memory"]["mac_used_percent"] == 40.0
    assert stats["memory"]["mac_total_bytes"] == total
    assert stats["memory"]["server_bytes"] is None
    assert stats["speed"] == {"chat": None, "test": None}


def test_a_running_server_shows_its_memory_chat_and_saved_test(app, monkeypatch):
    _serve(app, monkeypatch)
    _store(app, 21.0)
    done = {"repo": REPO, "stats": {"tps": 33.0, "mode": "fast"}}
    routes.record_chat_speed(app, done, "mlx", 4321)
    stats = routes.server_stats(app, _probe)
    assert stats["memory"]["server_bytes"] == 2000
    assert stats["speed"]["chat"]["write_tps"] == 33.0
    assert stats["speed"]["test"]["rows"][0]["write_tps"] == 21.0
    assert stats["speed"]["test"]["stale"] is True  # it was stored with no fingerprint
    json.dumps(stats)


def test_an_unmeasured_reply_does_not_replace_the_last_speed(app):
    routes.record_chat_speed(app, {"repo": REPO, "stats": {"tps": 9.0}}, "mlx", 1)
    routes.record_chat_speed(app, {"repo": REPO, "stats": {}}, "mlx", 1)
    routes.record_chat_speed(app, None, "mlx", 1)
    assert app.STATE.last_chat_speed["write_tps"] == 9.0


def test_saved_runs_are_read_again_only_when_the_file_changes(app, monkeypatch):
    _serve(app, monkeypatch)
    _store(app, 21.0)
    reads, real = [], app._stored_runs
    monkeypatch.setattr(app, "_stored_runs", lambda repo: reads.append(repo) or real(repo))
    monkeypatch.setattr(routes, "time", SimpleNamespace(monotonic=lambda: 100.0))
    for _ in range(3):
        routes.server_stats(app, _probe)
    assert len(reads) == 1
    _store(app, 22.5)
    assert routes.server_stats(app, _probe)["speed"]["test"]["rows"][0]["write_tps"] == 22.5
    assert len(reads) == 2


def test_unreadable_saved_runs_never_break_the_state(app, monkeypatch):
    _serve(app, monkeypatch)

    def broken(repo):
        raise RuntimeError("x")

    monkeypatch.setattr(app, "_stored_runs", broken)
    assert routes.server_stats(app, _probe)["speed"]["test"] is None
