"""Configuration for 2.0.13 (row 848): clamp, per-model context, load_test, new keys.

No server process is started and no model is loaded.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_row838_settings import REPO, post, write

OTHER = "org/other"
ROOT = Path(__file__).resolve().parent.parent


def stamp(app):
    return app._launch_stamp(app.engines.MLX, app.server_settings([REPO]), REPO)


class TestContextChange:
    @pytest.mark.parametrize(("engine", "waits"), [("optiq", True), ("mlx", False)])
    def test_only_an_engine_that_takes_it_waits_for_apply(self, app_module, engine, waits):
        """mlx_lm has no context flag: a restart would change nothing."""
        app = app_module
        app.STATE.server_repos, app.STATE.server_engine = [REPO], engine
        app.save_settings({"max_context_tokens": 5000}, REPO)
        chosen = app.engines.get(engine)
        app.STATE.server_started_with = app._launch_stamp(chosen, app.server_settings([REPO]), REPO)
        assert app._settings_pending(True) is False
        app.save_settings({"max_context_tokens": 7000}, REPO)
        assert app._settings_pending(True) is waits


class TestSampling:
    @pytest.mark.parametrize(
        ("key", "value"),
        [("chat_temperature", 0), ("chat_temperature", 1.5), ("chat_temperature", 2),
         ("chat_top_p", 0.05), ("chat_top_p", 1)],
    )  # fmt: skip
    def test_read_and_sent_for_the_model(self, app_module, key, value):
        write(app_module, {"models": {REPO: {key: value}}})
        sent = key.removeprefix("chat_")
        assert app_module.chat_settings(REPO)[sent] == value
        assert app_module.chat_settings(OTHER)[sent] == app_module.CHAT_SETTINGS[sent]

    @pytest.mark.parametrize(
        ("key", "bad"),
        [("chat_temperature", -0.1), ("chat_temperature", 2.1), ("chat_temperature", True),
         ("chat_temperature", "1"), ("chat_temperature", float("nan")), ("chat_top_p", 0),
         ("chat_top_p", 1.01)],
    )  # fmt: skip
    def test_bad_values_are_refused(self, app_module, base_url, key, bad):
        status, body = post(base_url, {"values": {key: bad}})
        assert status == 400 and "number" in body["error"]
        assert not app_module._settings_file().exists()

    def test_other_keys_still_take_whole_numbers_only(self, app_module, base_url):
        assert post(base_url, {"values": {"prompt_cache_size": 2.5}})[0] == 400

    def test_a_decimal_is_saved_and_needs_no_restart(self, app_module, base_url):
        assert post(base_url, {"values": {"chat_top_p": 0.5}})[0] == 200
        assert app_module.user_settings()["chat_top_p"] == 0.5
        assert stamp(app_module) == "{}"


LOAD_TEST = {"sizes": [4096, 8192], "reply_tokens": 128}


def saved(app):
    return json.loads(app._settings_file().read_text())


class TestLoadTestBlock:
    def test_the_defaults(self, app_module):
        view = app_module.load_test_view()
        values = {key: entry["value"] for key, entry in view.items()}
        assert values == {
            "sizes": [4096 << step for step in range(9)],
            "last_sizes": [],
            "reply_tokens": 256,
            "rounds": 2,
            "timeout_seconds": 900,
        }
        assert (view["reply_tokens"]["min"], view["reply_tokens"]["max"]) == (32, 1024)

    def test_it_survives_saving_and_reset(self, app_module, base_url):
        assert post(base_url, {"load_test": LOAD_TEST})[0] == 200
        assert saved(app_module) == {"load_test": LOAD_TEST}
        assert post(base_url, {"values": {"prompt_cache_size": 2}})[0] == 200
        assert post(base_url, {"repo": REPO, "values": {"prompt_cache_size": 3}})[0] == 200
        assert saved(app_module)["load_test"] == LOAD_TEST
        assert post(base_url, {"repo": REPO, "reset": True})[0] == 200
        assert post(base_url, {"reset": True})[0] == 200
        assert saved(app_module) == {"load_test": LOAD_TEST}

    def test_one_key_is_merged_and_cleared(self, app_module, base_url):
        post(base_url, {"load_test": LOAD_TEST})
        assert post(base_url, {"load_test": {"reply_tokens": None, "rounds": 1}})[0] == 200
        assert saved(app_module) == {"load_test": {"sizes": [4096, 8192], "rounds": 1}}
        assert post(base_url, {"load_test": None})[0] == 200
        assert saved(app_module) == {}

    @pytest.mark.parametrize(
        "body",
        [
            {"load_test": {"margin_percent": 4}},
            {"load_test": {"margin_percent": 21}},
            {"load_test": {"rounds": 4}},
            {"load_test": {"reply_tokens": 2000}},
            {"load_test": {"timeout_seconds": 59}},
            {"load_test": {"sizes": []}},
            {"load_test": {"sizes": [4096, 4096]}},
            {"load_test": {"sizes": [100]}},
            {"load_test": {"sizes": "4096"}},
            {"load_test": {"kill_percent": 90}},
            {"load_test": 5},
            {"load_test": {"rounds": 1}, "repo": REPO},
        ],
    )
    def test_bad_values_are_refused(self, app_module, base_url, body):
        assert post(base_url, body)[0] == 400
        assert not app_module._settings_file().exists()

    def test_a_bad_stored_value_falls_back_alone(self, app_module):
        write(app_module, {"load_test": {"rounds": 9, "reply_tokens": 64}})
        view = app_module.load_test_view()
        assert (view["rounds"]["value"], view["reply_tokens"]["value"]) == (2, 64)


def get(base, path):
    try:
        with urllib.request.urlopen(urllib.request.Request(base + path), timeout=10) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestConfigRoute:
    def test_own_inherited_effective_and_range(self, app_module, base_url):
        models = {REPO: {"prompt_cache_size": 5}}
        write(app_module, {"chat_top_p": 0.5, "models": models})
        status, body = get(base_url, f"/api/config?repo={REPO}")
        assert status == 200 and body["repo"] == REPO
        keys = body["settings"]
        assert set(keys) == set(app_module.SETTINGS)
        assert (keys["prompt_cache_size"]["own"], keys["prompt_cache_size"]["inherited"]) == (
            5,
            None,
        )
        assert keys["prompt_cache_size"]["effective"] == "5"
        top = keys["chat_top_p"]
        assert (top["own"], top["inherited"], top["effective"]) == (None, 0.5, "0.5")
        assert (top["min"], top["max"]) == (0.05, 1)
        assert not any(entry["global_only"] for entry in keys.values())
        assert "applied_context" not in body and "context_message" not in body
        assert body["load_test"]["rounds"]["value"] == 2

    def test_without_a_model_the_global_values_are_its_own(self, app_module, base_url):
        write(app_module, {"prompt_cache_size": 3})
        body = get(base_url, "/api/config")[1]
        entry = body["settings"]["prompt_cache_size"]
        assert body["repo"] is None
        assert (entry["own"], entry["inherited"], entry["value"]) == (3, None, 3)

    def test_a_bad_repo(self, base_url):
        assert get(base_url, "/api/config?repo=a%20b")[0] == 400


class TestDocs:
    def test_the_routes_and_the_stops_of_a_test_are_said(self):
        text = (ROOT / "docs" / "API.md").read_text("utf-8")
        for route in ("/api/config", "/api/loadtest/start", "/api/loadtest/cancel"):
            assert f"{route}" in text
        assert "| `POST /api/loadtest/apply`" not in text
        assert "critical memory pressure" in text and "swap growth over 0.5 GB" in text
