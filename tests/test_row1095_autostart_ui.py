"""Row 1095, review 1093 findings 4 to 6: the login switch and Test Connection."""

from __future__ import annotations

from pathlib import Path

from test_row1077_wiring import VIEW
from test_ui_behaviour import needs_node, run

ROOT = Path(__file__).resolve().parents[1]

# A fake app: each test route answers from a queue, and timers fire at once.
TESTING = (
    VIEW
    + """
globalThis.setTimeout = (fn) => { Promise.resolve().then(fn); return 0; };
const lost = () => Promise.reject(new TypeError("Load failed"));
const said = (body, ok = true) => Promise.resolve({ ok, statusText: "no", json: async () => body });
const busy = () => said({ running: true });
const done = () => said({ ok: true, pid: 7, reply: { status: "pass", detail: "hi" },
  tool: { status: "pass", detail: "tool" } });
const queue = { GET: [], POST: [] };
respond = (path, opts) => path !== "/api/server/test" ? said({})
  : (queue[opts?.method || "GET"].shift() || (() => said({ error: "No test." }, false)))();
const settle = async () => { for (let i = 0; i < 500; i++) await null; };
const server = { running: true, ready: true, pid: 7 };
const ready = () => { state.server = server;
  Object.assign(state.test, { pid: 7, running: false, result: null, error: "" }); };
const shown = () => [state.test.running, !!state.test.result, state.test.error];
"""
)


def test_a_switch_that_cannot_be_used_looks_disabled():
    css = (ROOT / "ui" / "style.css").read_text("utf-8")
    rule = css.split('.check:has(input[aria-disabled="true"])', 1)[1].split("}", 1)[0]
    assert "opacity" in rule and "not-allowed" in rule


@needs_node
class TestSwitch:
    def test_a_click_on_a_blocked_switch_does_not_flip_it(self, tmp_path):
        out = run(
            tmp_path,
            VIEW
            + """
            return [{ default: null }, {}].map((over) => {
              state.autostart = auto(over); paintAutostart();
              let stopped = false;
              sw().listeners.click({ target: sw(), preventDefault() { stopped = true; } });
              return stopped;
            });
            """,
        )
        assert out == [True, False]


@needs_node
class TestRunningTest:
    def test_one_failed_poll_does_not_drop_it(self, tmp_path):
        out = run(
            tmp_path,
            TESTING
            + """
            ready();
            queue.POST = [busy];
            queue.GET = [lost, busy, lost, done];
            await testConnection();
            return shown();
            """,
        )
        assert out == [False, True, ""]

    def test_it_gives_up_when_the_app_stays_unreachable(self, tmp_path):
        out = run(
            tmp_path,
            TESTING
            + """
            ready();
            queue.POST = [busy];
            queue.GET = Array(20).fill(lost);
            await testConnection();
            return [...shown(), queue.GET.length];
            """,
        )
        assert out == [False, False, "Load failed", 15]

    def test_a_refusal_while_polling_is_final(self, tmp_path):
        out = run(
            tmp_path,
            TESTING
            + """
            ready();
            queue.POST = [busy];
            queue.GET = [() => said({ error: "The server changed." }, false), done];
            await testConnection();
            return [...shown(), queue.GET.length];
            """,
        )
        assert out == [False, False, "The server changed.", 1]

    def test_a_press_joins_the_test_already_running(self, tmp_path):
        out = run(
            tmp_path,
            TESTING
            + """
            ready();
            queue.POST = [() => said({ error: "A connection test is already running." }, false)];
            queue.GET = [busy, done];
            await testConnection();
            return shown();
            """,
        )
        assert out == [False, True, ""]

    def test_a_refused_start_with_nothing_running_is_shown(self, tmp_path):
        out = run(
            tmp_path,
            TESTING
            + """
            ready();
            queue.POST = [() => said({ error: "The server is starting." }, false)];
            queue.GET = [done];
            await testConnection();
            return shown();
            """,
        )
        assert out == [False, False, "The server is starting."]

    def test_a_reloaded_page_picks_it_up(self, tmp_path):
        out = run(
            tmp_path,
            TESTING
            + """
            queue.GET = [busy, done];
            state.server = server;
            paintTest(server);
            await settle();
            return [...shown(), calls.filter((c) => c.opts?.method === "POST").length];
            """,
        )
        assert out == [False, True, "", 0]

    def test_a_reloaded_page_shows_no_old_result(self, tmp_path):
        out = run(
            tmp_path,
            TESTING
            + """
            queue.GET = [done];
            state.server = server;
            paintTest(server);
            await settle();
            return shown();
            """,
        )
        assert out == [False, False, ""]


@needs_node
class TestStaleStatus:
    def test_a_slow_status_does_not_undo_a_change(self, tmp_path):
        out = run(
            tmp_path,
            VIEW
            + """
            const okay = (body) => ({ ok: true, json: async () => body });
            const held = [];
            respond = (path, opts) => opts?.method === "POST"
              ? Promise.resolve(okay(auto({ enabled: true })))
              : new Promise((end) => held.push(end));
            state.autostart = auto(); state.autostartAt = "x";
            const slow = loadAutostart();
            sw().checked = true; paintAutostart(); sw().checked = true;
            await switchAutostart();
            held[0](okay(auto({ enabled: false })));
            await slow;
            return [state.autostart.enabled, sw().checked, state.autostartAt];
            """,
        )
        assert out == [True, True, None]  # None: asked again with the next poll

    def test_a_status_answered_while_a_change_runs_is_dropped(self, tmp_path):
        out = run(
            tmp_path,
            VIEW
            + """
            const okay = (body) => ({ ok: true, json: async () => body });
            let release;
            respond = (path, opts) => opts?.method === "POST"
              ? new Promise((end) => { release = end; })
              : Promise.resolve(okay(auto({ login_item: "stale" })));
            state.autostart = auto();
            const change = changeAutostart("/api/autostart", { enabled: true });
            await loadAutostart();
            const during = state.autostart.login_item;
            release(okay(auto({ enabled: true })));
            await change;
            return [during, state.autostart.enabled];
            """,
        )
        assert out == ["missing", True]
