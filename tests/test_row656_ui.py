"""Row 656: the API Server screen picks, shows and restarts the model (one since row 887)."""

from __future__ import annotations

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location(
    "ui_behaviour_helpers", ROOT / "tests" / "test_ui_behaviour.py"
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
run, needs_node = helpers.run, helpers.needs_node

# A server screen with three installed models and a fake API.
SETUP = """
const models = ["org/a", "org/b", "org/c"].map((repo) =>
  ({ repo, path: repo, size_h: "4 GB", complete: true }));
const server = (over = {}) => ({ running: false, endpoint: "e", lines: [], tool_support: {},
  repo: null, repos: [], ...over });
const view = (over = {}, rest = {}) => snap({ models, server: server(over), ...rest });
const running = (over = {}) => view({ running: true, pid: 7, repo: "org/a",
  repos: ["org/a"], ...over });
let current = view();
// The harness never answers the page's first state request; forget it, as it would end.
state.refreshing = null;
respond = () => reply(current);
const show = async (s) => { current = s; state.snapshot = s; render(s);
  await new Promise((r) => setTimeout(r, 0)); };
// Puts this model on the card, as the add and remove buttons do.
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
def test_start_sends_the_chosen_model(tmp_path):
    out = ui(
        tmp_path,
        'await pick("org/b"); await $("#serverStartBtn").listeners.click();'
        ' return sent("/api/server/start");',
    )
    assert out == [{"repo": "org/b", "background": True}]


@needs_node
def test_restart_after_a_crash_sends_the_model_that_was_running(tmp_path):
    out = ui(
        tmp_path,
        'await show(running()); await show(view({ down_notice: "gone" }));'
        ' await $("#serverRestartBtn").listeners.click(); return sent("/api/server/start");',
    )
    assert out == [{"repo": "org/a", "background": True}]


@needs_node
def test_the_running_state_names_the_model(tmp_path):
    out = ui(
        tmp_path,
        'await show(running()); const on = html("#serverState");'
        ' await show(view()); return [on, html("#serverState")];',
    )
    assert "Running — a" in out[0]
    assert "Running —" not in out[1]


@needs_node
def test_tool_warning_follows_the_model(tmp_path):
    out = ui(
        tmp_path,
        'const support = { "org/a": true, "org/b": false };'
        " await show(view({ tool_support: support }));"
        ' await pick("org/b");'
        ' const picked = [$("#serverToolsWarn").hidden, text("#serverToolsWarnText")];'
        ' await show(running({ repo: "org/b", repos: ["org/b"], tool_support: support }));'
        ' const on = $("#serverToolsWarn").hidden;'
        " await show(running({ tool_support: support }));"
        ' return [picked, on, $("#serverToolsWarn").hidden];',
    )
    (hidden, words), without, with_tools = out
    assert hidden is False and words.startswith("Agent clients")
    assert without is False and with_tools is True


@needs_node
def test_a_hostile_name_is_escaped(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ repo: "org/<img src=x>", repos: ["org/<img src=x>"] }));'
        ' return [html("#serverState"), html("#serverModelList")];',
    )
    for markup in out:
        assert "<img" not in markup and "&lt;img" in markup


def test_the_screen_still_has_search_and_copy():
    script = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
    assert 'createPicker("#serverModelSelect"' in script
    assert "navigator.clipboard.writeText" in script
