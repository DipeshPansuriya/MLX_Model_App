"""Performance mode (settings, server flags, guard) and the Chat reply stats.

Everything is fake: no model is loaded and no server is started.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest
from test_row838_settings import OTHER, REPO, write

SAVE = {"kv_bits": 8, "prefill_step_size": 256}
FLAGS = ("--kv-bits", "--kv-group-size", "--prefill-step-size")
PLAIN = {"num_hidden_layers": 2, "num_attention_heads": 4, "hidden_size": 512}
SLIDING = {**PLAIN, "model_type": "gemma3_text"}
ODD_HEAD = {**PLAIN, "head_dim": 96}


def after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


def stamp(app_module, repo=REPO):
    settings = app_module.server_settings([repo])
    return app_module._launch_stamp(app_module.engines.MLX, settings, repo)


def command(app_module, repo=REPO):
    settings = app_module.server_settings([repo])
    flags = app_module.performance_flags(repo)[0]
    return app_module._server_command(repo, settings, 18123, performance=flags)


@pytest.fixture
def configs(app_module, monkeypatch):
    """What each model's config.json says."""
    found = {}
    monkeypatch.setattr(app_module, "_model_config", lambda repo: found.get(repo, PLAIN))
    return found


# Settings ------------------------------------------------------------------ #
class TestSettings:
    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("performance_mode", "standard"),
            ("performance_mode", "save_memory"),
            ("performance_mode", "advanced"),
            ("kv_bits", 4),
            ("kv_bits", 8),
            ("kv_group_size", 32),
            ("kv_group_size", 128),
            ("prefill_step_size", 64),
            ("prefill_step_size", 2048),
        ],
    )
    def test_accepted(self, app_module, configs, key, value):
        assert app_module._settings_change_problem({"values": {key: value}}) is None

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("performance_mode", "fast"),
            ("performance_mode", 1),
            ("kv_bits", 5),
            ("kv_bits", 2),
            ("kv_bits", "8"),
            ("kv_bits", 8.0),
            ("kv_group_size", 16),
            ("kv_group_size", True),
            ("prefill_step_size", 63),
            ("prefill_step_size", 2049),  # never above mlx_lm's own: it needs more memory
        ],
    )
    def test_refused_in_plain_words(self, app_module, configs, key, value):
        problem = app_module._settings_change_problem({"values": {key: value}})
        assert problem.startswith(f"{key} must be")

    @pytest.mark.parametrize(
        ("key", "value"),
        [
            ("performance_mode", "turbo"),
            ("kv_bits", 6),
            ("kv_group_size", "64"),
            ("prefill_step_size", 5000),
        ],
    )
    def test_a_bad_saved_value_falls_back_alone(self, app_module, key, value):
        write(app_module, {key: value, "prompt_cache_size": 3})
        read = app_module.user_settings()
        assert read[key] is None and read["prompt_cache_size"] == 3

    def test_both_ends_of_the_read_step_are_read_and_documented(self, app_module):
        for value in (64, 2048):
            write(app_module, {"prefill_step_size": value})
            assert app_module.user_settings()["prefill_step_size"] == value
        text = (Path(__file__).resolve().parent.parent / "docs" / "API.md").read_text("utf-8")
        for key in ("performance_mode", "kv_bits", "kv_group_size", "prefill_step_size"):
            row = next(line for line in text.splitlines() if line.startswith(f"| `{key}` |"))
            assert key != "prefill_step_size" or "64 to 2048" in row

    def test_view_lists_choices(self, app_module, configs):
        view = app_module.config_view()
        assert view["settings"]["performance_mode"]["choices"] == [
            "standard",
            "save_memory",
            "advanced",
        ]
        assert view["settings"]["kv_bits"]["choices"] == [4, 8]
        assert view["performance"] == {"mode": "standard", "reason": None, "blocked": {}}


class TestFlags:
    @pytest.mark.parametrize("saved", [{}, {"performance_mode": "standard", "kv_bits": 4}])
    def test_standard_adds_nothing(self, app_module, configs, saved):
        plain_stamp = stamp(app_module)
        write(app_module, saved)
        assert not set(FLAGS) & set(command(app_module))
        assert stamp(app_module) == plain_stamp

    def test_save_memory(self, app_module, configs):
        before = command(app_module)
        write(app_module, {"performance_mode": "save_memory", "kv_bits": 4})
        cmd = command(app_module)
        assert after(cmd, "--kv-bits") == "8"
        assert after(cmd, "--prefill-step-size") == "256"
        assert "--kv-group-size" not in cmd
        # Nothing else changes: the memory limit is not raised for a compressed mode.
        assert cmd == [*before, "--kv-bits", "8", "--prefill-step-size", "256"]

    def test_advanced_passes_only_what_is_set(self, app_module, configs):
        write(app_module, {"performance_mode": "advanced", "kv_group_size": 32, "kv_bits": 4})
        cmd = command(app_module)
        assert (after(cmd, "--kv-bits"), after(cmd, "--kv-group-size")) == ("4", "32")
        assert "--prefill-step-size" not in cmd

    def test_a_models_own_choice_wins(self, app_module, configs):
        write(
            app_module,
            {
                "performance_mode": "save_memory",
                "models": {REPO: {"performance_mode": "standard"}},
            },
        )
        assert app_module.performance_flags(REPO) == ({}, None)
        assert app_module.performance_flags(OTHER) == (SAVE, None)
        assert not set(FLAGS) & set(command(app_module))
        assert after(command(app_module, OTHER), "--kv-bits") == "8"
        assert "performance" not in json.loads(stamp(app_module))
        assert json.loads(stamp(app_module, OTHER))["performance"] == SAVE

    def test_server_settings_leaves_the_choices_to_the_flags(self, app_module, configs):
        write(app_module, {"models": {REPO: {"performance_mode": "save_memory"}}})
        assert app_module.server_settings([REPO])["performance_mode"] is None

    def test_fingerprint_changes_only_with_flags(self, app_module, configs, monkeypatch):
        monkeypatch.setattr(app_module, "_snapshot_dir", lambda _: Path("rev1"))
        standard = app_module._loadtest_fingerprint(REPO)
        assert not set(standard["settings"]) & {"performance_mode", *SAVE, "kv_group_size"}
        write(app_module, {"performance_mode": "save_memory"})
        saving = app_module._loadtest_fingerprint(REPO)["settings"]
        assert saving == {**standard["settings"], **SAVE}


class TestBlocked:
    @pytest.mark.parametrize(
        ("config", "words"), [(SLIDING, "sliding window"), (ODD_HEAD, "groups of 64")]
    )
    def test_a_model_that_cannot_stays_standard(self, app_module, configs, config, words):
        configs[REPO] = config
        write(app_module, {"performance_mode": "save_memory"})
        flags, reason = app_module.performance_flags(REPO)
        assert flags == {} and words in reason
        assert not set(FLAGS) & set(command(app_module))
        view = app_module.config_view(REPO)["performance"]
        assert words in view["blocked"]["save_memory"] and words in view["reason"]

    def test_saving_it_for_that_model_is_refused(self, app_module, configs):
        configs[REPO] = SLIDING
        for values in (
            {"performance_mode": "save_memory"},
            {"performance_mode": "advanced", "kv_bits": 8},
        ):
            problem = app_module._settings_change_problem({"repo": REPO, "values": values})
            assert "sliding window" in problem and "Standard" in problem
        for values in ({"performance_mode": "standard"}, {"performance_mode": None}):
            assert app_module._settings_change_problem({"repo": REPO, "values": values}) is None

    def test_a_smaller_group_is_allowed_where_it_fits(self, app_module, configs):
        configs[REPO] = ODD_HEAD
        view = app_module.config_view(REPO)["performance"]
        assert set(view["blocked"]["kv_group_size"]) == {"64", "128"}
        values = {"performance_mode": "advanced", "kv_bits": 8, "kv_group_size": 32}
        assert app_module._settings_change_problem({"repo": REPO, "values": values}) is None

    @pytest.mark.parametrize(
        ("config", "blocked"),
        [
            (PLAIN, False),
            # Qwen's own switch is off: nothing slides.
            ({**PLAIN, "sliding_window": 32768, "use_sliding_window": False}, False),
            ({**PLAIN, "sliding_window": None}, False),
            ({**PLAIN, "sliding_window": 4096}, True),
            ({**PLAIN, "sliding_window_size": 4096}, True),
            ({"text_config": SLIDING}, True),
            ({"model_type": "gpt_oss", **PLAIN}, True),
            ({**PLAIN, "layer_types": ["sliding_attention", "full_attention"]}, True),
            ({}, False),  # unread: nothing is known, so nothing is blocked
        ],
    )
    def test_from_the_config(self, app_module, config, blocked):
        assert bool(app_module.model_info.kv_problem(config)) is blocked


# What is shown ------------------------------------------------------------- #
class TestShown:
    @pytest.mark.parametrize(
        ("flags", "label"),
        [
            ({}, "Standard"),
            (SAVE, "Save memory"),
            ({"kv_bits": 4}, "Advanced (4-bit memory)"),
            (
                {"kv_bits": 8, "kv_group_size": 32, "prefill_step_size": 512},
                "Advanced (8-bit memory, groups of 32, read step 512)",
            ),
        ],
    )
    def test_labels(self, app_module, flags, label):
        assert app_module.performance_label(flags) == label

    def serving(self, app, started, verdict="ready"):
        app.STATE.server_repos = [REPO]
        app.STATE.server_started_with, app.STATE.server_verdict = started, verdict

    def test_in_force_is_what_the_server_was_started_with(self, app_module, configs):
        write(app_module, {"performance_mode": "save_memory"})
        self.serving(app_module, stamp(app_module))
        assert app_module.performance_in_force(REPO) == "Save memory"
        assert app_module.performance_in_force(OTHER) is None  # not served
        write(app_module, {"performance_mode": "standard"})
        view = app_module._performance_view([REPO, OTHER])
        assert view["saved"] == "Standard"
        assert view["models"][REPO] == {
            "saved": "Standard",
            "in_force": "Save memory",
            "note": None,
        }
        assert view["models"][OTHER]["in_force"] is None

    @pytest.mark.parametrize(
        ("started", "verdict"),
        [(None, "ready"), ("{}", None), ("{}", "failed"), ("not json", "ready"), ("[]", "ready")],
    )
    def test_unknown_stays_unknown(self, app_module, started, verdict):
        self.serving(app_module, started, verdict)
        assert app_module.performance_in_force(REPO) is None

    def test_stats_from_usage_and_the_clock(self, app_module):
        usage = {"prompt_tokens": 812, "completion_tokens": 62}
        assert app_module._reply_stats(usage, 10.0, 11.5, 13.5, "Standard") == {
            "first_s": 1.5,
            "tps": 31.0,
            "prompt_tokens": 812,
            "reply_tokens": 62,
            "mode": "Standard",
        }

    @pytest.mark.parametrize(
        "usage",
        [{}, {"prompt_tokens": "812", "completion_tokens": None}, {"completion_tokens": True}],
    )
    def test_no_figure_is_made_up(self, app_module, usage):
        assert app_module._reply_stats(usage, 10.0, None, 13.5, None) == dict.fromkeys(
            ("first_s", "tps", "prompt_tokens", "reply_tokens", "mode")
        )

    def test_no_speed_from_one_token_or_no_time(self, app_module):
        one = {"completion_tokens": 1}
        assert app_module._reply_stats(one, 0.0, 1.0, 3.0, None)["tps"] is None
        many = {"completion_tokens": 50}
        assert app_module._reply_stats(many, 0.0, 1.0, 1.001, None)["tps"] is None


# mlx_lm's own server -------------------------------------------------------- #
def test_installed_mlx_lm_still_takes_the_flags_passed(app_module, configs):
    """Reads mlx_lm's source: every flag of the command, and the read step default."""
    pytest.importorskip("mlx_lm")  # CI's Linux job installs no MLX; macOS must run it
    root = Path(importlib.util.find_spec("mlx_lm").origin).parent
    source = (root / "server.py").read_text("utf-8")
    write(app_module, {"prompt_cache_size": 2, "prompt_cache_bytes": 10**9})
    passed = {part for part in command(app_module) if part.startswith("--")}
    for flag in {*passed, *FLAGS}:
        assert f'"{flag}"' in source
    assert "default=2048" in source
