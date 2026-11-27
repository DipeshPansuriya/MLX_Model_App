"""The API server refuses a too long prompt before reading it (row 838).

The installed mlx_lm handler and its own ``_tokenize`` run with a fake tokenizer:
one token per character of the messages and tools. No model is loaded.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import threading
import types
from functools import partial
from http.server import ThreadingHTTPServer

import pytest
from test_guard import GUARD, cache_repo
from test_ui_behaviour import needs_node, run

REPO, OTHER = "org/seven", "org/other"
GIB = 10**9
ROOMY = 100 * GIB  # 80% of RAM that is never the limit
# 2000 bytes of cache per token: 2 layers x 1 head x 250 x 2 x 2.
CONFIG = {
    "num_hidden_layers": 2,
    "num_attention_heads": 1,
    "hidden_size": 250,
    "max_position_embeddings": 100,
}


class Tokenizer:
    """The chat template: every character of messages and tools is a token."""

    has_chat_template = True
    has_tool_calling = True
    has_thinking = False
    response_initial_state = None
    tool_parser = None

    def apply_chat_template(self, messages, add_generation_prompt=True, tools=None, **kwargs):
        text = "".join(m["content"] for m in messages)
        if tools:
            text += json.dumps(tools)
        return list(range(len(text)))

    def encode(self, text, **kwargs):
        return list(range(len(text)))


BIG = {**CONFIG, "max_position_embeddings": 10**6}


def room_for(guard, tokens):
    """Bytes whose reply room holds ``tokens`` of CONFIG, with the 2 byte weights file."""
    return int((tokens + 0.5) * guard.ONE_MODEL_FACTOR * 2000) + guard.MEMORY_MARGIN + 2


def snapshot_for(cache, repo, config):
    folder = cache_repo(cache, repo)
    (folder / "config.json").write_text(json.dumps(config), "utf-8")
    return folder


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("mlx_guard_row838", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def limited(guard, tmp_path, monkeypatch):
    """``start(config, budget, kv_tokens, ...)`` serves the guarded handler; returns ``send``."""
    server = pytest.importorskip("mlx_lm.server")
    for owner, name in (
        (server.ResponseGenerator, "_tokenize"),
        (server.APIHandler, "handle_completion"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    servers = []

    def start(
        config=CONFIG, budget=10 * GIB, kv_tokens=None, config_text=None, ceiling=ROOMY, **extra
    ):
        folder = snapshot_for(tmp_path / f"hub{len(servers)}", REPO, config)
        if config_text is not None:
            (folder / "config.json").write_text(config_text, "utf-8")
        reports, generated = [], []
        problem = guard.install_prompt_limits(
            server,
            lambda *args, **fields: reports.append(fields),
            [REPO],
            budget,
            kv_tokens,
            lambda repo: folder,
            ceiling=ceiling,
            **extra,
        )
        assert problem is None
        cli = types.SimpleNamespace(
            allowed_origins=[],
            model=REPO,
            adapter_path=None,
            draft_model=None,
            num_draft_tokens=3,
            max_tokens=8,
            temp=0.0,
            top_p=1.0,
            top_k=0,
            min_p=0.0,
            chat_template_args={},
        )
        provider = types.SimpleNamespace(model_key=(REPO, None, None), processor=None, cli_args=cli)
        reader = object.__new__(server.ResponseGenerator)
        reader.model_provider = provider

        def generate(request, args, progress_callback=None):
            # As the generation thread does: tokenize first, and hand an error back.
            prompt = server.ResponseGenerator._tokenize(reader, Tokenizer(), request, args)[0]
            generated.append(len(prompt))
            raise RuntimeError("generation reached")

        generator = types.SimpleNamespace(cli_args=cli, generate=generate, is_healthy=True)
        handler = partial(server.APIHandler, generator, system_fingerprint="test")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)

        def send(content, path="/v1/chat/completions", **extra):
            body = {"messages": [{"role": "user", "content": content}], **extra}
            if path == "/v1/completions":
                body = {"prompt": content, **extra}
            conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
            try:
                conn.request("POST", path, json.dumps(body), {"Content-Type": "application/json"})
                response = conn.getresponse()
                return response.status, json.loads(response.read())
            finally:
                conn.close()

        return types.SimpleNamespace(send=send, generated=generated, reports=reports)

    yield start
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


def reached(answer):
    return answer == (404, {"error": "generation reached"})


class TestRefusal:
    @pytest.mark.parametrize("size", [99, 100])
    def test_up_to_the_limit_is_generated(self, limited, size):
        api = limited()
        assert reached(api.send("x" * size))
        assert api.generated == [size]

    @pytest.mark.parametrize("stream", [False, True])
    def test_over_the_limit_is_an_openai_400_before_generation(self, limited, stream):
        api = limited()
        status, body = api.send("x" * 101, stream=stream)
        assert status == 400
        error = body["error"]
        assert error["code"] == "context_length_exceeded"
        assert error["type"] == "invalid_request_error"
        assert error["param"] == "messages"
        assert "maximum context length is 100 tokens" in error["message"]
        assert "your prompt is 101 tokens" in error["message"]
        assert "new chat" in error["message"]
        assert "compact" in error["message"]
        assert api.generated == []

    def test_tools_count_toward_the_prompt(self, limited):
        api = limited()
        tools = [{"type": "function", "function": {"name": "n" * 60, "parameters": {}}}]
        assert reached(api.send("x" * 50))
        status, body = api.send("x" * 50, tools=tools)
        assert status == 400
        assert body["error"]["code"] == "context_length_exceeded"

    def test_a_plain_completion_is_checked_too(self, limited):
        assert limited().send("x" * 101, "/v1/completions")[0] == 400

    def test_the_server_keeps_answering_after_a_refusal(self, limited):
        api = limited()
        assert api.send("x" * 101)[0] == 400
        assert reached(api.send("x"))

    def test_the_enforced_limit_is_reported(self, limited):
        assert limited().reports == [{"prompt_limits": {REPO: 100}}]


class TestWhichLimit:
    def refused_at(self, api, limit):
        assert reached(api.send("x" * limit)), limit
        assert api.send("x" * (limit + 1))[0] == 400, limit

    def test_two_models_use_the_context_the_server_was_started_with(self, limited):
        self.refused_at(limited(kv_tokens=40), 40)

    def test_the_models_own_limit_wins_when_smaller(self, limited):
        self.refused_at(limited(kv_tokens=4000), 100)

    def test_one_model_uses_the_memory_safe_context(self, limited, guard):
        api = limited(BIG, room_for(guard, 50000))
        limit = api.reports[0]["prompt_limits"][REPO]
        assert limit == 50000
        assert api.send("x" * (limit + 1))[0] == 400

    def test_one_model_is_not_limited_below_32k_when_80_percent_allows(self, limited, guard):
        self.refused_at(limited(BIG, room_for(guard, 10000)), guard.KV_TOKENS)

    def test_the_floor_is_the_models_own_limit_when_that_is_smaller(self, guard):
        config = {**CONFIG, "max_position_embeddings": 20000}
        tight = room_for(guard, 10000)
        assert guard.prompt_limit(config, 2, tight, None, ROOMY) == 20000

    def test_a_larger_safe_context_never_passes_the_models_limit(self, guard):
        config = {**CONFIG, "max_position_embeddings": 40000}
        roomy = room_for(guard, 50000)
        assert guard.prompt_limit(config, 2, roomy, None, ROOMY) == 40000

    def test_the_floor_never_passes_80_percent_of_ram(self, limited, guard):
        api = limited(BIG, room_for(guard, 10000), ceiling=room_for(guard, 16000))
        self.refused_at(api, 16000)
        assert api.reports == [{"prompt_limits": {REPO: 16000}}]

    def test_the_safe_context_never_passes_80_percent_of_ram(self, guard):
        over = room_for(guard, 50000)
        assert guard.prompt_limit(BIG, 2, over, None, room_for(guard, 40000)) == 40000

    @pytest.mark.parametrize("stream", [False, True])
    def test_no_room_within_80_percent_refuses_every_prompt_and_says_why(
        self, limited, guard, stream
    ):
        api = limited(BIG, room_for(guard, 10000), ceiling=guard.MEMORY_MARGIN)
        status, body = api.send("x", stream=stream)
        assert status == 400
        message = body["error"]["message"]
        assert "80% of this Mac's memory" in message
        assert "smaller model" in message
        assert "compact" not in message
        assert api.generated == []

    @pytest.mark.parametrize("chosen, expected", [(200_000, 10_000), (8000, 8000)])
    def test_an_unreadable_cache_size_checks_the_users_figure_with_the_fallback(
        self, guard, chosen, expected
    ):
        per_token = guard.UNKNOWN_KV_PER_TOKEN
        room = int(10_000.5 * guard.ONE_MODEL_FACTOR * per_token)
        ceiling = GIB + guard.MEMORY_MARGIN + room
        assert guard.prompt_limit({}, GIB, 0, None, ceiling, chosen) == expected

    @pytest.mark.parametrize(
        "chosen, holds, expected",
        [(100_000, 10**6, 32768), (100_000, 10_000, 10_000), (8192, 10**6, 8192)],
    )
    def test_unread_weights_cap_the_users_figure(self, guard, chosen, holds, expected):
        ceiling = room_for(guard, holds)
        assert guard.prompt_limit(BIG, 0, 0, None, ceiling, chosen) == expected

    @pytest.mark.parametrize("unread", ["config", "weights"])
    def test_a_fallback_figure_is_said_once_in_the_log(
        self, limited, guard, monkeypatch, capsys, unread
    ):
        if unread == "weights":
            monkeypatch.setattr(guard, "weights_bytes", lambda snapshot: 0)
        config = {} if unread == "config" else BIG
        api = limited(config, max_context=10**9, ceiling=64 * GIB)
        for _ in range(2):
            assert reached(api.send("x"))
        assert api.reports[0]["prompt_limits"][REPO] < 10**9
        said = [line for line in capsys.readouterr().err.splitlines() if "fallback" in line]
        assert len(said) == 1 and REPO in said[0] and "cannot be read" in said[0]

    def test_two_models_get_no_floor(self, guard):
        assert guard.prompt_limit(BIG, 2, ROOMY, 8192, ROOMY) == 8192
        assert guard.prompt_limit(BIG, 2, ROOMY, 8192, 0) == 8192

    def test_the_ceiling_is_80_percent_of_ram(self, guard, monkeypatch):
        monkeypatch.setattr(guard, "ram_bytes", lambda: 32 * 2**30)
        assert guard.memory_ceiling() == int(0.80 * 32 * 2**30)
        assert "memory_ceiling" in guard.main.__code__.co_names

    @pytest.mark.parametrize(
        "config, weights, budget",
        [({}, GIB, 10 * GIB), (CONFIG, 0, 10 * GIB), (CONFIG, GIB, 0)],
    )
    def test_an_unknown_memory_figure_leaves_the_models_limit(
        self,
        guard,
        config,
        weights,
        budget,
    ):
        assert guard.prompt_limit(config, weights, budget, None, ROOMY) == config.get(
            "max_position_embeddings"
        )

    def test_nothing_known_refuses_nothing(self, limited):
        api = limited(config_text="not json", budget=0)
        assert reached(api.send("x" * 100000))
        assert api.reports == []

    def test_a_changed_mlx_lm_starts_without_the_check(self, guard):
        problem = guard.install_prompt_limits(object(), print, [REPO], 1, None, str, ceiling=1)
        assert problem

    def test_main_installs_the_check(self, guard):
        assert "install_prompt_limits" in guard.main.__code__.co_names


class TestShownInTheApp:
    def test_the_snapshot_passes_the_reported_limits_on(self, app_module):
        app = app_module
        app.APP_DIR.mkdir(parents=True, exist_ok=True)
        limits = {REPO: 100, OTHER: "x", "org/zero": 0}
        app._resident_file().write_text(json.dumps({"pid": 7, "prompt_limits": limits}))
        assert app._resident_view(7)["prompt_limits"] == {REPO: 100, "org/zero": 0}
        assert app._resident_view(8)["prompt_limits"] == {}

    def test_the_servers_refusal_reaches_chat_in_its_own_words(self, app_module):
        said = json.dumps({"error": {"message": "maximum context length is 5 tokens"}})
        stream = types.SimpleNamespace(read=lambda n: said.encode(), status=400, reason="Bad")
        assert "maximum context length is 5 tokens" in app_module._chat_refusal(stream)

    @needs_node
    def test_the_api_server_card_shows_the_limit(self, tmp_path):
        out = run(
            tmp_path,
            """
            const server = { running: true, repos: ["org/seven"], repo: "org/seven",
              endpoint: "e", lines: [], tool_support: {}, ready: true,
              prompt_limits: { "org/seven": 52224 } };
            render(snap({ server }));
            const el = document.querySelector("#serverPromptLimit");
            const shown = [el.hidden, el.textContent];
            render(snap({ server: { ...server, running: false } }));
            return [...shown, el.hidden];
            """,
        )
        assert out[0] is False
        assert "seven 52224 tokens" in out[1]
        assert out[2] is True
