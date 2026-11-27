"""Row 656: the guard keeps two models loaded. No real model is loaded or downloaded."""

from __future__ import annotations

import importlib.util
import json
import stat
import sys
import types
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "mlx_guard.py"


def load_guard():
    spec = importlib.util.spec_from_file_location("mlx_guard_row656", GUARD)
    module = importlib.util.module_from_spec(spec)
    # dataclasses look their module up while the class is built.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


guard = load_guard()

GB = 10**9
QWEN = {
    "num_hidden_layers": 28,
    "num_key_value_heads": 4,
    "num_attention_heads": 28,
    "hidden_size": 3584,
    "head_dim": 128,
    "max_position_embeddings": 32768,
}


# 1. flags ------------------------------------------------------------------- #
class TestSplitStudioArgs:
    def test_nothing_to_remove_means_one_model(self):
        argv = ["--model", "org/a", "--port", "8080"]
        rest, opts = guard.split_studio_args(argv)
        assert rest == argv
        assert (opts.also, opts.max_models) == (None, 1)

    def test_flags_removed_and_order_kept(self):
        argv = [
            "--model", "org/a", "--studio-also-model", "org/b", "--port", "8080",
            "--studio-max-models=2", "--studio-budget-bytes", "100",
            "--studio-kv-tokens", "4096",
        ]  # fmt: skip
        rest, opts = guard.split_studio_args(argv)
        assert rest == ["--model", "org/a", "--port", "8080"]
        assert opts.also == "org/b"
        assert (opts.max_models, opts.budget_bytes, opts.kv_tokens) == (2, 100, 4096)

    def test_defaults(self):
        opts = guard.split_studio_args(["--studio-max-models", "2"])[1]
        assert opts.kv_tokens == 32768
        assert opts.budget_bytes is None

    @pytest.mark.parametrize(
        "argv",
        [
            ["--studio-x", "1"],
            ["--studio-max-models", "3"],
            ["--studio-max-models", "0"],
            ["--studio-max-models", "two"],
            ["--studio-max-models"],
            ["--studio-budget-bytes", "-1"],
            ["--studio-kv-tokens", "0"],
            ["--studio-also-model", "org/b"],
            [
                "--studio-max-models",
                "2",
                "--studio-also-model",
                "a/b",
                "--studio-also-model",
                "a/c",
            ],
            ["--studio-max-models", "2", "--studio-also-model", "--port"],
        ],
    )
    def test_bad_flags_stop_the_start(self, argv):
        with pytest.raises(SystemExit) as stop:
            guard.split_studio_args(argv)
        assert "mlx_guard" in str(stop.value)


# 2. memory figures ---------------------------------------------------------- #
class TestFigures:
    def test_qwen_bytes_per_token(self):
        assert guard.kv_bytes_per_token(QWEN) == 57_344

    def test_head_dim_and_kv_heads_fall_back(self):
        config = {"num_hidden_layers": 2, "num_attention_heads": 8, "hidden_size": 64}
        assert guard.kv_bytes_per_token(config) == 2 * 8 * 8 * 4

    def test_nested_text_config_is_read_first(self):
        assert guard.kv_bytes_per_token({"num_hidden_layers": 1, "text_config": QWEN}) == 57_344

    @pytest.mark.parametrize(
        "config",
        [None, [], {}, {"num_hidden_layers": "2"}, {**QWEN, "num_hidden_layers": 0},
         {"num_hidden_layers": 2, "num_attention_heads": 0, "hidden_size": 64}],
    )  # fmt: skip
    def test_unreadable_config_is_none(self, config):
        assert guard.kv_bytes_per_token(config) is None

    def test_reserve_is_3_1_times_the_tokens_capped_by_the_model(self):
        assert guard.kv_reserve(QWEN, 1000) == int(3.1 * 57_344 * 1000)
        assert guard.kv_reserve(QWEN, 10**9) == int(3.1 * 57_344 * 32768)

    def test_unknown_model_reserves_as_a_large_one(self):
        assert guard.kv_reserve({}, 1000) == int(3.1 * 131_072 * 1000)

    def test_weights_are_the_model_safetensors(self, tmp_path):
        (tmp_path / "blob").write_bytes(b"x" * 30)
        (tmp_path / "model-00001-of-00002.safetensors").symlink_to(tmp_path / "blob")
        (tmp_path / "model-00002-of-00002.safetensors").write_bytes(b"x" * 12)
        (tmp_path / "other.bin").write_bytes(b"x" * 99)
        assert guard.weights_bytes(tmp_path) == 42
        assert guard.weights_bytes(tmp_path / "missing") == 0


# 3. admission --------------------------------------------------------------- #
def entry(key, weights=4 * GB, kv=2 * GB):
    return guard.Resident(key, weights, kv)


class TestAdmission:
    def plan(self, resident, new, active, cache=0, budget=18 * GB, max_models=2):
        return guard.admission_plan(resident, new, active, cache, budget, max_models)

    def test_fits(self):
        plan = self.plan([entry("a")], entry("b"), 4 * GB)
        assert (plan.evict, plan.trim_to_bytes, plan.alone) == ([], None, False)

    def test_two_slots_full_evicts_the_oldest(self):
        plan = self.plan([entry("a", GB), entry("b", GB)], entry("c", GB), 2 * GB)
        assert plan.evict == ["a"] and not plan.alone

    def test_over_budget_evicts(self):
        plan = self.plan([entry("a", 10 * GB)], entry("b", 10 * GB), 10 * GB)
        assert plan.evict == ["a"] and not plan.alone

    def test_largest_reply_room_counts_once_plus_a_margin(self):
        resident, new = [entry("a", 4 * GB, 3 * GB)], entry("b", 4 * GB, 2 * GB)
        need = 4 * GB + 4 * GB + 3 * GB + 14 * GB // 10
        assert self.plan(resident, new, 4 * GB, budget=need).evict == []
        assert self.plan(resident, new, 4 * GB, budget=need - 1).evict == ["a"]

    def test_then_trims_the_prompt_cache(self):
        # 5.6 active (3 of it cache) + 10 new + 2 room + 1.4 margin = 19, one over.
        plan = self.plan([], entry("b", 10 * GB), 56 * GB // 10, cache=3 * GB)
        assert (plan.evict, plan.trim_to_bytes, plan.alone) == ([], 2 * GB, False)

    def test_then_loads_alone(self):
        plan = self.plan([entry("a")], entry("b", 30 * GB), 7 * GB, cache=GB)
        assert plan.evict == ["a"] and plan.alone and plan.trim_to_bytes == 0
        assert plan.reason

    def test_alone_without_a_cache_has_nothing_to_trim(self):
        plan = self.plan([], entry("b", 30 * GB), 0)
        assert plan.alone and plan.trim_to_bytes is None

    def test_one_slot_always_replaces(self):
        plan = self.plan([entry("a", GB)], entry("b", GB), GB, max_models=1)
        assert plan.evict == ["a"]

    def full(self, resident, cache, **kwargs):
        """6 GB models, 3.7 GB reply room, 18.3 GB budget: 0.8 GB over once a goes."""
        kv = 37 * GB // 10
        residents = [entry(key, 6 * GB, kv) for key in resident]
        active = kwargs.pop("active", 14 * GB)
        return self.plan(residents, entry("c", 6 * GB, kv), active, cache, 183 * GB // 10, **kwargs)

    def test_trim_keeps_the_last_resident(self):
        plan = self.full("ab", 2 * GB)
        assert plan.evict == ["a"] and not plan.alone
        assert plan.trim_to_bytes == 12 * GB // 10

    def test_cache_too_small_still_evicts_both(self):
        plan = self.full("ab", GB // 2)
        assert plan.evict == ["a", "b"] and plan.trim_to_bytes is None

    def test_trim_boundary_over_equals_cache(self):
        plan = self.full("ab", 8 * GB // 10)
        assert plan.evict == ["a"] and plan.trim_to_bytes == 0

    def test_one_slot_never_trims_to_keep(self):
        plan = self.full("a", 2 * GB, active=8 * GB, max_models=1)
        assert plan.evict == ["a"]


# 4. status file ------------------------------------------------------------- #
class TestStatusWriter:
    def test_private_atomic_and_capped(self, tmp_path):
        path = tmp_path / "server.resident.json"
        status = guard.status_writer(str(path))
        for i in range(25):
            status("load", f"org/m{i}", resident=[f"org/m{i}"])
        data = json.loads(path.read_text("utf-8"))
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert len(data["events"]) == 20
        assert data["events"][-1]["event"] == "load"
        assert data["events"][-1]["model"] == "org/m24"
        assert data["resident"] == ["org/m24"]
        assert data["pid"] > 0
        assert [p.name for p in tmp_path.iterdir()] == [path.name]

    def test_replaces_the_file(self, tmp_path):
        path = tmp_path / "r.json"
        path.write_text("old", encoding="utf-8")
        before = path.stat().st_ino
        guard.status_writer(str(path))(multi=False, reason="moved")
        data = json.loads(path.read_text("utf-8"))
        assert (data["multi"], data["reason"]) == (False, "moved")
        assert path.stat().st_ino != before

    def test_no_path_or_unwritable_path_is_ignored(self, tmp_path):
        guard.status_writer(None)("load", "a")
        guard.status_writer(str(tmp_path / "no" / "dir.json"))("load", "a")


# 5. slots, over a stand-in for mlx_lm --------------------------------------- #
class FakeModel(dict):
    """mlx's nn.Module is a dict of its weights."""

    def __init__(self, name):
        super().__init__(weights=name)
        self.name = name


class FakeMx:
    def __init__(self):
        self.active = 0
        self.cleared = 0

    def get_active_memory(self):
        return self.active

    def get_peak_memory(self):
        return self.active + 1

    def reset_peak_memory(self):
        pass

    def clear_cache(self):
        self.cleared += 1


class FakeCache:
    def __init__(self, mx=None):
        self.mx = mx  # given: trimming frees the stand-in memory too
        self.nbytes = 0
        self.trimmed = []

    def trim_to(self, *, n_bytes=None):
        self.trimmed.append(n_bytes)
        dropped = self.nbytes - min(self.nbytes, n_bytes)
        self.nbytes -= dropped
        if self.mx:
            self.mx.active -= dropped


def fake_server(broken=()):
    mx = FakeMx()
    MEMBERS = ("model", "tokenizer", "processor", "draft_model", "is_batchable")

    class ModelProvider:
        def __init__(self, default):
            self._model_map = {"default_model": default}
            self.loads = []
            self.reset()

        def reset(self):
            self.model_key = None
            for name in MEMBERS:
                setattr(self, name, None)

        def _load(self, model_path, adapter_path=None, draft_model_path=None):
            self.reset()
            if model_path in broken:
                raise ValueError("cannot load")
            self.loads.append(model_path)
            self.model_key = (model_path, adapter_path, draft_model_path)
            self.model = FakeModel(model_path)
            self.tokenizer = f"tok-{model_path}"
            self.processor = f"proc-{model_path}"
            self.draft_model = None
            self.is_batchable = True

        def load_default(self):
            self.load("default_model", None, "default_model")

        def load(self, model_path, adapter_path=None, draft_model_path=None):
            model_path = self._model_map.get(model_path, model_path)
            draft_model_path = None
            if self.model_key != (model_path, adapter_path, draft_model_path):
                self._load(model_path, adapter_path, draft_model_path)
            return self.model, self.tokenizer

    class ResponseGenerator:
        def __init__(self, model_provider, prompt_cache):
            self.model_provider = model_provider
            self.prompt_cache = prompt_cache

    return types.SimpleNamespace(
        ModelProvider=ModelProvider, ResponseGenerator=ResponseGenerator, mx=mx
    )


class Rig:
    def __init__(self, tmp_path, also=None, sizes=None, budget=18 * GB, broken=(), cached=None):
        self.sizes = sizes or {}
        self.server = fake_server(broken)
        self.path = tmp_path / "resident.json"
        argv = ["--studio-max-models", "2", "--studio-budget-bytes", str(budget)]
        if also:
            argv += ["--studio-also-model", also]
        opts = guard.split_studio_args(argv)[1]
        guard.install_multi(
            self.server,
            opts,
            guard.status_writer(str(self.path)),
            figures=lambda repo, tokens: (self.sizes.get(repo, 2 * GB), GB),
            is_cached=lambda repo: cached is None or repo in cached,
        )
        self.provider = self.server.ModelProvider("org/a")
        self.cache = FakeCache(self.server.mx)
        self.server.ResponseGenerator(self.provider, self.cache)

    def status(self):
        return json.loads(self.path.read_text("utf-8"))

    def events(self):
        return [(e["event"], e["model"]) for e in self.status()["events"]]


class TestSlots:
    def test_a_resident_model_is_not_loaded_again(self, tmp_path):
        rig = Rig(tmp_path)
        p = rig.provider
        first = p.load("org/a")
        members = (p.model, p.tokenizer, p.processor, p.draft_model, p.is_batchable, p.model_key)
        p.load("org/b")
        assert p.load("org/a") == first
        assert p.loads == ["org/a", "org/b"]
        assert members == (
            p.model, p.tokenizer, p.processor, p.draft_model, p.is_batchable, p.model_key,
        )  # fmt: skip
        assert p.model is first[0]
        assert rig.status()["resident"] == ["org/a", "org/b"]
        assert rig.events()[-1] == ("hit", "org/a")

    def test_third_model_evicts_the_least_recently_used_and_frees_it(self, tmp_path):
        rig = Rig(tmp_path)
        p = rig.provider
        a = p.load("org/a")[0]
        held = p.load("org/b")[0]
        p.load("org/a")
        p.load("org/c")
        assert rig.status()["resident"] == ["org/c", "org/a"]
        assert ("evict", "org/b") in rig.events()
        # mlx_lm's generation thread still holds the model it last loaded.
        assert held == {} and a != {}
        assert rig.server.mx.cleared > 0
        p.load("org/b")
        assert p.loads == ["org/a", "org/b", "org/c", "org/b"]

    def test_too_big_loads_alone_and_frees_the_one_still_held(self, tmp_path):
        rig = Rig(tmp_path, sizes={"org/big": 30 * GB})
        held = rig.provider.load("org/a")[0]
        rig.provider.load("org/big")
        assert held == {}
        assert rig.status()["resident"] == ["org/big"]
        assert ("alone", "org/big") in rig.events()

    def test_prompt_cache_is_trimmed_to_what_fits(self, tmp_path):
        rig = Rig(tmp_path, sizes={"org/a": 16 * GB}, budget=204 * GB // 10)
        rig.cache.nbytes = 3 * GB
        rig.server.mx.active = 3 * GB
        rig.provider.load("org/a")
        assert rig.cache.trimmed == [2 * GB]
        assert ("trim", "org/a") in rig.events()

    def two_and_a_third(self, tmp_path):
        """a and b resident, 2 GB of cached prompts, c 0.8 GB over once a goes."""
        sizes = dict.fromkeys(("org/a", "org/b", "org/c"), 6 * GB)
        rig = Rig(tmp_path, sizes=sizes, budget=204 * GB // 10)
        rig.provider.load("org/a")
        rig.provider.load("org/b")
        rig.server.mx.active = 128 * GB // 10
        rig.cache.nbytes = 2 * GB
        return rig

    def test_third_model_trims_cache_and_keeps_one(self, tmp_path):
        rig = self.two_and_a_third(tmp_path)
        rig.provider.load("org/c")
        assert rig.status()["resident"] == ["org/c", "org/b"]
        assert rig.cache.trimmed == [12 * GB // 10]
        assert ("evict", "org/b") not in rig.events()

    def test_trim_that_frees_nothing_falls_back_to_evict(self, tmp_path):
        rig = self.two_and_a_third(tmp_path)
        rig.cache.mx = None
        rig.provider.load("org/c")
        assert ("evict", "org/b") in rig.events()
        assert rig.cache.trimmed[0] == 12 * GB // 10
        assert rig.status()["resident"] == ["org/c"]

    def test_default_model_and_full_name_share_a_slot(self, tmp_path):
        rig = Rig(tmp_path)
        rig.provider.load("default_model")
        rig.provider.load("org/b")
        rig.provider.load("org/a")
        assert rig.provider.loads == ["org/a", "org/b"]

    def test_status_has_measured_memory(self, tmp_path):
        rig = Rig(tmp_path)
        rig.server.mx.active = 7
        rig.provider.load("org/a")
        data = rig.status()
        assert (data["active_bytes"], data["peak_bytes"], data["budget_bytes"]) == (7, 8, 18 * GB)
        assert data["multi"] is True

    def test_failed_load_leaves_the_resident_model_usable(self, tmp_path):
        rig = Rig(tmp_path, broken=("org/bad",))
        rig.provider.load("org/a")
        with pytest.raises(ValueError, match="cannot load"):
            rig.provider.load("org/bad")
        assert rig.status()["resident"] == ["org/a"]
        rig.provider.load("org/a")
        assert rig.provider.loads == ["org/a"]
        assert rig.provider.model == {"weights": "org/a"}

    def test_start_preloads_the_second_and_ends_on_the_first(self, tmp_path):
        rig = Rig(tmp_path, also="org/b")
        rig.provider.load_default()
        assert rig.provider.loads == ["org/a", "org/b"]
        assert rig.provider.model_key == ("org/a", None, None)
        assert rig.status()["resident"] == ["org/a", "org/b"]

    @pytest.mark.parametrize(
        "kwargs",
        [
            {"sizes": {"org/b": 30 * GB}},
            {"cached": {"org/a"}},
            {"broken": ("org/b",)},
        ],
    )
    def test_second_model_that_cannot_load_is_skipped(self, tmp_path, kwargs):
        rig = Rig(tmp_path, also="org/b", **kwargs)
        rig.provider.load_default()
        assert rig.provider.loads == ["org/a"]
        assert rig.provider.model == {"weights": "org/a"}
        assert ("preload-skipped", "org/b") in rig.events()
        assert rig.status()["resident"] == ["org/a"]


# 6. mlx_lm moved ------------------------------------------------------------ #
def run_main(monkeypatch, tmp_path, argv, problem, status_file=True):
    """Run main() against stand-ins; returns what mlx_lm would have seen."""
    seen = {"multi": 0}
    server = types.SimpleNamespace(main=lambda: seen.update(argv=list(sys.argv[1:])))
    utils = types.SimpleNamespace(hf_repo_to_path=lambda repo: str(tmp_path / repo))
    package = types.ModuleType("mlx_lm")
    package.server, package.utils = server, utils
    monkeypatch.setitem(sys.modules, "mlx_lm", package)
    monkeypatch.setitem(sys.modules, "mlx_lm.server", server)
    monkeypatch.setitem(sys.modules, "mlx_lm.utils", utils)
    monkeypatch.setenv("MLX_STUDIO_SERVED_FILE", str(tmp_path / "served"))
    monkeypatch.setenv("MLX_STUDIO_RESIDENT_FILE", str(tmp_path / "resident.json"))
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(guard, "install", lambda *a: None)
    monkeypatch.setattr(guard, "multi_problem", lambda s: problem)
    monkeypatch.setattr(guard, "install_multi", lambda *a, **k: seen.update(multi=1))
    monkeypatch.setattr(guard, "install_report", lambda *a: seen.update(report=1))
    if not status_file:
        monkeypatch.delenv("MLX_STUDIO_RESIDENT_FILE")
    monkeypatch.setattr(sys, "argv", ["mlx_guard.py", *argv])
    guard.main()
    return seen


TWO = ["--model", "org/a", "--studio-also-model", "org/b", "--studio-max-models", "2"]


class TestMain:
    def test_moved_hooks_mean_one_model_not_a_refusal(self, monkeypatch, tmp_path):
        seen = run_main(monkeypatch, tmp_path, TWO, "ModelProvider._load changed")
        assert seen == {"multi": 0, "report": 1, "argv": ["--model", "org/a"]}
        data = json.loads((tmp_path / "resident.json").read_text("utf-8"))
        assert (data["multi"], data["reason"]) == (False, "ModelProvider._load changed")
        assert not (tmp_path / "served").exists()

    def test_two_models_are_installed_and_the_second_recorded(self, monkeypatch, tmp_path):
        seen = run_main(monkeypatch, tmp_path, TWO, None)
        assert seen == {"multi": 1, "argv": ["--model", "org/a"]}
        assert (tmp_path / "served").read_text("utf-8") == "org/b\n"

    def test_one_model_only_reports_its_memory(self, monkeypatch, tmp_path):
        seen = run_main(monkeypatch, tmp_path, ["--model", "org/a"], "must not be asked")
        assert seen == {"multi": 0, "report": 1, "argv": ["--model", "org/a"]}

    def test_one_model_without_a_status_file_changes_nothing(self, monkeypatch, tmp_path):
        seen = run_main(
            monkeypatch, tmp_path, ["--model", "org/a"], "must not be asked", status_file=False
        )
        assert seen == {"multi": 0, "argv": ["--model", "org/a"]}
        assert list(tmp_path.iterdir()) == []


# One model: the status file for the memory meter ----------------------------- #
class TestOneModelReport:
    def rig(self, tmp_path, broken=()):
        server = fake_server(broken)
        path = tmp_path / "resident.json"
        guard.install_report(server, guard.status_writer(str(path)), 18 * GB)
        server.mx.active = 7
        return server, server.ModelProvider("org/a"), lambda: json.loads(path.read_text("utf-8"))

    def test_each_load_is_reported(self, tmp_path):
        _, provider, status = self.rig(tmp_path)
        provider.load_default()
        data = status()
        assert data["resident"] == ["org/a"]
        assert (data["active_bytes"], data["peak_bytes"], data["budget_bytes"]) == (7, 8, 18 * GB)
        assert data["pid"] > 0 and "multi" not in data

    def test_models_swap_as_before(self, tmp_path):
        server, provider, status = self.rig(tmp_path)
        first = provider.load("org/a")[0]
        provider.load("org/b")
        provider.load("org/a")
        assert provider.loads == ["org/a", "org/b", "org/a"]
        assert first == {"weights": "org/a"} and server.mx.cleared == 0
        assert status()["resident"] == ["org/a"]

    def test_a_failed_load_still_fails_and_shows_nothing_loaded(self, tmp_path):
        _, provider, status = self.rig(tmp_path, broken=("org/bad",))
        provider.load("org/a")
        with pytest.raises(ValueError, match="cannot load"):
            provider.load("org/bad")
        assert status()["resident"] == []

    def test_a_meter_that_cannot_read_never_stops_a_load(self, tmp_path):
        server, provider, _ = self.rig(tmp_path)
        server.mx = None
        assert provider.load("org/a")[0] == {"weights": "org/a"}

    def test_a_moved_mlx_lm_is_left_alone(self, tmp_path):
        guard.install_report(types.SimpleNamespace(), guard.status_writer(None), GB)


# The checks against the installed mlx_lm are in test_guard.py (CI no-skip list).
