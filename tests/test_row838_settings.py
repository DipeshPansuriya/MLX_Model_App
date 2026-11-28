"""User settings in ``~/.mlx_studio/settings.json`` (row 838).

Every key is optional and an absent one keeps the built-in rule. No server process
is started and no model is loaded: the launch command is read from a fake Popen.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.request

import pytest
from test_ui_behaviour import needs_node, run

KEYS = {
    "max_context_tokens": (4096, 100_000_000),
    "server_max_reply_tokens": (64, 131072),
    "chat_max_reply_tokens": (64, 131072),
    "prompt_cache_size": (1, 100),
    "prompt_cache_bytes": (100_000_000, 100_000_000_000),
    "chat_temperature": (0, 2),
    "chat_top_p": (0.05, 1),
}
# Performance mode and its raw flags are not merged by "lower value wins": their values,
# merge and docs are tested in test_row_perfmode.py. Here: (first, last) of each.
PERFORMANCE_KEYS = {
    "performance_mode": ("standard", "advanced"),
    "kv_bits": (4, 8),
    "kv_group_size": (32, 128),
    "prefill_step_size": (64, 2048),
}
ALL_KEYS = {**KEYS, **PERFORMANCE_KEYS, "server_reasoning": ("on", "off")}
GB = 10**9
REPO, OTHER = "org/seven", "org/other"


def write(app, text):
    app.APP_DIR.mkdir(parents=True, exist_ok=True)
    app._settings_file().write_text(text if isinstance(text, str) else json.dumps(text), "utf-8")


def chosen(app):
    return {key: value for key, value in app.user_settings().items() if value is not None}


def complaints(app):
    if not app.APP_LOG.exists():
        return []
    return [line for line in app.APP_LOG.read_text("utf-8").splitlines() if "settings" in line]


class TestReading:
    def test_the_keys_and_ranges(self, app_module):
        assert {k: (s[0], s[1]) for k, s in app_module.SETTINGS.items()} == ALL_KEYS

    def test_no_file_means_every_key_is_automatic_and_nothing_is_logged(self, app_module):
        assert app_module.user_settings() == dict.fromkeys(ALL_KEYS)
        assert complaints(app_module) == []

    @pytest.mark.parametrize("key", KEYS)
    def test_both_ends_of_the_range_are_read(self, app_module, key):
        for value in KEYS[key]:
            write(app_module, {key: value})
            assert chosen(app_module) == {key: value}

    @pytest.mark.parametrize("key", KEYS)
    @pytest.mark.parametrize("bad", ["low", "high", True, "5000", 5000.5, [1]])
    def test_a_bad_value_falls_back_alone(self, app_module, key, bad):
        low, high = KEYS[key]
        value = {"low": low - 1, "high": high + 1}.get(str(bad), bad)
        other = "chat_max_reply_tokens" if key != "chat_max_reply_tokens" else "prompt_cache_size"
        write(app_module, {key: value, other: KEYS[other][0]})
        assert chosen(app_module) == {other: KEYS[other][0]}

    def test_a_bad_value_is_logged_once(self, app_module):
        write(app_module, {"max_context_tokens": 95})
        for _ in range(3):
            app_module.user_settings()
        assert len(complaints(app_module)) == 1
        assert "max_context_tokens" in complaints(app_module)[0]

    def test_null_and_unknown_keys_are_quietly_automatic(self, app_module):
        write(app_module, {"max_context_tokens": None, "port": 1})
        assert chosen(app_module) == {}
        assert complaints(app_module) == []

    def test_a_removed_setting_in_an_old_file_is_quietly_ignored(self, app_module):
        old = {"memory_limit_percent": 70, "two_model_context_tokens": 9000}
        write(app_module, {**old, "mlx_cache_limit_mb": 1, "models": {REPO: old}})
        assert chosen(app_module) == {}
        assert app_module.user_settings(REPO) == dict.fromkeys(ALL_KEYS)
        assert complaints(app_module) == []

    @pytest.mark.parametrize(
        "text",
        ["not json", "[1]", '{"prompt_cache_size": ' + " " * 70000 + "2}", "[" * 4000, "\udcff"],
    )
    def test_a_bad_file_is_ignored_and_logged_once(self, app_module, text):
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module._settings_file().write_bytes(text.encode("utf-8", "surrogateescape"))
        assert chosen(app_module) == {}
        assert chosen(app_module) == {}
        assert len(complaints(app_module)) == 1

    def test_a_link_is_not_followed(self, app_module, tmp_path):
        real = tmp_path / "elsewhere.json"
        real.write_text('{"prompt_cache_size": 2}', "utf-8")
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module._settings_file().symlink_to(real)
        assert chosen(app_module) == {}

    @pytest.mark.parametrize("make", [os.mkfifo, os.mkdir])
    def test_only_a_regular_file_is_read(self, app_module, make):
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        make(app_module._settings_file())
        assert chosen(app_module) == {}


def post(base, body, **headers):
    request = urllib.request.Request(
        f"{base}/api/server/settings",
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", **headers},
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


class TestSaving:
    def test_saved_privately_and_merged_and_cleared(self, app_module, base_url):
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        assert post(base_url, {"values": {"max_context_tokens": 5000}})[0] == 200
        assert post(base_url, {"values": {"prompt_cache_size": 3}})[0] == 200
        assert chosen(app_module) == {"max_context_tokens": 5000, "prompt_cache_size": 3}
        mode = stat.S_IMODE(app_module._settings_file().stat().st_mode)
        assert mode == 0o600
        assert post(base_url, {"values": {"max_context_tokens": None}})[0] == 200
        assert json.loads(app_module._settings_file().read_text()) == {"prompt_cache_size": 3}

    def test_reset_removes_the_file(self, app_module, base_url):
        write(app_module, {"prompt_cache_size": 3})
        assert post(base_url, {"reset": True})[0] == 200
        assert not app_module._settings_file().exists()
        assert post(base_url, {"reset": True})[0] == 200

    @pytest.mark.parametrize("key", KEYS)
    def test_a_value_out_of_range_is_refused_and_nothing_is_written(
        self, app_module, base_url, key
    ):
        low, high = KEYS[key]
        for value in (low - 1, high + 1, True, "x"):
            status, body = post(base_url, {"values": {key: value}})
            assert status == 400
            assert str(low) in body["error"] and str(high) in body["error"]
        assert not app_module._settings_file().exists()

    @pytest.mark.parametrize("body", [{}, {"values": {}}, {"values": []}, {"values": {"port": 1}}])
    def test_anything_else_is_refused(self, app_module, base_url, body):
        assert post(base_url, body)[0] == 400
        assert not app_module._settings_file().exists()

    def test_writing_replaces_a_link_and_leaves_its_target(self, app_module, base_url, tmp_path):
        real = tmp_path / "elsewhere.json"
        real.write_text("keep", "utf-8")
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module._settings_file().symlink_to(real)
        assert post(base_url, {"values": {"prompt_cache_size": 3}})[0] == 200
        assert real.read_text() == "keep"
        assert not app_module._settings_file().is_symlink()

    def test_a_file_that_cannot_be_written_is_an_error(self, app_module, base_url):
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        app_module._settings_file().mkdir()
        status, body = post(base_url, {"values": {"prompt_cache_size": 3}})
        assert status == 500 and body["error"]

    def test_the_token_and_origin_checks_apply(self, app_module, server):
        base = "http://{}:{}".format(*server.server_address[:2])
        values = {"values": {"prompt_cache_size": 3}}
        assert post(base, values)[0] == 403
        token = {"X-MLX-Token": app_module.API_TOKEN}
        assert post(base, values, Origin="https://evil.example", **token)[0] == 403
        assert not app_module._settings_file().exists()


def launch(app, monkeypatch, repo=REPO):
    """The command and environment a start would use; nothing is started."""
    seen = {}

    def fake_popen(cmd, **kwargs):
        seen.update(cmd=cmd, env=kwargs.get("env"))
        raise OSError("not started in tests")

    monkeypatch.setattr(app, "stop_server", lambda *a, **k: None)
    monkeypatch.setattr(app.subprocess, "Popen", fake_popen)
    app.LOG_DIR.mkdir(parents=True, exist_ok=True)
    app.worker_server_start(repo, False)
    return seen


def after(cmd, flag):
    assert cmd.count(flag) <= 1
    return cmd[cmd.index(flag) + 1] if flag in cmd else None


CACHE_FLAGS = {
    "prompt_cache_size": ("--prompt-cache-size", 3),
    "prompt_cache_bytes": ("--prompt-cache-bytes", 2 * GB),
}


class TestLaunch:
    def test_with_nothing_set_only_the_reply_default_is_passed(self, app_module, monkeypatch):
        cmd = launch(app_module, monkeypatch)["cmd"]
        assert after(cmd, "--max-tokens") == "8192"
        assert not [flag for flag, _ in CACHE_FLAGS.values() if flag in cmd]
        assert not [flag for flag in cmd if flag.startswith("--studio")]

    @pytest.mark.parametrize("key", CACHE_FLAGS)
    def test_a_set_key_adds_its_flag_only(self, app_module, monkeypatch, key):
        flag, value = CACHE_FLAGS[key]
        write(app_module, {key: value})
        cmd = launch(app_module, monkeypatch)["cmd"]
        assert after(cmd, flag) == str(value)
        assert [f for f, _ in CACHE_FLAGS.values() if f in cmd] == [flag]

    def test_the_server_reply_cap(self, app_module, monkeypatch):
        write(app_module, {"server_max_reply_tokens": 1000})
        assert after(launch(app_module, monkeypatch)["cmd"], "--max-tokens") == "1000"

    def test_the_start_records_only_what_its_command_was_given(self, app_module, monkeypatch):
        settings = {
            "prompt_cache_size": 3,
            "chat_max_reply_tokens": 100,
            "max_context_tokens": 5000,
        }
        write(app_module, settings)
        env = launch(app_module, monkeypatch)["env"]
        assert json.loads(app_module.STATE.server_started_with) == {"prompt_cache_size": 3}
        assert not [name for name in env if name.startswith("MLX_STUDIO")]
        assert env["HF_HUB_OFFLINE"] == "1"


class TestChatReplyCap:
    @pytest.mark.parametrize(("value", "sent"), [(None, 2048), (100, 100)])
    def test_the_chat_request(self, app_module, value, sent):
        if value:
            write(app_module, {"chat_max_reply_tokens": value})
        assert app_module.chat_settings()["max_tokens"] == sent
        assert app_module.CHAT_SETTINGS["max_tokens"] == 2048
        assert "chat_settings" in app_module.stream_chat.__code__.co_names


class TestSnapshot:
    def test_each_key_says_its_range_and_effective_value(self, app_module):
        write(app_module, {"prompt_cache_size": 3})
        shown = app_module.STATE.snapshot()["server"]["settings"]
        assert set(shown) == set(ALL_KEYS)
        assert shown["prompt_cache_size"] == {
            "value": 3, "min": 1, "max": 100, "effective": "3",
        }  # fmt: skip
        assert shown["server_max_reply_tokens"]["effective"] == "automatic (8192)"
        assert shown["chat_max_reply_tokens"]["effective"] == "automatic (2048)"
        assert shown["max_context_tokens"]["effective"] == (
            "automatic (OptiQ: its own default. MLX: not available)"
        )
        assert shown["prompt_cache_bytes"]["value"] is None

    def pending(self, app, started):
        app.STATE.server_repos, app.STATE.server_started_with = [REPO], started
        return app._settings_pending(True)

    def test_a_changed_launch_setting_waits_for_apply(self, app_module):
        assert self.pending(app_module, "{}") is False
        write(app_module, {"prompt_cache_size": 3})
        assert self.pending(app_module, "{}") is True
        assert self.pending(app_module, '{"prompt_cache_size": 3}') is False
        # An adopted server with no recorded settings started with none.
        assert self.pending(app_module, None) is True
        assert app_module._settings_pending(False) is False

    def test_the_chat_cap_needs_no_restart(self, app_module):
        write(app_module, {"chat_max_reply_tokens": 100})
        assert self.pending(app_module, "{}") is False


@needs_node
class TestCard:
    """The settings fields are on the Configuration page now (tests/test_row848_ui.py)."""

    def test_a_saved_change_waits_for_apply(self, tmp_path):
        out = run(
            tmp_path,
            """
            const server = { running: true, repos: ["org/custom"], repo: "org/custom",
              endpoint: "e", lines: [], tool_support: {}, ready: true, background: false,
              settings_pending: false };
            render(snap({ server }));
            const first = serverPending();
            render(snap({ server: { ...server, settings_pending: true } }));
            return [first, serverPending()];
            """,
        )
        assert out == [False, True]

    def test_every_key_has_a_labelled_field(self):
        from pathlib import Path

        page = (Path(__file__).resolve().parent.parent / "ui" / "index.html").read_text("utf-8")
        for key in KEYS:
            assert f'for="cfg-{key}"' in page
            assert f'id="cfg-{key}"' in page
        assert 'id="configResetBtn"' in page


class TestDocs:
    def test_every_key_range_is_in_the_table(self):
        from pathlib import Path

        text = (Path(__file__).resolve().parent.parent / "docs" / "API.md").read_text("utf-8")
        for key, (low, high) in KEYS.items():
            row = next(line for line in text.splitlines() if line.startswith(f"| `{key}`"))
            assert f"{low} to {high}" in row


class TestPerModel:
    @pytest.mark.parametrize("key", KEYS)
    def test_the_model_value_beats_the_global_one(self, app_module, key):
        low, high = KEYS[key]
        write(app_module, {key: low, "models": {REPO: {key: high}}})
        assert app_module.user_settings(REPO)[key] == high
        assert app_module.user_settings(OTHER)[key] == low
        assert app_module.user_settings()[key] == low

    def test_a_bad_model_value_falls_back_to_the_global_one_and_is_logged_once(self, app_module):
        write(app_module, {"prompt_cache_size": 2, "models": {REPO: {"prompt_cache_size": 0}}})
        for repo in (REPO, OTHER, None, REPO):
            assert app_module.user_settings(repo)["prompt_cache_size"] == 2
        assert len(complaints(app_module)) == 1

    @pytest.mark.parametrize("models", [[1], {"a b": {}}, {REPO: 5}])
    def test_a_bad_models_part_is_ignored(self, app_module, models):
        write(app_module, {"prompt_cache_size": 2, "models": models})
        assert app_module.user_settings(REPO)["prompt_cache_size"] == 2
        assert len(complaints(app_module)) == 1

    def test_saving_clearing_and_reset(self, app_module, base_url):
        def saved():
            return json.loads(app_module._settings_file().read_text())

        mine = {"repo": REPO, "values": {"prompt_cache_size": 5}}
        assert post(base_url, mine)[0] == 200
        assert post(base_url, {"values": {"prompt_cache_size": 2}})[0] == 200
        assert saved() == {"prompt_cache_size": 2, "models": {REPO: {"prompt_cache_size": 5}}}
        # The card's reset clears the global values and keeps the models' own.
        assert post(base_url, {"reset": True})[0] == 200
        assert saved() == {"models": {REPO: {"prompt_cache_size": 5}}}
        assert post(base_url, {"repo": OTHER, "values": {"prompt_cache_size": 7}})[0] == 200
        assert post(base_url, {"repo": REPO, "reset": True})[0] == 200
        assert saved() == {"models": {OTHER: {"prompt_cache_size": 7}}}
        assert post(base_url, {"repo": OTHER, "values": {"prompt_cache_size": None}})[0] == 200
        assert saved() == {}

    @pytest.mark.parametrize(
        "body",
        [
            {"repo": REPO, "values": {"memory_limit_percent": 40}},  # removed setting
            {"repo": REPO, "values": {"prompt_cache_size": 0}},
            {"repo": "a b", "values": {"prompt_cache_size": 2}},
            {"repo": 5, "values": {"prompt_cache_size": 2}},
            {"repo": "", "reset": True},
        ],
    )
    def test_refused(self, app_module, base_url, body):
        assert post(base_url, body)[0] == 400
        assert not app_module._settings_file().exists()

    def test_a_file_that_would_grow_too_large_is_refused(self, app_module, base_url, monkeypatch):
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(app_module, "SETTINGS_MAX_BYTES", 40)
        status, body = post(base_url, {"repo": REPO, "values": {"prompt_cache_bytes": 10**9}})
        assert status == 400 and "too many" in body["error"]
        assert not app_module._settings_file().exists()

    def test_a_model_starts_with_its_own_values(self, app_module, monkeypatch):
        models = {REPO: {"prompt_cache_size": 5}, OTHER: {"server_max_reply_tokens": 99}}
        write(app_module, {"prompt_cache_size": 3, "models": models})
        cmd = launch(app_module, monkeypatch)["cmd"]
        assert after(cmd, "--prompt-cache-size") == "5"
        assert after(cmd, "--max-tokens") == "8192"
        cmd = launch(app_module, monkeypatch, OTHER)["cmd"]
        assert after(cmd, "--prompt-cache-size") == "3"
        assert after(cmd, "--max-tokens") == "99"

    def test_chat_uses_the_models_value(self, app_module):
        write(app_module, {"models": {REPO: {"chat_max_reply_tokens": 100}}})
        assert app_module.chat_settings(REPO)["max_tokens"] == 100
        assert app_module.chat_settings(OTHER)["max_tokens"] == 2048

    def test_only_a_served_models_change_waits_for_apply(self, app_module):
        app = app_module
        app.STATE.server_repos, app.STATE.server_started_with = [REPO], "{}"
        write(app, {"models": {OTHER: {"prompt_cache_size": 3}}})
        assert app._settings_pending(True) is False
        write(app, {"models": {REPO: {"prompt_cache_size": 3}}})
        assert app._settings_pending(True) is True

    def test_the_snapshot_lists_the_overrides(self, app_module):
        write(app_module, {"models": {REPO: {"prompt_cache_size": 3}}})
        shown = app_module.STATE.snapshot()["server"]["model_settings"]
        assert shown == {REPO: {"prompt_cache_size": 3}}
