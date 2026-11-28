"""Real front-end behaviour: ui/app.js runs under node against a stub DOM."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

PRELUDE = """
const snap = (over = {}) => ({
  app: { version: "2.0.9", release: {} }, hardware: { chip: "M", ram: "32 GB" }, python: "3",
  versions: {}, loaded: {}, downloads: [], install: { lines: [] },
  server: { running: false, endpoint: "e", lines: [], tool_support: {} },
  models: [{ repo: "org/custom", path: "/c/custom", size_h: "4.2 GB", complete: true },
           { repo: "org/other", path: "/c/other", size_h: "1.0 GB", complete: true }],
  ...over,
});
const html = (s) => document.querySelector(s).innerHTML;
const reply = (body, ok = true) => Promise.resolve({ ok, statusText: "bad", json: async () => body });
"""


def run(tmp_path, scenario, **boot):
    path = tmp_path / "scenario.js"
    path.write_text(PRELUDE + scenario, encoding="utf-8")
    harness = str(ROOT / "tests" / "ui_harness.js")
    ui = tmp_path / "ui.js"  # the page's scripts, in its load order
    names = ("download.js", "features.js", "app.js", "projects.js")
    ui.write_text("\n".join((ROOT / "ui" / name).read_text() for name in names))
    proc = subprocess.run(
        [NODE, harness, str(ui), str(path), json.dumps(boot)],
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout)


@needs_node
class TestModelCards:
    def test_unknown_model_shows_its_real_size_and_no_blank_metadata(self, tmp_path):
        out = run(tmp_path, 'render(snap()); return html("#modelCards");')
        assert "4.2 GB" in out
        assert " ·  GB" not in out
        assert not re.search(r'class="card-sub">\s*(·|</div>)', out)

    def test_catalogue_fields_are_added_and_escaped(self, tmp_path):
        out = run(
            tmp_path,
            'state.catalog = [{ repo: "org/custom", params: "7B", pick: "Code",'
            ' use: "<b>x</b>", fit: "good", size_gb: 4 }];'
            ' render(snap()); return html("#modelCards");',
        )
        assert "4.2 GB · 7B · Code" in out
        assert "&lt;b&gt;x" in out and "good fit" in out

    def test_unchanged_polls_do_not_rewrite_the_dom(self, tmp_path):
        out = run(
            tmp_path,
            'state.catalog = [{ repo: "org/a", fit: "good", size_gb: 1, installed: false }];'
            " const s = snap({ downloads: [{ repo: 'org/d', status: 'running', pct: 5 }] });"
            " render(s); renderCatalog();"
            ' const ids = ["#modelCards", "#chatModelSelect", "#serverModelSelect",'
            ' "#loadState", "#serverState", "#catalogCards"];'
            " const card = document.querySelector('#downloadCards').children[0];"
            " const count = () => [...ids.map((i) => document.querySelector(i).writes),"
            " card.writes, document.querySelector('#serverLog').textWrites,"
            " document.querySelector('#globalStatus').textWrites];"
            " const before = count(); render(s); renderCatalog(); return [before, count()];",
        )
        assert out[0] == out[1]

    def test_server_choice_is_kept_while_the_server_runs(self, tmp_path):
        out = run(
            tmp_path,
            " const s = snap({ server: { running: true, repo: 'org/custom', lines: [] } });"
            " render(s); const first = serverSet(); editServerSet(['org/other']); render(s);"
            " return [first, serverSet()];",
        )
        assert out == [["org/custom"], ["org/other"]]

    def test_counts_match_the_cards_shown_while_downloading(self, tmp_path):
        out = run(
            tmp_path,
            " render(snap({ downloads: [{ repo: 'org/other', status: 'running', pct: 1 }] }));"
            " return [document.querySelector('#navModelCount').textContent,"
            " document.querySelector('#modelsSummary').textContent];",
        )
        assert out == ["1", "1 model(s)"]

    def test_incomplete_model_offers_resume_not_load(self, tmp_path):
        out = run(
            tmp_path,
            " render(snap({ models: [{ repo: 'org/half', path: '/h', size_h: '1 KB',"
            " complete: false }] }));"
            ' return [html("#modelCards"), html("#chatModelSelect")];',
        )
        assert "incomplete" in out[0] and 'data-act="download"' in out[0]
        assert 'data-act="load"' not in out[0]
        assert "org/half" not in out[1]

    def test_running_download_has_a_cancel_button(self, tmp_path):
        out = run(
            tmp_path,
            " render(snap({ downloads: [{ repo: 'org/d', status: 'running', pct: 5 }] }));"
            " return document.querySelector('#downloadCards').children[0].innerHTML;",
        )
        assert 'data-cancel="org/d"' in out

    def test_reveal_calls_the_app(self, tmp_path):
        out = run(
            tmp_path,
            " respond = () => reply({ ok: true });"
            " await modelAction('reveal', 'org/custom');"
            " const c = calls.find((x) => x.path === '/api/reveal');"
            " return [c.opts.body, c.opts.headers['X-MLX-Token']];",
        )
        assert out == ['{"repo":"org/custom"}', "test-token"]


@needs_node
class TestChatFailure:
    @pytest.mark.parametrize(
        "respond",
        [
            "() => reply({ error: 'busy' }, false)",
            "() => Promise.reject(new Error('offline'))",
            # stream reports an error, then ends
            "() => stream('event: error\\ndata: \"boom\"\\n\\n')",
            # stream ends without a done event
            "() => stream('event: token\\ndata: \"par\"\\n\\n')",
        ],
    )
    def test_failed_reply_removes_the_user_turn(self, tmp_path, respond):
        out = run(
            tmp_path,
            " const stream = (text) => { let sent = false; return Promise.resolve({ ok: true,"
            " body: { getReader: () => ({ read: async () => sent ? { done: true } :"
            " (sent = true, { done: false, value: new TextEncoder().encode(text) }) }) } }); };"
            " state.snapshot = snap({ loaded: { repo: 'org/custom' } });"
            f" const chat = {respond};"
            " respond = (p) => p === '/api/chat' ? chat() : new Promise(() => {});"
            " document.querySelector('#input').value = 'hi';"
            " await sendMessage(); return state.messages;",
        )
        assert out == []


@needs_node
class TestUpdateLink:
    @pytest.mark.parametrize(
        ("url", "opened"),
        [
            ("https://example.com/a.pkg", ["https://example.com/a.pkg"]),
            ("javascript:alert(1)", []),
            ("http://example.com/a.pkg", []),
        ],
    )
    def test_only_https_links_are_offered(self, tmp_path, url, opened):
        out = run(
            tmp_path,
            f" render(snap({{ app: {{ version: '1', update: true, latest: '2',"
            f" release: {{ url: {json.dumps(url)}, sha256: 'ab12' }} }} }}));"
            " const b = document.querySelector('#appUpdateDownload'); if (b.onclick) b.onclick();"
            " return [opened, document.querySelector('#appUpdatePanel').hidden,"
            " document.querySelector('#appUpdateSha').textContent];",
        )
        assert out[0] == opened
        assert out[1] is (not opened)
        if opened:
            assert out[2] == "ab12"


HTML = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
CSS = (ROOT / "ui" / "style.css").read_text(encoding="utf-8")


class TestAccessibleNames:
    def test_every_form_control_has_a_name(self):
        for tag in re.findall(r"<(?:select|textarea|input)\b[^>]*>", HTML):
            if 'type="checkbox"' in tag:
                continue
            control = re.search(r'id="([^"]+)"', tag).group(1)
            assert "aria-label=" in tag or f'for="{control}"' in HTML, control

    def test_live_regions_exist(self):
        for element_id in ("toast", "chatAnnounce"):
            tag = re.search(rf'<[^>]*id="{element_id}"[^>]*>', HTML)
            assert tag and 'role="status"' in tag.group(0), element_id

    def test_nav_marks_the_current_view_and_the_server_status_has_text(self):
        assert 'aria-current="page"' in HTML
        assert 'id="navServerText"' in HTML

    def test_download_toggle_is_named_and_reuses_shared_styles(self):
        tag = re.search(r'<button[^>]*id="toggleDownloads"[^>]*>', HTML).group(0)
        assert 'aria-label="' in tag and 'aria-controls="downloadCards downloadEmpty"' in tag
        assert "#toggleDownloads" not in CSS and "#downloadSection" not in CSS

    def test_narrow_window_keeps_nav_names(self):
        narrow = CSS[CSS.index("@media (max-width: 900px)") :]
        assert not re.search(r"\.nav-item span[^{]*\{\s*display:\s*none", narrow)

    @needs_node
    def test_view_change_and_server_state_are_announced(self, tmp_path):
        out = run(
            tmp_path,
            " const make = (v) => { const b = element(v); b.dataset.view = v; return b; };"
            " const navs = [make('chat'), make('models')];"
            " document.querySelectorAll = (s) => s === '.nav-item' ? navs : [];"
            " showView('models'); render(snap({ server: { running: true, lines: [] } }));"
            " return [navs.map((n) => n.attrs['aria-current'] || null),"
            " document.querySelector('#navServerText').textContent];",
        )
        assert out == [[None, "page"], "server running"]


def contrast(first, second):
    def luminance(colour):
        parts = [int(colour[i : i + 2], 16) / 255 for i in (1, 3, 5)]
        linear = [p / 12.92 if p <= 0.03928 else ((p + 0.055) / 1.055) ** 2.4 for p in parts]
        return 0.2126 * linear[0] + 0.7152 * linear[1] + 0.0722 * linear[2]

    light, dark = sorted((luminance(first), luminance(second)), reverse=True)
    return (light + 0.05) / (dark + 0.05)


def token(name):
    return re.search(rf"--{name}:\s*(#[0-9a-fA-F]{{6}})", CSS).group(1)


class TestContrast:
    @pytest.mark.parametrize("background", ["bg", "bg-2", "panel", "panel-2"])
    @pytest.mark.parametrize("text", ["text-2", "text-3"])
    def test_secondary_text(self, text, background):
        assert contrast(token(text), token(background)) >= 4.5

    @pytest.mark.parametrize("name", ["button", "button-hover"])
    def test_white_on_primary_button(self, name):
        assert contrast("#ffffff", token(name)) >= 4.5

    def test_primary_button_uses_the_tokens(self):
        assert re.search(r"\.btn-primary\s*\{[^}]*background:\s*var\(--button\)", CSS)
        assert re.search(r"\.btn-primary:hover\s*\{[^}]*background:\s*var\(--button-hover\)", CSS)


@needs_node
def test_every_api_request_from_the_ui_carries_the_token(tmp_path):
    out = run(
        tmp_path,
        " respond = () => Promise.resolve({ ok: false, status: 500, statusText: 'x',"
        " json: async () => ({}) });"
        " await exportDiagnostics(); await loadLogs(); await loadAppLog(); await loadLogFiles();"
        " await loadCatalog(); await doSearch('q');"
        " return calls.map((c) => [c.path, c.opts?.headers?.['X-MLX-Token'] || null]);",
    )
    # /api/state is the poll the page sends while it boots.
    assert {"/api/state", "/api/logs/export", "/api/catalog"} <= {path for path, _ in out}
    assert [path for path, token in out if token != "test-token"] == []


TOKEN = "Ab0_-" * 8 + "xyz"  # 43 characters, the shape token_urlsafe(32) makes

# What the page did while booting: what it sent, kept and left in the address bar.
SESSION = """
return {
  sent: calls.map((c) => c.opts.headers["X-MLX-Token"]),
  stored: stored.get("mlx-token") ?? null,
  replaced, hash: location.hash, intervals,
  toast: document.querySelector("#toast").textContent,
};
"""


@needs_node
class TestSessionToken:
    def test_the_fragment_token_is_used_kept_and_taken_out_of_the_url(self, tmp_path):
        out = run(tmp_path, SESSION, hash=f"#t={TOKEN}", storage={})
        assert out["sent"] and set(out["sent"]) == {TOKEN}
        assert out["stored"] == TOKEN
        assert out["replaced"] == ["/"]
        assert out["hash"].endswith("server") and TOKEN not in out["hash"]

    def test_a_new_fragment_replaces_the_token_of_an_earlier_launch(self, tmp_path):
        out = run(tmp_path, SESSION, hash=f"#t={TOKEN}", storage={"mlx-token": "old" * 12})
        assert set(out["sent"]) == {TOKEN} and out["stored"] == TOKEN

    def test_a_reload_takes_the_token_from_storage_and_keeps_the_view(self, tmp_path):
        out = run(tmp_path, SESSION, hash="#models", storage={"mlx-token": TOKEN})
        assert out["sent"] and set(out["sent"]) == {TOKEN}
        assert out["replaced"] == []
        assert out["hash"] == "#models"

    def test_with_storage_off_the_token_still_works_for_this_load(self, tmp_path):
        out = run(tmp_path, SESSION, hash=f"#t={TOKEN}", storageBroken=True)
        assert out["sent"] and set(out["sent"]) == {TOKEN}
        assert out["replaced"] == ["/"]

    @pytest.mark.parametrize("fragment", ["", "#t=short", f"#t={TOKEN}!", f"#x&t={TOKEN}"])
    def test_without_a_session_it_says_so_once_and_asks_for_nothing(self, tmp_path, fragment):
        out = run(tmp_path, SESSION, hash=fragment, storage={})
        assert out["stored"] is None
        assert out["sent"] == []
        assert out["intervals"] == []
        assert out["toast"] == "This page has no session. Open MLX Studio from its app icon."

    def test_with_a_session_the_page_polls(self, tmp_path):
        # Boot waits for the first state reply before it starts the timer.
        out = run(
            tmp_path,
            "await new Promise((r) => setTimeout(r, 20)); return intervals;",
            storage={"mlx-token": TOKEN},
            answered=True,
        )
        assert out == [2000]
