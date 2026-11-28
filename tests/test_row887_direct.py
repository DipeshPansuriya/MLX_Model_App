"""Row 887: MLX is mlx_lm's own server. Command, model name, probe and Load Test."""

import sys
import threading

import pytest
from test_app_robustness import make_model

REPO = "org/model"
NO_SETTINGS = dict.fromkeys(
    ("server_max_reply_tokens", "prompt_cache_size", "prompt_cache_bytes", "max_context_tokens")
)
ORIGINS = "http://127.0.0.1:8765,http://localhost:8765"


class TestCommand:
    def test_default_command_is_exactly_mlx_lm_server(self, app_module):
        assert app_module._server_command(REPO, NO_SETTINGS, 8080) == [
            sys.executable, "-m", "mlx_lm", "server",
            "--model", REPO, "--host", "127.0.0.1", "--port", "8080",
            "--max-tokens", "8192", "--allowed-origins", ORIGINS,
        ]  # fmt: skip

    def test_only_set_values_are_added_and_max_context_never(self, app_module):
        settings = {
            "server_max_reply_tokens": 100,
            "prompt_cache_size": 3,
            "prompt_cache_bytes": 200_000_000,
            "max_context_tokens": 9000,
        }
        command = app_module._server_command(REPO, settings, 9000, {"kv_bits": 8})
        assert command[command.index("--max-tokens") + 1] == "100"
        assert command[-6:] == [
            "--prompt-cache-size", "3", "--prompt-cache-bytes", "200000000", "--kv-bits", "8",
        ]  # fmt: skip
        assert command[command.index("--port") + 1] == "9000"
        assert not {"--max-context", "--trust-remote-code"} & set(command)
        assert command.count("9000") == 1

    def test_environment_is_clean_and_offline(self, app_module, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "not-for-the-server")
        env = app_module._server_env()
        assert env["HF_HUB_OFFLINE"] == "1"
        assert "HF_TOKEN" not in env
        assert env == {**app_module.engine_runtime.minimal_environment(), "HF_HUB_OFFLINE": "1"}

    def test_both_engines_are_asked_for_default_model(self, app_module):
        assert {e.request_model for e in app_module.engines.REGISTRY if e.selectable} == {
            "default_model"
        }


@pytest.fixture
def probe(app_module, monkeypatch):
    """An MLX server with pid 4242, and what the port, /health and the warm-up answer."""
    seen = {"holders": {4242}, "health": 200, "warm": True, "asked": []}
    runtime = app_module.engine_runtime

    def health(port):
        seen["asked"].append("health")
        return seen["health"]

    def warm_up(port, key, model, seconds):
        seen["asked"].append(("warm", key, model))
        return seen["warm"]

    monkeypatch.setattr(runtime, "listeners", lambda port: seen["holders"])
    monkeypatch.setattr(runtime, "listener_is", lambda port, pid: True)
    monkeypatch.setattr(runtime, "health", health)
    monkeypatch.setattr(runtime, "warm_up", warm_up)
    state = app_module.STATE
    state.server_pid, state.server_engine, state.server_verdict = 4242, "mlx", None
    return seen


class TestProbe:
    def test_ready_after_health_and_one_reply_without_a_key(self, app_module, probe):
        assert app_module._probe_direct(4242) is None
        assert app_module.STATE.server_verdict == "ready"
        assert probe["asked"] == ["health", ("warm", None, "default_model")]

    def test_503_means_the_model_failed(self, app_module, probe):
        probe["health"] = 503
        assert "could not be loaded" in app_module._probe_direct(4242)
        assert probe["asked"] == ["health"]

    def test_another_listener_gets_nothing(self, app_module, probe):
        probe["holders"] = {4242, 7}
        assert "another program listens on port 8080" in app_module._probe_direct(4242)
        assert probe["asked"] == []

    def test_an_adopted_server_is_not_asked(self, app_module, probe):
        assert app_module._probe_direct(4242, adopted=True) is None
        assert app_module.STATE.server_verdict == "ready"
        assert probe["asked"] == []


@pytest.mark.parametrize(
    ("command", "ours"),
    [
        (f"python -m mlx_lm server --model {REPO} --host 127.0.0.1 --port 8080", True),
        (f"python /a/mlx_guard.py --model {REPO} --host 127.0.0.1 --port 8080", True),
        ("python -m mlx_lm server --model org/other --host 127.0.0.1 --port 8080", False),
        (f"python -m mlx_lm server --model {REPO} --host 127.0.0.1 --port 9999", False),
        (f"python other.py --model {REPO} --host 127.0.0.1 --port 8080", False),
    ],
)
def test_which_leftover_process_is_ours(app_module, monkeypatch, command, ours):
    monkeypatch.setattr(app_module, "_pid_alive", lambda pid: True)
    monkeypatch.setattr(app_module, "_ps_field", lambda pid, field: command)
    assert app_module._still_our_server(4242, REPO) is ours


def model_of(app_module, limit):
    root = make_model(app_module, REPO)
    config = root / "snapshots" / "abc" / "config.json"
    config.write_text(f'{{"max_position_embeddings": {limit}}}', encoding="utf-8")


class TestLoadTest:
    def test_a_1m_model_offers_its_own_limit_as_the_top_size(self, app_module):
        model_of(app_module, 1_000_000)
        sizes = app_module.loadtest_view(REPO)["sizes"]
        allowed = [entry["size"] for entry in sizes["offered"] if entry["allowed"]]
        assert sizes["max"] == max(allowed) == 1_000_000

    def test_a_test_at_the_limit_starts_with_the_direct_command(self, app_module, monkeypatch):
        model_of(app_module, 1_000_000)
        monkeypatch.setattr(app_module, "_complete_models", lambda: {REPO})
        monkeypatch.setattr(app_module, "_loadtest_command", lambda: [sys.executable, "-c", ""])
        before = set(threading.enumerate())
        status, reply = app_module.start_loadtest(REPO, [1_000_000])
        for thread in set(threading.enumerate()) - before:
            thread.join(timeout=10)
        assert status == 200, reply
        record = app_module.STATE.loadtest["record"]
        assert record["flags"][:2] == ["--max-tokens", "8192"]
        # No percentage limit exists, so none is recorded or shown.
        assert not {"limit_percent", "margin_percent"} & {*record, *record["values"]}
