"""The MLX cache limit setting (row 853) and the one-model memory formula (row 850).

Everything is fake: no model is loaded and no memory of this Mac is read.
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
import types
from functools import partial
from pathlib import Path

import pytest
import test_row838_prompt_limit as shared
from test_row838_settings import OTHER, REPO, write

# Fixtures and helpers shared with the other settings tests.
BIG, reached, room_for = shared.BIG, shared.reached, shared.room_for
guard, limited = shared.guard, shared.limited
ROOT = Path(__file__).resolve().parent.parent
KEY = "mlx_cache_limit_mb"
FLAG = "--studio-cache-limit-mb"
ENV = "MLX_STUDIO_MODEL_CACHE_LIMITS"

# row-perf-baseline.md and row-perf-24k.md: Apple M6, 32 GB, fp16 cache,
# Qwen2.5-7B-Instruct-1M-4bit. (prompt tokens, server phys_footprint in GB).
MEASURED = [
    (915, 5.62),
    (7_592, 7.65),
    (13_975, 8.84),
    (17_008, 9.70),
    (21_090, 11.16),
    (22_151, 13.72),
    (23_221, 14.09),
    (32_223, 15.36),
]
WEIGHTS = 4_284_346_255
QWEN = {
    "max_position_embeddings": 1_010_000,
    "num_hidden_layers": 28,
    "num_attention_heads": 28,
    "num_key_value_heads": 4,
    "hidden_size": 3584,
}
MAC_RAM = 32 * 2**30


def after(cmd, flag):
    return cmd[cmd.index(flag) + 1]


# The formula --------------------------------------------------------------- #
class TestFormula:
    @pytest.mark.parametrize(("tokens", "server_gb"), MEASURED)
    def test_no_measured_server_is_under_estimated(self, guard, tokens, server_gb):
        per_token = guard.kv_bytes_per_token(QWEN)
        said = WEIGHTS + guard.MEMORY_MARGIN + guard.ONE_MODEL_FACTOR * per_token * tokens
        assert said >= server_gb * 1e9

    @pytest.mark.parametrize(("share", "tokens"), [(0.41, 21_024), (0.50, 12_598)])
    def test_the_automatic_figure_on_the_measured_mac(self, guard, share, tokens):
        ceiling, others = int(0.80 * MAC_RAM), int(share * MAC_RAM)
        budget = int(0.52 * MAC_RAM)
        assert guard.prompt_limit(QWEN, WEIGHTS, budget, None, ceiling, others=others) == tokens

    def test_what_other_apps_hold_comes_off_the_ceiling(self, guard):
        ceiling = room_for(guard, 60_000)
        others = ceiling - room_for(guard, 10_000)
        got = guard.prompt_limit(BIG, 2, ceiling, None, ceiling, others=others)
        assert got == 10_000  # no 32K floor once the other apps' memory is known

    def test_free_memory_may_allow_more_than_the_fixed_share(self, guard):
        got = guard.prompt_limit(
            BIG, 2, room_for(guard, 40_000), None, room_for(guard, 60_000), others=0
        )
        assert got == 60_000

    def test_unreadable_keeps_the_fixed_share_and_floor(self, guard):
        tight, roomy = room_for(guard, 10_000), room_for(guard, 60_000)
        assert guard.prompt_limit(BIG, 2, tight, None, roomy, others=None) == guard.KV_TOKENS
        assert guard.prompt_limit(BIG, 2, roomy, None, roomy * 2, others=None) == 60_000

    @pytest.mark.parametrize(("room", "limit"), [(4096, 4096), (4095, 0), (0, 0)])
    def test_below_4096_is_refused(self, guard, room, limit):
        ceiling = room_for(guard, 60_000)
        others = ceiling - room_for(guard, room)
        assert guard.prompt_limit(BIG, 2, ceiling, None, ceiling, others=others) == limit

    def test_more_used_than_the_ceiling_is_refused(self, guard):
        assert guard.prompt_limit(BIG, 2, 10**10, None, 10**10, others=2 * 10**10) == 0

    def test_below_4096_is_refused_without_a_reading_too(self, guard):
        small = room_for(guard, 3000)
        assert guard.prompt_limit(BIG, 2, small, None, small) == 0

    def test_a_model_smaller_than_4096_is_not_refused(self, guard):
        config = {**BIG, "max_position_embeddings": 2048}
        ceiling = room_for(guard, 3000)
        assert guard.prompt_limit(config, 2, ceiling, None, ceiling, others=0) == 2048

    def test_the_users_figure_leaves_room_for_other_apps(self, guard):
        ceiling = room_for(guard, 6000)
        ask = partial(guard.prompt_limit, BIG, 2, ceiling, None, ceiling)
        assert ask(7000, others=0) == 6000
        assert ask(7000, others=ceiling - room_for(guard, 5000)) == 5000
        assert ask(7000, others=ceiling) == 0

    def test_two_models_are_as_before(self, guard):
        assert guard.prompt_limit(BIG, 2, 10**10, 8192, 10**10, others=10**10) == 8192
        assert guard.REPLY_FACTOR == 3.1


class TestOtherApps:
    def test_the_servers_own_memory_is_taken_off(self, guard, monkeypatch, other_apps):
        monkeypatch.setattr(guard, "ram_bytes", lambda: 1000)
        other_apps.percent, other_apps.footprint = 50.0, 100
        assert guard.other_apps_bytes(42) == 400
        assert other_apps.pids == [42]

    def test_never_below_zero(self, guard, monkeypatch, other_apps):
        monkeypatch.setattr(guard, "ram_bytes", lambda: 1000)
        other_apps.percent, other_apps.footprint = 10.0, 500
        assert guard.other_apps_bytes(None) == 0

    def test_unreadable_is_none(self, guard):
        assert guard.other_apps_bytes(None) is None

    def test_the_server_is_limited_by_it_and_says_why(self, limited, guard, capsys):
        ceiling = room_for(guard, 60_000)
        api = limited(BIG, ceiling, ceiling=ceiling, others=ceiling - room_for(guard, 100))
        status, body = api.send("x")
        assert status == 400
        message = body["error"]["message"]
        for words in ("4096", "Close other apps", "Stop, then Start Server"):
            assert words in message
        assert "Max context" not in message
        err = capsys.readouterr().err
        assert err.count("below 4096") == 1 and "Close other apps" in err

    def test_a_figure_above_4096_is_enforced(self, limited, guard):
        ceiling = room_for(guard, 60_000)
        api = limited(BIG, ceiling, ceiling=ceiling, others=ceiling - room_for(guard, 5000))
        assert reached(api.send("x" * 5000))
        assert api.send("x" * 5001)[0] == 400
        assert api.reports == [{"prompt_limits": {REPO: 5000}}]

    def test_main_reads_it_for_one_model_only(self, guard):
        assert "other_apps_bytes" in guard.main.__code__.co_names

    def test_reading_it_writes_no_bytecode_into_the_bundle(self, tmp_path):
        for name in ("mlx_guard.py", "mlx_loadtest.py"):
            shutil.copy(ROOT / name, tmp_path / name)
        code = (
            "import sys; sys.dont_write_bytecode = True; sys.path.insert(0, sys.argv[1]); "
            "import mlx_guard; sys.dont_write_bytecode = False; "
            "mlx_guard.other_apps_bytes(None); "
            "assert 'mlx_loadtest' in sys.modules; assert not sys.dont_write_bytecode"
        )
        env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
        env.pop("PYTHONPYCACHEPREFIX", None)
        subprocess.run([sys.executable, "-c", code, str(tmp_path)], env=env, check=True)
        assert list(tmp_path.rglob("*.pyc")) == []


class TestTheAppSaysTheSame:
    @pytest.fixture
    def mac(self, app_module, monkeypatch, other_apps):
        guard = app_module._guard_module()
        monkeypatch.setattr(guard, "ram_bytes", lambda: MAC_RAM)
        return other_apps

    def estimate(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "_server_budget", lambda: int(0.52 * MAC_RAM))
        monkeypatch.setattr(app_module, "_server_models", lambda repos: [(REPO, QWEN, WEIGHTS)])
        return app_module.server_memory_estimate([REPO])

    def test_the_context_follows_what_other_apps_hold(self, app_module, monkeypatch, mac):
        mac.percent = 41.0
        got = self.estimate(app_module, monkeypatch)
        assert got["models"][0]["safe_context"] == pytest.approx(21_024, abs=2)
        ceiling = int(0.80 * MAC_RAM)
        assert got["budget_bytes"] == pytest.approx(ceiling - 0.41 * MAC_RAM, abs=2)
        assert got["context_warning"] is None
        assert mac.pids[-1] is None  # no server runs, so nothing is taken off

    def test_a_running_servers_memory_is_not_counted(self, app_module, monkeypatch, mac):
        mac.percent, mac.footprint = 60.0, 5 * 10**9
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: pid == 77)
        app_module.STATE.server_pid = 77
        self.estimate(app_module, monkeypatch)
        assert 77 in mac.pids

    def test_below_4096_is_said(self, app_module, monkeypatch, mac):
        mac.percent = 70.0
        got = self.estimate(app_module, monkeypatch)
        assert got["models"][0]["safe_context"] == 0
        for words in ("4096", "Close other apps", "70%", "Start Server"):
            assert words in got["context_warning"]
        assert "set Max context" not in got["context_warning"]

    @pytest.mark.parametrize(("percent", "applied"), [(41.0, 8192), (70.0, 0)])
    def test_a_set_context_follows_the_same_rule(
        self, app_module, monkeypatch, mac, percent, applied
    ):
        mac.percent = percent
        write(app_module, {"max_context_tokens": 8192})
        got = self.estimate(app_module, monkeypatch)
        assert got["models"][0]["safe_context"] == applied
        assert (got["context_warning"] is None) is bool(applied)

    def test_unreadable_keeps_the_fixed_share(self, app_module, monkeypatch, mac):
        got = self.estimate(app_module, monkeypatch)
        assert got["budget_bytes"] == int(0.52 * MAC_RAM)
        assert got["models"][0]["safe_context"] == pytest.approx(33_195, abs=2)

    def test_one_models_need_uses_the_one_model_factor(self, app_module, monkeypatch, mac):
        got = self.estimate(app_module, monkeypatch)
        guard = app_module._guard_module()
        assert got["reply_bytes"] == int(guard.ONE_MODEL_FACTOR * 57_344 * guard.KV_TOKENS)


# The MLX cache limit ------------------------------------------------------- #
class TestTheSetting:
    def test_the_range_is_the_guards(self, app_module):
        assert app_module.SETTINGS[KEY] == app_module._guard_module().CACHE_LIMIT_MB_RANGE
        assert app_module.SETTINGS[KEY] == (0, 1_000_000)
        assert KEY in app_module.MODEL_SETTINGS

    def test_zero_is_kept(self, app_module):
        write(app_module, {KEY: 0})
        assert app_module.user_settings()[KEY] == 0
        assert app_module.settings_view()[KEY]["effective"] == "0"

    def test_absent_says_mlxs_own(self, app_module):
        assert app_module.settings_view()[KEY]["effective"] == "automatic (MLX default)"

    def command(self, app_module, repo=REPO):
        return app_module._server_command(repo, app_module.server_settings([repo]), 18080)

    def test_absent_passes_no_flag(self, app_module):
        assert FLAG not in self.command(app_module)

    @pytest.mark.parametrize("value", [0, 2000])
    def test_a_set_value_is_passed(self, app_module, value):
        write(app_module, {KEY: value})
        assert after(self.command(app_module), FLAG) == str(value)

    def test_a_models_own_value_wins(self, app_module):
        write(app_module, {KEY: 4000, "models": {REPO: {KEY: 0}}})
        assert after(self.command(app_module), FLAG) == "0"
        assert after(self.command(app_module, OTHER), FLAG) == "4000"

    def test_a_change_needs_apply(self, app_module):
        assert app_module._launch_stamp(app_module.server_settings([REPO])) == "{}"
        write(app_module, {KEY: 0})
        zero = app_module._launch_stamp(app_module.server_settings([REPO]))
        assert json.loads(zero) == {KEY: 0}
        write(app_module, {KEY: 0, "models": {OTHER: {KEY: 7}}})
        assert app_module._launch_stamp(app_module.server_settings([REPO])) != zero

    @pytest.mark.parametrize(
        ("stored", "flag", "own"),
        [
            ({}, None, {}),
            ({"models": {OTHER: {KEY: 7}}}, None, {OTHER: 7}),
            ({KEY: 9, "models": {REPO: {KEY: 0}}}, "9", {REPO: 0}),
        ],
    )
    def test_a_start_sends_the_global_value_and_each_models_own(
        self, app_module, monkeypatch, stored, flag, own
    ):
        write(app_module, stored)
        seen = {}

        def popen(cmd, **kwargs):
            seen.update(cmd=cmd, env=kwargs["env"])
            raise OSError("not started in a test")

        monkeypatch.setattr(app_module.subprocess, "Popen", popen)
        monkeypatch.setattr(app_module, "stop_server", lambda *a, **k: None)
        app_module.worker_server_start(REPO)
        cmd = seen["cmd"]
        assert (after(cmd, FLAG) if FLAG in cmd else None) == flag
        assert json.loads(seen["env"][ENV]) == own

    def test_the_load_test_server_and_its_result_carry_it(self, app_module, tmp_path):
        write(app_module, {"models": {REPO: {KEY: 512}}})
        job, env = app_module._loadtest_job(REPO, [4096], "run1", tmp_path)
        assert after(job["command"], FLAG) == "512"
        assert json.loads(env[ENV]) == {}
        assert FLAG in job["command"][job["command"].index("--max-tokens") :]
        assert app_module._loadtest_fingerprint(REPO)["settings"][KEY] == 512


def fake_server(previous=999, fail=False):
    calls = []

    def set_cache_limit(limit):
        if fail:
            raise AttributeError("no such call")
        calls.append(limit)
        return previous if len(calls) == 1 else calls[-2]

    class ModelProvider:
        def __init__(self):
            self.model_key = None

        def _load(self, repo, *args):
            if repo == "broken":
                self.model_key = None
                raise ValueError(repo)
            self.model_key = (repo, None, None)
            return repo

    return types.SimpleNamespace(
        ModelProvider=ModelProvider,
        mx=types.SimpleNamespace(set_cache_limit=set_cache_limit),
        calls=calls,
    )


class TestGuard:
    @pytest.mark.parametrize("value", ["0", "2000", "1000000"])
    def test_the_flag_is_read_and_not_passed_on(self, guard, value):
        rest, opts = guard.split_studio_args([FLAG, value, "--port", "1"])
        assert rest == ["--port", "1"] and opts.cache_limit_mb == int(value)

    @pytest.mark.parametrize("value", ["-1", "1.5", "big", "", "1000001", "9" * 20, "007"])
    def test_an_unusable_value_is_ignored(self, guard, value):
        rest, opts = guard.split_studio_args([f"{FLAG}={value}", "--port", "1"])
        assert rest == ["--port", "1"] and opts.cache_limit_mb is None

    def test_a_repeated_flag_stops_the_start(self, guard):
        with pytest.raises(SystemExit):
            guard.split_studio_args([FLAG, "1", FLAG, "2"])

    def test_nothing_set_calls_nothing(self, guard):
        server = fake_server()
        load = server.ModelProvider._load
        guard.install_cache_limit(server, None, {})
        server.ModelProvider()._load(REPO)
        assert server.calls == [] and server.ModelProvider._load is load

    @pytest.mark.parametrize("value", [0, 2000])
    def test_the_start_value_is_applied_in_decimal_megabytes(self, guard, value):
        server = fake_server()
        guard.install_cache_limit(server, value, {})
        assert server.calls == [value * 10**6]

    def test_each_model_gets_its_own_and_others_the_start_value(self, guard):
        server = fake_server()
        guard.install_cache_limit(server, 5, {REPO: 0})
        provider = server.ModelProvider()
        assert provider._load(REPO) == REPO
        provider._load(OTHER)
        assert server.calls == [5_000_000, 0, 5_000_000]

    def test_a_model_without_one_gets_mlxs_default_back(self, guard):
        server = fake_server(previous=999)
        guard.install_cache_limit(server, None, {REPO: 3})
        provider = server.ModelProvider()
        provider._load(OTHER)
        assert server.calls == []  # nothing was changed yet
        provider._load(REPO)
        provider._load(OTHER)
        assert server.calls == [3_000_000, 999]

    def test_a_failed_load_goes_back_to_the_start_value(self, guard):
        server = fake_server()
        guard.install_cache_limit(server, 5, {REPO: 0})
        provider = server.ModelProvider()
        provider._load(REPO)
        with pytest.raises(ValueError):
            provider._load("broken")
        assert server.calls[-1] == 5_000_000

    def test_a_missing_call_is_said_and_stops_nothing(self, guard, capsys):
        server = fake_server(fail=True)
        guard.install_cache_limit(server, 5, {})
        assert "MLX cache limit was not set" in capsys.readouterr().err

    def test_a_changed_mlx_lm_is_said(self, guard, capsys):
        server = fake_server()
        server.ModelProvider = None
        guard.install_cache_limit(server, None, {REPO: 1})
        assert "MLX cache limit" in capsys.readouterr().err

    @pytest.mark.parametrize(
        ("text", "found"),
        [
            (None, {}),
            ("not json", {}),
            ("[1]", {}),
            ('{"a": 0, "b": 5, "c": -1, "d": true, "e": 1.5, "f": 1000001}', {"a": 0, "b": 5}),
        ],
    )
    def test_only_usable_per_model_figures_are_read(self, guard, text, found):
        assert guard.read_model_cache_limits(text) == found

    def test_main_installs_it(self, guard):
        assert "install_cache_limit" in guard.main.__code__.co_names

    def test_the_installed_mlx_has_the_call(self):
        spec = importlib.util.find_spec("mlx")
        if spec is None:
            pytest.fail("mlx is not installed")
        stub = Path(spec.submodule_search_locations[0]) / "core" / "__init__.pyi"
        assert "def set_cache_limit(limit: int) -> int:" in stub.read_text("utf-8")
