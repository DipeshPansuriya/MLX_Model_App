"""Row 656: the API Server screen picks, shows and restarts two models."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "ui_behaviour_helpers", ROOT / "tests" / "test_ui_behaviour.py"
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
run, needs_node = helpers.run, helpers.needs_node
HTML = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")

# A server screen with three installed models and a fake API.
SETUP = """
const models = ["org/a", "org/b", "org/c"].map((repo) =>
  ({ repo, path: repo, size_h: "4 GB", complete: true }));
const server = (over = {}) => ({ running: false, endpoint: "e", lines: [], tool_support: {},
  repo: null, repos: [], resident: [], memory: null, events: [], multi_off: null, ...over });
const view = (over = {}, rest = {}) => snap({ models, server: server(over), ...rest });
const running = (over = {}) => view({ running: true, pid: 7, repo: "org/a",
  repos: ["org/a", "org/b"], ...over });
let estimate = { fits: true, refused: null, context_tokens: 26624, free_bytes: 25e9,
  total_bytes: 17.8e9, budget_bytes: 17.9e9, models: [
    { repo: "org/a", safe_context: 26624, context_limit: 1000000 },
    { repo: "org/b", safe_context: 16384, context_limit: 16384 }] };
let current = view();
// The harness never answers the page's first state request; forget it, as it would end.
state.refreshing = null;
respond = (path) => reply(path === "/api/server/estimate" ? estimate : current);
const show = async (s) => { current = s; state.snapshot = s; render(s);
  await new Promise((r) => setTimeout(r, 0)); };
// Puts these models on the card, as the add and remove buttons do.
const pick = async (...repos) => {
  await show(current);
  editServerSet(repos);
  await new Promise((r) => setTimeout(r, 0));
};
const text = (s) => $(s).textContent;
const sent = (path) => calls.filter((c) => c.path === path).map((c) => JSON.parse(c.opts.body));
"""


def ui(tmp_path, scenario):
    return run(tmp_path, SETUP + scenario)


@needs_node
def test_estimate_and_safe_context(tmp_path):
    out = ui(
        tmp_path,
        'await pick("org/a", "org/b");'
        ' return [$("#serverEstimate").hidden, text("#serverEstimate"),'
        ' text("#serverContextHint"), $("#serverStartBtn").disabled,'
        ' sent("/api/server/estimate")];',
    )
    hidden, estimate, hint, disabled, asked = out
    assert not hidden and not disabled
    assert estimate.startswith("Two models: about 26K tokens each")
    assert "Uses about 17.8 of 25.0 GB free." in estimate
    assert "a about 26K" in hint and "b about 16K (model limit)" in hint
    assert "estimate" in hint.lower()
    assert asked == [{"repo": "org/a", "also": ["org/b"]}]


@needs_node
def test_start_is_off_over_budget_and_back_with_one_model(tmp_path):
    out = ui(
        tmp_path,
        'estimate = { ...estimate, fits: false, refused: "21.6 GB needed" };'
        ' await pick("org/a", "org/b");'
        ' const over = [$("#serverStartBtn").disabled, text("#serverWhy")];'
        ' await pick("org/a");'
        ' return [over, $("#serverStartBtn").disabled, $("#serverPairHint").hidden];',
    )
    (disabled, reason), after, hidden = out
    assert disabled and reason == "21.6 GB needed"
    assert after is False and hidden is True


@needs_node
def test_polls_do_not_ask_again(tmp_path):
    out = ui(
        tmp_path,
        'await pick("org/a", "org/b"); await show(current); await show(current);'
        ' const before = sent("/api/server/estimate").length;'
        " await show(view({}));"
        ' return [before, sent("/api/server/estimate").length];',
    )
    assert out == [1, 1]


@needs_node
def test_a_failed_estimate_says_why(tmp_path):
    out = ui(
        tmp_path,
        'respond = (path) => path === "/api/server/estimate"'
        ' ? reply({ error: "org/b is not installed completely" }, false) : reply(current);'
        ' await pick("org/a", "org/b");'
        ' return [text("#serverWhy"), $("#serverStartBtn").disabled];',
    )
    assert "not installed completely" in out[0] and out[1] is False


@needs_node
def test_start_sends_both_or_one(tmp_path):
    out = ui(
        tmp_path,
        'await pick("org/a", "org/b"); await $("#serverStartBtn").listeners.click();'
        ' await pick("org/a"); await $("#serverStartBtn").listeners.click();'
        ' return sent("/api/server/start");',
    )
    assert out == [
        {"repo": "org/a", "also": ["org/b"], "background": False},
        {"repo": "org/a", "background": False},
    ]


@needs_node
def test_restart_after_a_crash_sends_the_pair_that_was_running(tmp_path):
    out = ui(
        tmp_path,
        'await show(running()); await show(view({ down_notice: "gone" }));'
        ' await $("#serverRestartBtn").listeners.click(); return sent("/api/server/start");',
    )
    assert out == [{"repo": "org/a", "also": ["org/b"], "background": False}]


@needs_node
def test_unguarded_restart_sends_the_running_pair(tmp_path):
    out = ui(
        tmp_path,
        "await show(running({ unguarded: true, background: true }));"
        ' editServerSet(["org/c"]);'
        ' await $("#serverUnguardedRestart").listeners.click();'
        ' return sent("/api/server/start");',
    )
    assert out == [{"repo": "org/a", "also": ["org/b"], "background": True}]


@needs_node
def test_running_state_lists_resident_models_and_measured_memory(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ resident: ["org/b", "org/a"], memory: { active_bytes: 9.1e9,'
        " peak_bytes: 12.4e9, budget_bytes: 17.9e9, measured: true } }));"
        ' return [html("#serverState"), text("#serverMemory"), $("#serverMemory").hidden,'
        ' $("#serverMulti").hidden];',
    )
    state, memory, hidden, notice_hidden = out
    assert "Running: b (current), a" in state
    assert "Using 9.1 GB (peak 12.4 GB)" in memory and "measured" in memory.lower()
    assert hidden is False and notice_hidden is True


@needs_node
def test_a_one_model_server_looks_as_before_with_its_memory(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ repos: ["org/a"], resident: ["org/a"], memory: {'
        " active_bytes: 4e9, peak_bytes: 5e9, budget_bytes: 17.9e9, measured: true } }));"
        ' const one = [html("#serverState"), text("#serverMemory")];'
        " await show(view());"
        ' return [...one, $("#serverMemory").hidden, $("#serverMulti").hidden];',
    )
    assert "Running — a" in out[0] and "4.0 GB" in out[1]
    assert out[2:] == [True, True]


@needs_node
def test_notices(tmp_path):
    out = ui(
        tmp_path,
        'const seen = []; const note = () => seen.push([$("#serverMulti").hidden,'
        ' text("#serverMultiText")]);'
        ' await show(running({ multi_off: "ModelProvider._load changed" })); note();'
        ' await show(running({ resident: ["org/a"],'
        ' events: [{ event: "preload-skipped", model: "org/b" }] })); note();'
        ' await show(running({ resident: ["org/c", "org/a"] })); note();'
        ' await show(running({ resident: ["org/a", "org/b"] })); note();'
        " return seen;",
    )
    assert not out[0][0] and "ModelProvider._load changed" in out[0][1]
    assert not out[1][0] and "b was not preloaded" in out[1][1]
    assert not out[2][0] and "b was replaced by c on request" in out[2][1]
    assert out[3][0]


@needs_node
def test_memory_events_join_the_server_log(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ lines: ["ready"], events: ['
        ' { event: "load", model: "org/a" }, { event: "evict", model: "org/b" },'
        ' { event: "hit", model: "org/a" }] })); return text("#serverLog");',
    )
    assert out == "ready\n[memory] evict org/b"


@needs_node
def test_tool_warning_checks_both_models(tmp_path):
    out = ui(
        tmp_path,
        'const support = { "org/a": true, "org/b": false };'
        " await show(view({ tool_support: support }));"
        ' await pick("org/a", "org/b");'
        ' const picked = [$("#serverToolsWarn").hidden, text("#serverToolsWarnText")];'
        " await show(running({ tool_support: support }));"
        ' return [picked, $("#serverToolsWarn").hidden];',
    )
    (hidden, words), running_hidden = out
    assert hidden is False and words.startswith("b: ")
    assert running_hidden is False


@needs_node
def test_a_hostile_name_is_escaped(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ resident: ["org/<img src=x>", "org/a"] }));'
        ' return html("#serverState");',
    )
    assert "<img" not in out and "&lt;img" in out


def test_the_screen_still_has_search_and_copy():
    script = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'createPicker("#serverModelSelect"' in script
    assert "navigator.clipboard.writeText" in script
    assert json.dumps("/api/server/estimate") in script
