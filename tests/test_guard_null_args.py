"""The guard drops a null tool argument only when it can only mean "not given".

Policy tests need the standard library. The reply tests run the installed mlx_lm
handler with a stub generator and skip without it. No model is loaded.
"""

from __future__ import annotations

import copy
import http.client
import importlib.util
import json
import threading
import types
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

GUARD = Path(__file__).resolve().parent.parent / "mlx_guard.py"
STRING = {"type": "string"}


@pytest.fixture
def guard():
    spec = importlib.util.spec_from_file_location("mlx_guard_null_args", GUARD)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tool(name, properties, required=(), **extra):
    schema = {"type": "object", "properties": properties, "required": list(required), **extra}
    return {"type": "function", "function": {"name": name, "parameters": schema}}


WRITE = tool(
    "write",
    {"path": STRING, "content": STRING, "sandbox_permissions": STRING, "justification": STRING},
    ["path", "content"],
)
LISTING = tool("working_directory", {"cd": STRING})
WRITE_ARGS = {
    "path": "fib.py",
    "content": "print(1)",
    "sandbox_permissions": None,
    "justification": None,
}
WRITE_KEPT = {"path": "fib.py", "content": "print(1)"}


# Policy ---------------------------------------------------------------------- #
@pytest.mark.parametrize("kind", ["string", "number", "integer", "boolean", "object", "array"])
def test_an_optional_non_nullable_null_is_dropped(guard, kind):
    tools = [tool("t", {"a": STRING, "b": {"type": kind}}, ["a"])]
    assert guard.drop_null_arguments("t", {"a": "x", "b": None}, tools) == {"a": "x"}


def test_a_schema_without_a_required_list_has_only_optional_keys(guard):
    tools = [tool("t", {"a": STRING, "b": STRING})]
    del tools[0]["function"]["parameters"]["required"]
    assert guard.drop_null_arguments("t", {"a": "x", "b": None}, tools) == {"a": "x"}


def test_every_other_value_and_nested_content_is_untouched(guard):
    names = ["zero", "no", "empty", "text", "obj", "items", "drop"]
    tools = [tool("t", {name: STRING for name in names})]
    args = {
        "zero": 0,
        "no": False,
        "empty": "",
        "text": "null",
        "obj": {"drop": None},
        "items": [None],
        "drop": None,
    }
    kept = guard.drop_null_arguments("t", args, tools)
    assert kept == {name: value for name, value in args.items() if name != "drop"}


@pytest.mark.parametrize("args", [{"cd": None}, {"cd": None, "also": None}])
def test_arguments_are_never_emptied(guard, args):
    tools = [tool("working_directory", {"cd": STRING, "also": STRING})]
    assert guard.drop_null_arguments("working_directory", args, tools) == args


@pytest.mark.parametrize(
    "spec",
    [
        {"type": "null"},
        {"type": ["string", "null"]},
        {"type": ["string"]},
        {"type": "string", "nullable": True},
        {"type": "string", "enum": ["a", None]},
        {"type": "string", "anyOf": [{"type": "string"}, {"type": "null"}]},
        {"type": "string", "oneOf": [{"type": "string"}, {"type": "null"}]},
        {"anyOf": [{"type": "string"}, {"type": "null"}]},
        {"type": "string", "required": True},
        {"type": "any"},
        {"description": "no type"},
        {},
        True,
        "string",
        {"type": "string", "enum": 7},
    ],
)
def test_a_null_the_schema_may_allow_is_kept(guard, spec):
    args = {"a": "x", "b": None}
    assert guard.drop_null_arguments("t", args, [tool("t", {"a": STRING, "b": spec})]) == args


def test_required_and_undeclared_nulls_are_kept(guard):
    tools = [tool("t", {"a": STRING, "b": STRING, "c": STRING}, ["b"])]
    args = {"a": "x", "b": None, "c": None, "unknown": None}
    kept = guard.drop_null_arguments("t", args, tools)
    assert kept == {"a": "x", "b": None, "unknown": None}


@pytest.mark.parametrize(
    "word",
    [
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "dependentRequired",
        "dependentSchemas",
        "dependencies",
        "minProperties",
    ],
)
def test_a_schema_that_may_require_a_key_conditionally_is_left_alone(guard, word):
    tools = [tool("t", {"a": STRING, "b": STRING}, **{word: 1})]
    args = {"a": "x", "b": None}
    assert guard.drop_null_arguments("t", args, tools) == args


@pytest.mark.parametrize(
    "tools",
    [
        None,
        [],
        "write",
        [7],
        [{"type": "function"}],
        [{"function": {"name": "t"}}],
        [{"function": {"name": "t", "parameters": None}}],
        [{"function": {"name": "t", "parameters": {"properties": []}}}],
        [{"function": {"name": "t", "parameters": {"properties": {"b": STRING}, "required": "b"}}}],
        [tool("other", {"b": STRING})],
        [tool("t", {"b": STRING}), tool("t", {"b": {"type": "string", "nullable": True}})],
    ],
)
def test_an_unknown_or_unreadable_tool_changes_nothing(guard, tools):
    args = {"a": "x", "b": None}
    assert guard.drop_null_arguments("t", args, tools) == args


@pytest.mark.parametrize("args", [None, "x", '{"b": null}', 7, [None], {}])
def test_arguments_that_are_not_a_filled_object_pass(guard, args):
    assert guard.drop_null_arguments("write", args, [WRITE]) == args


def test_neither_the_request_nor_the_arguments_are_modified(guard):
    tools, args = [copy.deepcopy(WRITE)], dict(WRITE_ARGS)
    assert guard.drop_null_arguments("write", args, tools) == WRITE_KEPT
    assert tools == [WRITE] and args == WRITE_ARGS


# Replies from the installed mlx_lm handler ----------------------------------- #
def call(name, arguments):
    return json.dumps({"name": name, "arguments": arguments})


@pytest.fixture
def reply(guard, monkeypatch):
    """Ask the guarded handler for a completion; the stub model says ``outputs``."""
    server = pytest.importorskip("mlx_lm.server")
    generate = pytest.importorskip("mlx_lm.generate")
    parser = pytest.importorskip("mlx_lm.tool_parsers.json_tools").parse_tool_call

    for owner, name in (
        (server.APIHandler, "parse_request"),
        (server.APIHandler, "validate_model_parameters"),
        (server.ToolCallFormatter, "_format"),
    ):
        monkeypatch.setattr(owner, name, getattr(owner, name))
    assert guard.install(server, lambda repo: True) is None

    state = types.SimpleNamespace(outputs=[], meet=None)

    def respond(text, finish=None):
        return server.Response(text, 1, 0.0, finish, ())

    def tokens(outputs):
        for output in outputs:
            for piece in ("<tool_call>", output, "</tool_call>"):
                yield respond(piece)
            if state.meet:
                state.meet.wait(timeout=10)  # both requests are now mid-reply
            yield respond(" ")
        yield respond("", "stop")

    def stub(request, args, progress_callback=None):
        context = server.GenerationContext(
            has_tool_calling=True,
            has_thinking=False,
            tool_parser=parser,
            text_sm=generate.TextStateMachine(
                {
                    "normal": [("<tool_call>", "tool")],
                    "tool": [("</tool_call>", "normal")],
                }
            ),
            initial_state="normal",
            prompt=[1],
            prompt_cache_count=0,
        )
        return context, tokens(list(state.outputs))

    cli = types.SimpleNamespace(
        allowed_origins=[],
        model="org/start",
        num_draft_tokens=3,
        max_tokens=8,
        temp=0.0,
        top_p=1.0,
        top_k=0,
        min_p=0.0,
        chat_template_args={},
    )
    generator = types.SimpleNamespace(cli_args=cli, generate=stub, is_healthy=True)
    handler = partial(server.APIHandler, generator, system_fingerprint="test")
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()

    def ask(tools, stream):
        body = {"messages": [{"role": "user", "content": "hi"}], "stream": stream}
        if tools is not None:
            body["tools"] = tools
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=20)
        try:
            conn.request(
                "POST",
                "/v1/chat/completions",
                json.dumps(body),
                {"Content-Type": "application/json"},
            )
            response = conn.getresponse()
            text = response.read().decode()
        finally:
            conn.close()
        assert response.status == 200, text
        if not stream:
            return json.loads(text)["choices"][0]["message"].get("tool_calls", [])
        assert text.rstrip().endswith("data: [DONE]"), text
        calls = []
        for line in text.splitlines():
            if line.startswith("data: {"):
                for choice in json.loads(line[6:])["choices"]:
                    calls += choice["delta"].get("tool_calls", [])
        return calls

    state.ask = ask
    try:
        yield state
    finally:
        httpd.shutdown()
        httpd.server_close()


def arguments(calls):
    return [json.loads(item["function"]["arguments"]) for item in calls]


@pytest.mark.parametrize("stream", [False, True])
class TestReplies:
    def test_the_write_call_loses_only_its_optional_nulls(self, reply, stream):
        reply.outputs = [call("write", WRITE_ARGS)]
        calls = reply.ask([WRITE], stream)
        assert arguments(calls) == [WRITE_KEPT]
        assert calls[0]["function"]["name"] == "write" and calls[0]["id"]

    def test_the_listing_call_keeps_its_only_null(self, reply, stream):
        reply.outputs = [call("working_directory", {"cd": None})]
        assert arguments(reply.ask([LISTING], stream)) == [{"cd": None}]

    def test_each_call_of_one_reply_is_judged_by_its_own_tool(self, reply, stream):
        reply.outputs = [
            call("write", WRITE_ARGS),
            call("working_directory", {"cd": None}),
            call("unknown", {"a": 1, "b": None}),
        ]
        calls = reply.ask([WRITE, LISTING], stream)
        assert arguments(calls) == [WRITE_KEPT, {"cd": None}, {"a": 1, "b": None}]
        if stream:
            assert [item["index"] for item in calls] == [0, 1, 2]

    def test_a_request_without_tools_is_unchanged(self, reply, stream):
        reply.outputs = [call("write", WRITE_ARGS)]
        assert arguments(reply.ask(None, stream)) == [WRITE_ARGS]

    @pytest.mark.parametrize("args", ["not an object", None, [None]])
    def test_non_object_arguments_pass(self, reply, stream, args):
        reply.outputs = [call("write", args)]
        assert arguments(reply.ask([WRITE], stream)) == [args]

    def test_malformed_output_is_still_skipped(self, reply, stream):
        reply.outputs = ['{"name": "write", "argu', call("write", WRITE_ARGS)]
        assert arguments(reply.ask([WRITE], stream)) == [WRITE_KEPT]

    def test_concurrent_requests_use_only_their_own_schema(self, reply, stream):
        nullable = copy.deepcopy(WRITE)
        for key in ("sandbox_permissions", "justification"):
            nullable["function"]["parameters"]["properties"][key]["type"] = ["string", "null"]
        reply.outputs = [call("write", WRITE_ARGS)]
        reply.meet = threading.Barrier(2)
        for _ in range(5):
            seen = {}

            def ask(label, tools, seen=seen):
                seen[label] = arguments(reply.ask(tools, stream))

            threads = [
                threading.Thread(target=ask, args=("strict", [WRITE])),
                threading.Thread(target=ask, args=("nullable", [nullable])),
            ]
            for thread in threads:
                thread.start()
            for thread in threads:
                thread.join(30)
            assert seen == {"strict": [WRITE_KEPT], "nullable": [WRITE_ARGS]}
