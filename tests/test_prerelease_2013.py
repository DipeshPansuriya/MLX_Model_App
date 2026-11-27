"""Pre-release fixes for 2.0.13: the 80% rule, the cache cap and the load test port.

No model is loaded; every server here listens on a free loopback port.
"""

# ruff: noqa: F811 - the imported fixtures are used by name
from __future__ import annotations

import io
import json
import os
import re
import socket
import threading
import types
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from test_row837_chat import ONE, PID, running
from test_row837_ui import ui as chat_ui
from test_row838_prompt_limit import BIG, REPO, Tokenizer, guard, room_for, snapshot_for  # noqa: F401
from test_row838_settings import after, launch, one_estimate, write
from test_row848_loadtest import lt, run_job  # noqa: F401
from test_ui_behaviour import needs_node, run

ROOT = Path(__file__).resolve().parent.parent
RAM = 32 * 2**30
CLOSE = ("Close other apps", "web browsers", "Start Server")
HUGE = 10**11


def says_what_to_do(text, percent):
    for words in (*CLOSE, f"{percent}%"):
        assert words in text, words
    assert "set Max context" not in text


# 1. The user's figure also leaves room for other apps ------------------------- #
class TestStrictClamp:
    def limit(self, guard, chosen, room, config=BIG):
        ceiling = room_for(guard, 60_000)
        others = ceiling - room_for(guard, room)
        return guard.prompt_limit(config, 2, ceiling, None, ceiling, chosen, others)

    def test_what_other_apps_hold_comes_off_the_users_figure(self, guard):
        assert self.limit(guard, 40_000, 6000) == 6000

    def test_a_figure_that_fits_applies(self, guard):
        assert self.limit(guard, 5000, 6000) == 5000

    def test_a_small_figure_that_fits_applies_below_the_floor(self, guard):
        assert self.limit(guard, 2000, 3000) == 2000

    def test_a_clamp_below_4096_accepts_nothing(self, guard):
        assert self.limit(guard, 40_000, 3000) == 0

    def test_the_floor_is_the_models_own_smaller_limit(self, guard):
        config = {**BIG, "max_position_embeddings": 2048}
        assert self.limit(guard, 40_000, 3000, config) == 2048

    def test_at_72_percent_the_users_40000_is_refused(self, app_module, monkeypatch, other_apps):
        guard = app_module._guard_module()
        monkeypatch.setattr(guard, "ram_bytes", lambda: RAM)
        monkeypatch.setattr(app_module, "_server_budget", lambda: int(0.52 * RAM))
        qwen = {
            "num_hidden_layers": 28,
            "num_attention_heads": 28,
            "num_key_value_heads": 4,
            "hidden_size": 3584,
            "max_position_embeddings": 10**6,
        }
        monkeypatch.setattr(app_module, "_server_models", lambda r: [(REPO, qwen, 4_300_000_000)])
        write(app_module, {"max_context_tokens": 40_000})
        other_apps.percent = 72.0
        got = app_module.server_memory_estimate([REPO])
        assert got["models"][0]["safe_context"] == 0
        says_what_to_do(got["context_warning"], 72)
        view = app_module.config_view(REPO)
        assert view["applied_context"] == 0
        says_what_to_do(view["context_message"], 72)

    def test_a_lowered_figure_says_what_to_close(self, app_module, monkeypatch, other_apps):
        other_apps.percent = 55.0
        guard = app_module._guard_module()
        monkeypatch.setattr(guard, "ram_bytes", lambda: 0)  # others: 0 bytes
        write(app_module, {"max_context_tokens": 7000})
        warning = one_estimate(app_module, monkeypatch)["context_warning"]
        assert "6000 tokens apply" in warning
        says_what_to_do(warning, 55)

    def test_the_guards_log_says_it_too(self, guard, tmp_path, other_apps, capsys, tokenize):
        other_apps.percent = 82.0
        tokenize.start(ceiling=guard.MEMORY_MARGIN, max_context=40_000)
        says_what_to_do(capsys.readouterr().err, 82)


# The guard's own wrappers, around a tokenizer that returns the asked count ---- #
class Cache:
    def __init__(self, nbytes=0):
        self.nbytes, self.max_bytes, self.trimmed = nbytes, 1 << 63, []

    def trim_to(self, n_bytes):
        self.trimmed.append(n_bytes)
        self.nbytes = n_bytes


@pytest.fixture
def tokenize(guard, tmp_path, monkeypatch):
    server = pytest.importorskip("mlx_lm.server")

    def counted(self, tokenizer, request, args):
        return (list(range(request)),)

    monkeypatch.setattr(server.ResponseGenerator, "_tokenize", counted)
    monkeypatch.setattr(server.APIHandler, "handle_completion", server.APIHandler.handle_completion)
    box = types.SimpleNamespace(reports=[], server=server)

    def start(config=BIG, ceiling=HUGE, kv_tokens=None, config_text=None, cache_bytes=None, **kw):
        folder = snapshot_for(tmp_path / "hub", REPO, config)
        if config_text is not None:
            (folder / "config.json").write_text(config_text, "utf-8")
        problem = guard.install_prompt_limits(
            server,
            lambda *a, **fields: box.reports.append(fields),
            [REPO],
            HUGE,
            kv_tokens,
            lambda repo: folder,
            ceiling=ceiling,
            **kw,
        )
        assert problem is None
        cli = types.SimpleNamespace(prompt_cache_bytes=cache_bytes)
        box.reader = types.SimpleNamespace(
            model_provider=types.SimpleNamespace(model_key=(REPO, None, None), cli_args=cli),
            prompt_cache=Cache(),
        )

        def ask(prompt, max_tokens=8):
            args = types.SimpleNamespace(max_tokens=max_tokens)
            server.ResponseGenerator._tokenize(box.reader, None, prompt, args)
            return args.max_tokens

        return ask

    box.start = start
    return box


# 2 and 4. A limit of 0 -------------------------------------------------------- #
class TestNoPromptAccepted:
    def test_zero_is_reported(self, guard, tokenize):
        tokenize.start(ceiling=guard.MEMORY_MARGIN)
        assert tokenize.reports == [{"prompt_limits": {REPO: 0}}]

    def test_the_refusal_says_what_to_close_and_the_percent(self, guard, other_apps):
        other_apps.percent = 82.4
        says_what_to_do(str(guard.PromptTooLong(10, 0)), 82)

    def test_an_unreadable_percent_is_left_out(self, guard):
        message = str(guard.PromptTooLong(10, 0))
        assert "%" not in message.rsplit("more than 80%", maxsplit=1)[-1]
        for words in CLOSE:
            assert words in message

    @pytest.mark.parametrize(
        ("limit", "code"), [(0, "insufficient_memory"), (40, "context_length_exceeded")]
    )
    def test_zero_has_its_own_error_code(self, limited_http, guard, limit, code):
        status, body = limited_http(limit)
        assert status == 400
        assert body["error"]["code"] == code

    def test_the_app_keeps_zero_and_adds_the_percent(self, app_module, other_apps):
        other_apps.percent = 82.0
        app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
        limits = {REPO: 0, ONE: 5000, "org/bad": -1, "org/text": "x"}
        app_module._resident_file().write_text(json.dumps({"pid": 7, "prompt_limits": limits}))
        view = app_module._resident_view(7)
        assert view["prompt_limits"] == {REPO: 0, ONE: 5000}
        assert view["memory_used_percent"] == 82
        app_module._resident_file().write_text(
            json.dumps({"pid": 7, "prompt_limits": {REPO: 5000}})
        )
        assert app_module._resident_view(7)["memory_used_percent"] is None

    @needs_node
    def test_the_card_says_stop_then_start(self, tmp_path):
        out = run(
            tmp_path,
            """
            const server = { running: true, repos: ["org/seven"], repo: "org/seven",
              endpoint: "e", lines: [], tool_support: {}, ready: true,
              memory_used_percent: 82, prompt_limits: { "org/seven": 0 } };
            render(snap({ server }));
            const line = document.querySelector("#serverPromptLimit");
            const why = document.querySelector("#serverWhy");
            return [line.hidden, line.textContent, why.hidden, why.textContent];
            """,
        )
        assert out[0] is False and out[2] is False
        for text in (out[1], out[3]):
            assert "seven accepts no prompt" in text
            assert "Stop, then Start" in text
        assert "82%" in out[3] and "Close other apps" in out[3]
        assert "A longer prompt" not in out[1]

    @needs_node
    def test_the_row_does_not_promise_the_old_estimate(self, tmp_path):
        out = run(
            tmp_path,
            """
            const server = { running: true, repos: ["org/seven"], repo: "org/seven",
              endpoint: "e", lines: [], tool_support: {}, ready: true,
              prompt_limits: { "org/seven": 0 } };
            render(snap({ server }));
            const view = { live: true, two: false, server, estimate: { models:
              [{ repo: "org/seven", safe_context: 32768, context_limit: 1000000 }] } };
            return contextWords("org/seven", view);
            """,
        )
        assert out == "accepts no prompt"

    def test_the_runbook_says_stop_then_start(self):
        text = (ROOT / "docs" / "RUNBOOK.md").read_text("utf-8")
        part = text[text.index("one model: the safe context follows") :][:900]
        assert "**Stop**, then **Start Server**" in part
        assert "Apply changes" not in part and "Or set **Max context**" not in part


@pytest.fixture
def limited_http(guard, tmp_path, monkeypatch):
    """The guarded handler's refusal over HTTP, for a server limit of ``limit``."""
    import http.client
    from functools import partial

    server = pytest.importorskip("mlx_lm.server")
    for owner, name in (
        (server.ResponseGenerator, "_tokenize"),
        (server.APIHandler, "handle_completion"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    servers = []

    def ask(limit):
        folder = snapshot_for(tmp_path / "hub", REPO, BIG)
        guard.install_prompt_limits(
            server,
            lambda *a, **k: None,
            [REPO],
            HUGE,
            limit or None,
            lambda repo: folder,
            ceiling=HUGE if limit else guard.MEMORY_MARGIN,
        )
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
            server.ResponseGenerator._tokenize(reader, Tokenizer(), request, args)
            raise RuntimeError("generation reached")

        generator = types.SimpleNamespace(cli_args=cli, generate=generate, is_healthy=True)
        handler = partial(server.APIHandler, generator, system_fingerprint="test")
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        servers.append(httpd)
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=10)
        try:
            body = {"messages": [{"role": "user", "content": "x" * 100}]}
            conn.request(
                "POST",
                "/v1/chat/completions",
                json.dumps(body),
                {"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            return response.status, json.loads(response.read())
        finally:
            conn.close()

    yield ask
    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()


# L1. Prompt plus reply stay within the limit ---------------------------------- #
class TestReplyClamp:
    @pytest.mark.parametrize(
        ("prompt", "asked", "applied"),
        [(60, 1_000_000, 40), (60, 40, 40), (60, 8, 8), (99, 500, 1), (100, 500, 1)],
    )
    def test_the_reply_is_clamped(self, tokenize, prompt, asked, applied):
        ask = tokenize.start({**BIG, "max_position_embeddings": 100})
        assert ask(prompt, asked) == applied

    def test_two_models_clamp_to_their_context(self, tokenize):
        assert tokenize.start(kv_tokens=200)(150, 10_000) == 50

    def test_an_unknown_limit_changes_nothing(self, tokenize):
        assert tokenize.start(config_text="not json")(60, 10**9) == 10**9

    def test_mlx_lms_arguments_can_be_changed(self):
        server = pytest.importorskip("mlx_lm.server")
        assert server.GenerationArguments.__dataclass_params__.frozen is False


# 5. The prompt cache --------------------------------------------------------- #
class TestCacheCap:
    PER = 2000  # bytes per token of BIG

    def test_one_model_gets_two_conversations_of_the_limit(self, tokenize):
        ask = tokenize.start({**BIG, "max_position_embeddings": 100})
        ask(10)
        assert tokenize.reader.prompt_cache.max_bytes == 2 * self.PER * 100

    @pytest.mark.parametrize(("asked", "cap"), [(1000, 1000), (10**12, 400_000)])
    def test_the_users_figure_only_lowers_it(self, tokenize, asked, cap):
        ask = tokenize.start({**BIG, "max_position_embeddings": 100}, cache_bytes=asked)
        ask(10)
        assert tokenize.reader.prompt_cache.max_bytes == cap

    def test_an_earlier_models_cache_is_trimmed_to_it(self, tokenize):
        ask = tokenize.start({**BIG, "max_position_embeddings": 100})
        tokenize.reader.prompt_cache.nbytes = 10**9
        ask(10)
        assert tokenize.reader.prompt_cache.trimmed == [400_000]

    def test_an_unreadable_config_still_has_a_cap(self, tokenize, guard):
        tokenize.start(config_text="not json")(10)
        cap = 2 * guard.UNKNOWN_KV_PER_TOKEN * guard.KV_TOKENS
        assert tokenize.reader.prompt_cache.max_bytes == cap

    def test_two_models_keep_the_planned_cap(self, tokenize):
        ask = tokenize.start(kv_tokens=200, cache_bytes=12345)
        tokenize.reader.prompt_cache.max_bytes = 12345
        ask(10)
        assert tokenize.reader.prompt_cache.max_bytes == 12345

    @pytest.mark.parametrize(("chosen", "passed"), [(None, "2"), (5, "5")])
    def test_the_command_passes_a_cache_size(self, app_module, monkeypatch, chosen, passed):
        if chosen:
            write(app_module, {"prompt_cache_size": chosen})
        cmd = launch(app_module, monkeypatch)["cmd"]
        assert after(cmd, "--prompt-cache-size") == passed

    def test_the_settings_view_says_the_defaults(self, app_module):
        shown = app_module.settings_view()
        assert shown["prompt_cache_size"]["effective"] == "automatic (2)"
        assert "calculated" in shown["prompt_cache_bytes"]["effective"]
        assert "not passed" not in shown["prompt_cache_bytes"]["effective"]


# 6. Load test sizes above what the server accepts ------------------------------ #
class TestLoadTestRefused:
    @pytest.mark.parametrize("mode", ["limit-low", "refused"])
    def test_the_size_is_skipped_not_failed(self, lt, tmp_path, mode):
        status, reason, rows, _, spawned = run_job(lt, tmp_path, mode=mode, sizes=(4096, 8192))
        assert (status, rows[4096]["status"], rows[8192]["status"]) == (
            "memory",
            "skipped",
            "skipped",
        )
        assert "Close other apps" in reason
        assert rows[4096]["steps_done"] == 0 and len(spawned) == 1

    def test_a_size_within_the_reported_limit_runs(self, lt, tmp_path):
        status, _, rows, _, _ = run_job(lt, tmp_path, mode="limit-4096", sizes=(4096, 8192))
        assert rows[4096]["status"] == "passed"
        assert rows[8192]["status"] == "skipped" and status == "memory"


# L2. The test server's port belongs to the child ------------------------------- #
class TestPortOwner:
    def test_a_listener_is_recognised_by_its_pid(self, lt):
        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            sock.listen()
            port = sock.getsockname()[1]
            assert lt.listens(os.getpid(), port) is True
            assert lt.listens(os.getppid(), port) is False

    def test_another_programs_answer_is_not_trusted(self, lt, tmp_path, monkeypatch):
        posts = []

        class Intruder(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_POST(self):
                posts.append(self.path)
                self.send_error(500)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), Intruder)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            monkeypatch.setattr(lt, "free_port", lambda: httpd.server_address[1])
            status, reason, _, _, spawned = run_job(lt, tmp_path, mode="no-listen", sizes=(4096,))
        finally:
            httpd.shutdown()
            httpd.server_close()
        assert posts == []
        assert status == "failed" and "another program" in reason
        assert len(spawned) == lt.PORT_ATTEMPTS


# I1 and I2. The chat proxy ---------------------------------------------------- #
class Stream(io.BytesIO):
    status = 200


class TestChatProxy:
    @pytest.mark.parametrize("bad", ["x/y", "5", "", "1.5/3", "²/3"])
    def test_a_bad_keepalive_does_not_end_the_reply(self, app_module, bad):
        lines = (
            f": keepalive {bad}\n\n"
            'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n'
            "data: [DONE]\n\n"
        )
        conn = types.SimpleNamespace(
            request=lambda *a: None, getresponse=lambda: Stream(lines.encode())
        )
        said = []
        app_module._read_reply(conn, b"{}", said.append)
        assert [kind for kind, _ in said] == ["token", "end"]

    def test_an_adopted_pid_that_is_not_our_server_is_not_asked(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "_pid_alive", app_module._pid_alive)
        running(app_module, owned=False)
        asked = []
        monkeypatch.setattr(app_module, "_is_our_server", lambda *a, **k: asked.append(a) or False)
        assert "Start the API server first" in app_module._chat_problem(ONE)
        assert asked and asked[0][:2] == (PID, ONE)

    def test_our_adopted_server_is_asked(self, app_module, monkeypatch):
        monkeypatch.setattr(app_module, "_pid_alive", app_module._pid_alive)
        running(app_module, owned=False)
        monkeypatch.setattr(app_module, "_is_our_server", lambda *a, **k: True)
        assert app_module._chat_problem(ONE) is None

    def test_an_ended_child_is_not_asked(self, app_module):
        running(app_module)
        app_module.STATE.server_proc.poll = lambda: 1
        assert "Start the API server first" in app_module._chat_problem(ONE)


# 7. The card and the page ------------------------------------------------------ #
class TestCard:
    @pytest.mark.parametrize(("auto", "sized"), [(6000, 6000), (60_000, 32768)])
    def test_one_model_is_sized_at_what_it_will_serve(self, app_module, monkeypatch, auto, sized):
        got = one_estimate(app_module, monkeypatch, auto_tokens=auto)
        guard = app_module._guard_module()
        assert got["context_tokens"] == sized
        assert got["reply_bytes"] == guard.kv_reserve(BIG, sized, guard.ONE_MODEL_FACTOR)

    def test_a_refusing_model_is_sized_at_the_smallest_it_serves(self, app_module, monkeypatch):
        got = one_estimate(app_module, monkeypatch, auto_tokens=100)
        assert got["models"][0]["safe_context"] == 0
        assert got["context_tokens"] == app_module._guard_module().MIN_AUTO_CONTEXT

    def test_chat_only_hints_are_described(self):
        html = (ROOT / "ui" / "index.html").read_text("utf-8")
        hints = re.findall(r'<span class="hint"( id="([\w-]+)")?>Chat only', html)
        assert len(hints) >= 3
        for _, name in hints:
            assert name, "a Chat only hint has no id"
            assert re.search(rf'aria-describedby="[^"]*\b{name}\b', html), name


# Browser test defects D1 to D7 and D9 ------------------------------------------ #
STOPPED = '["done", { tokens: 1, tps: 1, stopped: true }]'


@needs_node
class TestBrowserDefects:
    def test_d1_the_second_click_of_send_does_not_stop(self, tmp_path):
        out = chat_ui(
            tmp_path,
            """
            show(srv()); respond = sse([]);
            wire(); const stop = el("#stopBtn").listeners.click;
            const stops = () => calls.filter((c) => c.path === "/api/chat/stop").length;
            await stop({ detail: 2 }); const second = stops();
            await stop({ detail: 1 }); await stop({ detail: 0 });
            return [second, stops()];
            """,
        )
        assert out == [0, 2]

    def test_d2_a_stopped_reply_is_said_as_stopped(self, tmp_path):
        out = chat_ui(tmp_path, f'return say([["token", "a"], {STOPPED}]);')
        assert out["announced"] == "Reply stopped"

    def test_d3_focus_leaves_the_chats_stop_button_for_the_input(self, tmp_path):
        out = chat_ui(
            tmp_path,
            f"""
            show(srv()); respond = sse([["token", "a"], {STOPPED}]);
            el("#input").value = "hi"; const sent = sendMessage();
            el("#stopBtn").focus(); await sent;
            return document.activeElement === el("#input");
            """,
        )
        assert out is True

    def test_d3_focus_leaves_the_load_tests_stop_button(self, tmp_path):
        out = chat_ui(
            tmp_path,
            """
            const paint = (s) => { state.snapshot = snap({ server: s }); render(state.snapshot); };
            paint(srv()); const stop = el("#ltStopServerBtn"); stop.focus();
            paint(off());
            return [stop.hidden, document.activeElement !== stop,
              document.activeElement === el("#ltStartBtn")
              || document.activeElement === el("#ltModelSelectSearch")];
            """,
        )
        assert out == [True, True, True]

    def test_d7_a_failed_load_is_said_on_both_pages(self, tmp_path):
        out = chat_ui(
            tmp_path,
            """
            show(srv({ load_failed: true, ready: false }));
            return [el("#input").placeholder, html("#serverState"),
              el("#navServerText").textContent, el("#navServerDot").className];
            """,
        )
        assert "Start the API server first" not in out[0] and "Stop, then Start" in out[0]
        assert "dot-bad" in out[1] and "Model not loaded" in out[1]
        assert out[2] == "model not loaded" and "dot-bad" in out[3]


class TestBrowserDefectsInPython:
    def test_d4_a_cancel_marks_the_remaining_sizes_cancelled(self, lt, tmp_path):
        cancel = threading.Event()
        cancel.set()
        status, _, rows, _, _ = run_job(lt, tmp_path, cancel=cancel, sizes=(4096, 8192))
        assert status == "cancelled"
        for row in rows.values():
            assert (row["status"], row["reason"]) == ("cancelled", "cancelled by you")

    @pytest.mark.parametrize("percent", [None, 70])
    def test_d5_the_memory_limit_says_the_one_model_rule(self, app_module, percent):
        if percent:
            write(app_module, {"memory_limit_percent": percent})
        said = app_module.settings_view()["memory_limit_percent"]["effective"]
        assert f"{percent or 80}% ceiling" in said and "other apps" in said
        assert f"{min(52, percent or 52)}% share for two models" in said

    @pytest.mark.parametrize(
        ("auto", "said"), [(6000, "6000 (lowered from 7000)"), (100, "0 (no prompt accepted)")]
    )
    def test_d6_effective_shows_the_applied_figure(self, app_module, monkeypatch, auto, said):
        write(app_module, {"max_context_tokens": 7000})
        one_estimate(app_module, monkeypatch, auto_tokens=auto)
        monkeypatch.setattr(app_module, "_snapshot_dir", lambda repo: Path("/nonexistent"))
        view = app_module.config_view(REPO)
        assert view["settings"]["max_context_tokens"]["effective"] == said

    @pytest.mark.parametrize("serving", [False, True])
    def test_d9_the_advice_fits_the_servers_state(self, app_module, monkeypatch, serving):
        if serving:
            running(app_module)
        got = one_estimate(app_module, monkeypatch, auto_tokens=100)
        warning = got["context_warning"]
        assert "Close other apps" in warning and "Start Server" in warning
        assert ("press Stop, then" in warning) is serving
