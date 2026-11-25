"""Row 573/582: the low UI findings V24, V26, V27, the Background box and the favicon."""

from __future__ import annotations

import re
import urllib.error
import urllib.request

import pytest
from test_ui_behaviour import HTML, needs_node, run

# Background box ------------------------------------------------------------- #
BOX = (
    "const box = document.querySelector('#serverBackground');"
    " const poll = (server) => render(snap({ server:"
    " { endpoint: 'e', lines: [], tool_support: {}, ...server } }));"
    " const up = (pid, background) => poll({ running: true, repo: 'org/custom', pid, background });"
    " const down = () => poll({ running: false, background: false });"
)


@needs_node
class TestBackgroundBox:
    @pytest.mark.parametrize("background", [True, False])
    def test_it_shows_the_mode_of_the_running_server(self, tmp_path, background):
        flag = str(background).lower()
        out = run(
            tmp_path,
            BOX
            + f" box.checked = {str(not background).lower()}; up(7, {flag}); return box.checked;",
        )
        assert out is background

    def test_polling_keeps_a_choice_made_while_the_server_runs(self, tmp_path):
        out = run(
            tmp_path, BOX + " up(7, true); box.checked = false; up(7, true); return box.checked;"
        )
        assert out is False

    def test_stopping_keeps_the_mode_for_the_next_start(self, tmp_path):
        out = run(tmp_path, BOX + " up(7, true); down(); down(); return box.checked;")
        assert out is True

    def test_a_new_server_in_another_mode_updates_it(self, tmp_path):
        out = run(
            tmp_path,
            BOX + " up(7, true); down(); up(8, false); return box.checked;",
        )
        assert out is False

    def test_a_stopped_server_leaves_the_users_choice_alone(self, tmp_path):
        out = run(tmp_path, BOX + " down(); box.checked = true; down(); return box.checked;")
        assert out is True


# V24 ------------------------------------------------------------------------ #
# The browser rules the stub lacks: a disabled or removed control loses focus,
# and a disabled one cannot take it.
def focus_dom(wrap: str, draw: str) -> str:
    return (
        f"{draw} const wrap = document.querySelector('{wrap}');"
        " const body = element('body'); document.activeElement = body;"
        " let markup = ''; Object.defineProperty(wrap, 'innerHTML', { get: () => markup,"
        " set(v) { markup = v; this._html = v; document.activeElement = body; } });"
        " wrap.contains = (n) => n === document.activeElement;"
        " const camelKey = (k) => k.replace(/-([a-z])/g, (_, c) => c.toUpperCase());"
        ' const off = (tag) => / disabled[ >=]/.test(tag.replace(/"[^"]*"/g, \'""\'));'
        " wrap.querySelector = (sel) => {"
        ' const want = [...sel.matchAll(/\\[data-([a-z-]+)="([^"]*)"\\]/g)];'
        " const tag = (markup.match(/<button\\b[^>]*>/g) || []).find((t) =>"
        ' want.length && want.every(([, k, v]) => t.includes(`data-${k}="${v}"`)));'
        " if (!tag) return null; return { focus() { if (off(tag)) return;"
        " document.activeElement = { tag, dataset: Object.fromEntries("
        '[...tag.matchAll(/ data-([a-z-]+)="([^"]*)"/g)].map(([, k, v]) => [camelKey(k), v])) };'
        " } }; };"
        # Focus the Download button the way a keyboard user would.
        " const focusDownload = () => {"
        " const b = wrap.querySelectorAll('[data-cat-download]')[0]; let dis = b.disabled;"
        " Object.defineProperty(b, 'disabled', { get: () => dis, set(v) { dis = v;"
        " if (v && document.activeElement === b) document.activeElement = body; } });"
        " document.activeElement = b; return b; };"
        " const focused = () => document.activeElement === body ? 'page'"
        " : document.activeElement.dataset.catLoad ? 'load'"
        " : document.activeElement.dataset.catDownload ? 'download' : 'other';"
        # The boot request never settles in the stub; let later refreshes run.
        " state.refreshing = null;"
        " const server = { downloads: [], models: [] };"
        " respond = (path) => reply(path === '/api/state' ? snap(server)"
        " : path === '/api/catalog' ? { catalog: state.catalog || [], hardware: {} } : {});"
    )


GRIDS = {
    "recommended": (
        "#catalogCards",
        "const draw = (rows) => { state.catalog = rows; renderCatalog(); };",
    ),
    "search": (
        "#searchResults",
        "const draw = (rows) => { state.search = rows; renderSearch(); };",
    ),
}
RUNNING = "{ repo: 'org/n', status: 'running', pct: 1 }"


@needs_node
@pytest.mark.parametrize("where", list(GRIDS))
class TestDownloadKeepsKeyboardFocus:
    def go(self, tmp_path, where, scenario):
        setup = focus_dom(*GRIDS[where])
        return run(
            tmp_path,
            setup
            + " draw([{ repo: 'org/n', fit: 'good' }]); const b = focusDownload();"
            + scenario,
        )

    def test_focus_stays_from_click_to_load(self, tmp_path, where):
        out = self.go(
            tmp_path,
            where,
            f" server.downloads = [{RUNNING}]; const click = b.listeners.click();"
            " const seen = [focused()]; await click; await refreshState(); seen.push(focused());"
            " server.downloads = []; server.models = [{ repo: 'org/n', complete: true }];"
            " await refreshState(); seen.push(focused()); return seen;",
        )
        assert out == ["download", "download", "load"]

    @pytest.mark.parametrize("ending", ["failed", "cancelled"])
    def test_focus_stays_when_the_download_ends_badly(self, tmp_path, where, ending):
        out = self.go(
            tmp_path,
            where,
            f" server.downloads = [{RUNNING}]; await b.listeners.click(); await refreshState();"
            f" server.downloads = [{{ repo: 'org/n', status: '{ending}' }}];"
            " await refreshState(); return [focused(), wrap.innerHTML.includes('aria-disabled')];",
        )
        assert out == ["download", False]

    def test_a_busy_button_does_not_start_a_second_download(self, tmp_path, where):
        out = self.go(
            tmp_path,
            where,
            f" server.downloads = [{RUNNING}]; b.listeners.click(); await b.listeners.click();"
            " await refreshState();"
            " const redrawn = wrap.querySelectorAll('[data-cat-download]')[0];"
            " await redrawn.listeners.click?.();"
            " return [redrawn.getAttribute('aria-disabled'),"
            " calls.filter((c) => c.path === '/api/download').length];",
        )
        assert out == ["true", 1]


# V26 ------------------------------------------------------------------------ #
@needs_node
@pytest.mark.parametrize("where", list(GRIDS))
class TestInstalledFollowsTheSnapshot:
    def card(self, tmp_path, where, models):
        wrap, draw = GRIDS[where]
        return run(
            tmp_path,
            f"{draw} state.snapshot = snap({{ models: {models} }});"
            " draw([{ repo: 'org/n', fit: 'good', installed: true }]);"
            f" return html('{wrap}');",
        )

    @pytest.mark.parametrize("models", ["[]", "[{ repo: 'org/n', complete: false }]"])
    def test_a_model_removed_outside_the_app_offers_download_not_load(
        self, tmp_path, where, models
    ):
        out = self.card(tmp_path, where, models)
        assert "data-cat-download" in out
        assert "data-cat-load" not in out and "Installed" not in out

    def test_a_model_still_on_disk_offers_load(self, tmp_path, where):
        out = self.card(tmp_path, where, "[{ repo: 'org/n', complete: true }]")
        assert "data-cat-load" in out and "Installed" in out


# V27 ------------------------------------------------------------------------ #
@needs_node
def test_download_toggle_name_never_contradicts_its_state(tmp_path):
    tag = re.search(r'<button[^>]*id="toggleDownloads"[^>]*>', HTML).group(0)
    label = re.search(r'aria-label="([^"]*)"', tag).group(1)
    states = run(
        tmp_path,
        "const btn = document.querySelector('#toggleDownloads');"
        " const view = () => [btn.attrs['aria-expanded'], btn.attrs['aria-label'] ?? null];"
        " setDownloadsOpen(true); const open = view(); setDownloadsOpen(false);"
        " return [open, view()];",
    )
    for expanded, renamed in states:
        name = (renamed or label).lower()
        # The state is announced by aria-expanded; the name only says what it is.
        assert "downloads" in name
        assert not re.search(r"\b(show|hide|expand|collapse)", name), (expanded, name)


# Favicon -------------------------------------------------------------------- #
def test_the_page_asks_for_no_missing_icon(base_url):
    with urllib.request.urlopen(f"{base_url}/", timeout=5) as page:
        html = page.read().decode()
    link = re.search(r'<link[^>]*rel="icon"[^>]*>', html)
    # With no icon link a browser asks for /favicon.ico by itself.
    href = re.search(r'href="([^"]*)"', link.group(0)).group(1) if link else "/favicon.ico"
    if href.startswith("data:"):
        return
    try:
        with urllib.request.urlopen(f"{base_url}/{href.lstrip('/')}", timeout=5) as icon:
            assert icon.status == 200
    except urllib.error.HTTPError as err:
        pytest.fail(f"{href} answered {err.code}")
