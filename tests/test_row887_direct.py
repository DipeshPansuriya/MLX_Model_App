"""Row 887: MLX is mlx_lm's own server. Command, model name, probe and Load Test."""

import itertools
import re
import sys
import threading
import urllib.error
import urllib.request

import pytest
from test_app_robustness import make_model

import model_info

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

    def test_an_adopted_server_is_asked_for_health_and_not_warmed(self, app_module, probe):
        assert app_module._probe_direct(4242, adopted=True) is None
        assert app_module.STATE.server_verdict == "ready"
        assert probe["asked"] == ["health"]

    def test_an_adopted_server_whose_model_failed_is_not_ready(self, app_module, probe):
        probe["health"] = 503
        assert "could not be loaded" in app_module._probe_direct(4242, adopted=True)
        assert app_module.STATE.server_verdict != "ready"

    def test_an_adopted_server_that_does_not_answer_is_not_ready_yet(self, app_module, probe):
        probe["health"] = None
        assert app_module._probe_direct(4242, adopted=True) is None
        assert app_module.STATE.server_verdict is None

    @pytest.mark.parametrize(("health", "stopped"), [(503, True), (200, False), (None, False)])
    def test_an_adopted_server_is_still_watched_once_ready(
        self, app_module, probe, health, stopped
    ):
        """/health answers 200 while the model loads, so a failure can come later."""
        app_module.STATE.server_verdict = "ready"
        probe["health"] = health
        why = app_module._probe_direct(4242, adopted=True)
        assert ("could not be loaded" in (why or "")) is stopped
        assert probe["asked"] == ["health"]

    def test_a_ready_adopted_server_gets_nothing_when_another_holds_the_port(
        self, app_module, probe
    ):
        app_module.STATE.server_verdict = "ready"
        probe["holders"] = {7}
        assert app_module._probe_direct(4242, adopted=True) is None
        assert probe["asked"] == []

    def test_a_ready_server_this_app_started_is_not_asked_again(self, app_module, probe):
        app_module.STATE.server_verdict = "ready"
        assert app_module._probe_direct(4242) is None
        assert probe["asked"] == []

    def test_the_watcher_stops_an_adopted_server_that_fails_after_it_was_ready(
        self, app_module, probe, monkeypatch
    ):
        app, ended = app_module, []

        def sleep(_seconds):
            probe["health"] = 503  # the load fails after the first look
            assert len(slept) < 40, "the failed load was never noticed"
            slept.append(1)

        slept = []
        monkeypatch.setattr(app.time, "sleep", sleep)
        monkeypatch.setattr(app, "_pid_alive", lambda pid: True)
        monkeypatch.setattr(app, "_still_our_server", lambda pid, repo: True)
        monkeypatch.setattr(app, "_remember_models", lambda pid: None)
        monkeypatch.setattr(app, "_end_direct", lambda pid, proc, repo: ended.append(pid))
        app._watch_server(4242, "org/model", None)
        assert ended == [4242]
        assert app.STATE.server_pid is None
        assert app.STATE.server_last_error == "its model could not be loaded"
        assert "could not be loaded" in app.STATE.server_down_notice


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


# repo names are checked without the removed guard module ----------------------- #
@pytest.mark.parametrize(
    ("repo", "valid"),
    [
        ("org/name", True),
        ("name", True),
        ("org/Qwen3.5-4B_4bit", True),
        ("--model", False),
        ("-x/y", False),
        ("../x", False),
        ("org/../x", False),
        ("a/b/c", False),
        ("/etc/passwd", False),
        ("org/name ", False),
        ("org/näme", False),
        ("o/" + "n" * 300, False),
        ("", False),
        (None, False),
    ],
)
def test_a_repo_name_is_checked(app_module, repo, valid):
    assert app_module._valid_repo(repo) is valid


def test_the_repo_name_check_accepts_the_same_ids_without_backtracking():
    # The one-pattern form it replaces (CodeQL: polynomial regular expression).
    old = re.compile(r"(?:[A-Za-z0-9_][\w.-]*/)?[A-Za-z0-9_][\w.-]*", re.ASCII)
    for length in range(7):
        for chars in itertools.product("a0_.-/ \né", repeat=length):
            text = "".join(chars)
            assert model_info.is_repo_id(text) is bool(old.fullmatch(text)), repr(text)
    # One repeated class and nothing after it that can fail: nothing to retry.
    assert model_info.REPO_PART.pattern == r"[A-Za-z0-9_][\w.-]*"
    long = "0" * 1_000_000
    assert model_info.is_repo_id(f"{long}/{long}") is True
    assert model_info.is_repo_id(f"{long}/{long}/") is False


@pytest.mark.parametrize(
    ("path", "status"),
    [("/api/engines?repo=org/name", 200), ("/api/engines?repo=--x", 400)],
)
def test_the_engines_route_answers(app_module, base_url, path, status):
    try:
        with urllib.request.urlopen(base_url + path, timeout=10) as reply:
            got = reply.status
    except urllib.error.HTTPError as error:
        got = error.code
    assert got == status
