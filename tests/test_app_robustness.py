"""Concurrency, in-use guards, cache handling and request validation."""

from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import threading
import time
import types
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
from test_security import good_headers, request

ROOT = Path(__file__).resolve().parent.parent


def post(server, app_module, path, payload):
    body = json.dumps(payload).encode()
    status, _, raw = request(server, "POST", path, body, good_headers(app_module))
    return status, json.loads(raw or b"{}")


def make_model(app_module, repo="org/model", files=("config.json", "model.safetensors"), sha="abc"):
    root = app_module.CACHE_DIR / f"models--{repo.replace('/', '--')}"
    snap = root / "snapshots" / sha
    snap.mkdir(parents=True, exist_ok=True)
    (root / "refs").mkdir(exist_ok=True)
    (root / "refs" / "main").write_text(sha, encoding="utf-8")
    for name in files:
        (snap / name).write_text("{}", encoding="utf-8")
    (root / "blobs").mkdir(exist_ok=True)
    return root


class Recorder:
    """Replaces a worker so a handler test starts no real work."""

    def __init__(self):
        self.calls = []

    def __call__(self, *args):
        self.calls.append(args)


def race(count, call):
    results = []
    threads = [threading.Thread(target=lambda: results.append(call())) for _ in range(count)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=10)
    return results


# M1 ------------------------------------------------------------------------- #
class TestDownloadReservation:
    def test_simultaneous_requests_start_one_worker(self, server, app_module, monkeypatch):
        worker = Recorder()
        monkeypatch.setattr(app_module, "worker_download", worker)
        results = race(8, lambda: post(server, app_module, "/api/download", {"repo": "org/m"})[0])
        assert sorted(results) == [200] + [409] * 7
        assert len(worker.calls) == 1

    def test_snapshot_returns_copies(self, app_module):
        app_module.STATE.reserve_download("org/m")
        shot = app_module.STATE.snapshot()["downloads"][0]
        shot["status"] = "changed"
        assert app_module.STATE.downloads["org/m"]["status"] == "running"


# M2 ------------------------------------------------------------------------- #
class TestServerStartLock:
    def test_concurrent_starts_leave_no_orphan(self, app_module, monkeypatch):
        real_popen = subprocess.Popen
        procs = []

        def fake_popen(cmd, **kwargs):
            if cmd[0] == "ps":
                return real_popen(cmd, **kwargs)
            time.sleep(0.1)
            proc = real_popen(
                [sys.executable, "-c", "import time; time.sleep(30)", *cmd[1:]],
                stdout=subprocess.DEVNULL,
                start_new_session=True,
            )
            procs.append(proc)
            return proc

        monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
        try:
            race(3, lambda: app_module.worker_server_start("org/model", True))
            alive = [p for p in procs if p.poll() is None]
            assert [p.pid for p in alive] == [app_module.STATE.server_pid]
        finally:
            for proc in procs:
                proc.kill()
                proc.wait()


# M3 ------------------------------------------------------------------------- #
class TestDeleteGuard:
    @pytest.mark.parametrize("state", ["loaded", "loading", "serving", "starting", "downloading"])
    def test_model_in_use_is_not_deleted(self, server, app_module, state):
        root = make_model(app_module)
        s = app_module.STATE
        if state == "loaded":
            s.loaded_repo = "org/model"
        elif state == "loading":
            s.loading_repo = "org/model"
        elif state == "serving":
            s.server_repo, s.server_pid = "org/model", os.getpid()
        elif state == "starting":
            s.server_pending.append("org/model")
        else:
            s.reserve_download("org/model")
        assert post(server, app_module, "/api/delete", {"repo": "org/model"})[0] == 409
        assert root.exists()

    def test_failed_removal_is_reported(self, server, app_module, monkeypatch):
        make_model(app_module)

        def refuse(path, ignore_errors=False):
            if not ignore_errors:
                raise PermissionError("locked")

        monkeypatch.setattr(app_module.shutil, "rmtree", refuse)
        status, body = post(server, app_module, "/api/delete", {"repo": "org/model"})
        assert status == 500 and "locked" in body["error"]
        assert app_module.read_activity()[-1]["status"] == "failed"

    def test_idle_model_is_deleted_and_leaves_the_list(self, server, app_module):
        root = make_model(app_module)
        assert app_module.installed_models()
        assert post(server, app_module, "/api/delete", {"repo": "org/model"})[0] == 200
        assert not root.exists() and app_module.installed_models() == []


# M4 ------------------------------------------------------------------------- #
class TestSingleModelSlot:
    def test_two_quick_loads_start_one_worker(self, server, app_module, monkeypatch):
        worker = Recorder()
        monkeypatch.setattr(app_module, "worker_load", worker)
        results = race(6, lambda: post(server, app_module, "/api/load", {"repo": "org/m"})[0])
        assert sorted(results) == [200] + [409] * 5
        assert len(worker.calls) == 1

    def test_old_model_is_released_before_the_new_one_loads(self, app_module, monkeypatch):
        seen = []

        def load(repo):
            seen.append((app_module.STATE.model, app_module.STATE.loaded_repo))
            return "model", "tokenizer"

        monkeypatch.setitem(sys.modules, "mlx_lm", types.SimpleNamespace(load=load))
        app_module.STATE.model, app_module.STATE.loaded_repo = "old", "org/old"
        app_module.worker_load("org/new")
        assert seen == [(None, None)]
        assert app_module.STATE.loaded_repo == "org/new"

    @pytest.mark.parametrize("path", ["/api/load", "/api/unload"])
    def test_model_cannot_change_during_a_reply(self, server, app_module, path):
        app_module.STATE.model, app_module.STATE.loaded_repo = "old", "org/old"
        app_module.STATE.generating = True
        assert post(server, app_module, path, {"repo": "org/new"})[0] == 409
        assert app_module.STATE.model == "old"


# M5 ------------------------------------------------------------------------- #
class TestSingleGeneration:
    def test_second_chat_is_refused_and_stop_reaches_the_first(
        self, server, app_module, monkeypatch
    ):
        started, release = threading.Event(), threading.Event()
        stops = []

        def fake_stream(messages, stop):
            stops.append(stop)
            started.set()
            release.wait(5)
            yield ("done", {"tokens": 0, "tps": 0})

        monkeypatch.setattr(app_module, "stream_chat", fake_stream)
        app_module.STATE.model = "model"
        chat = {"messages": [{"role": "user", "content": "hi"}]}
        body = json.dumps(chat).encode()
        headers = good_headers(app_module)
        first = threading.Thread(
            target=lambda: request(server, "POST", "/api/chat", body, headers), daemon=True
        )
        first.start()
        assert started.wait(5)
        assert post(server, app_module, "/api/chat", chat)[0] == 409
        assert not stops[0].is_set()
        post(server, app_module, "/api/chat/stop", {})
        assert stops[0].is_set()
        release.set()
        first.join(timeout=5)
        assert app_module.STATE.generating is False
        post(server, app_module, "/api/chat/stop", {})  # no reply running: harmless
        request(server, "POST", "/api/chat", body, headers)
        assert len(stops) == 2 and not stops[1].is_set()


# M6 ------------------------------------------------------------------------- #
def launch_command(app_module, monkeypatch):
    seen = []

    def fake_popen(cmd, **kwargs):
        seen.append(cmd)
        raise OSError("not started in tests")

    monkeypatch.setattr(app_module, "stop_server", lambda *a, **k: None)
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
    app_module.worker_server_start("org/model", False)
    return seen[0]


class TestApiServerOrigins:
    def test_origins_are_an_explicit_list(self, app_module, monkeypatch):
        cmd = launch_command(app_module, monkeypatch)
        value = cmd[cmd.index("--allowed-origins") + 1]
        assert value == "http://127.0.0.1:8765,http://localhost:8765"

    def test_installed_mlx_lm_honours_the_list(self, app_module, monkeypatch):
        """No model is loaded: only the preflight answer is exercised."""
        mlx_server = pytest.importorskip("mlx_lm.server")
        cmd = launch_command(app_module, monkeypatch)
        origins = cmd[cmd.index("--allowed-origins") + 1].split(",")  # the flag's own parser
        generator = types.SimpleNamespace(cli_args=types.SimpleNamespace(allowed_origins=origins))
        handler = partial(mlx_server.APIHandler, generator, system_fingerprint="test")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            evil = request(
                httpd, "OPTIONS", "/v1/chat/completions", headers={"Origin": "https://x"}
            )
            plain = request(httpd, "OPTIONS", "/v1/chat/completions")
        finally:
            httpd.shutdown()
            httpd.server_close()
        assert evil[1]["Access-Control-Allow-Origin"] is None
        assert plain[0] == 204  # a client that sends no Origin is unaffected


# M7 ------------------------------------------------------------------------- #
class TestCacheDir:
    def test_app_uses_the_hub_library_constant(self, tmp_path):
        pytest.importorskip("huggingface_hub")
        for env in ({"HF_HOME": str(tmp_path / "h")}, {"HF_HUB_CACHE": str(tmp_path / "c")}):
            code = (
                "import importlib.util as u;"
                f"s=u.spec_from_file_location('a', {str(ROOT / 'app.py')!r});"
                "m=u.module_from_spec(s);s.loader.exec_module(m);print(m.CACHE_DIR)"
            )
            base = {k: v for k, v in os.environ.items() if not k.startswith(("HF_", "HUGGING"))}
            out = subprocess.run(
                [sys.executable, "-c", code],
                env={**base, "HOME": str(tmp_path), **env},
                capture_output=True,
                text=True,
                check=True,
            ).stdout.strip()
            expected = env.get("HF_HUB_CACHE") or str(Path(env["HF_HOME"]) / "hub")
            assert out == expected


# M8 ------------------------------------------------------------------------- #
def fake_hub(monkeypatch, files, download):
    class Api:
        def repo_info(self, repo_id, repo_type=None, files_metadata=False):
            siblings = [
                types.SimpleNamespace(rfilename=name, size=size if files_metadata else None)
                for name, size in files
            ]
            return types.SimpleNamespace(siblings=siblings, sha="abc")

    module = types.ModuleType("huggingface_hub")
    module.HfApi = Api
    module.hf_hub_download = download
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)


class TestByteProgressAndCancel:
    def test_progress_follows_bytes_and_names_the_current_file(self, app_module, monkeypatch):
        seen = []

        def download(repo_id, filename, cache_dir, tqdm_class):
            entry = app_module.STATE.downloads[repo_id]
            seen.append((entry["detail"], entry["pct"]))
            size = 10 if filename == "config.json" else 990
            with tqdm_class(total=size, initial=0, desc=filename) as bar:
                bar.update(size // 2)
                seen.append((filename, app_module.STATE.downloads[repo_id]["pct"]))
                bar.update(size - size // 2)

        fake_hub(monkeypatch, [("config.json", 10), ("model.safetensors", 990)], download)
        app_module.worker_download("org/m")
        assert seen[2] == ("Downloading model.safetensors", 1)
        assert seen[3] == ("model.safetensors", 50)
        assert app_module.STATE.downloads["org/m"]["pct"] == 100

    def test_cancel_stops_mid_file(self, server, app_module, monkeypatch):
        started = []

        def download(repo_id, filename, cache_dir, tqdm_class):
            started.append(filename)
            bar = tqdm_class(total=100, initial=0)
            bar.update(10)
            assert post(server, app_module, "/api/download/cancel", {"repo": repo_id})[0] == 200
            bar.update(10)
            pytest.fail("cancel did not interrupt the file")

        fake_hub(monkeypatch, [("a.safetensors", 100), ("b.safetensors", 100)], download)
        app_module.worker_download("org/m")
        assert started == ["a.safetensors"]
        assert app_module.STATE.downloads["org/m"]["status"] == "cancelled"

    def test_cancel_without_a_download_is_404(self, server, app_module):
        assert post(server, app_module, "/api/download/cancel", {"repo": "org/m"})[0] == 404


# M9 ------------------------------------------------------------------------- #
class TestCompleteness:
    def complete(self, app_module):
        app_module.invalidate_models()
        return {m["repo"]: m["complete"] for m in app_module.installed_models()}

    def test_full_snapshot_is_installed(self, app_module):
        make_model(app_module)
        assert self.complete(app_module) == {"org/model": True}

    @pytest.mark.parametrize(
        "files",
        [
            ("config.json",),
            ("config.json", "model-00001-of-00002.safetensors"),
        ],
    )
    def test_missing_weights_are_incomplete(self, app_module, files):
        make_model(app_module, files=files)
        assert self.complete(app_module) == {"org/model": False}

    def test_missing_indexed_shard_is_incomplete(self, app_module):
        root = make_model(app_module, files=("config.json", "a.safetensors"))
        index = {"weight_map": {"x": "a.safetensors", "y": "b.safetensors"}}
        (root / "snapshots/abc/model.safetensors.index.json").write_text(json.dumps(index))
        assert self.complete(app_module) == {"org/model": False}

    def test_leftover_partial_blob_is_incomplete(self, app_module):
        root = make_model(app_module)
        (root / "blobs" / "deadbeef.incomplete").write_text("x")
        assert self.complete(app_module) == {"org/model": False}

    def test_failed_download_is_not_offered_as_installed(self, app_module, monkeypatch):
        def download(repo_id, filename, cache_dir, tqdm_class):
            if filename == "tokenizer.json":
                raise OSError("network down")
            make_model(app_module, repo_id, files=(filename,))

        files = [("config.json", 1), ("model.safetensors", 1), ("tokenizer.json", 1)]
        repo = app_module.MODEL_CATALOG[0]["repo"]
        fake_hub(monkeypatch, files, download)
        app_module.worker_download(repo)
        assert self.complete(app_module) == {repo: False}
        entry = next(m for m in app_module.model_catalog() if m["repo"] == repo)
        assert entry["installed"] is False
        # finishing the files by any means clears the mark
        make_model(app_module, repo, files=("tokenizer.json",))
        assert self.complete(app_module) == {repo: True}


# M10 ------------------------------------------------------------------------ #
class TestCaching:
    def test_hardware_is_probed_once(self, app_module, monkeypatch):
        calls = []
        monkeypatch.setattr(
            app_module, "run_cmd", lambda cmd, **k: calls.append(cmd) or (True, "8")
        )
        for _ in range(5):
            app_module.hardware()
        assert len(calls) == 2

    def test_unchanged_cache_is_not_walked_again(self, app_module, monkeypatch):
        make_model(app_module)
        first = app_module.installed_models()
        walks = []
        real = app_module._scan_model
        monkeypatch.setattr(app_module, "_scan_model", lambda e: walks.append(e) or real(e))
        assert app_module.installed_models() == first and walks == []
        make_model(app_module, "org/second")
        assert len(app_module.installed_models()) == 2 and walks


# M12 ------------------------------------------------------------------------ #
class TestReveal:
    def test_reveals_only_installed_models(self, server, app_module, monkeypatch):
        root = make_model(app_module)
        opened = []
        monkeypatch.setattr(
            app_module, "run_cmd", lambda cmd, **k: opened.append(cmd) or (True, "")
        )
        assert post(server, app_module, "/api/reveal", {"repo": "../../etc"})[0] == 404
        assert opened == []
        assert post(server, app_module, "/api/reveal", {"repo": "org/model"})[0] == 200
        assert opened == [["open", "-R", str(root)]]


# L2 ------------------------------------------------------------------------- #
class TestBadBodies:
    @pytest.mark.parametrize(
        ("path", "body"),
        [
            ("/api/load", b"[1]"),
            ("/api/download", b'{"repo": ["a"]}'),
            ("/api/delete", b'{"repo": 5}'),
            ("/api/chat", b'{"messages": "hi"}'),
            ("/api/chat", b'{"messages": [1]}'),
        ],
    )
    def test_wrong_types_are_400(self, server, app_module, path, body):
        status, _, raw = request(server, "POST", path, body, good_headers(app_module))
        assert status == 400 and json.loads(raw)["error"]

    def test_oversized_body_is_413_and_closes(self, server, app_module):
        body = b"x" * (app_module.Handler.MAX_BODY + 1)
        status, headers, raw = request(server, "POST", "/api/chat", body, good_headers(app_module))
        assert status == 413 and headers["Connection"] == "close"
        assert "too large" in json.loads(raw)["error"]

    @pytest.mark.parametrize("length", ["abc", "-1"])
    def test_bad_length_is_400_and_closes(self, server, app_module, length):
        headers = {**good_headers(app_module), "Content-Length": length}
        status, reply_headers, _ = request(server, "POST", "/api/unload", None, headers)
        assert status == 400 and reply_headers["Connection"] == "close"

    def test_rejected_body_is_not_read_as_a_second_request(self, server):
        import socket

        smuggled = (
            b"GET /api/logs HTTP/1.1\r\nHost: 127.0.0.1:%d\r\n\r\n" % server.server_address[1]
        )
        head = (
            b"POST /api/unload HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nContent-Type: text/plain\r\n"
            b"Content-Length: %d\r\n\r\n" % (server.server_address[1], len(smuggled))
        )
        with socket.create_connection(server.server_address, timeout=5) as sock:
            sock.sendall(head + smuggled)
            data = io.BytesIO()
            while chunk := sock.recv(4096):
                data.write(chunk)
        assert len(re.findall(rb"HTTP/1\.1 \d{3}", data.getvalue())) == 1


# L4 ------------------------------------------------------------------------- #
class TestUpdateManifest:
    @pytest.mark.parametrize(
        ("url", "sha", "expected"),
        [
            ("https://x.test/a.pkg", "a" * 64, ("https://x.test/a.pkg", "a" * 64)),
            ("http://x.test/a.pkg", "zz", (None, None)),
            ("file:///etc/passwd", None, (None, None)),
        ],
    )
    def test_only_https_url_and_real_sha_are_kept(
        self, app_module, monkeypatch, url, sha, expected
    ):
        manifest = json.dumps({"version": "9.0.0", "url": url, "sha256": sha}).encode()
        monkeypatch.setattr(app_module, "APP_UPDATE_URL", "https://x.test/latest.json")
        monkeypatch.setattr(
            app_module.urllib.request, "urlopen", lambda *a, **k: io.BytesIO(manifest)
        )
        assert app_module.latest_app_version() == "9.0.0"
        release = app_module.STATE.app_release
        assert (release["url"], release["sha256"]) == expected


# L5 ------------------------------------------------------------------------- #
class TestPinnedInstall:
    def test_pins_match_requirements(self, app_module):
        text = (ROOT / "requirements.txt").read_text(encoding="utf-8")
        for name, version in app_module.PINNED_PACKAGES.items():
            assert f"\n{name}=={version}\n" in text

    @pytest.mark.parametrize(
        ("installed", "expected"), [(None, "{pin}"), ("0.0.1", "{pin}"), ("999.0", "999.0")]
    )
    def test_install_never_passes_the_pin_or_downgrades(
        self, app_module, monkeypatch, installed, expected
    ):
        monkeypatch.setattr(app_module, "pkg_version", lambda name: installed)
        specs = app_module.install_specs()
        assert specs == [
            f"{n}=={expected.format(pin=v)}" for n, v in app_module.PINNED_PACKAGES.items()
        ]

    def test_update_flag_means_the_pin_is_newer(self, app_module, monkeypatch):
        app_module.STATE.versions = {"mlx_latest": "999.0"}
        pin = app_module.PINNED_PACKAGES["mlx"]
        monkeypatch.setattr(app_module, "pkg_version", lambda name: pin)
        assert app_module.STATE.snapshot()["versions"]["mlx_update"] is False

    def test_managed_python_is_explained(self, app_module, monkeypatch):
        class Proc:
            stdout = iter(["error: externally-managed-environment\n"])

            def wait(self):
                return 1

        monkeypatch.setattr(app_module.subprocess, "Popen", lambda *a, **k: Proc())
        monkeypatch.setattr(app_module, "worker_check_versions", lambda: None)
        app_module.worker_install("install")
        assert "virtual environment" in app_module.STATE.install["lines"][-1]


# L6 ------------------------------------------------------------------------- #
class TestRotation:
    def test_parallel_writers_lose_no_line(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "LOG_MAX_BYTES", 2000)
        monkeypatch.setattr(app_module, "LOG_BACKUPS", 500)
        path = app_module.LOG_DIR / "race.log"
        real = app_module._rotate

        def slow(target):
            real(target)
            time.sleep(0.0005)

        monkeypatch.setattr(app_module, "_rotate", slow)
        counter = iter(range(8))

        def write():
            n = next(counter)
            for i in range(150):
                app_module._append(path, f"{n}-{i:04d}-{'x' * 40}")

        race(8, write)
        lines = [x for f in app_module.LOG_DIR.glob("race.log*") for x in f.read_text().split()]
        assert len(lines) == 1200
        sizes = [f.stat().st_size for f in app_module.LOG_DIR.glob("race.log*")]
        assert max(sizes) < 2200

    def test_server_log_is_rotated_before_a_background_start(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "LOG_MAX_BYTES", 10)
        app_module.SERVER_LOG.write_text("o" * 50, encoding="utf-8")
        launch_command_background(app_module, monkeypatch)
        assert app_module.SERVER_LOG.with_name("server.log.1").read_text() == "o" * 50


def launch_command_background(app_module, monkeypatch):
    def fake_popen(cmd, **kwargs):
        raise OSError("not started in tests")

    monkeypatch.setattr(app_module, "stop_server", lambda *a, **k: None)
    monkeypatch.setattr(app_module.subprocess, "Popen", fake_popen)
    app_module.worker_server_start("org/model", True)


class TestProgressClassFitsTheHubLibrary:
    """The real library drives our stand-in for tqdm; no network is used."""

    def test_http_and_xet_progress_paths(self, app_module):
        pytest.importorskip("huggingface_hub")
        from huggingface_hub.utils._xet_progress_reporting import XetDownloadProgressReporter
        from huggingface_hub.utils.tqdm import _get_progress_bar_context

        seen = []
        cls = app_module._byte_progress(threading.Event(), seen.append)
        with _get_progress_bar_context(desc="f", log_level=20, total=10, tqdm_class=cls) as bar:
            bar.update(4)
        group = types.SimpleNamespace(
            total_bytes=10,
            total_bytes_completed=7,
            total_transfer_bytes_completed=9,
            total_bytes_completion_rate=1.0,
            total_transfer_bytes_completion_rate=1.0,
        )
        with XetDownloadProgressReporter(
            reconstruction_desc="f", total=10, log_level=20, tqdm_class=cls
        ) as reporter:
            reporter.update_progress(group)
        assert seen == [0, 4, 0, 7]
