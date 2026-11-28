"""Report the text "null" sent as a tool-call value (row 1088, point 19).

A check only: it reads the model's tool calls and the offered tool schemas and
says what it saw. Nothing is rewritten, logged or sent. A real JSON null is a
different case and is not reported here. Standard library only.
"""

from __future__ import annotations

import json

# How "no value" gets written as text, compared without case or wrapping marks.
NULL_WORDS = frozenset({"null", "none", "nil", "undefined"})
WRAPPING = " \t\r\n\"'<>()"
WORD_MAX = 32
DEPTH_MAX = 20
CHOICE_DEPTH_MAX = 8
MOST_FINDINGS = 20
NAME_MAX = 80
NAMED_MAX = 160  # characters of field names in the warning


def _is_null_word(value) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= WORD_MAX
        and value.strip(WRAPPING).casefold() in NULL_WORDS
    )


def _plain(text: str) -> str:
    """A name from the model or a schema, safe to show: printable and short."""
    return "".join(c for c in text[:NAME_MAX] if c.isprintable())


def _choices(schema, depth: int = 0) -> list[dict]:
    """The alternatives a schema allows, with anyOf and oneOf opened up."""
    if not isinstance(schema, dict) or depth > CHOICE_DEPTH_MAX:
        return []
    nested = [
        option
        for key in ("anyOf", "oneOf")
        if isinstance(schema.get(key), list)
        for option in schema[key]
    ]
    if not nested:
        return [schema]
    return [found for option in nested for found in _choices(option, depth + 1)]


def _types(choice: dict) -> list[str]:
    kind = choice.get("type")
    kinds = kind if isinstance(kind, list) else [kind]
    return [k for k in kinds if isinstance(k, str)]


def _listed(choice: dict) -> list | None:
    """The only values this alternative takes (enum or const); None if not limited."""
    if "const" in choice:
        return [choice["const"]]
    return choice["enum"] if isinstance(choice.get("enum"), list) else None


def _takes_any_text(choice: dict) -> bool:
    kinds = _types(choice)
    return _listed(choice) is None and (not kinds or "string" in kinds)


def _inside(choices: list[dict], key) -> list:
    """The schemas for one member of an object (str key) or of a list (int key)."""
    found = []
    for choice in choices:
        if isinstance(key, int):
            found.append(choice.get("items"))
            continue
        named = choice.get("properties")
        if isinstance(named, dict) and key in named:
            found.append(named[key])
        else:
            found.append(choice.get("additionalProperties"))
    return found


def _walk(value, schemas: list, path: str, required: bool, found: list, depth: int = 0) -> None:
    if depth > DEPTH_MAX or len(found) >= MOST_FINDINGS:
        return
    choices = [choice for schema in schemas for choice in _choices(schema)]
    if isinstance(value, dict):
        for key, member in value.items():
            if not isinstance(key, str):
                continue
            needed = any(
                isinstance(c.get("required"), list) and key in c["required"] for c in choices
            )
            below = f"{path}.{key}" if path else key
            _walk(member, _inside(choices, key), below, needed, found, depth + 1)
    elif isinstance(value, list):
        for index, member in enumerate(value):
            below = f"{path}[{index}]"
            _walk(member, _inside(choices, index), below, False, found, depth + 1)
    elif _is_null_word(value) and not any(value in (_listed(c) or []) for c in choices):
        expected = list(dict.fromkeys(kind for c in choices for kind in _types(c)))
        found.append(
            {
                "path": _plain(path),
                "expected": expected,
                "required": required,
                # Certain only if no alternative could have meant this as ordinary text.
                "certain": bool(choices) and not any(map(_takes_any_text, choices)),
            }
        )


def _schemas(tools) -> dict:
    """Tool name to its parameter schema, from an OpenAI-style ``tools`` list."""
    offered = {}
    for tool in tools if isinstance(tools, list) else []:
        function = tool.get("function") if isinstance(tool, dict) else None
        if isinstance(function, dict) and isinstance(function.get("name"), str):
            offered[function["name"]] = function.get("parameters")
    return offered


def _arguments(written):
    if not isinstance(written, str):
        return written
    try:
        return json.loads(written)
    except (ValueError, RecursionError):
        return None


def check(calls, tools) -> dict | None:
    """``{"warning": words, "findings": [...]}`` for a reply's ``tool_calls``, or None.

    Each finding: ``tool``, ``path``, ``expected`` (schema types), ``required`` and
    ``certain``. Calls that cannot be read, or name a tool not offered, are skipped.
    No finding does not prove the model never does this.
    """
    offered = _schemas(tools)
    findings: list[dict] = []
    for call in calls if isinstance(calls, list) else []:
        function = call.get("function") if isinstance(call, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        arguments = _arguments(function.get("arguments"))
        if not (isinstance(name, str) and name in offered and isinstance(arguments, dict)):
            continue
        here: list[dict] = []
        _walk(arguments, [offered[name]], "", False, here)
        findings += [{"tool": _plain(name), **finding} for finding in here]
    if not findings:
        return None
    findings = findings[:MOST_FINDINGS]
    names = ", ".join(f"{f['tool']}.{f['path']}" for f in findings)
    if len(names) > NAMED_MAX:
        names = names[:NAMED_MAX] + "..."
    doubt = "" if all(f["certain"] for f in findings) else " Where text is allowed it may be meant."
    return {
        "warning": (
            f'The model sent "null" (or a word like it) as text in: {names}. A tool may '
            f"take it as a real value.{doubt} MLX Studio does not change replies."
        ),
        "findings": findings,
    }
