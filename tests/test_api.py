"""HTTP contract: every endpoint, its status codes and its JSON shape."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.headers, resp.read()


def get_json(url, timeout=5):
    status, _, body = get(url, timeout)
    return status, json.loads(body)


def post(url, payload=None, timeout=5):
    data = json.dumps(payload or {}).encode()
    req = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


class TestMeta:
    def test_ping(self, base_url, app_module):
        status, body = get_json(f"{base_url}/api/ping")
        assert status == 200
        assert body == {"ok": True, "app": app_module.APP_VERSION}

    def test_state_has_every_top_level_key(self, base_url):
        status, body = get_json(f"{base_url}/api/state")
        assert status == 200
        for key in (
            "app",
            "hardware",
            "python",
            "versions",
            "models",
            "server",
            "downloads",
            "install",
            "generating",
        ):
            assert key in body, key

    def test_state_app_block(self, base_url, app_module):
        _, body = get_json(f"{base_url}/api/state")
        assert body["app"]["name"] == "MLX Studio"
        assert body["app"]["version"] == app_module.APP_VERSION
        assert isinstance(body["app"]["update"], bool)

    def test_state_server_block(self, base_url, app_module):
        _, body = get_json(f"{base_url}/api/state")
        server = body["server"]
        assert server["port"] == app_module.SERVER_PORT
        assert server["endpoint"] == f"http://127.0.0.1:{app_module.SERVER_PORT}/v1"
        assert server["running"] is False
        assert server["background"] is False
        assert server["down_notice"] is None

    def test_versions_block(self, base_url):
        _, body = get_json(f"{base_url}/api/state")
        versions = body["versions"]
        for key in ("mlx", "mlx_lm", "huggingface_hub", "mlx_missing", "mlx_lm_missing"):
            assert key in versions

    def test_environment_endpoint(self, base_url, app_module):
        status, body = get_json(f"{base_url}/api/environment")
        assert status == 200
        assert body["app"]["version"] == app_module.APP_VERSION
        assert body["ports"]["app"] == app_module.PORT

    def test_catalog_endpoint(self, base_url, app_module):
        status, body = get_json(f"{base_url}/api/catalog")
        assert status == 200
        assert len(body["catalog"]) == len(app_module.MODEL_CATALOG)
        assert "hardware" in body
        for item in body["catalog"]:
            assert item["fit"] in {"great", "good", "tight", "too-large", "unknown"}
            assert isinstance(item["installed"], bool)
            assert item["size_gb"] > 0
            assert item["repo"].startswith("mlx-community/")

    def test_catalog_is_sorted_by_fit_then_size(self, base_url):
        _, body = get_json(f"{base_url}/api/catalog")
        order = {"great": 0, "good": 1, "tight": 2, "too-large": 3, "unknown": 4}
        keys = [(order[m["fit"]], m["size_gb"]) for m in body["catalog"]]
        assert keys == sorted(keys)


class TestLogsEndpoints:
    def test_logs_empty(self, base_url):
        status, body = get_json(f"{base_url}/api/logs")
        assert status == 200
        assert body == {"entries": []}

    def test_logs_after_activity(self, base_url, app_module):
        app_module.log_activity("chat", "org/model", "success", "hello")
        _, body = get_json(f"{base_url}/api/logs")
        assert body["entries"][0]["detail"] == "hello"

    def test_logs_files_lists_the_folder(self, base_url, app_module):
        app_module.log_app("info", "started")
        status, body = get_json(f"{base_url}/api/logs/files")
        assert status == 200
        assert body["dir"] == str(app_module.LOG_DIR)
        assert any(f["name"] == "app.log" for f in body["files"])

    def test_logs_app_respects_limit(self, base_url, app_module):
        for index in range(10):
            app_module.log_app("info", str(index))
        _, body = get_json(f"{base_url}/api/logs/app?limit=3")
        assert [e["message"] for e in body["entries"]] == ["7", "8", "9"]

    def test_logs_app_rejects_non_numeric_limit(self, base_url, app_module):
        app_module.log_app("info", "x")
        status, body = get_json(f"{base_url}/api/logs/app?limit=abc")
        assert status == 200
        assert len(body["entries"]) == 1

    def test_logs_app_clamps_limit(self, base_url):
        status, _ = get_json(f"{base_url}/api/logs/app?limit=99999")
        assert status == 200

    def test_export_returns_a_zip(self, base_url, app_module):
        app_module.log_app("info", "started")
        status, headers, body = get(f"{base_url}/api/logs/export")
        assert status == 200
        assert headers["Content-Type"] == "application/zip"
        assert "attachment; filename=" in headers["Content-Disposition"]
        assert body[:2] == b"PK"

    def test_clear_removes_activity_but_keeps_app_log(self, base_url, app_module):
        app_module.log_activity("chat", "m", "success", "x")
        app_module.log_app("info", "keep me")
        status, body = post(f"{base_url}/api/logs/clear")
        assert status == 200
        assert body["ok"] is True
        assert app_module.read_activity() == []
        messages = [e["message"] for e in app_module.read_app_log()]
        assert "keep me" in messages
        assert "activity log cleared" in messages


class TestModelsEndpoints:
    def test_models_empty(self, base_url):
        status, body = get_json(f"{base_url}/api/models")
        assert status == 200
        assert body == {"models": []}

    def test_delete_requires_repo(self, base_url):
        status, body = post(f"{base_url}/api/delete", {})
        assert status == 400
        assert "repo" in body["error"]

    def test_delete_unknown_model_is_404(self, base_url):
        status, body = post(f"{base_url}/api/delete", {"repo": "org/nope"})
        assert status == 404
        assert body["error"] == "not installed"


class TestServerEndpoints:
    def test_start_requires_repo(self, base_url):
        status, body = post(f"{base_url}/api/server/start", {})
        assert status == 400
        assert "repo" in body["error"]

    def test_stop_when_not_running(self, base_url):
        status, body = post(f"{base_url}/api/server/stop")
        assert status == 200
        assert body["ok"] is True

    def test_notice_clear(self, base_url, app_module):
        app_module.STATE.server_down_notice = "boom"
        status, body = post(f"{base_url}/api/server/notice/clear")
        assert status == 200
        assert body["ok"] is True
        assert app_module.STATE.server_down_notice is None


class TestFocusEndpoint:
    def test_focus_without_a_window_reports_false(self, base_url):
        # No pywebview window exists in the test process.
        status, body = post(f"{base_url}/api/focus")
        assert status == 200
        assert body == {"ok": False}

    def test_focus_with_a_stub_window_reports_true(self, base_url, app_module):
        calls = []

        class StubWindow:
            def restore(self):
                calls.append("restore")

            def show(self):
                calls.append("show")

        app_module.STATE.window = StubWindow()
        status, body = post(f"{base_url}/api/focus")
        assert status == 200
        assert body == {"ok": True}
        assert calls == ["restore", "show"]


class TestUnknownRoutes:
    def test_unknown_get_is_404(self, base_url):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"{base_url}/api/nope")
        assert exc.value.code == 404

    def test_unknown_post_is_404(self, base_url):
        status, body = post(f"{base_url}/api/nope")
        assert status == 404
        assert body["error"] == "unknown endpoint"

    def test_quit_is_gone(self, base_url):
        # Removed in the security audit; must never come back.
        status, _ = post(f"{base_url}/api/quit")
        assert status == 404


class TestStatic:
    def test_index_is_served(self, base_url):
        status, headers, body = get(f"{base_url}/")
        assert status == 200
        assert headers["Content-Type"].startswith("text/html")
        assert b"MLX Studio" in body

    def test_css_and_js_are_served(self, base_url):
        for path, ctype in (("/style.css", "text/css"), ("/app.js", "javascript")):
            status, headers, _ = get(f"{base_url}{path}")
            assert status == 200
            assert ctype in headers["Content-Type"]

    @pytest.mark.parametrize(
        "path",
        [
            "/../app.py",
            "/../ui-evil/leak.txt",
            "/%2e%2e/app.py",
            "/ui/../../app.py",
            "/nope.html",
        ],
    )
    def test_traversal_and_unknown_paths_are_404(self, base_url, path):
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(f"{base_url}{path}")
        assert exc.value.code == 404

    def test_static_response_is_not_cached(self, base_url):
        _, headers, _ = get(f"{base_url}/")
        assert headers["Cache-Control"] == "no-store"


class TestBodyHandling:
    def test_oversized_content_length_is_rejected(self, base_url):
        req = urllib.request.Request(
            f"{base_url}/api/load",
            data=b"{}",
            headers={"Content-Type": "application/json", "Content-Length": str(9 * 1024 * 1024)},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 413

    def test_malformed_json_body_is_treated_as_empty(self, base_url):
        req = urllib.request.Request(
            f"{base_url}/api/load",
            data=b"not json",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                status = resp.status
        except urllib.error.HTTPError as exc:
            status = exc.code
        assert status == 400

    def test_zero_content_length(self, base_url):
        req = urllib.request.Request(
            f"{base_url}/api/chat/stop",
            data=b"",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=5) as resp:
            assert resp.status == 200
