"""Findings V1 and V6 to V13 from the 2.0.9 re-review."""

from __future__ import annotations

import json
import re
import threading
import time
import urllib.error
import urllib.request

import pytest
from conftest import ROOT
from test_row656_app import cache_model
from test_ui_behaviour import needs_node, run

REPO = "org/model"


def post(server, app_module, path, body):
    url = f"http://127.0.0.1:{server.server_address[1]}{path}"
    request = urllib.request.Request(
        url,
        data=json.dumps(body).encode(),
        headers={
            "Content-Type": "application/json",
            app_module.TOKEN_HEADER: app_module.API_TOKEN,
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=5) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read())


def wait_for(check):
    deadline = time.monotonic() + 5
    while not check():
        assert time.monotonic() < deadline, "timed out"
        time.sleep(0.01)


# V1 ------------------------------------------------------------------------- #
CATALOG = (
    'state.catalog = [{ repo: "org/a", fit: "good", size_gb: 1, installed: false }];'
    " const wrap = document.querySelector('#catalogCards');"
    " const btn = element('btn'); btn.dataset.catDownload = 'org/a';"
    " wrap.querySelectorAll = (s) => s === '[data-cat-download]' ? [btn] : [];"
    " const buttonHtml = () => wrap.innerHTML.match(/<button[^>]*data-cat-download[^>]*>[^<]*</)[0];"
    " const dl = (status) => snap({ downloads: [{ repo: 'org/a', status, pct: 1 }] });"
    " const show = (s) => { state.snapshot = s; render(s); renderCatalog(); };"
)


@needs_node
class TestCatalogueDownloadButton:
    @pytest.mark.parametrize("ending", ["cancelled", "failed"])
    def test_button_returns_after_the_download_ends(self, tmp_path, ending):
        out = run(
            tmp_path,
            CATALOG
            + " respond = (path) => reply(path === '/api/catalog' ? { catalog: state.catalog, hardware: {} } : snap()); show(snap());"
            " await btn.listeners.click();"
            f" show(dl('running')); const during = buttonHtml(); show(dl('{ending}'));"
            " return [during, buttonHtml()];",
        )
        assert "disabled" in out[0] and "Downloading" in out[0]
        assert "disabled" not in out[1] and out[1].endswith(">Download<")

    def test_a_poll_alone_updates_the_button(self, tmp_path):
        out = run(
            tmp_path,
            CATALOG + " show(snap()); state.snapshot = dl('running'); render(state.snapshot);"
            " const during = buttonHtml(); state.snapshot = dl('cancelled');"
            " render(state.snapshot); return [during, buttonHtml()];",
        )
        assert "Downloading" in out[0]
        assert out[1].endswith(">Download<")

    def test_a_clicked_button_is_redrawn_even_if_no_download_was_ever_seen(self, tmp_path):
        out = run(
            tmp_path,
            CATALOG
            + " respond = (path) => reply(path === '/api/catalog' ? { catalog: state.catalog, hardware: {} } : snap()); show(snap()); const before = wrap.writes;"
            " await btn.listeners.click(); show(snap()); return [before, wrap.writes];",
        )
        assert out[1] > out[0]


# V6 ------------------------------------------------------------------------- #
FILES = [".gitattributes", "README.md", "config.json", "model.safetensors", "tokenizer.json"]
RUNTIME = ["config.json", "model.safetensors", "tokenizer.json"]


def cached(app_module, revisions, current, wanted=FILES, marker_revision="aaa"):
    root = app_module.CACHE_DIR / "models--org--model"
    for revision, names in revisions.items():
        folder = root / "snapshots" / revision
        folder.mkdir(parents=True)
        for name in names:
            (folder / name).write_text("{}", encoding="utf-8")
    (root / "refs").mkdir()
    (root / "refs" / "main").write_text(current, encoding="utf-8")
    (root / "blobs").mkdir()
    (root / "blobs" / "dead.incomplete").write_text("x", encoding="utf-8")
    (root / app_module.DOWNLOAD_MARKER).write_text(
        json.dumps({"revision": marker_revision, "files": wanted}), encoding="utf-8"
    )
    return root


class TestStaleDownloadMarker:
    def test_files_needed_to_run_are_enough(self, app_module):
        root = cached(app_module, {"aaa": RUNTIME}, "aaa")
        assert app_module._model_complete(root, REPO) is True

    @pytest.mark.parametrize("missing", RUNTIME)
    def test_a_missing_runtime_file_stays_incomplete(self, app_module, missing):
        root = cached(app_module, {"aaa": [n for n in FILES if n != missing]}, "aaa")
        assert app_module._model_complete(root, REPO) is False

    def test_a_newer_complete_revision_counts(self, app_module):
        root = cached(app_module, {"aaa": ["config.json"], "bbb": RUNTIME}, "bbb")
        assert app_module._model_complete(root, REPO) is True

    @pytest.mark.parametrize("names", [["config.json"], ["model.safetensors"]])
    def test_a_newer_partial_revision_stays_incomplete(self, app_module, names):
        root = cached(app_module, {"aaa": ["config.json"], "bbb": names}, "bbb")
        assert app_module._model_complete(root, REPO) is False

    def test_a_newer_revision_the_hub_reports_incomplete(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "_hub_reports_incomplete", lambda repo: True)
        root = cached(app_module, {"aaa": ["config.json"], "bbb": RUNTIME}, "bbb")
        assert app_module._model_complete(root, REPO) is False

    def test_other_weight_formats_still_need_every_file(self, app_module):
        wanted = ["config.json", "weights.npz"]
        root = cached(app_module, {"aaa": ["config.json"]}, "aaa", wanted=wanted)
        assert app_module._model_complete(root, REPO) is False


# V7 ------------------------------------------------------------------------- #
@needs_node
class TestNewChatDuringReply:
    def test_the_old_reply_does_not_enter_the_new_history(self, tmp_path):
        out = run(
            tmp_path,
            "let release; const gate = new Promise((r) => { release = r; });"
            " const frames = ['event: token\\ndata: \"hi\"\\n\\n',"
            ' \'event: done\\ndata: {"tokens":1,"tps":1,"peak_gb":1}\\n\\n\'];'
            " let i = 0; respond = (path) => path !== '/api/chat' ? reply(snap()) :"
            " Promise.resolve({ ok: true, body: { getReader: () => ({ read: async () => {"
            " await gate; return i < frames.length"
            " ? { done: false, value: new TextEncoder().encode(frames[i++]) }"
            " : { done: true }; } }) } });"
            " state.chatStatus = 'ready';"
            " document.querySelector('#input').value = 'q'; wire();"
            " const sending = sendMessage();"
            " document.querySelector('#newChatBtn').listeners.click();"
            " release(); await sending;"
            " return { messages: state.messages, paths: calls.map((c) => c.path) };",
        )
        assert out["messages"] == []
        assert "/api/chat/stop" in out["paths"]

    def test_new_chat_when_idle_sends_no_stop(self, tmp_path):
        out = run(
            tmp_path,
            "wire(); document.querySelector('#newChatBtn').listeners.click();"
            " return calls.map((c) => c.path);",
        )
        assert "/api/chat/stop" not in out


# V9 ------------------------------------------------------------------------- #
class TestStopBeatsQueuedStart:
    @pytest.fixture(autouse=True)
    def installed(self, app_module):
        cache_model(app_module, REPO)  # a start is accepted for installed models only

    def test_stop_cancels_a_start_that_has_not_launched(self, server, app_module, monkeypatch):
        launched = []
        monkeypatch.setattr(app_module, "_launch_server", lambda *a: launched.append(a))
        s = app_module.STATE
        held, release = threading.Event(), threading.Event()
        real = app_module.worker_server_start
        done = threading.Event()

        def queued(*args):
            held.set()
            release.wait(5)
            real(*args)
            done.set()

        monkeypatch.setattr(app_module, "worker_server_start", queued)
        assert post(server, app_module, "/api/server/start", {"repo": REPO})[0] == 200
        assert held.wait(5)
        assert post(server, app_module, "/api/server/stop", {})[0] == 200
        release.set()
        assert done.wait(5)
        assert launched == [] and s.server_pending == []

    def test_a_start_after_stop_still_launches(self, server, app_module, monkeypatch):
        launched = []
        monkeypatch.setattr(app_module, "_launch_server", lambda *a: launched.append(a))
        post(server, app_module, "/api/server/stop", {})
        post(server, app_module, "/api/server/start", {"repo": REPO})
        wait_for(lambda: launched)
        assert launched == [(REPO, False)]


# V10 ------------------------------------------------------------------------ #
class TestSingleInstall:
    def test_a_second_request_is_refused_before_the_worker_runs(
        self, server, app_module, monkeypatch
    ):
        started = []
        monkeypatch.setattr(app_module, "worker_install", started.append)
        assert post(server, app_module, "/api/install", {})[0] == 200
        assert post(server, app_module, "/api/install", {})[0] == 409
        wait_for(lambda: started)
        assert started == ["install"]

    def test_only_one_of_many_claims_wins(self, app_module):
        results = []
        threads = [
            threading.Thread(target=lambda: results.append(app_module.STATE.reserve_install("x")))
            for _ in range(20)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert results.count(True) == 1


# V11 and V12 ---------------------------------------------------------------- #
@needs_node
class TestAccessibility:
    def test_progress_bar_has_a_role_name_and_value(self, tmp_path):
        out = run(
            tmp_path,
            "render(snap({ downloads: [{ repo: 'org/d', status: 'running', pct: 42 }] }));"
            " return document.querySelector('#downloadCards').children[0].innerHTML;",
        )
        bar = re.search(r'<div class="progress-bar"[^>]*>', out).group(0)
        for part in (
            'role="progressbar"',
            'aria-valuemin="0"',
            'aria-valuemax="100"',
            'aria-valuenow="42"',
            'aria-label="Download progress for org/d"',
        ):
            assert part in bar


# V13 ------------------------------------------------------------------------ #
class TestHubPin:
    def test_setup_installs_the_pinned_hub(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "pkg_version", lambda name: "1.0.0")
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        pin = re.search(r"^huggingface-hub==(\S+)$", text, re.MULTILINE).group(1)
        assert f"huggingface-hub=={pin}" in app_module.install_specs()

    @pytest.mark.parametrize(("installed", "update"), [("1.0.0", True), ("999.0", False)])
    def test_an_older_hub_is_flagged(self, app_module, monkeypatch, installed, update):
        monkeypatch.setattr(app_module, "pkg_version", lambda name: installed)
        versions = app_module.STATE.snapshot()["versions"]
        assert versions["huggingface_hub_update"] is update
        assert versions["huggingface_hub_target"] == app_module.PINNED_PACKAGES["huggingface-hub"]

    @needs_node
    def test_setup_offers_the_hub_update(self, tmp_path):
        out = run(
            tmp_path,
            "render(snap({ versions: { huggingface_hub: '1.0.0', huggingface_hub_update: true,"
            " huggingface_hub_target: '2.4.0', mlx: '1', mlx_lm: '1', checked_at: 'now' } }));"
            " return [document.querySelector('#setHfPill').textContent,"
            " document.querySelector('#setupHint').textContent];",
        )
        assert "2.4.0" in out[0]
        assert "up to date" not in out[1]
