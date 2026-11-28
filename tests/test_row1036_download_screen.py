"""The combined Download Model screen: its routes, its page and its script."""

from __future__ import annotations

import contextlib
import importlib.util
import json
import re
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import portguard
import pytest
from test_ui_behaviour import NODE, PRELUDE, needs_node

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


# ---- routes ----------------------------------------------------------------------
CATALOG = [
    {
        "repo": "mlx-community/pick-small",
        "params": "1B",
        "size_gb": 1.0,
        "category": "Fast",
        "use": "Quick answers",
    }
]


def fake_app():
    return SimpleNamespace(
        MODEL_CATALOG=CATALOG,
        hardware=lambda: {"ram_bytes": 32 * 10**9, "ram": "32 GB", "chip": "Test"},
        fit_rating=lambda size, ram: "great" if size <= 8 else "too-large",
        installed_models=lambda: [
            {"repo": "mlx-community/pick-small", "complete": True},
            {"repo": "mlx-community/half", "complete": False},
        ],
    )


def hub_listing(**_):
    return [SimpleNamespace(id="mlx-community/half", downloads=5)]


def hub_info(**_):
    return SimpleNamespace(
        siblings=[SimpleNamespace(rfilename="model.safetensors", size=2_000_000_000)]
    )


@pytest.fixture
def routes():
    import catalog_routes

    return catalog_routes.Routes(fake_app(), list_models=hub_listing, repo_info=hub_info)


class TestRoutes:
    def test_catalog_returns_cards_and_hardware(self, routes):
        body, code = routes.get("/api/catalog", {})
        assert code == 200 and body["hardware"]["chip"] == "Test"
        (card,) = body["catalog"]
        assert card["pick_tag"] == "Best for Mac" and card["installed"] is True
        assert card["url"] == "https://huggingface.co/mlx-community/pick-small"

    def test_search_is_not_wrapped_twice_and_marks_only_complete_installs(self, routes):
        body, code = routes.get("/api/search", {"q": ["half"]})
        assert code == 200 and body["error"] is None
        assert [(c["repo"], c["installed"]) for c in body["results"]] == [
            ("mlx-community/half", False)
        ]

    def test_search_failure_keeps_the_picks_and_says_so(self):
        import catalog_routes

        def broken(**_):
            raise OSError("secret-detail")

        body, code = catalog_routes.Routes(fake_app(), list_models=broken).get("/api/search", {})
        assert code == 200 and "Hugging Face" in body["error"]
        assert "secret-detail" not in json.dumps(body)
        assert [c["repo"] for c in body["results"]] == ["mlx-community/pick-small"]

    def test_model_size(self, routes):
        body, code = routes.get("/api/model-size", {"repo": ["mlx-community/half"]})
        assert code == 200
        assert (body["size_status"], body["size_gb"], body["fit"]) == ("ok", 2.0, "great")

    @pytest.mark.parametrize("repo", [None, "", "half", "other/model", "mlx-community/../x"])
    def test_model_size_refuses_bad_ids(self, routes, repo):
        query = {} if repo is None else {"repo": [repo]}
        assert routes.get("/api/model-size", query) == ({"error": "unknown model"}, 400)

    def test_other_paths_are_left_to_the_app(self, routes):
        assert routes.get("/api/state", {}) is None
        assert routes.get("/api/catalog/x", {}) is None


# ---- the real request handler, with the app.py edits -----------------------------
@pytest.fixture
def wired(app_module, tmp_path):
    import catalog_routes

    path = tmp_path / "app_wired.py"
    path.write_text(read(ROOT / "app.py"), encoding="utf-8")
    spec = importlib.util.spec_from_file_location("mlxapp_wired", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    httpd = None
    try:
        spec.loader.exec_module(module)
        module.DOWNLOAD_ROUTES = catalog_routes.Routes(
            module, list_models=hub_listing, repo_info=hub_info
        )
        httpd = module.LocalServer(("127.0.0.1", 0), module.Handler)
        httpd.daemon_threads = True
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        yield module, f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        if httpd:
            httpd.shutdown()
            httpd.server_close()
        with contextlib.suppress(AttributeError):
            portguard.shut_down(module)
        sys.modules.pop(spec.name, None)


def get(url: str, headers: dict):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, None


ROUTES = ["/api/catalog", "/api/search?q=x", "/api/model-size?repo=mlx-community/half"]


class TestWiredServer:
    @pytest.mark.parametrize("route", ROUTES)
    def test_token_host_and_origin_are_checked_first(self, wired, route):
        module, base = wired
        token = {"X-MLX-Token": module.API_TOKEN}
        assert get(base + route, token)[0] == 200
        assert get(base + route, {})[0] == 403
        assert get(base + route, {**token, "Host": "evil.example"})[0] == 403
        assert get(base + route, {**token, "Origin": "https://evil.example"})[0] == 403

    def test_the_new_files_are_served(self, app_module):
        assert {"download.js", "download.css"} <= set(app_module.STATIC_FILES)


# ---- the screen, under node ------------------------------------------------------
SCREEN = """
const card = (over = {}) => {
  const repo = over.repo || 'mlx-community/a';
  return { repo, url: `https://huggingface.co/${repo}`, params: null, bits: null,
    size_gb: null, size_status: 'unchecked', fit: 'unknown', category: 'Text', chips: [],
    pick: null, pick_tag: null, use: null, base_model: null, base_model_url: null,
    license: null, library: null, downloads: null, likes: null, updated: null,
    gated: null, installed: false, ...over };
};
const pick = (over = {}) => card({ repo: 'mlx-community/pick', category: null, pick: 'Fast',
  pick_tag: 'Best for Mac', size_status: 'ok', size_gb: 2, fit: 'great', ...over });
const $q = (s) => document.querySelector(s);
const cards = () => html('#catalogCards');
const repos = () => [...cards().matchAll(/data-copy="([^"]*)"/g)].map((m) => m[1]);
const tick = () => new Promise((r) => setTimeout(r, 0));
const filterBy = (value) => { $q('#catalogFilter').value = value; renderCatalog(); };
const show = (...list) => { state.catalog = list; renderCatalog(); return cards(); };
// Only the repos a scenario scrolls to count as on screen.
let seen = () => {};
globalThis.IntersectionObserver = class {
  constructor(callback) {
    const watched = [];
    this.observe = (el) => watched.push(el);
    this.disconnect = () => { watched.length = 0; };
    seen = (...names) => callback(watched
      .map((el) => ({ target: el, isIntersecting: names.includes(el.dataset.repo) })));
  }
};
"""


def run(tmp_path, scenario):
    script = tmp_path / "screen.js"
    script.write_text(read(UI / "download.js") + "\n" + read(UI / "app.js"), encoding="utf-8")
    path = tmp_path / "scenario.js"
    path.write_text(PRELUDE + SCREEN + scenario, encoding="utf-8")
    proc = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui_harness.js"), str(script), str(path), "{}"],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
class TestCards:
    def test_every_published_value_is_text(self, tmp_path):
        out = run(
            tmp_path,
            " const bad = '\"><img src=x onerror=1>';"
            " return show(card({ repo: bad, url: bad, params: bad, bits: bad, size_status: 'ok',"
            " size_gb: bad, fit: bad, category: bad, chips: [bad], pick: bad, pick_tag: bad,"
            " use: bad, base_model: bad, base_model_url: bad, license: bad, library: bad,"
            " updated: bad }));",
        )
        assert "<img" not in out
        assert 'class="fit fit-unknown"' in out and "href=" not in out

    def test_links_open_outside_with_no_opener(self, tmp_path):
        out = run(
            tmp_path,
            " return show(card({ base_model: 'org/base',"
            " base_model_url: 'https://huggingface.co/org/base' }));",
        )
        links = re.findall(r"<a [^>]*>", out)
        assert len(links) == 2
        for link, repo in zip(links, ("org/base", "mlx-community/a"), strict=True):
            assert f'href="https://huggingface.co/{repo}"' in link
            assert 'target="_blank"' in link and 'rel="noopener noreferrer"' in link
        assert "Open on Hugging Face" in out and "Base model: org/base" in out

    @pytest.mark.parametrize(
        "url",
        [
            "null",
            "'javascript:alert(1)'",
            "'https://evil.example/a/b'",
            "'https://huggingface.co.evil.example/a/b'",
            "'https://huggingface.co/a/b?x=1'",
        ],
    )
    def test_no_link_unless_it_is_a_model_page(self, tmp_path, url):
        out = run(tmp_path, f" return show(card({{ url: {url}, base_model_url: {url} }}));")
        assert "<a " not in out

    def test_missing_values_say_not_published(self, tmp_path):
        out = run(tmp_path, " return show(card({ category: 'Not published' }));")
        for words in ("parameters", "bits", "task", "license"):
            assert f"{words} not published" in out
        assert "Base model not published" in out

    def test_nothing_is_claimed_when_the_hub_was_not_asked(self, tmp_path):
        out = run(tmp_path, " return show(pick({ params: '1B', use: 'Quick' }));")
        assert "not published" not in out
        assert "1B params" in out and "2 GB" in out and "Quick" in out

    def test_published_details_and_tags(self, tmp_path):
        out = run(
            tmp_path,
            " return show(card({ params: '7B', bits: 4, category: 'Vision', chips: ['Chat'],"
            " license: 'mit', library: 'mlx', downloads: 12345, likes: 7,"
            " updated: '2026-01-02', gated: true, pick: 'Code', pick_tag: 'Recommended, tight' }));",
        )
        for words in ("7B params", "4-bit", "Vision", ">Chat<", "license mit", "mlx", "Gated"):
            assert words in out
        for words in ("12,345 downloads", "7 likes", "updated 2026-01-02"):
            assert words in out
        assert "Pick: Code" in out and "Recommended, tight" in out

    @pytest.mark.parametrize(
        "status, words",
        [
            ("unchecked", "checking size"),
            ("not_published", "size not published"),
            ("failed", "could not check size"),
        ],
    )
    def test_size_text_and_download_stays_enabled(self, tmp_path, status, words):
        out = run(tmp_path, f" return show(card({{ size_status: '{status}' }}));")
        assert words in out and not re.search(r"data-cat-download[^>]*\sdisabled", out)

    def test_too_large_installed_and_running(self, tmp_path):
        out = run(
            tmp_path,
            " state.catalog = [card({ repo: 'o/big', fit: 'too-large' }),"
            " card({ repo: 'o/have', installed: true }), card({ repo: 'o/run' })];"
            " renderCatalog(); const before = cards();"
            " render(snap({ models: [], downloads: [{ repo: 'o/run', status: 'running' }] }));"
            " return [before, cards()];",
        )
        before, after = out
        assert "Needs more RAM" in before and ">Installed<" in before
        assert "Downloading…" in after and ">Installed<" not in after

    def test_download_posts_the_repo(self, tmp_path):
        out = run(
            tmp_path,
            " respond = () => reply({ ok: true, catalog: [], hardware: {} });"
            " show(card()); const [b] = $q('#catalogCards').querySelectorAll('[data-cat-download]');"
            " await b.listeners.click(); return calls.filter((c) => c.path === '/api/download')"
            " .map((c) => c.opts.body);",
        )
        assert [json.loads(body) for body in out] == [{"repo": "mlx-community/a"}]

    def test_installed_cards_keep_the_pick_label(self, tmp_path):
        out = run(
            tmp_path,
            " state.catalog = [pick({ repo: 'org/custom' })]; render(snap());"
            ' return html("#modelCards");',
        )
        assert "Fast" in out


@needs_node
class TestList:
    def test_picks_first_then_one_merged_list_then_back(self, tmp_path):
        out = run(
            tmp_path,
            " show(pick()); const first = [repos(), $q('#picksBtn').hidden,"
            " $q('#showAllBtn').hidden];"
            " respond = () => reply({ results: [pick(), card()], error: null });"
            " await doSearch('a b');"
            " const found = [repos(), $q('#picksBtn').hidden, $q('#showAllBtn').hidden,"
            " calls.at(-1).path];"
            " $q('#picksBtn').listeners.click();"
            " return [first, found, repos(), $q('#searchInput').value];",
        )
        first, found, back, box = out
        assert first == [["mlx-community/pick"], True, False]
        assert found == [
            ["mlx-community/pick", "mlx-community/a"],
            False,
            True,
            "/api/search?q=a%20b",
        ]
        assert back == ["mlx-community/pick"] and box == ""

    def test_focus_does_not_stay_on_a_button_that_hides(self, tmp_path):
        out = run(
            tmp_path,
            " respond = () => reply({ results: [card()] }); $q('#showAllBtn').focus();"
            " await doSearch(''); return document.activeElement === $q('#picksBtn');",
        )
        assert out is True

    def test_server_error_is_shown_with_the_picks(self, tmp_path):
        out = run(
            tmp_path,
            " respond = () => reply({ results: [pick()], error: 'Could not reach it.' });"
            " await doSearch('x');"
            " return [repos(), $q('#catalogNotice').textContent, $q('#catalogEmpty').hidden];",
        )
        assert out == [["mlx-community/pick"], "Could not reach it.", True]

    def test_error_with_no_match_is_not_called_no_results(self, tmp_path):
        out = run(
            tmp_path,
            " respond = () => reply({ results: [], error: 'Could not reach it.' });"
            " await doSearch('x');"
            " return $q('#catalogEmpty').querySelector('h2').textContent;",
        )
        assert out == "No recommended model matches"

    def test_failed_request_falls_back_to_the_picks(self, tmp_path):
        out = run(
            tmp_path,
            " show(pick()); respond = () => reply({ error: 'busy' }, false); await doSearch('x');"
            " return [repos(), $q('#catalogNotice').textContent];",
        )
        assert out[0] == ["mlx-community/pick"] and out[1].startswith("Search failed")

    def test_no_results(self, tmp_path):
        out = run(
            tmp_path,
            " respond = () => reply({ results: [], error: null }); await doSearch('x');"
            " return [$q('#catalogEmpty').hidden, cards(),"
            " $q('#catalogEmpty').querySelector('h2').textContent];",
        )
        assert out == [False, "", "No results"]

    def test_only_the_newest_search_draws(self, tmp_path):
        out = run(
            tmp_path,
            " const waiting = []; respond = () => new Promise((r) => waiting.push(r));"
            " const old = doSearch('old'); const now = doSearch('new'); const busy = cards();"
            " const answer = async (repo) => ({ ok: true, json: async () =>"
            " ({ results: [card({ repo })] }) });"
            " waiting[1](await answer('o/new')); await now;"
            " waiting[0](await answer('o/old')); await old; return [busy, repos()];",
        )
        assert "Searching" in out[0] and out[1] == ["o/new"]

    @pytest.mark.parametrize(
        "value, wanted",
        [
            ("all", ["o/pick", "o/tight", "o/code", "o/eye"]),
            ("best", ["o/pick"]),
            ("great", ["o/pick"]),
            ("tight", ["o/tight"]),
            ("Code", ["o/tight", "o/code"]),
            ("Reasoning", ["o/code"]),
            ("Vision", ["o/eye"]),
        ],
    )
    def test_filters_apply_to_search_hits(self, tmp_path, value, wanted):
        out = run(
            tmp_path,
            " respond = () => reply({ results: [pick({ repo: 'o/pick' }),"
            " pick({ repo: 'o/tight', pick: 'Code', pick_tag: 'Recommended, tight',"
            " fit: 'tight' }),"
            " card({ repo: 'o/code', chips: ['Code', 'Reasoning'] }),"
            " card({ repo: 'o/eye', category: 'Vision' })] });"
            f" await doSearch('x'); filterBy('{value}'); return repos();",
        )
        assert out == wanted

    def test_fit_filter_says_how_many_are_not_checked_yet(self, tmp_path):
        out = run(
            tmp_path,
            " show(pick(), card()); filterBy('great');"
            " const fit = $q('#catalogNotice').textContent; filterBy('all');"
            " return [fit, $q('#catalogNotice').textContent];",
        )
        assert out[0].startswith("1 model without a checked size is hidden")
        assert out[1] == ""


@needs_node
class TestSizes:
    SETUP = (
        " const asked = []; const waiting = [];"
        " respond = (path) => {"
        " if (path.startsWith('/api/search')) return reply({ results: names.map((repo) =>"
        " card({ repo })) });"
        " asked.push(decodeURIComponent(path.split('repo=')[1]));"
        " return new Promise((r) => waiting.push(r)); };"
        " const size = async (body, ok = true) => { waiting.shift()("
        " { ok, statusText: 'bad', json: async () => body }); await tick(); await tick(); };"
    )

    def test_only_cards_on_screen_are_asked_and_the_answer_is_merged(self, tmp_path):
        out = run(
            tmp_path,
            " const names = ['mlx-community/a', 'mlx-community/b'];"
            + self.SETUP
            + " await doSearch('x'); const none = asked.length; seen(names[1]); seen(names[1]);"
            " await size({ size_status: 'ok', size_gb: 40, fit: 'too-large' });"
            " seen(names[1]); return [none, asked, cards()];",
        )
        none, asked, drawn = out
        assert none == 0 and asked == ["mlx-community/b"]
        assert "40 GB" in drawn and "Needs more RAM" in drawn and "checking size" in drawn

    def test_at_most_four_at_a_time(self, tmp_path):
        out = run(
            tmp_path,
            " const names = [...'abcdef'].map((n) => `o/${n}`);"
            + self.SETUP
            + " await doSearch('x'); seen(...names); const first = asked.length;"
            " await size({ size_status: 'not_published', size_gb: null, fit: 'unknown' });"
            " return [first, asked.length];",
        )
        assert out == [4, 5]

    def test_a_new_search_drops_the_waiting_checks(self, tmp_path):
        out = run(
            tmp_path,
            " let names = [...'abcdef'].map((n) => `o/${n}`);"
            + self.SETUP
            + " await doSearch('x'); seen(...names); names = ['o/z']; await doSearch('z');"
            " seen('o/z'); await size({ size_status: 'ok', size_gb: 1, fit: 'great' });"
            " return asked;",
        )
        assert out == ["o/a", "o/b", "o/c", "o/d", "o/z"]

    def test_a_failure_is_shown_and_asked_again_only_after_a_new_search(self, tmp_path):
        out = run(
            tmp_path,
            " const names = ['o/a'];"
            + self.SETUP
            + " await doSearch('x'); seen('o/a'); await size({ error: 'x' }, false);"
            " const failed = cards(); seen('o/a'); const once = asked.length;"
            " await doSearch('x'); seen('o/a'); return [failed, once, asked.length];",
        )
        assert "could not check size" in out[0] and out[1:] == [1, 2]

    def test_without_an_observer_the_cards_are_still_checked(self, tmp_path):
        out = run(
            tmp_path,
            " globalThis.IntersectionObserver = undefined; const names = ['o/a'];"
            + self.SETUP
            + " await doSearch('x'); return asked;",
        )
        assert out == ["o/a"]


class TestPage:
    def test_one_list_with_labelled_controls(self):
        page = read(UI / "index.html")
        for gone in ('id="searchResults"', 'id="searchEmpty"'):
            assert gone not in page
        # History has a "Show all" of its own, so only the Download screen is checked.
        start = page.index("<!-- ══════════ Download")
        assert ">Show all<" not in page[start : page.index("<!-- ══════════ Server")]
        for one in (
            "catalogCards",
            "catalogFilter",
            "catalogEmpty",
            "catalogNotice",
            "catalogHardware",
            "searchInput",
            "searchBtn",
            "showAllBtn",
            "picksBtn",
            "downloadCards",
            "toggleDownloads",
        ):
            assert page.count(f'id="{one}"') == 1, one
        assert re.search(r'id="catalogNotice"[^>]*role="status"', page)
        assert 'aria-label="Filter models"' in page and "More from Hugging Face" in page
        assert 'value="best"' in page
        assert page.index('src="/download.js"') < page.index('src="/app.js"')

    def test_styles_follow_both_themes(self):
        css = read(UI / "download.css")
        assert not re.search(r"#[0-9a-fA-F]{3,8}\b|rgba?\(|hsl", css)
        assert ".dl-link:focus-visible" in css

    def test_script_builds_no_code_from_text(self):
        script = read(UI / "download.js")
        assert not re.search(r"\beval\(|new Function|insertAdjacentHTML|window\.open", script)

    def test_the_old_screen_code_is_gone_after_wiring(self):
        script = read(UI / "app.js")
        for name in ("loadCatalog", "renderCatalog", "offerCard", "renderOfferCards", "doSearch"):
            assert f"function {name}(" not in script
        assert "renderSearch" not in script
