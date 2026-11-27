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
ui, needs_node, HTML = base.ui, base.needs_node, base.HTML

VIEW = HTML[HTML.index('id="view-server"') : HTML.index('id="view-logs"')]
CARD = VIEW[VIEW.index('id="serverCard"') : VIEW.index('id="serverMulti"')]


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
        "serverMemoryLine",
        "serverWhy",
        "serverDetails",
        "serverBackground",
        "serverStartBtn",
        "serverStopBtn",
    ]
    positions = [CARD.index(f'id="{name}"') for name in order]
    assert positions == sorted(positions)
    assert "Models (max 2)" in CARD
    assert "Also keep loaded" not in HTML and "serverModel2" not in HTML


def test_the_long_explanations_sit_inside_details():
    details = CARD[CARD.index('id="serverDetails"') : CARD.index("</details>")]
    assert "<summary>Details</summary>" in details
    for name in (
        "serverEstimate",
        "serverContextHint",
        "serverMemory",
        "serverPromptHint",
        "serverPairHint",
    ):
        assert f'id="{name}"' in details
    assert 'id="serverWhy"' not in details and 'id="serverMemoryLine"' not in details


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
    assert "role" not in PAGE["serverMemoryLine"]
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
const pair = ["org/a", "org/b"];
const loaded = (over = {}) => running({ resident: pair, context_tokens: 32768, ...over });
const single = { fits: true, refused: null, context_tokens: 32768, free_bytes: null,
  total_bytes: 8e9, budget_bytes: 17.9e9,
  models: [{ repo: "org/a", safe_context: 61000, context_limit: 131072 }] };
const both = estimate;
respond = (path, opts) => reply(path !== "/api/server/estimate" ? current
  : JSON.parse(opts.body).also ? estimate : single);
"""


def screen(tmp_path, scenario):
    return ui(tmp_path, HELPERS + scenario)


@needs_node
def test_stopped_with_no_model(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); return { main: main(), why: why(), spoken: text('#serverStartWhy'),"
        " list: listed(), empty: $('#serverModelEmpty').hidden,"
        " memory: $('#serverMemoryLine').hidden, details: $('#serverDetails').hidden,"
        " stop: $('#serverStopBtn').hidden, note: text('#serverApplyNote'),"
        " asked: sent('/api/server/estimate').length };",
    )
    assert out == {
        "main": ["Start Server", True],
        "why": "Add a model to start the server.",
        "spoken": "Add a model to start the server.",
        "list": [],
        "empty": False,
        "memory": True,
        "details": True,
        "stop": True,
        "note": "",
        "asked": 0,
    }


@needs_node
def test_one_model_has_no_two_model_words(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await add('org/a'); await show(current);"
        " const ids = ['#serverWhy', '#serverStartWhy', '#serverEstimate', '#serverContextHint',"
        " '#serverMemory', '#serverPromptHint', '#serverMemoryHint', '#serverAddWhy',"
        " '#serverApplyNote', '#serverMemoryLine'];"
        " return { main: main(), line: text('#serverMemoryLine'),"
        " words: ids.map(text).join(' '), pair: $('#serverPairHint').hidden,"
        " rows: html('#serverModelList'), asked: sent('/api/server/estimate') };",
    )
    assert out["main"] == ["Start Server", False]
    assert out["line"] == "Memory: 8.0 / 17.9 GB"
    assert "two" not in out["words"].lower() and out["pair"] is True
    assert 'aria-label="Remove org/a"' in out["rows"] and 'data-remove="org/a"' in out["rows"]
    assert ">a<" in out["rows"] and "about 59K context" in out["rows"]
    assert out["asked"] == [{"repo": "org/a"}]


@needs_node
def test_two_models_fill_the_list(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await add('org/a'); await add('org/b');"
        " const seen = { list: listed(), add: $('#serverAddBtn').disabled,"
        " addWhy: text('#serverAddWhy'), pair: $('#serverPairHint').hidden,"
        " rows: html('#serverModelList'), line: text('#serverMemoryLine') };"
        " await add('org/c'); seen.third = listed();"
        " await press('#serverStartBtn'); seen.sent = sent('/api/server/start'); return seen;",
    )
    assert out["list"] == out["third"] == ["org/a", "org/b"]
    assert out["add"] is True and "maximum" in out["addWhy"]
    assert out["pair"] is False and out["line"] == "Memory: 17.8 / 17.9 GB"
    assert "about 26K context" in out["rows"] and "about 16K context" in out["rows"]
    assert out["sent"] == [{"repo": "org/a", "also": ["org/b"], "background": False}]


@needs_node
def test_the_same_model_is_not_offered_or_added_twice(tmp_path):
    out = screen(
        tmp_path,
        "await show(view()); await add('org/a'); await press('#serverAddBtn');"
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
        " await press('#serverAddBtn'); await add('org/b'); note();"
        " await drop('org/a'); note(); await drop('org/b'); note(); return seen;",
    )
    assert out == [
        ["#serverAddBtn", "Added a.", True],
        ["org/b", "Added b.", True],
        ["org/b", "Removed a.", True],
        ["#serverAddBtn", "Removed b.", True],
    ]


@needs_node
def test_a_refused_pair_is_said_without_opening_details(tmp_path):
    out = screen(
        tmp_path,
        'estimate = { ...both, fits: false, refused: "21.6 GB needed." };'
        " await show(view()); await add('org/a'); await add('org/b');"
        " return [main(), why(), text('#serverStartWhy'), text('#serverEstimate'),"
        " $('#serverWhy').hidden, html('#serverModelList')];",
    )
    assert out[:5] == [["Start Server", True], "21.6 GB needed.", "21.6 GB needed.", "", False]
    assert "context" not in out[5]


@needs_node
@pytest.mark.parametrize(
    ("limited", "words"),
    [
        ("true", "Shorter context: about 10K tokens per model. Close other apps for more."),
        ("false", "Shorter context: about 10K tokens per model."),
    ],
)
def test_a_shrunk_context_is_warned_without_opening_details(tmp_path, limited, words):
    out = screen(
        tmp_path,
        f"estimate = {{ ...both, context_tokens: 10240, free_is_limit: {limited} }};"
        " await show(view()); await add('org/a'); await add('org/b');"
        " return [why(), $('#serverWhy').hidden, $('#serverStartBtn').disabled];",
    )
    assert out == [words, False, False]


@needs_node
def test_full_context_needs_no_warning(tmp_path):
    out = screen(
        tmp_path,
        "estimate = { ...both, context_tokens: 32768 };"
        " await show(view()); await add('org/a'); await add('org/b');"
        " return [why(), $('#serverWhy').hidden];",
    )
    assert out == ["", True]


@needs_node
@pytest.mark.parametrize(
    ("safe", "words"),
    [
        (12288, "Shorter context: about 12K tokens."),
        (0, "This model may not fit: it needs about 21.0 GB and the API server may use 17.9 GB."),
    ],
)
def test_one_large_model_is_warned_but_can_still_start(tmp_path, safe, words):
    out = screen(
        tmp_path,
        "single.fits = false; single.total_bytes = 21e9;"
        f" single.models[0].safe_context = {safe};"
        " await show(view()); await add('org/a'); return [why(), main()];",
    )
    assert out == [words, ["Start Server", False]]


@needs_node
def test_running_shows_what_really_runs(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded({ context_tokens: 10240, memory: { active_bytes: 9.1e9,"
        " peak_bytes: 12e9, budget_bytes: 13.1e9 } }));"
        " return { list: listed(), rows: html('#serverModelList'), main: main(),"
        " stop: $('#serverStopBtn').hidden, note: text('#serverApplyNote'),"
        " line: text('#serverMemoryLine'), why: why() };",
    )
    assert out["list"] == ["org/a", "org/b"] and out["rows"].count("about 10K context") == 2
    assert out["main"] == ["Apply changes", True] and out["stop"] is False
    assert out["note"] == "" and out["line"] == "Memory: 9.1 / 13.1 GB"
    assert out["why"] == "Shorter context: about 10K tokens per model."


@needs_node
def test_a_pending_change_survives_polls_and_is_applied(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/b'); await add('org/c');"
        " await show(loaded()); await show(loaded());"
        " const seen = { list: listed(), main: main(), note: text('#serverApplyNote'),"
        " state: html('#serverState') };"
        " await press('#serverStartBtn'); seen.sent = sent('/api/server/start'); return seen;",
    )
    assert out["list"] == ["org/a", "org/c"]
    assert out["main"] == ["Apply changes", False]
    assert out["note"] == "Applying restarts the server: a reply in progress will be interrupted."
    assert "a (current), b" in out["state"]
    assert out["sent"] == [{"repo": "org/a", "also": ["org/c"], "background": False}]


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
        [{"repo": "org/a", "also": ["org/b"], "background": True}],
    ]


@needs_node
def test_the_same_set_in_another_order_is_not_a_change(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); const one = main();"
        " await add('org/a'); return [one, main()];",
    )
    assert out == [["Apply changes", False], ["Apply changes", True]]


@needs_node
def test_removing_every_model_asks_for_one_or_stop(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/a'); await drop('org/b'); await show(loaded());"
        " return [listed(), main(), why()];",
    )
    assert out == [[], ["Apply changes", True], "Add a model, or press Stop."]


@needs_node
def test_a_new_server_replaces_the_edits_with_what_runs(tmp_path):
    # After a failed Apply the previous set comes back under a new process.
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/b'); await add('org/c');"
        " $('#serverBackground').checked = true; $('#serverBackground').listeners.change();"
        " await press('#serverStartBtn'); await show(view({ starting: true }));"
        " const during = [listed(), main(), $('#serverStopBtn').hidden];"
        " await show(loaded({ pid: 8, apply_notice: 'The new server did not start: no.' }));"
        " return [during, listed(), $('#serverBackground').checked, main(),"
        " $('#serverApplyNotice').hidden, text('#serverApplyText')];",
    )
    during, listed, background, button, hidden, words = out
    assert during == [["org/a", "org/c"], ["Starting…", True], False]
    assert listed == ["org/a", "org/b"] and background is False
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
def test_a_pending_pair_that_does_not_fit_cannot_be_applied(tmp_path):
    out = screen(
        tmp_path,
        'estimate = { ...both, context_tokens: 32768, fits: false, refused: "No room." };'
        " await show(loaded()); const same = [why(), main()];"
        " await drop('org/b'); await add('org/c'); const pending = [why(), main()];"
        " await drop('org/c'); await add('org/b'); return [same, pending, [why(), main()]];",
    )
    unchanged = ["", ["Apply changes", True]]
    assert out == [unchanged, ["No room.", ["Apply changes", True]], unchanged]


@needs_node
def test_a_pending_pair_is_asked_again_while_the_server_runs(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/b'); await add('org/c');"
        " const first = sent('/api/server/estimate').length;"
        " state.estimateRetryAt = 0; await show(loaded());"
        " return sent('/api/server/estimate').length - first;",
    )
    assert out == 1


@needs_node
def test_a_refused_apply_keeps_the_edits_and_says_why(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/b'); await add('org/c');"
        ' const answer = respond; respond = (path, opts) => path === "/api/server/start"'
        ' ? reply({ error: "the model is being deleted" }, false) : answer(path, opts);'
        " await press('#serverStartBtn'); await show(loaded()); const said = why();"
        " await drop('org/c'); return [said, why(), listed()];",
    )
    assert out == ["the model is being deleted", "", ["org/a"]]


@needs_node
def test_after_stop_the_last_set_can_be_started_again(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await show(view()); const seen = [listed(), main()];"
        " await press('#serverStartBtn'); return [...seen, sent('/api/server/start')];",
    )
    assert out == [
        ["org/a", "org/b"],
        ["Start Server", False],
        [{"repo": "org/a", "also": ["org/b"], "background": False}],
    ]


@needs_node
def test_polls_do_not_redraw_or_announce_again(tmp_path):
    out = screen(
        tmp_path,
        'estimate = { ...both, fits: false, refused: "No room." };'
        " await show(view()); await add('org/a'); await add('org/b'); await show(current);"
        " const count = () => [$('#serverModelList').writes, $('#serverStartWhy').textWrites,"
        " $('#serverSetStatus').textWrites, $('#serverMemoryLine').textWrites,"
        " $('#serverStartBtn').textWrites, $('#serverModelSelect').writes];"
        " const before = count(); await show(current); await show(current);"
        " return [before, count()];",
    )
    assert out[0] == out[1]


@needs_node
def test_focus_leaves_a_button_that_can_no_longer_be_used(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/b'); $('#serverStartBtn').focus();"
        " await show(loaded({ pid: 9, repos: ['org/a'], resident: ['org/a'] }));"
        " const applied = focused(); $('#serverStopBtn').focus(); await show(view());"
        " return [applied, focused()];",
    )
    assert out == ["#serverAddBtn", "#serverStartBtn"]


@needs_node
def test_no_other_model_to_add(tmp_path):
    out = screen(
        tmp_path,
        "const only = { ...view(), models: [models[0]] }; await show(only);"
        " await add('org/a'); const one = [$('#serverAddBtn').disabled, text('#serverAddWhy')];"
        " await show({ ...view(), models: [] });"
        " return [one, $('#serverAddBtn').disabled, text('#serverAddWhy'), listed()];",
    )
    assert out[0] == [True, "No other installed model to add."]
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
        " await add('org/a'); $('#serverModelSelect').value = 'org/b';"
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
@pytest.mark.parametrize(
    ("repos", "target"),
    [(["org/a"], "#serverAddBtn"), (["org/a", "org/c"], "#serverBackground")],
)
def test_a_second_enter_after_apply_does_not_stop_the_server(tmp_path, repos, target):
    out = screen(
        tmp_path,
        f"await show(loaded()); editServerSet({repos}); await tick();"
        " current = view({ starting: true }); $('#serverStartBtn').focus();"
        " await press('#serverStartBtn'); const on = focused();"
        f" {ENTER} return [on, sent('/api/server/stop').length,"
        " sent('/api/server/start').length];",
    )
    assert out == [target, 0, 1]


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
        HOLD + "await show(loaded()); await drop('org/b');"
        f" hold('{slow}'); click(); await tick(); click(); await tick(); const during = starts();"
        " await release(); click(); await tick(); return [during, starts()];",
    )
    assert out == [1, 2]


@needs_node
def test_a_refused_start_can_be_tried_again(tmp_path):
    out = screen(
        tmp_path,
        "await show(loaded()); await drop('org/b'); const answer = respond;"
        ' respond = (path, opts) => path === "/api/server/start"'
        ' ? reply({ error: "busy" }, false) : answer(path, opts);'
        " await press('#serverStartBtn'); await press('#serverStartBtn');"
        " return sent('/api/server/start').length;",
    )
    assert out == 2


@needs_node
def test_a_refused_pair_shows_no_memory_line(tmp_path):
    # "12.4 / 7.0 GB" beside the refusal's own figures reads as a contradiction.
    out = screen(
        tmp_path,
        'estimate = { ...both, fits: false, refused: "No room." };'
        " await show(view()); await add('org/a'); const one = $('#serverMemoryLine').hidden;"
        " await add('org/b'); return [one, why(), $('#serverMemoryLine').hidden,"
        " text('#serverMemoryLine'), $('#serverMemoryHint').hidden];",
    )
    assert out == [False, "No room.", True, "", True]
