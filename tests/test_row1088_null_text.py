"""Row 1088 (2.0.14 point 19): the text "null" sent as a tool value is reported."""

from __future__ import annotations

import copy
import json

import pytest

import tool_null_text

SCHEMA = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "limit": {"type": "integer"},
        "mode": {"type": "string", "enum": ["none", "fast"]},
        "note": {"type": ["string", "null"]},
        "depth": {"anyOf": [{"type": "integer"}, {"type": "null"}]},
        "options": {"type": "object", "properties": {"deep": {"type": "boolean"}}},
        "names": {"type": "array", "items": {"type": "string"}},
        "free": {},
    },
    "required": ["path"],
}
TOOLS = [{"type": "function", "function": {"name": "read_file", "parameters": SCHEMA}}]


def call(arguments, name="read_file"):
    written = arguments if isinstance(arguments, str) else json.dumps(arguments)
    return {"id": "1", "type": "function", "function": {"name": name, "arguments": written}}


def findings(arguments, tools=TOOLS) -> list[dict]:
    result = tool_null_text.check([call(arguments)], tools)
    return result["findings"] if result else []


@pytest.mark.parametrize(
    "text",
    ["null", "NULL", "Null", " null ", "None", "nil", "undefined", '"null"', "'None'", "<null>"],
)
def test_each_spelling_is_found(text):
    (found,) = findings({"limit": text})
    assert found == {
        "tool": "read_file",
        "path": "limit",
        "expected": ["integer"],
        "required": False,
        "certain": True,
    }


@pytest.mark.parametrize(
    "text", ["", "nullable", "no null here", "n/a", "0", "false", "annul", "null.txt"]
)
def test_other_text_is_left_alone(text):
    assert findings({"path": text, "limit": 3}) == []


def test_real_values_and_a_real_null_are_not_this_problem():
    assert findings({"path": "a", "limit": None, "options": {"deep": False}}) == []


def test_where_text_is_allowed_it_is_only_a_maybe():
    (found,) = findings({"path": "null"})
    assert (found["certain"], found["required"], found["expected"]) == (False, True, ["string"])
    assert findings({"note": "null"})[0]["certain"] is False
    (free,) = findings({"free": "null"})
    assert (free["expected"], free["certain"]) == ([], False)


def test_a_word_the_schema_lists_is_wanted():
    assert findings({"mode": "none"}) == []
    assert findings({"mode": "null"})[0]["certain"] is True
    const = {"type": "object", "properties": {"kind": {"const": "None"}}}
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": const}}]
    assert findings({"kind": "None"}, tools) == []


def test_choices_in_the_schema_are_read():
    assert findings({"depth": "null"})[0]["expected"] == ["integer", "null"]


def test_nested_values_are_checked():
    found = findings({"options": {"deep": "null"}, "names": ["a", "None"]})
    assert [(f["path"], f["certain"]) for f in found] == [
        ("options.deep", True),
        ("names[1]", False),
    ]


def test_a_value_the_schema_does_not_name_is_reported_as_a_maybe():
    (found,) = findings({"extra": "null"})
    assert (found["expected"], found["certain"]) == ([], False)


def test_the_warning_says_what_happened_and_that_nothing_is_changed():
    result = tool_null_text.check([call({"limit": "null", "path": "null"})], TOOLS)
    words = result["warning"]
    assert "limit" in words and "path" in words and "read_file" in words
    assert "does not change replies" in words
    assert len(words) < 400


def test_the_models_output_is_never_changed():
    calls = [call({"limit": "null"}), {"function": {"name": "read_file", "arguments": {}}}]
    calls[1]["function"]["arguments"] = {"options": {"deep": "None"}}  # already parsed
    tools = copy.deepcopy(TOOLS)
    before = copy.deepcopy(calls)
    assert len(tool_null_text.check(calls, tools)["findings"]) == 2
    assert (calls, tools) == (before, TOOLS)


@pytest.mark.parametrize(
    "calls",
    [
        None,
        [],
        "null",
        [None],
        [{}],
        [{"function": 3}],
        [call("not json")],
        [call('"null"')],
        [call({"limit": "null"}, name="not_offered")],
        [call({"limit": "null"}, name=["x"])],
        [call("[" * 100_000)],
    ],
)
def test_what_cannot_be_judged_gives_no_warning(calls):
    assert tool_null_text.check(calls, TOOLS) is None


@pytest.mark.parametrize("tools", [None, [], [None], [{"function": {"name": 4}}]])
def test_unusable_tools_give_no_warning(tools):
    assert tool_null_text.check([call({"limit": "null"})], tools) is None


@pytest.mark.parametrize("schema", [None, 5, {"properties": []}, {"anyOf": 3}])
def test_an_unusable_schema_can_only_say_maybe(schema):
    tools = [{"type": "function", "function": {"name": "read_file", "parameters": schema}}]
    assert findings({"limit": "null"}, tools)[0]["certain"] is False


def test_deep_and_large_replies_are_bounded():
    deep = "null"
    for _ in range(200):
        deep = [deep]
    assert findings({"names": deep}) == []
    many = findings({"names": ["null"] * 500})
    assert len(many) == tool_null_text.MOST_FINDINGS


def test_names_from_the_model_are_cut_and_kept_plain():
    (found,) = findings({"x\u202e\n" + "y" * 500: "null"})
    assert len(found["path"]) <= tool_null_text.NAME_MAX
    assert found["path"].isprintable()
