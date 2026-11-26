"""Rows 759 and 745: two-model limits that follow the free memory, and the oversize words.

The rules are in the row 760 spec. Free memory is always injected (see conftest);
no model is loaded and no process started.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_for760", ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixes = sibling("test_row713_fixes")
apps, uis, guards = fixes.apps, fixes.uis, fixes.guards
guard, Rig, GB, A, B, CHAT = fixes.guard, fixes.Rig, fixes.GB, fixes.A, fixes.B, fixes.CHAT
cache_model, post, ram, loads = fixes.cache_model, fixes.post, fixes.ram, fixes.loads
serving, load_chat = fixes.serving, fixes.load_chat
ui, needs_node = fixes.ui, fixes.needs_node

QWEN = guards.QWEN  # 57,344 bytes per token, 32,768 tokens
CODER = {**QWEN, "max_position_embeddings": 1_000_000}
KV = 57_344
SEVEN_B = 4_284_000_000  # one Qwen2.5 7B 4-bit
QUIET, BUSY, FULL = 25 * GB, 15_110_000_000, 11_780_000_000

# The start of the row 743 run, as vm_stat printed it.
VM_STAT = """Mach Virtual Memory Statistics: (page size of 16384 bytes)
Pages free:                                   246371.
Pages active:                                 663360.
Pages inactive:                               654439.
Pages speculative:                              8482.
Pages throttled:                                   0.
Pages wired down:                             166916.
Pages purgeable:                               12852.
"Translation faults":                      478902463.
Pages copy-on-write:                        69726021.
File-backed pages:                            451118.
Anonymous pages:                              875163.
Pages occupied by compressor:                 307270.
"""


def reply(tokens: int, per_token: int = KV) -> int:
    return int(3.1 * per_token * tokens)


def need(tokens: int, weights: int = 2 * SEVEN_B) -> int:
    return weights + 2 * KV * tokens + reply(tokens) + 1_400_000_000


# 1, 2. reading the free memory ----------------------------------------------- #
def vm_stat_says(monkeypatch, stdout="", code=0, error=None):
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, **kwargs)
        if error:
            raise error
        if code and kwargs.get("check"):
            raise subprocess.CalledProcessError(code, cmd)
        return subprocess.CompletedProcess(cmd, code, stdout, "")

    monkeypatch.setattr(subprocess, "run", run)
    return seen


class TestFreeBytes:
    def test_the_captured_text(self, monkeypatch):
        seen = vm_stat_says(monkeypatch, VM_STAT)
        free = guard.free_bytes()
        assert free == (246371 + 8482 + 12852 + 451118) * 16384
        assert round(free / 1e9, 2) == 11.78
        assert seen["cmd"] == ["/usr/bin/vm_stat"]
        assert seen["timeout"] == 2 and not seen.get("shell")

    @pytest.mark.parametrize(
        "answer",
        [
            {"stdout": VM_STAT, "code": 1},
            {"error": subprocess.TimeoutExpired("vm_stat", 2)},
            {"error": FileNotFoundError("no vm_stat")},
            {"stdout": VM_STAT.replace("page size of 16384 bytes", "")},
            {"stdout": VM_STAT.replace("Pages purgeable:", "Pages other:")},
            {"stdout": VM_STAT.replace("451118.", "many.")},
            {"stdout": ""},
        ],
    )
    def test_anything_unexpected_is_none(self, monkeypatch, answer):
        vm_stat_says(monkeypatch, **answer)
        assert guard.free_bytes() is None

    def test_the_suite_never_reads_this_macs_memory(self, free_memory):
        free_memory.bytes = 123_456_789
        assert guard.free_bytes() == 123_456_789
        free_memory.bytes = None
        assert guard.free_bytes() is None


# 3, 4, 5. the formula -------------------------------------------------------- #
class TestContext:
    def test_the_constants(self):
        assert guard.RAM_FRACTION == 0.52
        assert guard.FREE_RESERVE == 2 * GB
        assert guard.MEMORY_MARGIN == 1_400_000_000
        assert guard.PROCESS_OVERHEAD == 400_000_000
        assert (guard.REPLY_FACTOR, guard.CACHED_CONVERSATIONS) == (3.1, 2)
        assert (guard.MIN_CONTEXT, guard.KV_TOKENS, guard.CONTEXT_STEP) == (8192, 32768, 1024)
        assert guard.UNKNOWN_KV_PER_TOKEN == 131_072
        assert not hasattr(guard, "UNKNOWN_KV_RESERVE")

    @pytest.mark.parametrize(
        ("budget", "tokens"),
        [(17_867_000_000, 26_624), (13_110_000_000, 10_240), (100 * GB, 32_768)],
    )
    def test_tokens_from_the_budget(self, budget, tokens):
        assert guard.context_tokens(budget, 2 * SEVEN_B, KV) == tokens

    @pytest.mark.parametrize("budget", [9_780_000_000, 0])
    def test_too_little_is_below_the_minimum_and_never_negative(self, budget):
        assert 0 <= guard.context_tokens(budget, 2 * SEVEN_B, KV) < guard.MIN_CONTEXT

    def test_reply_room_uses_the_new_factor(self):
        assert guard.kv_reserve(QWEN, 32768) == reply(32768)
        assert guard.kv_reserve(QWEN, 10**9) == reply(32768)

    def test_an_unreadable_config_is_a_large_model(self):
        assert guard.kv_reserve({}, 1000) == reply(1000, 131_072)

    def test_a_model_that_cannot_be_found_reserves_the_same(self):
        def missing(repo):
            raise LookupError(repo)

        assert guard.model_figures(missing)("org/x", 1000) == (0, reply(1000, 131_072))


# 6. the guard checks again at each load -------------------------------------- #
def spy(monkeypatch, free_memory):
    """Every read of the free memory, answered from ``free_memory``."""
    calls = []

    def read():
        calls.append(free_memory.bytes)
        return free_memory.bytes

    monkeypatch.setattr(guard, "free_bytes", read)
    return calls


class TestGuardBudget:
    def test_free_memory_lowers_it(self, tmp_path, monkeypatch, free_memory):
        spy(monkeypatch, free_memory)
        rig = Rig(tmp_path)
        rig.server.mx.active = 3 * GB
        free_memory.bytes = 10 * GB
        rig.provider.load("org/a")
        data = rig.status()
        assert data["budget_bytes"] == 11 * GB  # 3 held + 10 free - 2 kept back
        assert (data["free_bytes"], data["context_tokens"]) == (10 * GB, 32768)

    @pytest.mark.parametrize(
        ("file", "free", "expected"),
        [(9, 100, 9), (15, 12, 10), (None, 100, 18), (None, 1, 0)],
    )
    def test_the_smallest_wins(self, tmp_path, monkeypatch, free_memory, file, free, expected):
        spy(monkeypatch, free_memory)
        budget = tmp_path / "server.budget"
        if file:
            budget.write_text(str(file * GB), "utf-8")
        monkeypatch.setenv("MLX_STUDIO_BUDGET_FILE", str(budget))
        rig = Rig(tmp_path)
        free_memory.bytes = free * GB
        rig.provider.load("org/a")
        assert rig.status()["budget_bytes"] == expected * GB

    def test_unreadable_skips_the_live_term_and_says_so_once(
        self, tmp_path, monkeypatch, free_memory, capsys
    ):
        spy(monkeypatch, free_memory)
        free_memory.bytes = None
        rig = Rig(tmp_path)
        rig.provider.load("org/a")
        rig.provider.load("org/b")
        data = rig.status()
        assert (data["budget_bytes"], data["free_bytes"]) == (18 * GB, None)
        assert data["resident"] == ["org/b", "org/a"]
        assert capsys.readouterr().err.count("free memory could not be read") == 1

    def test_memory_that_drops_between_loads_evicts(self, tmp_path, monkeypatch, free_memory):
        spy(monkeypatch, free_memory)
        rig = Rig(tmp_path, sizes=dict.fromkeys(("org/a", "org/b"), 4 * GB))
        rig.provider.load("org/a")
        rig.server.mx.active = 4 * GB
        # 4 held + 6 free - 2 = 8: both with a reply and the margin need 10.4.
        free_memory.bytes = 6 * GB
        rig.provider.load("org/b")
        assert ("evict", "org/a") in rig.events()
        assert rig.status()["resident"] == ["org/b"]

    def test_a_switch_between_loaded_models_is_not_checked(
        self, tmp_path, monkeypatch, free_memory
    ):
        calls = spy(monkeypatch, free_memory)
        rig = Rig(tmp_path)
        rig.provider.load("org/a")
        rig.provider.load("org/b")
        assert len(calls) == 2
        free_memory.bytes = GB
        rig.provider.load("org/a")
        data = rig.status()
        assert len(calls) == 2 and rig.events()[-1] == ("hit", "org/a")
        assert (data["budget_bytes"], data["free_bytes"]) == (18 * GB, 100 * GB)
        assert data["resident"] == ["org/a", "org/b"]

    def test_the_second_model_is_not_preloaded_into_a_full_mac(self, tmp_path, monkeypatch):
        # Plenty for the first load, then 5 GB: under the 4.4 GB the second needs.
        answers = iter([100 * GB, 5 * GB])
        monkeypatch.setattr(guard, "free_bytes", lambda: next(answers))
        rig = Rig(tmp_path, also="org/b")
        rig.provider.load_default()
        assert rig.provider.loads == ["org/a"]
        assert ("preload-skipped", "org/b") in rig.events()
        assert rig.status()["budget_bytes"] == 3 * GB

    def test_the_context_comes_from_the_flag(self, tmp_path):
        server = guards.fake_server()
        path = tmp_path / "r.json"
        opts = guard.split_studio_args(["--studio-max-models", "2", "--studio-kv-tokens", "10240"])
        asked = []
        guard.install_multi(
            server,
            opts[1],
            guard.status_writer(str(path)),
            figures=lambda repo, tokens: asked.append(tokens) or (GB, GB),
            is_cached=None,
        )
        server.ModelProvider("org/a").load("org/a")
        assert asked == [10240]
        assert json.loads(path.read_text("utf-8"))["context_tokens"] == 10240


# 8, 10. the estimate --------------------------------------------------------- #
def seven_b_pair(app, second=QWEN):
    cache_model(app, A, weights=SEVEN_B, config=QWEN)
    cache_model(app, B, weights=SEVEN_B, config=second)


class TestEstimate:
    def test_a_quiet_mac_gets_what_the_fixed_rule_allows(self, app_module, ram, free_memory):
        seven_b_pair(app_module, CODER)
        free_memory.bytes = QUIET
        est = app_module.server_memory_estimate([A, B])
        assert est["context_tokens"] == 26_624
        assert est["budget_bytes"] == int(0.52 * apps.RAM)
        assert est["cache_cap_bytes"] == est["cache_bytes"] == 2 * KV * 26_624
        assert est["reply_bytes"] == reply(26_624)
        assert est["margin_bytes"] == 1_400_000_000
        assert est["total_bytes"] == need(26_624)
        assert (est["fits"], est["refused"], est["free_bytes"]) == (True, None, QUIET)
        assert [m["safe_context"] for m in est["models"]] == [26_624, 26_624]

    def test_a_model_with_a_short_limit_shows_its_limit(self, app_module, ram):
        seven_b_pair(app_module, {**QWEN, "max_position_embeddings": 4096})
        est = app_module.server_memory_estimate([A, B])
        assert [m["safe_context"] for m in est["models"]] == [26_624, 4096]

    def test_a_busy_mac_shrinks_the_context(self, app_module, ram, free_memory):
        seven_b_pair(app_module)
        free_memory.bytes = BUSY
        est = app_module.server_memory_estimate([A, B])
        assert est["context_tokens"] == 10_240
        assert est["budget_bytes"] == BUSY - 2 * GB
        assert est["total_bytes"] == need(10_240) <= est["budget_bytes"]
        assert (est["fits"], est["refused"]) == (True, None)

    def test_a_full_mac_is_refused_with_the_figures(self, app_module, ram, free_memory):
        seven_b_pair(app_module)
        free_memory.bytes = FULL
        est = app_module.server_memory_estimate([A, B])
        assert est["fits"] is False and est["context_tokens"] < 8192
        assert (est["free_bytes"], est["budget_bytes"]) == (FULL, FULL - 2 * GB)
        assert est["total_bytes"] == need(8192)
        # "needed" is the free memory it takes (need + reserve), so it is never below the "free" figure.
        # free is floored, needed is ceiled: 11.78 -> 11.7, 14.36 -> 14.4
        assert est["refused"] == (
            "Not enough free memory for two models: 11.7 GB free, 14.4 GB needed for 8K each. "
            "Close other apps or start one model."
        )

    def test_exactly_8192_is_allowed(self, app_module, ram, free_memory):
        seven_b_pair(app_module)
        free_memory.bytes = need(8192) + 2 * GB + 1
        est = app_module.server_memory_estimate([A, B])
        assert (est["context_tokens"], est["fits"]) == (8192, True)
        free_memory.bytes = need(8192) + 2 * GB - 10**6
        assert app_module.server_memory_estimate([A, B], fresh=True)["fits"] is False

    @pytest.mark.parametrize("free", [100 * GB, None])
    def test_models_too_big_for_the_fixed_rule(self, app_module, ram, free_memory, free):
        cache_model(app_module, A, weights=SEVEN_B)
        cache_model(app_module, B, weights=14 * GB)
        free_memory.bytes = free
        est = app_module.server_memory_estimate([A, B])
        assert est["fits"] is False
        # Closing apps cannot help here, so the words do not ask for it.
        assert est["refused"].startswith("Not enough memory for two models: 17.9 GB allowed")
        assert "Close other apps" not in est["refused"]

    def test_unknown_settings_count_as_a_large_model(self, app_module, ram):
        cache_model(app_module, A, weights=GB, config={})
        cache_model(app_module, B, weights=GB, config=QWEN)
        guard_ = app_module._guard_module()
        est = app_module.server_memory_estimate([A, B])
        tokens = guard_.context_tokens(est["budget_bytes"], 2 * GB, 131_072)
        assert est["context_tokens"] == tokens == 21_504
        assert est["cache_cap_bytes"] == 2 * 131_072 * tokens

    def test_unreadable_free_memory_uses_the_fixed_rule(
        self, app_module, ram, free_memory, base_url
    ):
        seven_b_pair(app_module)
        free_memory.bytes = None
        est = app_module.server_memory_estimate([A, B])
        assert (est["context_tokens"], est["free_bytes"], est["refused"]) == (26_624, None, None)
        assert app_module.STATE.snapshot()["server"]["memory_low"] is None

    def test_the_picker_reads_free_memory_at_most_every_ten_seconds(
        self, app_module, ram, free_memory, monkeypatch
    ):
        seven_b_pair(app_module)
        clock = [1000.0]
        monkeypatch.setattr(app_module.time, "monotonic", lambda: clock[0])
        for _ in range(3):
            app_module.server_memory_estimate([A, B])
        assert free_memory.reads == 1
        clock[0] += 9.9
        app_module.server_memory_estimate([A, B])
        assert free_memory.reads == 1
        clock[0] += 0.2
        app_module.server_memory_estimate([A, B])
        assert free_memory.reads == 2

    def test_a_running_server_counts_as_memory_a_restart_gets_back(
        self, app_module, monkeypatch, ram, free_memory
    ):
        serving(app_module, monkeypatch, resident=[A, B], **measured(9 * GB))
        for repo in (A, B):
            weigh(app_module, repo, SEVEN_B)
        free_memory.bytes = 6_110_000_000
        assert app_module.server_memory_estimate([A, B])["context_tokens"] == 10_240

    # F6: refused figure with held memory (running server)
    def test_refused_figure_includes_held_memory(self, app_module, monkeypatch, ram, free_memory):
        """Refused message subtracts held memory: need + reserve - held."""
        serving(app_module, monkeypatch, resident=[A, B], **measured(9 * GB))
        for repo in (A, B):
            weigh(app_module, repo, SEVEN_B)
        # Free = 10 GB, held = 9 GB (weights), need(8K) = 12.36 GB, reserve = 2 GB
        # wanted = 12.36 + 2 - 9 = 5.36 GB, free = 10 GB → fits
        free_memory.bytes = 10 * GB
        est = app_module.server_memory_estimate([A, B])
        assert est["fits"] is True
        # Free = 5 GB, held = 9 GB, need(8K) = 12.36 GB, reserve = 2 GB
        # wanted = 12.36 + 2 - 9 = 5.36 GB, free = 5 GB → refused
        free_memory.bytes = 5 * GB
        est = app_module.server_memory_estimate([A, B], fresh=True)
        assert est["fits"] is False
        assert "5.0 GB free" in est["refused"]
        # wanted = 5.36 GB → ceil to 0.1 GB = 5.4 GB
        assert "5.4 GB needed" in est["refused"]

    # F6: free between need and need+reserve
    def test_refused_when_free_between_need_and_need_plus_reserve(
        self, app_module, ram, free_memory
    ):
        """Free memory between need(8K) and need(8K)+reserve is refused."""
        seven_b_pair(app_module)
        # need(8192) = 12.36 GB, reserve = 2 GB, so need+reserve = 14.36 GB
        # Free = 13 GB: above need but below need+reserve → refused
        free_memory.bytes = 13 * GB
        est = app_module.server_memory_estimate([A, B])
        assert est["fits"] is False
        assert "13.0 GB free" in est["refused"]
        # wanted = 12.36 + 2 = 14.36 GB (no held memory in this test)
        assert "14.4 GB needed" in est["refused"]

    # F6: free 13.4 GB
    def test_refused_with_free_13_4_gb(self, app_module, ram, free_memory):
        """Free 13.4 GB: need=12.36, need+reserve=14.36 → refused with correct figures."""
        seven_b_pair(app_module)
        free_memory.bytes = 13_400_000_000
        est = app_module.server_memory_estimate([A, B])
        assert est["fits"] is False
        # free floored to 0.1 GB: 13.4
        assert "13.4 GB free" in est["refused"]
        # wanted = 14.36 → ceil to 0.1 GB = 14.4
        assert "14.4 GB needed" in est["refused"]

    # Evict event survives many refreshes
    def test_evict_event_survives_refreshes(self, app_module, monkeypatch, ram, free_memory):
        """Evict event in status file should persist through refresh events."""
        # Set up a server with an evict event
        events = [
            {"event": "evict", "model": "org/a", "at": 1000},
            {"event": "load", "model": "org/b", "at": 1001},
            {"event": "hit", "model": "org/b", "at": 1002},
        ]
        app_module.STATE.server_pid = 4242
        apps.resident_file(
            app_module,
            4242,
            resident=["org/b"],
            events=events,
            active_bytes=5 * GB,
            peak_bytes=5 * GB,
            budget_bytes=18 * GB,
            multi=True,
            reason=None,
        )
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: True)
        app_module.STATE.server_repos = ["org/a", "org/b"]

        # Simulate refresh by clearing cache and getting snapshot
        app_module.STATE.free_read = None
        server = app_module.STATE.snapshot()["server"]

        # Evict event should still be there
        assert any(e["event"] == "evict" and e["model"] == "org/a" for e in server["events"])

    # Preload case - no memory_low during preload
    def test_no_memory_low_during_preload(self, app_module, monkeypatch, ram, free_memory):
        """memory_low should not be set while preload is in progress."""
        # Simulate preload in progress: only first model loaded, no hit event yet
        events = [
            {"event": "load", "model": "org/a", "at": 1000},
            # No hit event for org/a yet - preload still in progress
        ]
        app_module.STATE.server_pid = 4242
        apps.resident_file(
            app_module,
            4242,
            resident=["org/a"],
            events=events,
            active_bytes=5 * GB,
            peak_bytes=5 * GB,
            budget_bytes=18 * GB,
            multi=True,
            reason=None,
        )
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: True)
        app_module.STATE.server_repos = ["org/a", "org/b"]
        app_module.STATE.server_context = 10_240

        free_memory.bytes = 0  # Very low free memory
        app_module.STATE.free_read = None
        server = app_module.STATE.snapshot()["server"]

        # memory_low should be None during preload
        assert server["memory_low"] is None

    # "Close other apps" only when free memory is the limit
    def test_close_other_apps_only_when_free_is_limit(self, app_module, ram, free_memory):
        """Shrink note should only appear when free memory is the limiting factor."""
        seven_b_pair(app_module)
        # Case 1: free memory is limit (free = 15.11 GB, budget = 13.11 GB)
        free_memory.bytes = 15_110_000_000
        est = app_module.server_memory_estimate([A, B])
        assert est["context_tokens"] == 10_240
        assert est["free_is_limit"] is True

        # Case 2: 52% rule is limit (free is huge, static budget binds)
        free_memory.bytes = 100 * GB
        est = app_module.server_memory_estimate([A, B], fresh=True)
        assert est["context_tokens"] == 26_624
        assert est["free_is_limit"] is False  # 52% rule binds, not free memory

    # Refused figure with a running server (held memory subtracted)
    def test_refused_figure_with_running_server(self, app_module, monkeypatch, ram, free_memory):
        """Refused message should subtract held memory from need."""
        # Running server holds 9 GB
        serving(app_module, monkeypatch, resident=[A, B], **measured(9 * GB))
        for repo in (A, B):
            weigh(app_module, repo, SEVEN_B)
        # Free = 10 GB, need(8K) = 12.36 GB, reserve = 2 GB, held = 9 GB
        # wanted = 12.36 + 2 - 9 = 5.36 GB, free = 10 GB → fits
        free_memory.bytes = 10 * GB
        est = app_module.server_memory_estimate([A, B])
        assert est["fits"] is True

        # Free = 5 GB, held = 9 GB
        # wanted = 12.36 + 2 - 9 = 5.36 GB, free = 5 GB → refused
        free_memory.bytes = 5 * GB
        est = app_module.server_memory_estimate([A, B], fresh=True)
        assert est["fits"] is False
        assert "5.0 GB free" in est["refused"]
        # wanted = 5.36 → ceil to 0.1 = 5.4 GB
        assert "5.4 GB needed" in est["refused"]


# 9, 13. the start ------------------------------------------------------------ #
def flag(cmd, name):
    return cmd[cmd.index(name) + 1]


class TestStart:
    def test_the_flags_follow_the_free_memory(self, app_module, monkeypatch, ram, free_memory):
        seven_b_pair(app_module)
        free_memory.bytes = BUSY
        cmd = apps.launched(app_module, monkeypatch, [B])["cmd"]
        assert cmd[-10:] == [
            "--prompt-cache-bytes", str(2 * KV * 10_240),
            "--studio-also-model", B,
            "--studio-max-models", "2",
            "--studio-budget-bytes", str(BUSY - 2 * GB),
            "--studio-kv-tokens", "10240",
        ]  # fmt: skip
        assert app_module.STATE.server_context == 10_240
        monkeypatch.setattr(app_module, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(app_module, "_ps_field", lambda pid, field: " ".join(cmd))
        assert app_module._is_our_server(4242, A, None)

    def test_the_start_reads_the_memory_again_after_the_old_server_stops(
        self, app_module, monkeypatch, ram, free_memory
    ):
        seven_b_pair(app_module)
        app_module.server_memory_estimate([A, B])  # cached: 100 GB free
        free_memory.bytes = FULL
        seen = apps.launched(app_module, monkeypatch, [B])
        assert seen == {}, "no process is started"
        assert "Not enough free memory" in app_module.STATE.server_down_notice
        assert app_module.STATE.server_last_error

    def test_the_request_is_refused_with_409(self, app_module, base_url, monkeypatch, ram,
                                             free_memory):  # fmt: skip
        started = []
        monkeypatch.setattr(app_module, "worker_server_start", lambda *a: started.append(a))
        seven_b_pair(app_module)
        app_module.server_memory_estimate([A, B])  # a cached figure must not decide
        free_memory.bytes = FULL
        status, body = post(base_url, "/api/server/start", {"repo": A, "also": [B]})
        assert status == 409 and body["error"] == body["estimate"]["refused"]
        assert body["estimate"]["free_bytes"] == FULL
        assert started == [] and app_module.STATE.server_pending == []

    def test_one_model_is_started_as_before(self, app_module, monkeypatch, ram, free_memory):
        cache_model(app_module, A, weights=40 * GB)
        free_memory.bytes = 0
        cmd = apps.launched(app_module, monkeypatch)["cmd"]
        assert "--prompt-cache-bytes" not in cmd and "--studio-kv-tokens" not in cmd
        assert free_memory.reads == 0 and app_module.STATE.server_context is None

    def test_one_model_request_reads_nothing(
        self, app_module, base_url, monkeypatch, ram, free_memory
    ):
        monkeypatch.setattr(app_module, "worker_server_start", lambda *a: None)
        cache_model(app_module, A, weights=40 * GB)
        assert post(base_url, "/api/server/start", {"repo": A})[0] == 200
        assert free_memory.reads == 0


# 11. a Chat model beside two ------------------------------------------------- #
def measured(active: int, **more) -> dict:
    return {"active_bytes": active, "peak_bytes": active, "budget_bytes": 17 * GB, **more}


def weigh(app, repo, weights):
    with open(app._snapshot_dir(repo) / "model.safetensors", "wb") as handle:
        handle.truncate(weights)


def small_pair(app, monkeypatch, **resident):
    """Two 4 GB models running at 8,192 tokens: 11.8 GB, so 6 GB is left of the share."""
    serving(app, monkeypatch, **resident)
    app.STATE.server_context = 8192
    return need(8192, 8 * GB)


class TestChatLoad:
    def test_refused_when_free_memory_is_short(
        self, app_module, base_url, monkeypatch, ram, free_memory, loads
    ):
        small_pair(app_module, monkeypatch, resident=[A, B], **measured(9 * GB))
        free_memory.bytes = 5 * GB
        status, body = load_chat(app_module, base_url, 2 * GB)
        assert status == 409 and "free memory" in body["error"]
        assert "5.0 GB free" in body["error"] and loads[0] == []

    def test_the_edge_counts_what_the_server_may_still_take(
        self, app_module, monkeypatch, ram, free_memory
    ):
        total = small_pair(app_module, monkeypatch, resident=[A, B], **measured(9 * GB))
        cache_model(app_module, CHAT, weights=2 * GB)
        free_memory.bytes = 2 * GB + (total - 9 * GB) + 2 * GB
        assert app_module.chat_load_problem(CHAT) is None
        free_memory.bytes -= 1
        assert app_module.chat_load_problem(CHAT)

    def test_without_a_measurement_the_whole_need_counts(
        self, app_module, monkeypatch, ram, free_memory
    ):
        total = small_pair(app_module, monkeypatch)
        cache_model(app_module, CHAT, weights=2 * GB)
        free_memory.bytes = 2 * GB + total + 2 * GB
        assert app_module.chat_load_problem(CHAT) is None
        free_memory.bytes -= 1
        assert app_module.chat_load_problem(CHAT)

    def test_unreadable_free_memory_leaves_the_fixed_rule(
        self, app_module, monkeypatch, ram, free_memory
    ):
        small_pair(app_module, monkeypatch)
        cache_model(app_module, CHAT, weights=2 * GB)
        free_memory.bytes = None
        assert app_module.chat_load_problem(CHAT) is None
        weigh(app_module, CHAT, 8 * GB)
        assert "allowed" in app_module.chat_load_problem(CHAT)

    def test_the_context_the_server_reports_counts(self, app_module, monkeypatch, ram):
        small_pair(app_module, monkeypatch, context_tokens=32768, resident=[A, B])
        cache_model(app_module, CHAT, weights=2 * GB)
        assert app_module.chat_load_problem(CHAT)

    def test_a_one_model_server_is_never_refused(self, app_module, monkeypatch, ram, free_memory):
        serving(app_module, monkeypatch, (A,))
        cache_model(app_module, CHAT, weights=30 * GB)
        free_memory.bytes = 0
        assert app_module.chat_load_problem(CHAT) is None
        assert free_memory.reads == 0


# 12. free memory drops after the start --------------------------------------- #
class TestMemoryLow:
    def run_server(self, app, monkeypatch, **resident):
        serving(app, monkeypatch, resident=[A, B], **resident)
        for repo in (A, B):
            weigh(app, repo, SEVEN_B)
        app.STATE.server_context = 10_240

    def server(self, app):
        app.STATE.free_read = None  # past the ten seconds
        return app.STATE.snapshot()["server"]

    def test_not_set_right_after_a_start(self, app_module, monkeypatch, ram, free_memory):
        self.run_server(app_module, monkeypatch, **measured(2 * SEVEN_B))
        # The weights and the process overhead have just come off the free memory.
        free_memory.bytes = BUSY - 2 * SEVEN_B - 400_000_000
        server = self.server(app_module)
        assert server["memory_low"] is None and server["context_tokens"] == 10_240

    def test_set_when_other_apps_take_it_and_cleared_again(
        self, app_module, monkeypatch, ram, free_memory
    ):
        self.run_server(app_module, monkeypatch, **measured(2 * SEVEN_B))
        left = need(10_240) - 400_000_000 - 2 * SEVEN_B
        free_memory.bytes = left + 2 * GB - GB
        assert self.server(app_module)["memory_low"] == {
            "free_bytes": left + GB,
            "short_by_bytes": GB,
        }
        free_memory.bytes = left + 2 * GB
        assert self.server(app_module)["memory_low"] is None

    @pytest.mark.parametrize("resident", [{}, {"multi": False, "reason": "moved", **measured(GB)}])
    def test_nothing_to_compare_means_no_notice(
        self, app_module, monkeypatch, ram, free_memory, resident
    ):
        serving(app_module, monkeypatch, **resident)
        free_memory.bytes = 0
        assert self.server(app_module)["memory_low"] is None
        assert free_memory.reads == 0

    def test_one_model_server_has_none(self, app_module, monkeypatch, ram, free_memory):
        serving(app_module, monkeypatch, (A,), resident=[A], **measured(GB))
        free_memory.bytes = 0
        server = self.server(app_module)
        assert (server["memory_low"], server["context_tokens"]) == (None, None)
        assert free_memory.reads == 0


# 14, 15. the words ----------------------------------------------------------- #
PAIR = (
    "estimate = { fits: true, refused: null, context_tokens: 10240, free_bytes: 15.11e9,"
    " cache_cap_bytes: 1.17e9, total_bytes: 12.96e9, budget_bytes: 13.11e9, free_is_limit: true, models: ["
    ' { repo: "org/a", safe_context: 10240, context_limit: 1010000 },'
    ' { repo: "org/b", safe_context: 4096, context_limit: 4096 }] };'
)
ASK = (
    ' await pick("org/a", "org/b"); return [text("#serverEstimate"), text("#serverStartWhy"),'
    ' $("#serverStartBtn").disabled, text("#serverContextHint"), toasts.length];'
)
NO_READ = "Free memory could not be read; using the fixed 52% rule."


@needs_node
def test_the_shrunk_estimate(tmp_path):
    words, spoken, disabled, hint, _ = ui(tmp_path, PAIR + ASK)
    assert (
        words
        == spoken
        == (
            "Two models: about 10K tokens each (one model alone can use more). "
            "Uses about 13.0 of 15.1 GB free. Close other apps for more."
        )
    )
    assert disabled is False
    assert "a about 10K" in hint and "b about 4K (model limit)" in hint
    assert "1010" not in hint and "986" not in hint


@needs_node
def test_the_full_estimate(tmp_path):
    change = " estimate.context_tokens = 32768; estimate.free_bytes = 40e9;"
    words = ui(tmp_path, PAIR + change + ASK)[0]
    assert words == "Two models: 32K tokens each. Uses about 13.0 of 40.0 GB free."


@needs_node
def test_the_shrunk_figure_is_never_rounded_up(tmp_path):
    words = ui(tmp_path, PAIR + " estimate.context_tokens = 26624;" + ASK)[0]
    assert words.startswith("Two models: about 26K tokens each")


@needs_node
def test_refused_stays_on_screen_as_the_reason_start_is_off(tmp_path):
    change = (
        ' estimate.fits = false; estimate.refused = "Not enough free memory for two models:'
        ' 11.8 GB free.";'
    )
    words, spoken, disabled, hint, toasted = ui(tmp_path, PAIR + change + ASK)
    assert words == spoken == "Not enough free memory for two models: 11.8 GB free."
    assert disabled is True and hint == "" and toasted == 0


@needs_node
def test_the_fallback_words(tmp_path):
    words = ui(tmp_path, PAIR + " estimate.free_bytes = null;" + ASK)[0]
    assert words == (
        "Two models: about 10K tokens each (one model alone can use more). "
        "Uses about 13.0 GB. " + NO_READ
    )
    change = ' estimate.free_bytes = null; estimate.fits = false; estimate.refused = "No.";'
    assert ui(tmp_path, PAIR + change + ASK)[0] == "No. " + NO_READ


@needs_node
def test_a_refused_pair_is_asked_again_after_ten_seconds(tmp_path):
    out = ui(
        tmp_path,
        PAIR + ' estimate.fits = false; estimate.refused = "No.";'
        ' await pick("org/a", "org/b"); const off = $("#serverStartBtn").disabled;'
        " estimate.fits = true; estimate.refused = null; await show(current);"
        ' const early = sent("/api/server/estimate").length;'
        " const wait = state.estimateRetryAt - Date.now(); state.estimateRetryAt = Date.now();"
        " await show(current);"
        ' return [off, early, wait, $("#serverStartBtn").disabled,'
        ' sent("/api/server/estimate").length];',
    )
    off, early, wait, disabled, asked = out
    assert (off, early, disabled, asked) == (True, 1, False, 2)
    assert 9000 < wait <= 10000


@needs_node
def test_a_start_refused_by_the_server_brings_the_reason_on_screen(tmp_path):
    out = ui(
        tmp_path,
        PAIR + ' await pick("org/a", "org/b");'
        ' respond = (path) => path === "/api/server/start" ? reply({ error: "No." }, false)'
        ' : reply(path === "/api/server/estimate"'
        ' ? { ...estimate, fits: false, refused: "No." } : current);'
        ' await $("#serverStartBtn").listeners.click(); await show(current);'
        ' return [text("#serverStartWhy"), $("#serverStartBtn").disabled];',
    )
    assert out == ["No.", True]


@needs_node
def test_a_running_server_is_not_asked_again_or_called_refused(tmp_path):
    out = ui(
        tmp_path,
        PAIR + ' estimate.fits = false; estimate.refused = "No.";'
        " await show(running()); await new Promise((r) => setTimeout(r, 0));"
        ' const first = [text("#serverEstimate"), $("#serverEstimate").hidden,'
        ' sent("/api/server/estimate").length];'
        " state.estimateRetryAt = 0; await show(running());"
        ' return [...first, sent("/api/server/estimate").length];',
    )
    assert out == ["", True, 1, 1]


@needs_node
def test_memory_low_and_the_prompt_length(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ resident: ["org/a", "org/b"], context_tokens: 10240,'
        " memory_low: { free_bytes: 3e9, short_by_bytes: 1.2e9 } }));"
        ' const low = [text("#serverMultiTitle"), text("#serverMultiText"),'
        ' $("#serverMulti").hidden, text("#serverPromptHint"), $("#serverPromptHint").hidden];'
        ' await show(running({ resident: ["org/a", "org/b"], context_tokens: 10240,'
        " memory_low: null }));"
        ' const fine = $("#serverMulti").hidden;'
        ' await show(running({ repos: ["org/a"], context_tokens: null }));'
        ' return [...low, fine, $("#serverPromptHint").hidden];',
    )
    title, words, hidden, prompt, prompt_hidden, fine, one = out
    assert (title, hidden, prompt_hidden, fine, one) == (
        "Free memory is low", False, False, True, True,
    )  # fmt: skip
    assert words == (
        "Other apps now use memory the API server was counting on (1.2 GB short). Replies may "
        "slow down. Close apps, or restart the server for a smaller context."
    )
    assert prompt == (
        "Prompts longer than 10240 tokens are not kept in cache; the next turn reads them again."
    )


@needs_node
def test_the_oversize_words(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ resident: ["org/c"], oversize: ["org/c"], context_tokens: 10240,'
        " memory: { active_bytes: 9e9, peak_bytes: 9e9, budget_bytes: 13.1e9 },"
        " memory_low: { free_bytes: 3e9, short_by_bytes: 1e9 } }));"
        ' return [text("#serverMultiTitle"), text("#serverMultiText")];',
    )
    assert out[0] == "Loaded without enough memory"
    assert out[1] == (
        "c was loaded on its own: it does not fit in the 13.1 GB the API server may use now, "
        "with room for a 10240-token reply. It was loaded alone because it did not fit with another model. macOS may slow down "
        "or stop the API server. Free some memory or choose a smaller model."
    )
    assert "larger than the memory budget" not in out[1]


@needs_node
def test_the_oversize_words_without_figures(tmp_path):
    words = ui(
        tmp_path,
        'await show(running({ resident: ["org/c"], oversize: ["org/c"] }));'
        ' return text("#serverMultiText");',
    )
    assert "does not fit in the memory the API server may use now." in words
    assert "undefined" not in words and "NaN" not in words


def test_the_prompt_hint_sits_under_the_memory_line():
    html = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
    assert html.index('id="serverMemory"') < html.index('id="serverPromptHint"')
