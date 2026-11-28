"""Row 831: the API Server screen is one card with a list of chosen models."""

from __future__ import annotations

import importlib.util
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
spec = importlib.util.spec_from_file_location("row656_ui_for831", ROOT / "tests/test_row656_ui.py")
base = importlib.util.module_from_spec(spec)
spec.loader.exec_module(base)
ui, needs_node = base.ui, base.needs_node
HTML = (ROOT / "ui" / "index.html").read_text("utf-8")

VIEW = HTML[HTML.index('id="view-server"') : HTML.index('id="view-logs"')]
CARD = VIEW[VIEW.index('id="serverCard"') : VIEW.index('id="serverStopBtn"') + 200]


class Tags(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.by_id = {}
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        found = dict(attrs)
        if "id" in found:
            self.by_id[found["id"]] = {**found, "tag": tag}


PAGE = Tags(HTML).by_id


# markup ---------------------------------------------------------------------- #
def test_the_top_bar_holds_only_the_title_and_the_state():
    bar = VIEW[: VIEW.index("</header>")]
    assert 'id="serverState"' in bar
    assert "<button" not in bar and "<input" not in bar and "<select" not in bar


def test_one_card_holds_the_choice_in_order():
    order = [
        "serverModelList",
        "serverAddBtn",
        "serverModelSelect",
        "serverWhy",
        "serverBackground",
        "serverStartBtn",
        "serverStopBtn",
    ]
    positions = [CARD.index(f'id="{name}"') for name in order]
    assert positions == sorted(positions)
    assert "max 2" not in CARD.lower()
    assert "Also keep loaded" not in HTML and "serverModel2" not in HTML


def test_the_controls_are_labelled():
    assert PAGE["serverAddBtn"]["aria-controls"] == "serverAddPicker"
    assert PAGE["serverAddBtn"]["aria-expanded"] == "false"
    assert PAGE["serverAddBtn"]["aria-describedby"] == "serverAddWhy"
    assert "hidden" in PAGE["serverAddPicker"]
    assert PAGE["serverStartBtn"]["aria-describedby"] == "serverStartWhy serverApplyNote"
    assert PAGE["serverModelList"]["aria-labelledby"] == "serverModelsTitle"
    # One live region says the reason; the visible copy is not read twice.
    assert PAGE["serverStartWhy"]["role"] == "status"
    assert "sr-only" in PAGE["serverStartWhy"]["class"] and "hidden" not in PAGE["serverStartWhy"]
    assert PAGE["serverWhy"]["aria-hidden"] == "true"
    assert PAGE["serverSetStatus"]["role"] == "status"
    notice = VIEW[VIEW.index('id="serverApplyNotice"') - 40 : VIEW.index('id="serverCard"')]
    assert 'role="alert"' in notice and 'id="serverApplyDismiss"' in notice
    assert "style=" not in VIEW


# behaviour ------------------------------------------------------------------- #
HELPERS = """
const tick = () => new Promise((r) => setTimeout(r, 0));
const add = async (repo) => {
  $("#serverModelSelect").value = repo; $("#serverModelSelect").listeners.change(); await tick(); };
const removers = () => $("#serverModelList").querySelectorAll("[data-remove]");
const drop = async (repo) => {
  removers().find((b) => b.dataset.remove === repo).listeners.click(); await tick(); };
const listed = () => removers().map((b) => b.dataset.remove);
const press = async (id) => { await $(id).listeners.click(); await tick(); };
const main = () => [$("#serverStartBtn").textContent, $("#serverStartBtn").disabled];
const why = () => text("#serverWhy");
const focused = () =>
  document.activeElement?.dataset?.remove || document.activeElement?.selector;
const loaded = (over = {}) => running(over);
"""


def screen(tmp_path, scenario):
    return ui(tmp_path, HELPERS + scenario)


@needs_node
def test_stopped_with_no_model(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); return { main: main(), why: why(), spoken: text('#serverStartWhy'),"
        " list: listed(), empty: $('#serverModelEmpty').hidden,"
        " stop: $('#serverStopBtn').hidden,"
        " note: text('#serverApplyNote') };",
    )
    assert out == {
        "main": ["Start Server", True],
        "why": "Add a model to start the server.",
        "spoken": "Add a model to start the server.",
        "list": [],
        "empty": False,
        "stop": True,
        "note": "",
    }


@needs_node
def test_one_model_fills_the_card_and_can_start(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await add('org/a'); await show(current);"
        " const seen = { main: main(), rows: html('#serverModelList') };"
        " await press('#serverStartBtn'); seen.sent = sent('/api/server/start'); return seen;",
    )
    assert out["main"] == ["Start Server", False]
    assert 'aria-label="Remove org/a"' in out["rows"] and 'data-remove="org/a"' in out["rows"]
    assert ">a<" in out["rows"] and "context" not in out["rows"]
    assert out["sent"] == [{"repo": "org/a", "background": True}]


@needs_node
def test_a_second_model_cannot_be_added(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await add('org/a');"
        " const seen = { add: $('#serverAddBtn').disabled, why: text('#serverAddWhy') };"
        " await add('org/b'); seen.list = listed(); return seen;",
    )
    assert out["list"] == ["org/a"]
    assert out["add"] is True and "one model" in out["why"]


@needs_node
def test_the_same_model_is_not_offered_or_added_twice(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await add('org/a');"
        " const offered = html('#serverModelSelect'); await add('org/a');"
        " return [offered, listed()];",
    )
    assert 'value="org/a"' not in out[0] and 'value="org/b"' in out[0]
    assert out[1] == ["org/a"]


@needs_node
def test_add_opens_the_searchable_picker_and_escape_closes_it(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await press('#serverAddBtn');"
        " const open = [$('#serverAddPicker').hidden, $('#serverModelSelectList').hidden,"
        " $('#serverAddBtn').attrs['aria-expanded'], focused()];"
        " $('#serverModelSelectSearch').listeners.keydown({ key: 'Escape', preventDefault() {} });"
        " return [open, [$('#serverAddPicker').hidden, $('#serverAddBtn').attrs['aria-expanded'],"
        " focused()]];",
    )
    assert out[0] == [False, False, "true", "#serverModelSelectSearch"]
    assert out[1] == [True, "false", "#serverAddBtn"]


@needs_node
def test_leaving_the_picker_hides_it_without_moving_focus(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await press('#serverAddBtn'); $('#copyEndpoint').focus();"
        " $('#serverModelSelectSearch').listeners.blur();"
        " return [$('#serverAddPicker').hidden, focused()];",
    )
    assert out == [True, "#copyEndpoint"]


@needs_node
def test_focus_and_announcement_follow_add_and_remove(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); const seen = [];"
        " const note = () => seen.push([focused(), text('#serverSetStatus'),"
        " $('#serverAddPicker').hidden]);"
        " await press('#serverAddBtn'); await add('org/a'); note();"
        " await drop('org/a'); note(); return seen;",
    )
    # The card is full after the one model, so focus goes to that model's remove button.
    assert out == [["org/a", "Added a.", True], ["#serverAddBtn", "Removed a.", True]]


@needs_node
def test_running_shows_what_really_runs(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded());"
        " return { list: listed(), main: main(), stop: $('#serverStopBtn').hidden,"
        " note: text('#serverApplyNote'), why: why() };",
    )
    assert out == {
        "list": ["org/a"],
        "main": ["Apply changes", True],
        "stop": False,
        "note": "",
        "why": "",
    }


@needs_node
def test_a_pending_change_survives_polls_and_is_applied(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await add('org/c');"
        " await show(loaded()); await show(loaded());"
        " const seen = { list: listed(), main: main(), note: text('#serverApplyNote'),"
        " state: html('#serverState') };"
        " await press('#serverStartBtn'); seen.sent = sent('/api/server/start'); return seen;",
    )
    assert out["list"] == ["org/c"]
    assert out["main"] == ["Apply changes", False]
    assert out["note"] == "Applying restarts the server: a reply in progress will be interrupted."
    assert "Running — a" in out["state"]
    assert out["sent"] == [{"repo": "org/c", "background": False}]


@needs_node
def test_background_alone_is_a_change_and_undoing_it_is_not(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); const box = $('#serverBackground');"
        " box.checked = true; box.listeners.change(); await show(loaded()); const on = main();"
        " const kept = box.checked; box.checked = false; box.listeners.change();"
        " const off = main(); box.checked = true; box.listeners.change();"
        " await press('#serverStartBtn');"
        " return [on, kept, off, sent('/api/server/start')];",
    )
    assert out == [
        ["Apply changes", False],
        True,
        ["Apply changes", True],
        [{"repo": "org/a", "background": True}],
    ]


@needs_node
def test_choosing_the_running_model_again_is_not_a_change(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await add('org/b'); const other = main();"
        " await drop('org/b'); await add('org/a'); return [other, main()];",
    )
    assert out == [["Apply changes", False], ["Apply changes", True]]


@needs_node
def test_removing_every_model_asks_for_one_or_stop(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await show(loaded());"
        " return [listed(), main(), why()];",
    )
    assert out == [[], ["Apply changes", True], "Add a model, or press Stop."]


@needs_node
def test_a_new_server_replaces_the_edits_with_what_runs(tmp_path):
    # After a failed Apply the previous model comes back under a new process.
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await add('org/c');"
        " $('#serverBackground').checked = true; $('#serverBackground').listeners.change();"
        " await press('#serverStartBtn'); await show(view({ starting: true }));"
        " const during = [listed(), main(), $('#serverStopBtn').hidden];"
        " await show(loaded({ pid: 8, apply_notice: 'The new server did not start: no.' }));"
        " return [during, listed(), $('#serverBackground').checked, main(),"
        " $('#serverApplyNotice').hidden, text('#serverApplyText')];",
    )
    during, listed, background, button, hidden, words = out
    assert during == [["org/c"], ["Starting…", True], False]
    assert listed == ["org/a"] and background is False
    assert button == ["Apply changes", True]
    assert hidden is False and words == "The new server did not start: no."


@needs_node
def test_the_apply_notice_can_be_dismissed(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded({ apply_notice: 'x' })); await press('#serverApplyDismiss');"
        " await show(loaded()); return [sent('/api/server/notice/clear').length,"
        " $('#serverApplyNotice').hidden];",
    )
    assert out == [1, True]


@needs_node
def test_a_refused_apply_keeps_the_edits_and_says_why(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await add('org/c');"
        ' const answer = respond; respond = (path, opts) => path === "/api/server/start"'
        ' ? reply({ error: "the model is being deleted" }, false) : answer(path, opts);'
        " await press('#serverStartBtn'); await show(loaded()); const said = why();"
        " await drop('org/c'); return [said, why(), listed()];",
    )
    assert out == ["the model is being deleted", "Add a model, or press Stop.", []]


@needs_node
def test_after_stop_the_last_set_can_be_started_again(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await show(view()); const seen = [listed(), main()];"
        " await press('#serverStartBtn'); return [...seen, sent('/api/server/start')];",
    )
    assert out == [
        ["org/a"],
        ["Start Server", False],
        [{"repo": "org/a", "background": False}],
    ]


@needs_node
def test_polls_do_not_redraw_or_announce_again(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await add('org/a'); await show(current);"
        " const count = () => [$('#serverModelList').writes, $('#serverStartWhy').textWrites,"
        " $('#serverSetStatus').textWrites, $('#serverStartBtn').textWrites,"
        " $('#serverModelSelect').writes];"
        " const before = count(); await show(current); await show(current);"
        " return [before, count()];",
    )
    assert out[0] == out[1]


@needs_node
def test_focus_leaves_a_button_that_can_no_longer_be_used(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await add('org/b');"
        " $('#serverStartBtn').focus();"
        " await show(loaded({ pid: 9, repo: 'org/b', repos: ['org/b'] }));"
        " const applied = focused(); $('#serverStopBtn').focus(); await show(view());"
        " return [applied, focused()];",
    )
    assert out == ["#serverBackground", "#serverStartBtn"]


@needs_node
def test_no_other_model_to_add(tmp_path):
    out = screen(
        tmp_path,
        "const only = { ...view(), models: [models[0]] }; await show(only);"
        " await add('org/a'); const one = [$('#serverAddBtn').disabled, text('#serverAddWhy')];"
        " await show({ ...view(), models: [] });"
        " return [one, $('#serverAddBtn').disabled, text('#serverAddWhy'), listed()];",
    )
    assert out[0] == [True, "The server serves one model. Remove it to choose another."]
    assert out[1:] == [True, "No models installed. Get one on the Download page.", []]


@needs_node
def test_focus_works_with_a_browser_node_list(tmp_path):
    # A real querySelectorAll answers a NodeList, which has no array methods but forEach.
    out = screen(
        tmp_path,
        "await show(view()); const list = $('#serverModelList');"
        " const real = list.querySelectorAll.bind(list);"
        " list.querySelectorAll = (s) => { const found = real(s);"
        " return { ...found, length: found.length, forEach: (f) => found.forEach(f) }; };"
        " $('#serverModelSelect').value = 'org/b';"
        " $('#serverModelSelect').listeners.change(); await tick(); return focused();",
    )
    assert out == "org/b"


@needs_node
def test_a_failed_apply_is_also_said_from_another_page_once(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await show(loaded({ pid: 8, apply_notice: 'Not started.' }));"
        " const first = text('#toast'); $('#toast').textContent = '';"
        " await show(loaded({ pid: 8, apply_notice: 'Not started.' }));"
        " return [first, text('#toast')];",
    )
    assert out == ["Not started.", ""]


# Enter on the focused control; a checkbox does not answer it.
ENTER = "await document.activeElement.listeners.click?.(); await tick();"


@needs_node
def test_a_second_enter_after_apply_does_not_stop_the_server(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); editServerSet(['org/c']); await tick();"
        " current = view({ starting: true }); $('#serverStartBtn').focus();"
        " await press('#serverStartBtn'); const on = focused();"
        f" {ENTER} return [on, sent('/api/server/stop').length,"
        " sent('/api/server/start').length];",
    )
    assert out == ["#serverBackground", 0, 1]


# Holds back the answers for one path until the scenario releases them.
HOLD = """
const held = []; const answer = respond;
const hold = (wanted) => { respond = (path, opts) => path === wanted
  ? new Promise((done) => held.push(() => done(answer(path, opts)))) : answer(path, opts); };
const release = async () => { respond = answer; held.splice(0).forEach((f) => f()); await tick(); };
const click = () => { $("#serverStartBtn").listeners.click(); };
const starts = () => sent("/api/server/start").length;
"""


@needs_node
@pytest.mark.parametrize("slow", ["/api/server/start", "/api/state"])
def test_a_second_click_is_ignored_until_the_start_is_on_screen(tmp_path, slow):
    # In flight: the request itself, then the state that shows what it did.
    out = screen(
        tmp_path,
        HOLD + "await show(loaded()); await drop('org/a'); await add('org/b');"
        f" hold('{slow}'); click(); await tick(); click(); await tick(); const during = starts();"
        " await release(); click(); await tick(); return [during, starts()];",
    )
    assert out == [1, 2]


@needs_node
def test_a_refused_start_can_be_tried_again(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await add('org/b');"
        " const answer = respond;"
        ' respond = (path, opts) => path === "/api/server/start"'
        ' ? reply({ error: "busy" }, false) : answer(path, opts);'
        " await press('#serverStartBtn'); await press('#serverStartBtn');"
        " return sent('/api/server/start').length;",
    )
    assert out == 2
