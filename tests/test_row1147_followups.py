"""Row 1147 follow-ups: MLX start during a package install, the install panel's
refresh, a ``ps`` that does not answer, and focus on the panel's two buttons."""

from __future__ import annotations

import json
import subprocess

import pytest
from test_row892_connection import post
from test_row1105_ui import needs_node, ui

import feature_routes

REPO = "org/run"


def installing(app, kind):
    app.STATE.install = {"running": True, "kind": kind, "lines": []}


class TestMlxStartDuringAnInstall:
    @pytest.mark.parametrize("kind", ["all", "install", "update", "reinstall"])
    def test_refused_while_packages_install(self, app_module, kind):
        installing(app_module, kind)
        why = app_module._engine_refusal("mlx", [REPO], port_too=False)
        assert why and "an install is running" in why

    def test_another_engines_own_install_does_not_refuse_it(self, app_module):
        installing(app_module, "optiq install")
        assert app_module._engine_refusal("mlx", [REPO], port_too=False) is None

    def test_a_finished_install_does_not_refuse_it(self, app_module):
        installing(app_module, "all")
        app_module.STATE.install["running"] = False
        assert app_module._engine_refusal("mlx", [REPO], port_too=False) is None

    def test_the_start_request_is_answered_409_and_queues_nothing(
        self, base_url, app_module, monkeypatch
    ):
        monkeypatch.setattr(app_module, "_complete_models", lambda: {REPO})
        monkeypatch.setattr(
            app_module, "worker_server_start", lambda *a, **k: pytest.fail("started")
        )
        installing(app_module, "all")
        code, body = post(base_url, "/api/server/start", {"repo": REPO})
        assert code == 409 and "an install is running" in body["error"]
        assert app_module.STATE.server_pending == []

    def test_a_start_that_got_in_first_stops_install_all(self, app_module, monkeypatch):
        plan = {"needed": True, "blocked": None, "items": [], "steps": []}

        def build(*args, busy=None, **kwargs):
            return {**plan, "blocked": busy}

        monkeypatch.setattr(app_module.feature_routes.engine_plan, "build_plan", build)
        monkeypatch.setattr(
            feature_routes, "worker_install_all", lambda app: pytest.fail("worker ran")
        )
        reserve = app_module.STATE.reserve_install

        def start_slips_in(kind):
            app_module.STATE.server_pending.append(REPO)
            return reserve(kind)

        monkeypatch.setattr(app_module.STATE, "reserve_install", start_slips_in)
        code, body = feature_routes.start_install_all(app_module)
        assert (code, body["error"]) == (409, feature_routes.SERVER_BUSY)
        assert app_module.STATE.install["running"] is False


def pidfile(app):
    app._server_pidfile().parent.mkdir(parents=True, exist_ok=True)
    app._server_pidfile().write_text(
        json.dumps({"pid": 4242, "repo": REPO, "started": "then", "background": True})
    )


def ps(app, monkeypatch, silent_field):
    """``ps`` times out on one column and answers the other."""
    command = f"python -m mlx_lm server --model {REPO} --host x --port {app.SERVER_PORT} "

    def run(args, **kwargs):
        field = args[-1].rstrip("=")
        if field == silent_field:
            raise subprocess.TimeoutExpired(args, 5)
        out = command if field == "command" else "then"
        return subprocess.CompletedProcess(args, 0, out, "")

    monkeypatch.setattr(app.subprocess, "run", run)
    monkeypatch.setattr(app, "_pid_alive", lambda pid: pid == 4242)


class TestSilentPs:
    @pytest.mark.parametrize("field", ["command", "lstart"])
    @pytest.mark.parametrize("look", ["adopt_late_server", "restore_server_state"])
    def test_the_pidfile_is_kept(self, app_module, monkeypatch, field, look):
        pidfile(app_module)
        ps(app_module, monkeypatch, field)
        getattr(app_module, look)()
        assert app_module._server_pidfile().is_file()
        assert app_module.STATE.server_pid is None

    def test_the_server_is_adopted_once_ps_answers(self, app_module, monkeypatch):
        pidfile(app_module)
        ps(app_module, monkeypatch, "command")
        app_module.adopt_late_server()
        ps(app_module, monkeypatch, "nothing")
        monkeypatch.setattr(app_module, "_watch_server", lambda *a: None)
        app_module.adopt_late_server()
        assert app_module.STATE.server_pid == 4242

    def test_a_silent_ps_is_not_proof_the_watched_server_is_gone(self, app_module, monkeypatch):
        looks = []

        def alive(pid):
            looks.append(pid)
            return len(looks) < 40

        asked = []
        monkeypatch.setattr(app_module, "_pid_alive", alive)
        monkeypatch.setattr(app_module, "_still_our_server", lambda *a: asked.append(a))
        monkeypatch.setattr(app_module.time, "sleep", lambda s: None)
        app_module._watch_server(4242, REPO, None)
        assert len(asked) >= 2  # asked again after the first unanswered look


ENGINES = "[{ id: 'optiq', state: '%s' }]"
SETUP = (
    "respond = () => reply(plan()); state.view = 'settings';"
    " const asks = () => sent().filter((s) => s.includes('/api/install/plan')).length;"
)


@needs_node
class TestPlanRefresh:
    def test_a_changed_engine_state_asks_again(self, tmp_path):
        out = ui(
            tmp_path,
            SETUP
            + f"""
            renderFeatures(snap({{ server: {{ engines: {ENGINES % "missing"} }} }}));
            renderFeatures(snap({{ server: {{ engines: {ENGINES % "installed"} }} }}));
            return asks();
            """,
        )
        assert out == 2

    def test_a_failed_fetch_is_asked_again(self, tmp_path):
        out = ui(
            tmp_path,
            SETUP
            + """
            respond = () => reply({ error: "no answer" }, false);
            renderFeatures(snap()); await tick(); await tick();
            const shown = text("#planWhy");
            respond = () => reply(plan());
            renderFeatures(snap()); await tick(); await tick();
            renderFeatures(snap());
            return { shown, asks: asks(), off: el("#planBtn").disabled };
            """,
        )
        assert out == {"shown": "no answer", "asks": 2, "off": False}


HELD = "(b) => [b.disabled, b.getAttribute('aria-disabled'), document.activeElement === b]"


@needs_node
class TestButtonsKeepFocus:
    def test_install_all(self, tmp_path):
        out = ui(
            tmp_path,
            f"""
            const held = {HELD};
            let done; respond = (p) => p === "/api/state" ? reply(snap())
              : new Promise((r) => {{ done = r; }});
            wireFeatures(); paintPlan(plan()); const b = el("#planBtn"); b.focus();
            const first = b.onclick(); await tick();
            const during = held(b);
            await b.onclick();
            const posts = sent().filter((s) => s.startsWith("POST")).length;
            done(await reply({{ ok: true }})); await first;
            ft.installing = true; paintPlan(plan());
            return {{ during, posts, after: held(b) }};
            """,
        )
        assert out == {"during": [False, "true", True], "posts": 1, "after": [False, "true", True]}

    def test_a_refused_install_can_be_pressed_again(self, tmp_path):
        out = ui(
            tmp_path,
            """
            respond = () => reply({ error: "refused" }, false);
            wireFeatures(); paintPlan(plan()); const b = el("#planBtn"); b.focus();
            await b.onclick(); await b.onclick();
            return { posts: sent().length, off: [b.disabled, b.getAttribute("aria-disabled")] };
            """,
        )
        assert out == {"posts": 2, "off": [False, "false"]}

    def test_a_button_without_focus_is_still_disabled(self, tmp_path):
        out = ui(
            tmp_path,
            """
            paintPlan(plan({ needed: false }));
            return [el("#planBtn").disabled, el("#planBtn").getAttribute("aria-disabled")];
            """,
        )
        assert out == [True, "true"]

    def test_release_check(self, tmp_path):
        out = ui(
            tmp_path,
            f"""
            const held = {HELD};
            let done; respond = () => new Promise((r) => {{ done = r; }});
            wireFeatures(); const b = el("#ftUpdateBtn"); b.focus();
            const first = b.onclick(); await tick();
            const during = held(b);
            b.onclick(); const asks = sent().length;
            done(await reply({{ status: "current", message: "m" }})); await first;
            return {{ during, asks, after: held(b) }};
            """,
        )
        assert out == {"during": [False, "true", True], "asks": 1, "after": [False, "false", True]}
