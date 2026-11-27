"""Row 831: browser-test defects, and the card remembers the last set that started.

No process is started and no model is loaded: Popen is a stand-in.
"""

from __future__ import annotations

import importlib.util
import json
import re
import stat
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


screen_tests = load("row831_screen_for_fix2", "test_row831_screen.py")
apply_tests = load("row831_apply_for_fix2", "test_row831_apply.py")
screen, needs_node = screen_tests.screen, screen_tests.needs_node
HOLD = screen_tests.HOLD
rig, ram = apply_tests.rig, apply_tests.ram
cache_model, models = apply_tests.cache_model, apply_tests.models
resident, run_watch, replaced = apply_tests.resident, apply_tests.run_watch, apply_tests.replaced
A, B, C = apply_tests.A, apply_tests.B, apply_tests.C

ONE = "loaded({ repos: ['org/a'], resident: ['org/a'] })"
NEW_B = "loaded({ pid: 8, repo: 'org/b', repos: ['org/b'], resident: ['org/b'] })"
APPLY_B = f"await show({ONE}); await drop('org/a'); await add('org/b');"


# D2, D5: the second click of a double click ------------------------------------ #
@needs_node
@pytest.mark.parametrize("event", ["click", "mousedown"])
def test_the_second_click_of_a_double_click_does_nothing(tmp_path, event):
    # The first click moves things: the second would land on whatever came under it.
    out = screen(
        tmp_path,
        "const page = $('#view-server');"
        " const hit = (detail, control) => { const seen = [];"
        f" page.listeners.{event}?.({{ detail, preventDefault: () => seen.push('prevent'),"
        " stopPropagation: () => seen.push('stop'), target: { closest: (s) =>"
        " control && s === 'button, label' ? {} : null } }); return seen; };"
        " return [hit(2, true), hit(3, true), hit(1, true), hit(0, true), hit(2, false),"
        f" !!page.captured.{event}];",
    )
    swallowed = ["prevent", "stop"]
    # Keyboard presses (0) and text selection outside buttons stay as they were.
    assert out == [swallowed, swallowed, [], [], [], True]


# a double press on Restart ------------------------------------------------------ #
@needs_node
@pytest.mark.parametrize(
    ("button", "shown"),
    [
        ("#serverRestartBtn", f"await show({ONE}); await show(view({{ down_notice: 'gone' }}));"),
        ("#serverUnguardedRestart", "await show(running({ unguarded: true }));"),
    ],
)
def test_a_second_press_on_restart_starts_once(tmp_path, button, shown):
    out = screen(
        tmp_path,
        HOLD + shown + f" const press2 = () => {{ $('{button}').listeners.click(); }};"
        " hold('/api/server/start'); press2(); await tick(); press2(); await tick();"
        " const during = starts(); await release(); press2(); await tick();"
        " return [during, starts()];",
    )
    assert out == [1, 2]


# D3: edits made while a start is on its way ------------------------------------- #
@needs_node
def test_a_model_added_while_starting_stays_as_a_pending_change(tmp_path):
    out = screen(
        tmp_path,
        APPLY_B + " await press('#serverStartBtn'); await show(view({ starting: true }));"
        f" await add('org/c'); await show({NEW_B}); await show({NEW_B});"
        " return [listed(), main()];",
    )
    assert out == [["org/b", "org/c"], ["Apply changes", False]]


@needs_node
def test_a_model_added_before_the_start_shows_on_screen_is_kept(tmp_path):
    # The click is out, but the old server is still the one drawn.
    out = screen(
        tmp_path,
        HOLD + APPLY_B + " hold('/api/server/start'); click(); await tick();"
        f" await add('org/c'); await release(); await show({NEW_B});"
        " return [listed(), main()];",
    )
    assert out == [["org/b", "org/c"], ["Apply changes", False]]


@needs_node
def test_going_back_to_the_old_set_while_starting_is_kept(tmp_path):
    out = screen(
        tmp_path,
        APPLY_B + " await press('#serverStartBtn');"
        " await show(running({ repos: ['org/a'], resident: ['org/a'], starting: true }));"
        " await drop('org/b'); await add('org/a');"
        f" await show({NEW_B}); return [listed(), main()];",
    )
    assert out == [["org/a"], ["Apply changes", False]]


@needs_node
def test_an_edit_that_ends_at_what_started_follows_the_server_again(tmp_path):
    out = screen(
        tmp_path,
        APPLY_B + " await press('#serverStartBtn'); await show(view({ starting: true }));"
        f" await add('org/c'); await drop('org/c'); await show({NEW_B});"
        " await show(loaded({ pid: 9 })); return listed();",
    )
    assert out == ["org/a", "org/b"]


@needs_node
def test_background_ticked_while_starting_is_kept(tmp_path):
    out = screen(
        tmp_path,
        APPLY_B + " await press('#serverStartBtn'); await show(view({ starting: true }));"
        " $('#serverBackground').checked = true; $('#serverBackground').listeners.change();"
        f" await show({NEW_B});"
        " return [$('#serverBackground').checked, listed(), main()];",
    )
    assert out == [True, ["org/b"], ["Apply changes", False]]


@needs_node
def test_the_next_start_replaces_the_kept_edits_again(tmp_path):
    # Kept once; the server that the next Apply starts is followed as before.
    out = screen(
        tmp_path,
        APPLY_B + " await press('#serverStartBtn'); await show(view({ starting: true }));"
        f" await add('org/c'); await show({NEW_B}); await press('#serverStartBtn');"
        " await show(loaded({ pid: 9, repos: ['org/a'], resident: ['org/a'] }));"
        " return listed();",
    )
    assert out == ["org/a"]


# D6: focus after Stop and Restart ------------------------------------------------ #
@needs_node
def test_focus_after_stop_when_nothing_else_can_be_used(tmp_path):
    # Two models that do not fit: Start and Add are both off.
    out = screen(
        tmp_path,
        'estimate = { ...both, fits: false, refused: "No room." };'
        f" await show({ONE}); await add('org/b'); $('#serverStopBtn').focus();"
        " await show(view()); return [$('#serverStartBtn').disabled,"
        " $('#serverAddBtn').disabled, focused()];",
    )
    assert out == [True, True, "#serverBackground"]


@needs_node
@pytest.mark.parametrize(
    ("button", "before", "after"),
    [
        (
            "#serverRestartBtn",
            f"await show({ONE}); await show(view({{ down_notice: 'gone' }}));",
            "view({ starting: true })",
        ),
        (
            "#serverUnguardedRestart",
            "await show(running({ repos: ['org/a'], unguarded: true }));",
            "loaded({ pid: 9, repos: ['org/a'], resident: ['org/a'] })",
        ),
    ],
)
def test_focus_leaves_a_restart_button_that_goes_away(tmp_path, button, before, after):
    out = screen(
        tmp_path,
        before + f" $('{button}').focus(); await show(current); const kept = focused();"
        f" await show({after}); const moved = focused();"
        " await document.activeElement.listeners.click?.(); await tick();"
        " return [kept, moved, sent('/api/server/stop').length];",
    )
    # While the notice is there the focus stays; then never on Start or Stop.
    assert out == [button, "#serverAddBtn", 0]


# D7: Restart brings back what ran ------------------------------------------------ #
CRASH = (
    f"await show({ONE}); await add('org/b'); $('#serverBackground').checked = true;"
    " $('#serverBackground').listeners.change();"
    " await show(view({ down_notice: 'gone' })); await press('#serverRestartBtn');"
)


@needs_node
def test_restart_after_a_crash_ignores_the_unapplied_choice(tmp_path):
    out = screen(tmp_path, CRASH + " return sent('/api/server/start');")
    assert out == [{"repo": "org/a", "background": False}]


@needs_node
def test_the_unapplied_choice_is_still_pending_after_restart(tmp_path):
    out = screen(
        tmp_path,
        CRASH + " await show(view({ starting: true }));"
        " await show(loaded({ pid: 9, repos: ['org/a'], resident: ['org/a'] }));"
        " return [listed(), $('#serverBackground').checked, main()];",
    )
    assert out == [["org/a", "org/b"], True, ["Apply changes", False]]


@needs_node
def test_restart_after_a_failed_start_tries_that_set_again(tmp_path):
    out = screen(
        tmp_path,
        f"await show({ONE}); await show(view()); await drop('org/a'); await add('org/b');"
        " await press('#serverStartBtn'); await show(view({ down_notice: 'no' }));"
        " await press('#serverRestartBtn'); return sent('/api/server/start');",
    )
    assert out == [{"repo": "org/b", "background": False}] * 2


# D8: a long name in a very narrow window ----------------------------------------- #
def test_the_context_moves_under_the_name_when_very_narrow():
    css = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")
    narrow = css[css.index("@media (max-width: 480px)") :]
    assert re.search(r"\.model-list li \{[^}]*flex-wrap: wrap", narrow)
    assert re.search(r"\.model-context \{[^}]*order: 1;[^}]*flex-basis: 100%", narrow)


# a first start that fails is said on screen --------------------------------------- #
@pytest.fixture
def idle(app_module, monkeypatch, ram):
    """Nothing is running; Popen is a stand-in that can be told to fail."""
    app = app_module
    for repo in (A, B):
        cache_model(app, repo)
    made, watched, broken = [], [], set()

    def popen(cmd, **kwargs):
        if cmd[3] in broken:
            raise OSError(f"cannot start {cmd[3]}")
        made.append(apply_tests.Proc(6000 + len(made), cmd))
        return made[-1]

    monkeypatch.setattr(app.subprocess, "Popen", popen)
    monkeypatch.setattr(app, "_ps_field", lambda pid, field: "")
    real_watch = app._watch_server
    monkeypatch.setattr(app, "_watch_server", lambda *args: watched.append(args))
    monkeypatch.setattr(app, "_pump_output", lambda proc: None)

    class Idle:
        pass

    r = Idle()
    r.app, r.made, r.watched, r.broken, r.watch = app, made, watched, broken, real_watch
    return r


def server(app):
    return app.STATE.snapshot()["server"]


class TestFirstStartFails:
    def check(self, app, why):
        shown = server(app)
        assert shown["running"] is False and shown["apply_notice"] is None
        assert shown["down_notice"] == f"The API server did not start: {why}."

    def test_a_spawn_error(self, idle):
        idle.broken.add(A)
        idle.app.worker_server_start(A, False)
        self.check(idle.app, "cannot start org/a")

    def test_an_unexpected_error(self, idle, monkeypatch):
        def broken(*args, **kwargs):
            raise RuntimeError("no estimate.")

        monkeypatch.setattr(idle.app, "server_memory_estimate", broken)
        idle.app.worker_server_start(A, False, None, [B])
        self.check(idle.app, "no estimate")

    def test_stop_pressed_meanwhile_says_nothing(self, idle, monkeypatch):
        def popen(cmd, **kwargs):
            idle.app.STATE.cancel_pending_starts()
            raise OSError("late")

        monkeypatch.setattr(idle.app.subprocess, "Popen", popen)
        idle.app.worker_server_start(A, False)
        assert server(idle.app)["down_notice"] is None

    def test_a_refusal_keeps_its_own_words(self, idle, free_memory):
        free_memory.bytes = 0
        idle.app.worker_server_start(A, False, None, [B])
        assert server(idle.app)["down_notice"].startswith("Not enough")


# the last set that started -------------------------------------------------------- #
def remembered(app):
    return server(app)["last_models"]


def watch(idle, monkeypatch, steps):
    run_watch(idle, monkeypatch, idle.watched.pop(), steps)


def exits(proc):
    def step():
        proc.returncode = 1

    return step


class TestRemembered:
    def test_nothing_at_first(self, idle):
        assert remembered(idle.app) == []

    def test_only_once_the_start_reports_ready(self, idle, monkeypatch):
        idle.app.worker_server_start(A, True)
        proc, seen = idle.made[-1], []
        look = lambda: seen.append(remembered(idle.app))  # noqa: E731
        watch(
            idle,
            monkeypatch,
            [
                look,
                lambda: resident(idle, proc, resident=[A]),
                look,
                exits(proc),
            ],
        )
        assert seen == [[], [A]]
        assert remembered(idle.app) == [A], "a crash does not forget it"

    def test_a_pair_only_after_both_were_tried(self, idle, monkeypatch):
        idle.app.worker_server_start(A, True, None, [B])
        proc, seen = idle.made[-1], []
        look = lambda: seen.append(remembered(idle.app))  # noqa: E731
        watch(
            idle,
            monkeypatch,
            [
                lambda: resident(idle, proc, resident=[A]),
                look,
                lambda: resident(idle, proc, resident=[A, B], preload_done=True),
                look,
                exits(proc),
            ],
        )
        assert seen == [[], [A, B]]

    def test_a_failed_load_is_not_remembered_even_if_it_loads_something_later(
        self, idle, monkeypatch
    ):
        idle.app.worker_server_start(A, True)
        proc = idle.made[-1]
        failed = [{"event": "load-failed", "model": A}]
        watch(
            idle,
            monkeypatch,
            [
                lambda: resident(idle, proc, resident=[], events=failed),
                lambda: None,
                lambda: resident(idle, proc, resident=[B]),
                lambda: None,
                exits(proc),
            ],
        )
        assert remembered(idle.app) == []

    def test_a_start_that_fails_is_not_remembered(self, idle):
        idle.broken.add(A)
        idle.app.worker_server_start(A, False)
        assert remembered(idle.app) == []

    def test_a_failed_apply_keeps_the_old_set_and_a_ready_one_replaces_it(self, rig, monkeypatch):
        def apply(repo, report):
            proc, args = replaced(rig, repo)
            run_watch(rig, monkeypatch, args, [lambda: report(proc), exits(proc)])

        apply(B, lambda proc: resident(rig, proc, resident=[B]))
        assert remembered(rig.app) == [B]
        rig.app.worker_server_start(A, True)
        rig.watched.clear()
        apply(C, lambda proc: resident(rig, proc, resident=[], preload_done=True))
        assert models(rig.made[-1]) == [A], "the old set came back"
        assert remembered(rig.app) == [B], "neither C nor the unready restore"

    def test_it_survives_an_app_restart_and_starts_nothing(self, idle, monkeypatch):
        app = idle.app
        app.worker_server_start(A, False, None, [B])
        proc = idle.made[-1]
        watch(
            idle,
            monkeypatch,
            [lambda: resident(idle, proc, resident=[A, B], preload_done=True), exits(proc)],
        )
        app.stop_server()
        spec = importlib.util.spec_from_file_location("mlxapp_again", ROOT / "app.py")
        again = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(again)
        count = len(idle.made)
        again.restore_server_state()
        assert again.APP_DIR == app.APP_DIR
        assert remembered(again) == [A, B]
        assert server(again)["running"] is False and server(again)["starting"] is False
        assert len(idle.made) == count
        kept = list(app.APP_DIR.glob("server.last*"))
        assert [p.name for p in kept] == ["server.last.json"]
        assert stat.S_IMODE(kept[0].stat().st_mode) == 0o600

    @pytest.mark.parametrize(
        "text", ["", "{", "[1]", '"org/a"', '{"repos": "org/a"}', '{"repos": [1, null]}']
    )
    def test_a_damaged_file_is_the_same_as_nothing(self, idle, text):
        (idle.app.APP_DIR / "server.last.json").write_text(text, "utf-8")
        assert remembered(idle.app) == []

    def test_never_more_than_two(self, idle):
        (idle.app.APP_DIR / "server.last.json").write_text(
            json.dumps({"repos": [A, B, C]}), "utf-8"
        )
        assert remembered(idle.app) == [A, B]


# the card after the app opens ------------------------------------------------------ #
@needs_node
def test_the_last_set_is_already_chosen_and_one_click_starts_it(tmp_path):
    out = screen(
        tmp_path,
        "await show(view({ last_models: ['org/a', 'org/b'] }));"
        " const seen = [listed(), main(), sent('/api/server/start').length];"
        " await press('#serverStartBtn'); return [...seen, sent('/api/server/start')];",
    )
    assert out == [
        ["org/a", "org/b"],
        ["Start Server", False],
        0,
        [{"repo": "org/a", "also": ["org/b"], "background": False}],
    ]


@needs_node
@pytest.mark.parametrize(
    ("last", "listed"),
    [("['org/c', 'org/a']", ["org/a"]), ("['org/gone']", []), ("[]", [])],
)
def test_a_remembered_model_that_cannot_be_used_is_skipped(tmp_path, last, listed):
    out = screen(
        tmp_path,
        "const some = [models[0], models[1], { ...models[2], complete: false }];"
        f" await show({{ ...view({{ last_models: {last} }}), models: some }});"
        " return [listed(), $('#serverStartBtn').disabled, sent('/api/server/start').length];",
    )
    assert out == [listed, not listed, 0]


@needs_node
def test_a_running_server_shows_what_really_runs(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded({ last_models: ['org/c'] })); return listed();",
    )
    assert out == ["org/a", "org/b"]


@needs_node
def test_removed_models_do_not_come_back(tmp_path):
    out = screen(
        tmp_path,
        "const last = view({ last_models: ['org/a'] }); await show(last);"
        " await drop('org/a'); await show(last); return [listed(), main()];",
    )
    assert out == [[], ["Start Server", True]]


@needs_node
def test_a_remembered_large_model_is_warned_but_can_start(tmp_path):
    out = screen(
        tmp_path,
        "single.fits = false; single.total_bytes = 21e9; single.models[0].safe_context = 0;"
        " await show(view({ last_models: ['org/a'] })); await show(current);"
        " return [why(), main()];",
    )
    assert out[0].startswith("This model may not fit") and out[1] == ["Start Server", False]
