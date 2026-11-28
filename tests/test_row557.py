"""Search cards match the recommended cards, collapsed downloads, visible model search."""

from __future__ import annotations

import re
import sys
from types import ModuleType, SimpleNamespace

import pytest
from test_ui_behaviour import CSS, HTML, needs_node, run

try:
    import huggingface_hub
    from huggingface_hub.hf_api import ModelInfo
except ImportError:  # CI installs only pytest: stand in for what the search reads
    huggingface_hub = ModuleType("huggingface_hub")

    def ModelInfo(safetensors=None, **data):
        info = SimpleNamespace(**safetensors) if safetensors else None
        return SimpleNamespace(safetensors=info, **data)


MODEL_SELECTS = ["serverModelSelect", "configModelSelect", "ltModelSelect"]


def hit(repo, parameters=None, tags=(), pipeline_tag="text-generation"):
    """A search hit shaped like the hub library's parse of an expanded listing."""
    data = {"id": repo, "downloads": 7, "tags": list(tags), "pipeline_tag": pipeline_tag}
    if parameters:
        data["safetensors"] = {"parameters": parameters, "total": sum(parameters.values())}
    return ModelInfo(**data)


def search(app_module, monkeypatch, *hits, ram_gb=32, old_library=False):
    seen = []

    class Api:
        def __init__(self, token=None):
            pass

        def list_models(self, **kwargs):
            seen.append(kwargs)
            if old_library and "expand" in kwargs:
                raise TypeError("unexpected keyword argument 'expand'")
            return list(hits)

    monkeypatch.setitem(sys.modules, "huggingface_hub", huggingface_hub)
    monkeypatch.setattr(huggingface_hub, "HfApi", Api, raising=False)
    monkeypatch.setattr(app_module, "hardware", lambda: {"ram_bytes": ram_gb * 1024**3})
    return app_module.search_hf("q"), seen


class TestSearchData:
    def test_quantised_size_is_estimated_from_parameters_and_bits(self, app_module, monkeypatch):
        # Real figures for Qwen3-8B-4bit; its repository holds 4.62 GB.
        counts = {"U32": 8_190_427_136, "BF16": 308_224}
        results, seen = search(app_module, monkeypatch, hit("org/q", counts, tags=["mlx", "4-bit"]))
        assert results == [
            {
                "repo": "org/q",
                "downloads": 7,
                "params": "8.2B",
                "size_gb": 4.61,
                "size_estimated": True,
                "category": None,
                "use": "Task: text generation",
                "fit": "great",
            }
        ]
        assert {"safetensors", "tags", "pipeline_tag", "downloads"} <= set(seen[0]["expand"])

    def test_unquantised_size_comes_from_the_dtype_widths(self, app_module, monkeypatch):
        results, _ = search(app_module, monkeypatch, hit("org/f", {"BF16": 500_000_000}))
        assert (results[0]["params"], results[0]["size_gb"]) == ("500M", 1.0)

    @pytest.mark.parametrize(
        ("ram_gb", "fit"), [(32, "good"), (16, "tight"), (8, "too-large"), (0, "unknown")]
    )
    def test_fit_uses_this_macs_memory(self, app_module, monkeypatch, ram_gb, fit):
        results, _ = search(
            app_module, monkeypatch, hit("org/f", {"BF16": 5_000_000_000}), ram_gb=ram_gb
        )
        assert results[0]["fit"] == fit

    def test_missing_data_stays_unknown(self, app_module, monkeypatch):
        results, _ = search(app_module, monkeypatch, hit("org/bare", pipeline_tag=None))
        found = results[0]
        assert [found[k] for k in ("params", "size_gb", "category", "use")] == [None] * 4
        assert found["fit"] == "unknown" and found["size_estimated"] is False

    @pytest.mark.parametrize("counts", [{"U32": 1_000_000_000}, {"BF16": 1_000_000_000, "XYZ9": 5}])
    def test_no_size_is_guessed_without_the_bit_width(self, app_module, monkeypatch, counts):
        results, _ = search(app_module, monkeypatch, hit("org/q", counts))
        assert results[0]["size_gb"] is None and results[0]["fit"] == "unknown"
        assert results[0]["params"]

    def test_recommended_models_keep_their_measured_values(self, app_module, monkeypatch):
        known = app_module.MODEL_CATALOG[0]
        results, _ = search(
            app_module, monkeypatch, hit(known["repo"], {"U32": 9_000_000_000}, tags=["8-bit"])
        )
        for key in ("params", "size_gb", "category", "use"):
            assert results[0][key] == known[key]
        assert results[0]["size_estimated"] is False and results[0]["fit"] == "great"

    def test_an_older_hub_library_still_searches(self, app_module, monkeypatch):
        results, seen = search(app_module, monkeypatch, hit("org/a"), old_library=True)
        assert [r["repo"] for r in results] == ["org/a"] and "expand" not in seen[-1]


FOUND = (
    "{ repo: 'org/m', params: '8B', size_gb: 4.62, category: 'Quality', use: 'Chat', fit: 'good' }"
)


def searched(tmp_path, result, then='return html("#catalogCards");', models="[]"):
    return run(
        tmp_path,
        f" const s = snap({{ models: {models} }}); state.snapshot = s;"
        f" respond = () => reply({{ results: [{result}] }}); await doSearch('x'); {then}",
    )


@needs_node
class TestSearchCards:
    def test_same_markup_as_a_recommended_card(self, tmp_path):
        out = searched(
            tmp_path,
            FOUND,
            'const found = html("#catalogCards");'
            f" state.search = null; state.catalog = [{FOUND}]; renderCatalog();"
            ' return [found, html("#catalogCards")];',
        )
        assert out[0] == out[1]
        line = "8B params · bits not published · 4.62 GB · Quality"
        for text in ("good fit", line, "Chat", "Copy model name"):
            assert text in out[0]

    def test_unknown_fields_are_labelled_and_download_stays_available(self, tmp_path):
        out = searched(tmp_path, "{ repo: 'org/bare' }")
        assert "fit unknown" in out and "size not published" in out
        # The Hub was not asked, so nothing else is called unpublished.
        assert out.count("not published") == 1
        assert "undefined" not in out and "null" not in out
        assert re.search(r'data-cat-download="org/bare"\s*>Download<', out)

    def test_no_size_is_shown_as_an_estimate(self, tmp_path):
        out = searched(
            tmp_path,
            "{ repo: 'org/e', size_gb: 4.61, size_estimated: true,"
            " size_status: 'unchecked', fit: 'good' }",
        )
        assert "checking size" in out
        assert "4.61" not in out and "estimate" not in out

    def test_too_large_cannot_be_downloaded(self, tmp_path):
        out = searched(tmp_path, "{ repo: 'org/big', size_gb: 90, fit: 'too-large' }")
        assert re.search(r'data-cat-download="org/big" disabled>Needs more RAM<', out)

    def test_installed_models_say_installed(self, tmp_path):
        out = searched(tmp_path, "{ repo: 'org/custom' }", models="[{ repo: 'org/custom' }]")
        assert "Installed" in out and "data-cat-load" not in out

    def test_polls_update_the_buttons_and_leave_unchanged_cards_alone(self, tmp_path):
        out = searched(
            tmp_path,
            FOUND,
            "const w = document.querySelector('#catalogCards');"
            " const busy = { ...s, downloads: [{ repo: 'org/m', status: 'running', pct: 3 }] };"
            " const poll = (x) => { state.snapshot = x; render(x); };"
            " poll(busy); const a = w.innerHTML; const writes = w.writes; poll(busy);"
            " const same = w.writes === writes;"
            " poll({ ...s, models: [{ repo: 'org/m' }] }); const b = w.innerHTML;"
            " poll(s); return [a, same, b, w.innerHTML];",
        )
        assert "Downloading…" in out[0] and out[1] is True
        assert ">Installed<" in out[2] and "data-cat-download" not in out[2]
        assert ">Download<" in out[3]

    def test_an_older_slower_search_does_not_replace_a_newer_one(self, tmp_path):
        out = run(
            tmp_path,
            " let first; respond = () => new Promise((r) => { first = r; });"
            " const old = doSearch('a');"
            " respond = () => reply({ results: [{ repo: 'org/new' }] }); await doSearch('b');"
            " first({ ok: true, json: async () => ({ results: [{ repo: 'org/old' }] }) });"
            ' await old; render(snap()); return html("#catalogCards");',
        )
        assert "org/new" in out and "org/old" not in out

    def test_no_results_clears_earlier_cards_and_polls_keep_it_clear(self, tmp_path):
        out = searched(
            tmp_path,
            FOUND,
            "respond = () => reply({ results: [] }); await doSearch('z'); render(s);"
            " return [html('#catalogCards'),"
            " document.querySelector('#catalogEmpty').hidden];",
        )
        assert out == ["", False]


class TestDownloadsMarkup:
    def test_collapsed_by_default(self):
        toggle = re.search(r'<button[^>]*id="toggleDownloads"[^>]*>', HTML).group(0)
        assert 'aria-expanded="false"' in toggle
        for element_id in ("downloadCards", "downloadEmpty"):
            tag = re.search(rf'<div[^>]*id="{element_id}"[^>]*>', HTML).group(0)
            assert "collapsed" in tag, element_id
        assert 'id="toggleDownloadsIcon" aria-hidden="true">▶<' in HTML


SUMMARY = "document.querySelector('#downloadCount').textContent"
TOGGLE = (
    "const btn = document.querySelector('#toggleDownloads');"
    " const cards = document.querySelector('#downloadCards');"
    " const empty = document.querySelector('#downloadEmpty');"
    " const view = () => [cards.classList.contains('collapsed'),"
    " empty.classList.contains('collapsed'), btn.attrs['aria-expanded'],"
    " document.querySelector('#toggleDownloadsIcon').textContent];"
    " const poll = (pct) => render(snap({ downloads:"
    " [{ repo: 'org/d', status: 'running', pct }] }));"
)


MIXED = (
    "[{ repo: 'o/a', status: 'running', pct: 20 },"
    " { repo: 'o/b', status: 'running', pct: 41 },"
    " { repo: 'o/c', status: 'success' }, { repo: 'o/d', status: 'failed' },"
    " { repo: 'o/e', status: 'cancelled' }]"
)


@needs_node
class TestDownloadsSection:
    @pytest.mark.parametrize(
        ("downloads", "text"),
        [
            ("[]", "0 downloads"),
            ("[{ repo: 'o/a', status: 'running', pct: 45 }]", "1 download · 1 running, 45%"),
            (
                MIXED,
                "5 downloads · 2 running, average 31% · 1 complete · 1 failed · 1 cancelled",
            ),
        ],
    )
    def test_header_summarises_count_and_progress(self, tmp_path, downloads, text):
        out = run(tmp_path, f" render(snap({{ downloads: {downloads} }})); return {SUMMARY};")
        assert out == text

    def test_the_users_choice_survives_polls(self, tmp_path):
        out = run(
            tmp_path,
            TOGGLE + " poll(1); const a = view(); btn.listeners.click(); poll(2); poll(3);"
            " const b = view(); btn.listeners.click(); poll(4); render(snap());"
            " return [a, b, view()];",
        )
        # The stub starts without the markup's classes, so a poll must not open or close.
        assert out[1] == [False, False, "true", "▼"]
        assert out[2] == [True, True, "false", "▶"]


PICK = """
const q = (s) => document.querySelector(s);
const sel = q('#testSelect'); const box = q('#testSelectSearch');
const list = q('#testSelectList'); const clear = q('#testSelectClear');
const chosen = q('#testSelectChosen'); const status = q('#testSelectStatus');
list.hidden = true; createPicker('#testSelect');
const paint = (x) => fillSelect('#testSelect', x.models.map((m) => m.repo), null);
const s = snap(); sel.value = 'org/custom'; paint(s);
const type = (text) => { box.value = text; box.listeners.input(); };
const enter = () => box.listeners.keydown({ key: 'Enter', preventDefault() {} });
"""


@needs_node
class TestSearchField:
    def test_the_box_is_empty_and_the_choice_is_shown_beside_it(self, tmp_path):
        out = run(
            tmp_path,
            PICK + "return [box.value, box.placeholder, chosen.textContent, clear.hidden];",
        )
        assert out == ["", "Search models…", "Selected: org/custom", True]

    def test_choosing_updates_the_selected_line_and_announces_it(self, tmp_path):
        out = run(
            tmp_path,
            PICK + "type('other'); const shown = !clear.hidden; enter(); paint(s); paint(s);"
            " return [shown, sel.value, box.value, clear.hidden, chosen.textContent,"
            " status.textContent, list.hidden];",
        )
        assert out == [
            True,
            "org/other",
            "",
            True,
            "Selected: org/other",
            "Selected org/other",
            True,
        ]

    def test_clear_empties_the_search_and_keeps_the_choice(self, tmp_path):
        out = run(
            tmp_path,
            PICK + "type('other'); let kept = false;"
            " clear.listeners.mousedown({ preventDefault: () => { kept = true; } });"
            " clear.listeners.click(); paint(s);"
            " return [kept, box.value, clear.hidden, list.hidden, list.innerHTML, sel.value];",
        )
        assert out[:4] == [True, "", True, False]
        assert "org/custom" in out[4] and "org/other" in out[4]
        assert out[5] == "org/custom"

    def test_without_models_the_field_says_so(self, tmp_path):
        out = run(
            tmp_path,
            PICK + "sel.value = ''; paint(snap({ models: [] }));"
            " return [box.placeholder, box.readOnly, chosen.textContent, clear.hidden];",
        )
        assert out == ["No models installed", True, "Selected: none", True]


class TestSearchFieldMarkup:
    @pytest.mark.parametrize("select", MODEL_SELECTS)
    def test_icon_placeholder_clear_button_and_description(self, select):
        field = re.search(
            rf'<div class="search-field">(?:(?!</div>).)*id="{select}Search".*?</div>', HTML, re.S
        ).group(0)
        assert re.search(r'<svg class="search-icon"[^>]*aria-hidden="true"', field)
        box = re.search(rf'<input[^>]*id="{select}Search"[^>]*>', field).group(0)
        assert 'placeholder="Search models…"' in box
        assert re.search(r'aria-label="Search [^"]*model', box)
        # The API Server's picker adds to a list: it keeps no chosen model to describe.
        keeps_choice = select != "serverModelSelect"
        assert (f'aria-describedby="{select}Chosen"' in box) is keeps_choice
        clear = re.search(rf'<button[^>]*id="{select}Clear"[^>]*>', field).group(0)
        for attribute in ('type="button"', 'aria-label="Clear model search"', "hidden"):
            assert attribute in clear
        assert (f'id="{select}Chosen"' in HTML) is keeps_choice

    def test_the_open_list_leaves_the_selected_line_visible_and_badges_stay_whole(self):
        assert re.search(
            r"\.search-field ~ \.picker-list\s*\{[^}]*top:\s*calc\(100% \+ 18px\)", CSS
        )
        assert re.search(r"\.fit \{[^}]*white-space:\s*nowrap", CSS)

    def test_the_field_is_styled(self):
        assert re.search(r"\.search-icon\s*\{", CSS)
        assert re.search(r"\.search-clear\[hidden\]\s*\{[^}]*display:\s*none", CSS)
