"""Performance mode (settings, server flags, guard) and the Chat reply stats.

Everything is fake: no model is loaded and no server is started.
"""

from __future__ import annotations

import ast
import importlib.util
import json
import types
from pathlib import Path

import pytest
import test_row838_prompt_limit as shared
from test_row838_settings import OTHER, REPO, write

guard = shared.guard
ENV = "MLX_STUDIO_MODEL_PERFORMANCE"
SAVE = {"kv_bits": 8, "prefill_step_size": 256}
FLAGS = ("--kv-bits", "--kv-group-size", "--prefill-step-size")
PLAIN = {"num_hidden_layers": 2, "num_attention_heads": 4, "hidden_size": 512}
SLIDING = {**PLAIN, "model_type": "gemma3_text"}
ODD_HEAD = {**PLAIN, "head_dim": 96}


def after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


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
        plain_stamp = app_module._launch_stamp(app_module.server_settings([REPO]))
        write(app_module, saved)
        assert not set(FLAGS) & set(command(app_module))
        assert app_module._launch_stamp(app_module.server_settings([REPO])) == plain_stamp

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
        assert app_module._model_performance([REPO]) == {REPO: {}}
        stamp = json.loads(app_module._launch_stamp(app_module.server_settings([REPO])))
        assert stamp["performance"] == SAVE
        assert stamp["model_performance"] == {REPO: {}}

    def test_min_merge_leaves_the_choices_alone(self, app_module, configs):
        write(app_module, {"models": {REPO: {"performance_mode": "save_memory"}}})
        merged = app_module.server_settings([REPO, OTHER])
        assert merged["performance_mode"] is None

    def test_environment_carries_each_models_flags(self, app_module):
        env = app_module._server_env("{}", Path("s"), Path("r"), {}, {}, {REPO: SAVE})
        assert json.loads(env[ENV]) == {REPO: SAVE}
        assert json.loads(app_module._server_env("{}", Path("s"), Path("r"), {}, {})[ENV]) == {}

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
        assert app_module._model_performance([REPO]) == {REPO: {}}
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
            ({}, False),  # unread: the guard looks again once the model is loaded
        ],
    )
    def test_from_the_config(self, guard, config, blocked):
        assert bool(guard.kv_problem(config)) is blocked


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

    def test_in_force_is_what_the_server_reports(self, app_module, configs, monkeypatch):
        reported = {"performance": {REPO: {**SAVE, "note": None}}}
        monkeypatch.setattr(app_module, "_read_resident", lambda _: reported)
        assert app_module.performance_in_force(REPO) == "Save memory"
        assert app_module.performance_in_force(OTHER) is None  # not loaded yet
        write(app_module, {"performance_mode": "standard"})
        view = app_module._performance_view([REPO, OTHER], 1)
        assert view["saved"] == "Standard"
        assert view["models"][REPO] == {
            "saved": "Standard",
            "in_force": "Save memory",
            "note": None,
        }
        assert view["models"][OTHER]["in_force"] is None

    @pytest.mark.parametrize("reported", [{}, {"performance": []}, {"performance": {REPO: 3}}])
    def test_unknown_stays_unknown(self, app_module, monkeypatch, reported):
        monkeypatch.setattr(app_module, "_read_resident", lambda _: reported)
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


# The guard ----------------------------------------------------------------- #
class KVCache:
    pass


class RotatingKVCache:
    pass


def fake_server(caches=None):
    cli = types.SimpleNamespace(kv_bits=None, kv_group_size=64, prefill_step_size=2048)

    class ModelProvider:
        def __init__(self):
            self.cli_args, self.model_key, self.model = cli, None, None
            self._model_map = {"default_model": REPO}

        def _load(self, model_path, adapter_path=None, draft_model_path=None):
            self.model_key, self.model = (model_path, adapter_path, draft_model_path), model_path

    class ResponseGenerator:
        def __init__(self, provider):
            self.model_provider, self.cli_args = provider, provider.cli_args

        def _is_batchable(self, args):
            return args.seed is None and self.cli_args.kv_bits is None

    return types.SimpleNamespace(
        ModelProvider=ModelProvider,
        ResponseGenerator=ResponseGenerator,
        make_prompt_cache=lambda model: (caches or {}).get(model, [KVCache()]),
    )


def request(model):
    return types.SimpleNamespace(seed=None, model=types.SimpleNamespace(model=model))


def installed(guard, flags, configs=None, caches=None, cli=None):
    server, seen = fake_server(caches), {}
    guard.read_config = lambda path: (configs or {}).get(str(path), PLAIN)
    assert guard.install_performance(server, lambda **f: seen.update(f), flags, Path) is None
    provider = server.ModelProvider()
    vars(provider.cli_args).update(cli or {})
    return provider, server.ResponseGenerator(provider), seen


class TestGuard:
    def test_each_model_gets_its_own_flags(self, guard):
        provider, _, seen = installed(guard, {OTHER: SAVE})
        provider._load(REPO)
        assert (provider.cli_args.kv_bits, provider.cli_args.prefill_step_size) == (None, 2048)
        provider._load(OTHER)
        assert (provider.cli_args.kv_bits, provider.cli_args.prefill_step_size) == (8, 256)
        assert provider.cli_args.kv_group_size == 64
        provider._load(REPO)
        assert (provider.cli_args.kv_bits, provider.cli_args.prefill_step_size) == (None, 2048)
        assert seen["performance"] == {REPO: {"note": None}, OTHER: {**SAVE, "note": None}}

    def test_the_command_line_is_the_default(self, guard):
        provider, _, seen = installed(guard, {OTHER: {}}, cli=SAVE)
        provider._load(OTHER)
        assert (provider.cli_args.kv_bits, provider.cli_args.prefill_step_size) == (None, 2048)
        provider._load(REPO)
        assert (provider.cli_args.kv_bits, provider.cli_args.prefill_step_size) == (8, 256)
        assert seen["performance"][REPO] == {**SAVE, "note": None}

    @pytest.mark.parametrize(
        ("configs", "caches"),
        [({REPO: SLIDING}, None), (None, {REPO: [KVCache(), RotatingKVCache()]})],
    )
    def test_a_model_that_cannot_runs_standard_and_says_why(self, guard, capsys, configs, caches):
        provider, _, seen = installed(guard, {}, configs, caches, cli=SAVE)
        provider._load(REPO)
        assert (provider.cli_args.kv_bits, provider.cli_args.prefill_step_size) == (None, 2048)
        assert "sliding window" in seen["performance"][REPO]["note"]
        assert "Standard" in capsys.readouterr().err

    def test_the_first_request_after_a_switch_is_not_batched(self, guard):
        provider, generator, _ = installed(guard, {OTHER: SAVE})
        provider._load(REPO)
        assert generator._is_batchable(request("default_model"))
        assert not generator._is_batchable(request(OTHER))  # before it is loaded
        provider._load(OTHER)
        assert not generator._is_batchable(request(OTHER))

    def test_a_changed_server_is_said(self, guard):
        server = types.SimpleNamespace()
        assert "changed" in guard.install_performance(server, None, {}, Path)

    @pytest.mark.parametrize(
        ("text", "kept"),
        [
            (None, {}),
            ("not json", {}),
            ("[]", {}),
            (json.dumps({REPO: SAVE, "bad id": SAVE}), {REPO: SAVE}),
            (json.dumps({REPO: {}}), {REPO: {}}),
            (json.dumps({REPO: {"kv_bits": 5}}), {}),
            (json.dumps({REPO: {"prefill_step_size": 0}}), {}),
            (json.dumps({REPO: {"other": 1}}), {}),
        ],
    )
    def test_environment_is_checked(self, guard, text, kept):
        assert guard.read_model_performance(text) == kept

    def test_the_app_and_guard_agree(self, app_module, guard):
        assert app_module.SETTINGS["prefill_step_size"] == guard.PREFILL_RANGE
        assert app_module.CHOICE_SETTINGS["kv_bits"] == guard.KV_BITS
        assert app_module.CHOICE_SETTINGS["kv_group_size"] == guard.KV_GROUPS

    def test_installed_mlx_lm_still_has_what_is_used(self):
        """Reads mlx_lm's source: the names and defaults the guard relies on."""
        root = Path(importlib.util.find_spec("mlx_lm").origin).parent
        source = (root / "server.py").read_text("utf-8")
        methods = {
            (node.name, item.name)
            for node in ast.parse(source).body
            if isinstance(node, ast.ClassDef)
            for item in node.body
            if isinstance(item, ast.FunctionDef)
        }
        assert {("ModelProvider", "_load"), ("ResponseGenerator", "_is_batchable")} <= methods
        assert "return self.model_provider.cli_args" in source
        assert "and self.cli_args.kv_bits is None" in source
        for flag in FLAGS:
            assert f'"{flag}"' in source
        assert "default=2048" in source and "make_prompt_cache" in source
