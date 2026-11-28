"""Every button does its job: ui/app.js runs under node and the test clicks it."""

from __future__ import annotations

import pytest
from test_ui_behaviour import needs_node, run

# Record requests and toasts; `fail` makes every request return that error.
CLICKS = """
const el = (s) => document.querySelector(s);
let fail = null;
respond = (path) => {
  if (fail) return reply({ error: fail }, false);
  if (path === '/api/catalog') return reply({ catalog: state.catalog || [], hardware: {} });
  if (path === '/api/search?q=' || path.startsWith('/api/search')) return reply({ results: [] });
  if (path === '/api/logs') return reply({ entries: [] });
  if (path === '/api/logs/files') return reply({ dir: '/logs', files: [] });
  if (path.startsWith('/api/logs/app')) return reply({ entries: [] });
  return reply(snap());
};
calls.length = 0;
const posts = () => calls.filter((c) => c.opts?.method === 'POST')
  .map((c) => [c.path, JSON.parse(c.opts.body)]);
const paths = () => calls.map((c) => c.path);
const toasted = () => [el('#toast').textContent, el('#toast').className];
const tick = () => new Promise((r) => setTimeout(r, 0));
const click = async (s) => { await el(s).listeners.click({}); await tick(); };
const press = async (button) => { await button.listeners.click(); await tick(); };
const find = (s, sel, key, value) =>
  el(s).querySelectorAll(sel).find((b) => b.dataset[key] === value);
"""


def go(tmp_path, scenario):
    return run(tmp_path, CLICKS + scenario)


MODELS = (
    "const draw = (complete = true) => render(snap({"
    " models: [{ repo: 'org/a&b', path: '/a', size_h: '1 KB', complete }] }));"
    " const act = (name) => find('#modelCards', '[data-act]', 'act', name);"
)


@needs_node
class TestModelCardButtons:
    @pytest.mark.parametrize("act", ["reveal", "delete"])
    def test_button_posts_its_action_for_that_model(self, tmp_path, act):
        out = go(tmp_path, MODELS + f" draw(); await press(act('{act}')); return posts();")
        assert out == [[f"/api/{act}", {"repo": "org/a&b"}]]

    def test_refusing_the_confirmation_deletes_nothing(self, tmp_path):
        out = go(
            tmp_path,
            MODELS + " let asked; confirm = (q) => { asked = q; return false; };"
            " draw(); await press(act('delete')); return [asked, paths()];",
        )
        assert out == ["Delete org/a&b from disk?", []]

    def test_delete_confirms_to_the_user(self, tmp_path):
        out = go(tmp_path, MODELS + " draw(); await press(act('delete')); return toasted();")
        assert out == ["Model deleted", "show"]

    def test_resume_downloads_and_opens_the_download_view(self, tmp_path):
        out = go(
            tmp_path,
            MODELS + " draw(false); await press(act('download')); return [posts(), location.hash];",
        )
        assert out == [[["/api/download", {"repo": "org/a&b"}]], "download"]

    def test_a_refused_action_shows_the_app_error(self, tmp_path):
        out = go(
            tmp_path,
            MODELS + " draw(); fail = 'model is busy'; await press(act('delete'));"
            " return toasted();",
        )
        assert out == ["model is busy", "show bad"]


OFFERS = {
    "recommended": (
        "#catalogCards",
        "const draw = (rows) => { state.catalog = rows; renderCatalog(); };",
    ),
    "search": (
        "#searchResults",
        "const draw = (rows) => { state.search = rows; renderSearch(); };",
    ),
}


@needs_node
@pytest.mark.parametrize("where", list(OFFERS))
class TestOfferCardButtons:
    def offers(self, tmp_path, where, scenario):
        wrap, draw = OFFERS[where]
        setup = (
            f"{draw} const dl = () => find('{wrap}', '[data-cat-download]', 'catDownload', 'org/n');"
            f" const ld = () => find('{wrap}', '[data-cat-load]', 'catLoad', 'org/n');"
        )
        return go(tmp_path, setup + scenario)

    def test_download_starts_and_opens_the_download_view(self, tmp_path, where):
        out = self.offers(
            tmp_path,
            where,
            " draw([{ repo: 'org/n', fit: 'good' }]); const b = dl(); await press(b);"
            " return [posts(), location.hash, b.getAttribute('aria-disabled'), b.textContent];",
        )
        assert out == [[["/api/download", {"repo": "org/n"}]], "download", "true", "Downloading…"]

    def test_a_refused_download_gives_the_button_back(self, tmp_path, where):
        out = self.offers(
            tmp_path,
            where,
            " draw([{ repo: 'org/n', fit: 'good' }]); const b = dl(); fail = 'disk full';"
            " await press(b);"
            " return [b.disabled, b.getAttribute('aria-disabled'), b.textContent, toasted()];",
        )
        assert out == [False, None, "Download", ["disk full", "show bad"]]

    def test_too_large_model_cannot_be_downloaded(self, tmp_path, where):
        out = self.offers(
            tmp_path, where, " draw([{ repo: 'org/n', fit: 'too-large' }]); return dl().disabled;"
        )
        assert out is True


CARD = "const card = () => el('#downloadCards').children[0];"


@needs_node
class TestDownloadCards:
    def test_running_download_shows_its_progress_in_the_download_section(self, tmp_path):
        out = go(
            tmp_path,
            CARD + " render(snap({ downloads: [{ repo: 'org/d', status: 'running', pct: 37 }] }));"
            " return [el('#downloadCards').hidden, el('#downloadEmpty').hidden,"
            " card().innerHTML];",
        )
        assert out[:2] == [False, True]
        assert 'role="progressbar"' in out[2] and 'aria-valuenow="37"' in out[2]
        assert 'class="progress-fill" style="width:37%"' in out[2]

    def test_finished_download_shows_where_it_was_saved(self, tmp_path):
        out = go(
            tmp_path,
            CARD + " const s = snap({ downloads: [{ repo: 'org/custom', status: 'success' }] });"
            " state.snapshot = s; render(s); return card().innerHTML;",
        )
        assert 'class="card-path" title="/c/custom">📁 /c/custom<' in out
        assert "data-cancel" not in out and "progressbar" not in out

    def test_cancel_stops_that_download(self, tmp_path):
        out = go(
            tmp_path,
            CARD + " render(snap({ downloads: [{ repo: 'org/d', status: 'running' }] }));"
            " await press(card().querySelectorAll('[data-cancel]')[0]); return posts();",
        )
        assert out == [["/api/download/cancel", {"repo": "org/d"}]]

    def test_a_failed_cancel_shows_the_error(self, tmp_path):
        out = go(
            tmp_path,
            CARD + " render(snap({ downloads: [{ repo: 'org/d', status: 'running' }] }));"
            " fail = 'already done';"
            " await press(card().querySelectorAll('[data-cancel]')[0]); return toasted();",
        )
        assert out == ["already done", "show bad"]


@needs_node
class TestToolbarButtons:
    @pytest.mark.parametrize(
        ("button", "path"),
        [
            ("#stopBtn", "/api/chat/stop"),
            ("#serverStopBtn", "/api/server/stop"),
            ("#serverNoticeDismiss", "/api/server/notice/clear"),
            ("#clearLogsBtn", "/api/logs/clear"),
        ],
    )
    def test_button_posts_its_action(self, tmp_path, button, path):
        out = go(tmp_path, f"await click('{button}'); return posts();")
        assert out == [[path, {}]]

    @pytest.mark.parametrize("button", ["#serverStopBtn", "#serverNoticeDismiss", "#clearLogsBtn"])
    def test_a_refusal_is_reported(self, tmp_path, button):
        out = go(tmp_path, f"fail = 'not now'; await click('{button}'); return toasted();")
        assert out == ["not now", "show bad"]

    def test_keeping_the_log_sends_nothing(self, tmp_path):
        out = go(
            tmp_path,
            "confirm = () => false; await click('#clearLogsBtn'); return paths();",
        )
        assert out == []

    @pytest.mark.parametrize(
        ("button", "message"),
        [
            ("#serverStartBtn", "No model chosen. Press + Add model, then Start."),
            ("#serverRestartBtn", "No model chosen. Press + Add model, then Start."),
        ],
    )
    def test_no_model_chosen(self, tmp_path, button, message):
        out = go(tmp_path, f"await click('{button}'); return [paths(), toasted()];")
        assert out == [[], [message, "show bad"]]

    @pytest.mark.parametrize("button", ["#serverStartBtn", "#serverRestartBtn"])
    @pytest.mark.parametrize("background", [True, False])
    def test_server_start_sends_model_and_mode(self, tmp_path, button, background):
        flag = "true" if background else "false"
        # Start shows the check panel first; Restart does not.
        confirm = " await click('#confirmStartBtn');" if button == "#serverStartBtn" else ""
        out = go(
            tmp_path,
            f"state.serverDraft = ['org/x']; el('#serverBackground').checked = {flag};"
            f" await click('{button}');{confirm} return posts();",
        )
        assert out == [["/api/server/start", {"repo": "org/x", "background": background}]]

    def test_restart_falls_back_to_the_running_model(self, tmp_path):
        out = go(
            tmp_path,
            "state.snapshot = snap({ server: { repo: 'org/run' } });"
            " await click('#serverRestartBtn'); return posts()[0][1].repo;",
        )
        assert out == "org/run"

    @pytest.mark.parametrize(("missing", "kind"), [("true", "install"), ("false", "update")])
    def test_install_button_installs_or_updates(self, tmp_path, missing, kind):
        out = go(
            tmp_path,
            f"state.snapshot = snap({{ versions: {{ mlx_missing: {missing} }} }});"
            " await click('#installBtn'); return posts();",
        )
        assert out == [["/api/install", {"kind": kind}]]

    def test_check_updates_asks_for_versions_and_confirms(self, tmp_path):
        out = go(
            tmp_path,
            "await click('#checkUpdatesBtn'); return [paths()[0], toasted()[0]];",
        )
        assert out == ["/api/versions", "Version check complete"]

    @pytest.mark.parametrize(
        ("button", "source"), [("#copyEndpoint", "#endpointText"), ("#copyLogDir", "#logDirPath")]
    )
    def test_copy_buttons_copy_the_shown_text(self, tmp_path, button, source):
        out = go(
            tmp_path,
            "let got; navigator.clipboard.writeText = async (t) => { got = t; };"
            f" el('{source}').textContent = 'shown'; await click('{button}');"
            " const ok = toasted()[1]; navigator.clipboard.writeText = async () => { throw 1; };"
            f" await click('{button}'); return [got, ok, toasted()];",
        )
        assert out == ["shown", "show", ["Copy failed", "show bad"]]

    @pytest.mark.parametrize(
        ("target", "event", "path"),
        [
            ("#refreshLogsBtn", "click", "/api/logs"),
            ("#logFilter", "change", "/api/logs"),
            ("#appLogLevel", "change", "/api/logs/app?limit=300"),
            ("#refreshLogFiles", "click", "/api/logs/files"),
        ],
    )
    def test_log_controls_reload_the_logs(self, tmp_path, target, event, path):
        out = go(
            tmp_path,
            f"await el('{target}').listeners.{event}({{}}); await tick(); return paths();",
        )
        assert path in out

    @pytest.mark.parametrize(
        ("act", "query"),
        [
            ("await click('#searchBtn')", "qwen%203"),
            ("el('#searchInput').listeners.keydown({ key: 'Enter' }); await tick()", "qwen%203"),
            ("await click('#showAllBtn')", ""),
        ],
    )
    def test_search_controls(self, tmp_path, act, query):
        out = go(
            tmp_path,
            f"el('#searchInput').value = '  qwen 3 '; {act}; return paths();",
        )
        assert out == [f"/api/search?q={query}"]

    @pytest.mark.parametrize(("shift", "sent"), [("false", ["/api/chat"]), ("true", [])])
    def test_enter_sends_and_shift_enter_does_not(self, tmp_path, shift, sent):
        out = go(
            tmp_path,
            "state.chatStatus = 'ready'; el('#input').value = 'hi';"
            f" el('#input').listeners.keydown({{ key: 'Enter', shiftKey: {shift},"
            " preventDefault() {} }); await tick();"
            " return paths().filter((p) => p === '/api/chat');",
        )
        assert out == sent


@needs_node
class TestEveryRequestCarriesTheToken:
    def test_actions_chat_and_export(self, tmp_path):
        out = go(
            tmp_path,
            "state.chatStatus = 'ready';"
            " await click('#serverStopBtn'); el('#input').value = 'hi'; await click('#sendBtn');"
            " await click('#exportLogsBtn');"
            " return calls.map((c) => [c.path, c.opts.headers['X-MLX-Token']]);",
        )
        assert {"/api/server/stop", "/api/chat", "/api/logs/export"} <= {path for path, _ in out}
        assert {token for _, token in out} == {"test-token"}
