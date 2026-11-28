"""Copy model name on every card, and type-to-filter model dropdowns."""

from __future__ import annotations

import re

import pytest
from test_ui_behaviour import CSS, HTML, needs_node, run

MODEL_SELECTS = ["serverModelSelect", "configModelSelect", "ltModelSelect"]
COPY = 'data-copy="{}"'

# Picker helpers: render, then drive the search box like a user would.
PICKER = """
const $s = document.querySelector('#testSelect');
const box = document.querySelector('#testSelectSearch');
const list = document.querySelector('#testSelectList');
const status = document.querySelector('#testSelectStatus');
const models = (...names) => names.map((repo) => ({ repo, path: '/c', size_h: '1 KB' }));
const three = snap({ models: models('org/Qwen3-4bit', 'org/Llama-8bit', 'org/Qwen3-8bit') });
const key = (k, extra = {}) => {
  const e = { key: k, prevented: false, preventDefault() { this.prevented = true; }, ...extra };
  box.listeners.keydown(e);
  return e;
};
const type = (text) => { box.value = text; box.listeners.input(); };
const options = () => [...list.innerHTML.matchAll(/data-value="([^"]*)"/g)].map((m) => m[1]);
const isOpen = () => !list.hidden;
list.hidden = true; createPicker('#testSelect');
const paint = (x) => fillSelect('#testSelect', x.models.map((m) => m.repo), null);
paint(three);
"""


def pick(tmp_path, scenario):
    return run(tmp_path, PICKER + scenario)


@needs_node
class TestCopyButtons:
    def test_installed_cards_including_incomplete_ones(self, tmp_path):
        out = run(
            tmp_path,
            " render(snap({ models: [{ repo: 'org/full', path: '/f', size_h: '1 KB' },"
            " { repo: 'org/half', path: '/h', size_h: '1 KB', complete: false }] }));"
            ' return html("#modelCards");',
        )
        assert COPY.format("org/full") in out and COPY.format("org/half") in out
        assert 'aria-label="Copy model name org/full"' in out

    def test_catalogue_cards_installed_or_not(self, tmp_path):
        out = run(
            tmp_path,
            " state.catalog = [{ repo: 'org/a', fit: 'good', installed: false },"
            " { repo: 'org/b', fit: 'too-large', installed: false },"
            " { repo: 'org/c', fit: 'good', installed: true }];"
            ' renderCatalog(); return html("#catalogCards");',
        )
        for repo in ("org/a", "org/b", "org/c"):
            assert COPY.format(repo) in out

    @pytest.mark.parametrize("status", ["running", "success", "failed", "cancelled"])
    def test_download_cards_in_every_status(self, tmp_path, status):
        out = run(
            tmp_path,
            f" render(snap({{ downloads: [{{ repo: 'org/d', status: '{status}' }}] }}));"
            " return document.querySelector('#downloadCards').children[0].innerHTML;",
        )
        assert COPY.format("org/d") in out

    def test_search_result_cards(self, tmp_path):
        out = run(
            tmp_path,
            " respond = () => reply({ results: [{ repo: 'org/found' }] });"
            " await doSearch('x'); return html(\"#searchResults\");",
        )
        assert COPY.format("org/found") in out

    def test_repo_id_is_escaped(self, tmp_path):
        out = run(tmp_path, "return copyButton('a\"><img>');")
        assert "<img>" not in out


@needs_node
class TestCopyAction:
    def test_copies_the_full_repo_id_and_confirms(self, tmp_path):
        out = run(
            tmp_path,
            " const written = []; navigator.clipboard.writeText = async (t) => written.push(t);"
            " const b = element('btn'); b.dataset.copy = 'mlx-community/Qwen3-0.6B-4bit';"
            " b.textContent = 'Copy model name';"
            " await document.listeners.click({ target: { closest: () => b } });"
            " return [written, document.querySelector('#toast').textContent, b.textContent];",
        )
        assert out[0] == ["mlx-community/Qwen3-0.6B-4bit"]
        assert "Copied mlx-community/Qwen3-0.6B-4bit" in out[1]
        assert out[2].startswith("Copied")

    @pytest.mark.parametrize(
        "clipboard",
        ["undefined", "{ writeText: async () => { throw new Error('denied'); } }"],
    )
    def test_falls_back_when_the_clipboard_api_is_unavailable(self, tmp_path, clipboard):
        out = run(
            tmp_path,
            f" navigator.clipboard = {clipboard}; let copied = null;"
            " document.execCommand = (c) => { const a = document.body.children.at(-1);"
            " copied = [c, a.value, a.selected]; return true; };"
            " const ok = await copyText('org/x');"
            " return [ok, copied, document.body.children.at(-1).removed];",
        )
        assert out == [True, ["copy", "org/x", True], True]

    def test_failure_is_reported(self, tmp_path):
        out = run(
            tmp_path,
            " navigator.clipboard = undefined; document.execCommand = () => false;"
            " const b = element('btn'); b.dataset.copy = 'org/x';"
            " await document.listeners.click({ target: { closest: () => b } });"
            " const t = document.querySelector('#toast'); return [t.textContent, t.className];",
        )
        assert "Copy failed" in out[0] and "bad" in out[1]

    def test_focus_stays_on_a_card_button_when_the_card_is_redrawn(self, tmp_path):
        out = run(
            tmp_path,
            " const wrap = element('wrap'); const old = element('old'); old.dataset.copy = 'org/d';"
            " document.activeElement = old; wrap.contains = (n) => n === old;"
            " let focused = null; wrap.querySelector = (s) => ({ focus: () => { focused = s; } });"
            " setHTML(wrap, '<b>new</b>'); return focused;",
        )
        assert out == '[data-copy="org/d"]'


@needs_node
class TestModelSearch:
    @pytest.mark.parametrize("select", MODEL_SELECTS)
    def test_typing_filters_by_name(self, tmp_path, select):
        out = run(
            tmp_path,
            " render(snap({ models: [{ repo: 'org/Qwen3-4bit' }, { repo: 'org/Llama' }] }));"
            f" const box = document.querySelector('#{select}Search');"
            " box.value = 'QWEN'; box.listeners.input();"
            f" const list = document.querySelector('#{select}List');"
            " return [list.hidden, list.innerHTML, box.attrs['aria-expanded']];",
        )
        assert out[0] is False and out[2] == "true"
        assert "org/Qwen3-4bit" in out[1] and "org/Llama" not in out[1]

    def test_every_word_must_match(self, tmp_path):
        assert pick(tmp_path, "type('qwen 8bit'); return options();") == ["org/Qwen3-8bit"]

    def test_opening_shows_all_models_and_marks_the_chosen_one(self, tmp_path):
        out = pick(
            tmp_path,
            "$s.value = 'org/Llama-8bit'; box.listeners.click();"
            " return [options().length, list.innerHTML, box.attrs['aria-activedescendant']];",
        )
        assert out[0] == 3
        assert re.search(
            r'id="testSelect-opt-1"[^>]*aria-selected="true"[^>]*data-value="org/Llama-8bit"',
            out[1],
        )
        assert out[2] == "testSelect-opt-1"

    def test_enter_picks_the_highlighted_model(self, tmp_path):
        out = pick(
            tmp_path,
            "type('8bit'); key('ArrowDown'); key('Enter');"
            " return [$s.value, box.value, isOpen(), box.attrs['aria-expanded']];",
        )
        assert out == ["org/Qwen3-8bit", "", False, "false"]

    def test_arrows_open_and_wrap(self, tmp_path):
        out = pick(
            tmp_path,
            "$s.value = 'org/Qwen3-4bit'; key('ArrowDown'); const a = isOpen();"
            " key('ArrowUp'); key('Enter'); return [a, $s.value];",
        )
        assert out == [True, "org/Qwen3-8bit"]

    @pytest.mark.parametrize("leave", ["key('Escape')", "box.listeners.blur()", "key('Tab')"])
    def test_leaving_keeps_the_previous_choice(self, tmp_path, leave):
        out = pick(
            tmp_path,
            f"$s.value = 'org/Llama-8bit'; type('qwen'); {leave};"
            " return [$s.value, box.value, isOpen()];",
        )
        assert out == ["org/Llama-8bit", "", False]

    def test_clicking_an_option_picks_it(self, tmp_path):
        out = pick(
            tmp_path,
            "box.listeners.click(); let kept = false;"
            " const target = { closest: () => ({ dataset: { value: 'org/Llama-8bit' } }) };"
            " list.listeners.mousedown({ preventDefault: () => { kept = true; } });"
            " list.listeners.click({ target }); return [$s.value, box.value, isOpen(), kept];",
        )
        assert out == ["org/Llama-8bit", "", False, True]

    def test_no_match_is_announced_and_enter_does_nothing(self, tmp_path):
        out = pick(
            tmp_path,
            "const before = $s.value; type('zzz'); key('Enter');"
            " return [options(), list.innerHTML, status.textContent, $s.value === before];",
        )
        assert out[0] == [] and "No models match" in out[1]
        assert out[2] == "No models match" and out[3] is True

    def test_match_count_is_announced(self, tmp_path):
        assert pick(tmp_path, "type('qwen'); return status.textContent;") == "2 models match"

    def test_polling_keeps_typed_text_and_highlight(self, tmp_path):
        out = pick(
            tmp_path,
            "type('qwen'); key('ArrowDown'); const writes = list.writes; paint(three);"
            " const same = list.writes === writes;"
            " paint(snap({ models: models('org/Aaa', 'org/Qwen3-4bit', 'org/Qwen3-8bit') }));"
            " const out = [same, box.value, isOpen(), options()]; key('Enter');"
            " return [...out, $s.value];",
        )
        assert out == [
            True,
            "qwen",
            True,
            ["org/Qwen3-4bit", "org/Qwen3-8bit"],
            "org/Qwen3-8bit",
        ]

    def test_polling_keeps_the_chosen_model(self, tmp_path):
        out = pick(
            tmp_path,
            "type('llama'); key('Enter'); paint(three); paint(three);"
            " return [$s.value, document.querySelector('#testSelectChosen').textContent];",
        )
        assert out == ["org/Llama-8bit", "Selected: org/Llama-8bit"]

    def test_no_installed_models(self, tmp_path):
        out = pick(
            tmp_path,
            "paint(snap({ models: [] })); box.listeners.click();"
            " return [box.value, box.placeholder, box.readOnly, isOpen(), $s.value];",
        )
        assert out == ["", "No models installed", True, False, ""]

    def test_picking_a_server_model_updates_the_tools_warning(self, tmp_path):
        out = run(
            tmp_path,
            " const s = snap({ server: { running: false, endpoint: 'e', lines: [],"
            " tool_support: { 'org/other': false } } });"
            " state.snapshot = s; render(s);"
            " const warn = document.querySelector('#serverToolsWarn'); const a = warn.hidden;"
            " const box = document.querySelector('#serverModelSelectSearch');"
            " box.value = 'other'; box.listeners.input();"
            " box.listeners.keydown({ key: 'Enter', preventDefault() {} });"
            " return [a, warn.hidden];",
        )
        assert out == [True, False]


class TestMarkup:
    @pytest.mark.parametrize("select", MODEL_SELECTS)
    def test_search_box_is_a_labelled_combobox_over_a_hidden_select(self, select):
        box = re.search(rf'<input[^>]*id="{select}Search"[^>]*>', HTML).group(0)
        for attribute in (
            'role="combobox"',
            'aria-expanded="false"',
            'aria-autocomplete="list"',
            f'aria-controls="{select}List"',
        ):
            assert attribute in box
        assert "aria-label=" in box or f'for="{select}Search"' in HTML
        assert re.search(rf'<select[^>]*id="{select}"[^>]*\shidden', HTML)
        listbox = re.search(rf'<ul[^>]*id="{select}List"[^>]*>', HTML).group(0)
        assert 'role="listbox"' in listbox and "aria-label=" in listbox
        status = re.search(rf'<[^>]*id="{select}Status"[^>]*>', HTML).group(0)
        assert 'role="status"' in status

    def test_every_model_select_has_a_search_box(self):
        found = re.findall(r'<select[^>]*id="(\w*[Mm]odel\w*)"', HTML)
        # Chat lists at most the two served models: a plain select, no search box.
        assert sorted(found) == sorted(["chatModelSelect", *MODEL_SELECTS])

    def test_list_scrolls_and_highlight_is_styled(self):
        assert re.search(r"\.picker-list\s*\{[^}]*overflow-y:\s*auto", CSS)
        assert '.picker-list li[aria-selected="true"]' in CSS
