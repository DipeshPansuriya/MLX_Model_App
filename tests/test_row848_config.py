"""Configuration for 2.0.13 (row 848): clamp, per-model context, load_test, new keys.

No server process is started and no model is loaded.
"""

# ruff: noqa: F811 - the imported fixtures are used by name
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_row838_prompt_limit import BIG, REPO, guard, limited, reached, room_for  # noqa: F401
from test_row838_settings import launch, one_estimate, post, write

OTHER = "org/other"
ROOT = Path(__file__).resolve().parent.parent


def after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


class TestClamp:
    def test_a_context_above_the_ceiling_is_clamped(self, guard):
        ceiling = room_for(guard, 6000)
        assert guard.CLAMP_USER_CONTEXT is True
        assert guard.prompt_limit(BIG, 2, ceiling, None, ceiling, 7000) == 6000
        assert guard.prompt_limit(BIG, 2, ceiling, None, ceiling, 5000) == 5000

    def test_above_the_automatic_figure_but_under_the_ceiling_applies(self, guard):
        automatic, ceiling = room_for(guard, 40000), room_for(guard, 60000)
        assert guard.prompt_limit(BIG, 2, automatic, None, ceiling, 50000) == 50000

    def test_unknown_figures_cannot_clamp(self, guard):
        assert guard.prompt_limit({}, 0, 0, None, 10**10, 5000) == 5000

    def test_the_server_refuses_above_the_clamped_figure_and_says_so(self, limited, guard, capsys):
        ceiling = room_for(guard, 5000)
        api = limited(BIG, ceiling, ceiling=ceiling, max_context=6000)
        assert reached(api.send("x" * 5000))
        assert api.send("x" * 5001)[0] == 400
        assert capsys.readouterr().err.count("5000 tokens apply") == 1

    def test_the_app_says_the_applied_figure(self, app_module, monkeypatch):
        write(app_module, {"max_context_tokens": 7000})
        warning = one_estimate(app_module, monkeypatch)["context_warning"]
        assert "7000" in warning and "6000 tokens apply" in warning
        assert "may pass" not in warning


class TestPerModelContext:
    def test_each_model_gets_its_own_figure_after_a_switch(self, limited):
        api = limited(BIG, max_context=50, model_contexts={REPO: 80, OTHER: 20})
        assert reached(api.send("x" * 80))
        assert api.send("x" * 81)[0] == 400

    def test_the_global_figure_is_the_fallback(self, limited):
        api = limited(BIG, max_context=50, model_contexts={OTHER: 20})
        assert reached(api.send("x" * 50))
        assert api.send("x" * 51)[0] == 400

    @pytest.mark.parametrize(
        ("text", "found"),
        [
            (None, {}),
            ("", {}),
            ("not json", {}),
            ("[1]", {}),
            ('{"a/b": 5000, "c/d": "5", "e/f": true, "g/h": 0}', {"a/b": 5000}),
        ],
    )
    def test_the_env_value(self, guard, text, found):
        assert guard.read_model_contexts(text) == found

    def test_main_passes_the_map(self, guard):
        assert "read_model_contexts" in guard.main.__code__.co_names
        assert "MLX_STUDIO_MODEL_CONTEXTS" in guard.main.__code__.co_consts

    def test_the_launch_passes_all_configured_models_and_the_global_value(
        self, app_module, monkeypatch
    ):
        models = {REPO: {"max_context_tokens": 5000}, OTHER: {"max_context_tokens": 9000}}
        write(app_module, {"max_context_tokens": 6000, "models": models})
        started = launch(app_module, monkeypatch)
        assert after(started["cmd"], "--studio-max-context") == "6000"
        env = started["env"]
        assert json.loads(env["MLX_STUDIO_MODEL_CONTEXTS"]) == {REPO: 5000, OTHER: 9000}

    def test_an_inherited_map_is_never_passed_on(self, app_module, monkeypatch):
        monkeypatch.setenv("MLX_STUDIO_MODEL_CONTEXTS", '{"a/b": 1}')
        env = launch(app_module, monkeypatch)["env"]
        assert env["MLX_STUDIO_MODEL_CONTEXTS"] == "{}"

    @pytest.mark.parametrize(
        "change",
        [({"max_context_tokens": 7000}, OTHER), ({"max_context_tokens": 7000}, None)],
    )
    def test_a_changed_context_waits_for_apply(self, app_module, change):
        app = app_module
        app.save_settings({"max_context_tokens": 5000}, REPO)
        app.STATE.server_repos = [REPO]
        app.STATE.server_started_with = app._launch_stamp(app.server_settings([REPO]))
        view = app._resident_view(None)
        assert app._settings_pending(True, view) is False
        app.save_settings(*change)
        assert app._settings_pending(True, view) is True


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
        assert app_module._launch_stamp(app_module.server_settings([REPO])) == "{}"


LOAD_TEST = {"sizes": [4096, 8192], "reply_tokens": 128}


def saved(app):
    return json.loads(app._settings_file().read_text())


class TestLoadTestBlock:
    def test_the_defaults(self, app_module):
        view = app_module.load_test_view()
        values = {key: entry["value"] for key, entry in view.items()}
        assert values == {
            "sizes": [4096, 8192, 16384, 32768],
            "last_sizes": [],
            "reply_tokens": 256,
            "rounds": 2,
            "margin_percent": 5,
            "timeout_seconds": 900,
        }
        assert (view["margin_percent"]["min"], view["margin_percent"]["max"]) == (5, 20)
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
        write(app_module, {"chat_top_p": 0.5, "memory_limit_percent": 70, "models": models})
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
        assert keys["memory_limit_percent"]["global_only"] is True
        assert keys["memory_limit_percent"]["own"] is None
        assert keys["memory_limit_percent"]["inherited"] == 70
        assert body["load_test"]["rounds"]["value"] == 2

    def test_without_a_model_the_global_values_are_its_own(self, app_module, base_url):
        write(app_module, {"prompt_cache_size": 3})
        body = get(base_url, "/api/config")[1]
        entry = body["settings"]["prompt_cache_size"]
        assert body["repo"] is None
        assert (entry["own"], entry["inherited"], entry["value"]) == (3, None, 3)

    def test_the_applied_context_is_shown(self, app_module, base_url, monkeypatch):
        app = app_module
        engine = app._guard_module()
        monkeypatch.setattr(engine, "memory_ceiling", lambda percent=None: room_for(engine, 6000))
        monkeypatch.setattr(app, "_server_models", lambda repos: [(REPO, BIG, 2)])
        app.save_settings({"max_context_tokens": 7000}, REPO)
        body = get(base_url, f"/api/config?repo={REPO}")[1]
        assert body["applied_context"] == 6000
        assert "6000 tokens apply" in body["context_message"]

    def test_a_bad_repo(self, base_url):
        assert get(base_url, "/api/config?repo=a%20b")[0] == 400


class TestDocsSayTheLimit:
    def test_sampling_cannot_guarantee_80_percent(self):
        text = (ROOT / "docs" / "API.md").read_text("utf-8")
        assert "cannot guarantee" in text
        for route in ("/api/config", "/api/loadtest/start", "/api/loadtest/apply"):
            assert route in text
