"""Downloads: progress reporting, cache location and model discovery.

Regression cover for three 2.0.7 defects:

1. ``worker_download`` wrote to ``~/.cache/huggingface`` while
   ``installed_models()`` scans ``~/.cache/huggingface/hub``, so a downloaded
   model never appeared in the Models list.
2. Progress jumped from 0 to 100 with no intermediate reporting.
3. The Download view rendered its cards below a 25-card catalogue, so an active
   download was never on screen.
"""

from __future__ import annotations

import json
import sys
import threading
import types
from pathlib import Path
from typing import ClassVar

import pytest


class FakeSibling:
    def __init__(self, name):
        self.rfilename = name


class FakeRepoInfo:
    def __init__(self, files):
        self.siblings = [FakeSibling(f) for f in files]


class FakeHfApi:
    """Stand-in for huggingface_hub.HfApi that records calls."""

    calls: ClassVar[list] = []

    def __init__(self, token=None):
        pass

    def repo_info(self, repo_id, repo_type=None, **kwargs):
        FakeHfApi.calls.append(("repo_info", repo_id, repo_type))
        return FakeRepoInfo(["config.json", "model.safetensors", "tokenizer.json"])


def install_fake_hf(monkeypatch, download_impl=None):
    """Install a fake huggingface_hub module for the duration of a test."""
    calls = []

    def fake_download(repo_id, filename, cache_dir, **kwargs):
        calls.append({"repo_id": repo_id, "filename": filename, "cache_dir": cache_dir})
        if download_impl:
            download_impl(repo_id, filename, cache_dir)

    module = types.ModuleType("huggingface_hub")
    module.HfApi = FakeHfApi
    module.hf_hub_download = fake_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    FakeHfApi.calls = []
    return calls


class TestCacheLocation:
    def test_download_uses_cache_dir_constant(self, app_module, monkeypatch):
        """The regression: cache_dir must be CACHE_DIR, not its parent."""
        calls = install_fake_hf(monkeypatch)
        app_module.worker_download("org/model")

        assert calls, "hf_hub_download was never called"
        for call in calls:
            assert call["cache_dir"] == str(app_module.CACHE_DIR)
            assert call["cache_dir"].endswith("/hub")

    def test_downloaded_model_appears_in_installed_models(self, app_module, monkeypatch):
        """End to end: a download creates the directory the scanner reads."""

        def write_files(repo_id, filename, cache_dir):
            target = Path(cache_dir) / f"models--{repo_id.replace('/', '--')}" / "snapshots"
            target.mkdir(parents=True, exist_ok=True)
            (target / filename).write_text("x", encoding="utf-8")

        install_fake_hf(monkeypatch, write_files)
        app_module.worker_download("org/model")

        repos = [m["repo"] for m in app_module.installed_models()]
        assert "org/model" in repos

    def test_installed_model_reports_a_path(self, app_module, monkeypatch):
        def write_files(repo_id, filename, cache_dir):
            target = Path(cache_dir) / f"models--{repo_id.replace('/', '--')}" / "snapshots"
            target.mkdir(parents=True, exist_ok=True)
            (target / filename).write_text("x", encoding="utf-8")

        install_fake_hf(monkeypatch, write_files)
        app_module.worker_download("org/model")

        entry = next(m for m in app_module.installed_models() if m["repo"] == "org/model")
        assert entry["path"].startswith(str(app_module.CACHE_DIR))
        assert entry["size"] > 0
        assert entry["size_h"]


class TestProgressReporting:
    def test_progress_reaches_every_file(self, app_module, monkeypatch):
        seen = []

        def record(repo_id, filename, cache_dir):
            seen.append(app_module.STATE.downloads[repo_id]["pct"])

        install_fake_hf(monkeypatch, record)
        app_module.worker_download("org/model")

        # Three files: the percentage observed before each file completes.
        assert seen == [0, 33, 66]

    def test_progress_ends_at_100(self, app_module, monkeypatch):
        install_fake_hf(monkeypatch)
        app_module.worker_download("org/model")
        assert app_module.STATE.downloads["org/model"]["pct"] == 100

    def test_status_transitions_running_then_success(self, app_module, monkeypatch):
        statuses = []

        def record(repo_id, filename, cache_dir):
            statuses.append(app_module.STATE.downloads[repo_id]["status"])

        install_fake_hf(monkeypatch, record)
        app_module.worker_download("org/model")

        assert set(statuses) == {"running"}
        assert app_module.STATE.downloads["org/model"]["status"] == "success"

    def test_detail_names_the_current_file(self, app_module, monkeypatch):
        details = []

        def record(repo_id, filename, cache_dir):
            details.append(app_module.STATE.downloads[repo_id]["detail"])

        install_fake_hf(monkeypatch, record)
        app_module.worker_download("org/model")

        assert any("config.json" in d for d in details)
        assert any("model.safetensors" in d for d in details)

    def test_pct_is_monotonic(self, app_module, monkeypatch):
        seen = []

        def record(repo_id, filename, cache_dir):
            seen.append(app_module.STATE.downloads[repo_id]["pct"])

        install_fake_hf(monkeypatch, record)
        app_module.worker_download("org/model")

        assert seen == sorted(seen)


class TestFailureHandling:
    def test_download_failure_is_recorded(self, app_module, monkeypatch):
        def boom(repo_id, filename, cache_dir):
            raise OSError("network down")

        install_fake_hf(monkeypatch, boom)
        app_module.worker_download("org/model")

        entry = app_module.STATE.downloads["org/model"]
        assert entry["status"] == "failed"
        assert "network down" in entry["detail"]
        assert entry["pct"] == 0

    def test_failure_is_logged_to_app_log(self, app_module, monkeypatch):
        def boom(repo_id, filename, cache_dir):
            raise OSError("network down")

        install_fake_hf(monkeypatch, boom)
        app_module.worker_download("org/model")

        messages = [e["message"] for e in app_module.read_app_log()]
        assert "download failed" in messages

    def test_empty_repository_fails_cleanly(self, app_module, monkeypatch):
        module = types.ModuleType("huggingface_hub")

        class EmptyApi:
            def __init__(self, token=None):
                pass

            def repo_info(self, repo_id, repo_type=None, **kwargs):
                return FakeRepoInfo([])

        module.HfApi = EmptyApi
        module.hf_hub_download = lambda **kw: pytest.fail("should not download")
        monkeypatch.setitem(sys.modules, "huggingface_hub", module)

        app_module.worker_download("org/empty")

        entry = app_module.STATE.downloads["org/empty"]
        assert entry["status"] == "failed"
        assert "No files" in entry["detail"]

    def test_failure_leaves_no_partial_success(self, app_module, monkeypatch):
        def boom(repo_id, filename, cache_dir):
            raise OSError("disk full")

        install_fake_hf(monkeypatch, boom)
        app_module.worker_download("org/model")
        assert app_module.STATE.downloads["org/model"]["status"] != "success"


def stub_worker(app_module, monkeypatch):
    """Replace the worker so a request starts no thread that outlives the test."""
    started = threading.Event()
    monkeypatch.setattr(app_module, "worker_download", lambda repo: started.set())
    return started


class TestDuplicateGuard:
    def test_second_request_while_running_is_rejected(self, base_url, app_module):
        app_module.STATE.downloads["org/model"] = {
            "repo": "org/model",
            "status": "running",
            "detail": "starting",
            "pct": 0,
        }
        status, body = post(f"{base_url}/api/download", {"repo": "org/model"})
        assert status == 409
        assert body["error"] == "already downloading"

    def test_retry_allowed_after_success(self, base_url, app_module, monkeypatch):
        started = stub_worker(app_module, monkeypatch)
        app_module.STATE.downloads["org/model"] = {
            "repo": "org/model",
            "status": "success",
            "detail": "complete",
            "pct": 100,
        }
        status, body = post(f"{base_url}/api/download", {"repo": "org/model"})
        assert status == 200
        assert body["ok"] is True
        assert started.wait(5)

    def test_retry_allowed_after_failure(self, base_url, app_module, monkeypatch):
        started = stub_worker(app_module, monkeypatch)
        app_module.STATE.downloads["org/model"] = {
            "repo": "org/model",
            "status": "failed",
            "detail": "boom",
            "pct": 0,
        }
        status, body = post(f"{base_url}/api/download", {"repo": "org/model"})
        assert status == 200
        assert body["ok"] is True
        assert started.wait(5)

    def test_missing_repo_is_400(self, base_url):
        status, body = post(f"{base_url}/api/download", {})
        assert status == 400
        assert "repo" in body["error"]


class TestDownloadsInState:
    def test_state_exposes_downloads(self, base_url, app_module):
        app_module.STATE.downloads["org/model"] = {
            "repo": "org/model",
            "status": "running",
            "detail": "Downloading config.json",
            "pct": 42,
        }
        _, body = get_json(f"{base_url}/api/state")
        entry = next(d for d in body["downloads"] if d["repo"] == "org/model")
        assert entry["pct"] == 42
        assert entry["detail"] == "Downloading config.json"

    def test_state_downloads_empty_by_default(self, base_url):
        _, body = get_json(f"{base_url}/api/state")
        assert body["downloads"] == []


class TestUiContract:
    """The Download view markup must keep the ids the renderer depends on."""

    @pytest.fixture
    def html(self):
        return (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text(
            encoding="utf-8"
        )

    def test_download_section_ids_exist(self, html):
        for element_id in ("downloadCards", "downloadEmpty", "downloadCount"):
            assert f'id="{element_id}"' in html, element_id

    def test_downloads_render_before_the_catalogue(self, html):
        """Regression: the section used to sit 1772px down the scroll container."""
        download_at = html.index('id="downloadCards"')
        catalog_at = html.index('id="catalogCards"')
        assert download_at < catalog_at, "Active Downloads must come first"


class TestPathStyling:
    @pytest.fixture
    def css(self):
        return (Path(__file__).resolve().parent.parent / "ui" / "style.css").read_text(
            encoding="utf-8"
        )

    def test_card_path_is_selectable(self, css):
        start = css.index(".card-path")
        block = css[start : start + 400]
        assert "user-select: all" in block

    def test_card_path_wraps_long_paths(self, css):
        start = css.index(".card-path")
        block = css[start : start + 400]
        assert "word-break" in block


def get_json(url, timeout=5):
    import urllib.request

    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, json.loads(resp.read())


def post(url, payload=None, timeout=5):
    import urllib.error
    import urllib.request

    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")
