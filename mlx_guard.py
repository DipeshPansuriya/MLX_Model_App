#!/usr/bin/env python3
"""Start ``mlx_lm server`` with request checks, in the same process and port.

One change is made to replies: a tool argument that is ``null`` where the
request's schema allows only "not given" is left out (see drop_null_arguments).

On its own the server accepts any web page's ``text/plain`` POST, answers any
Host name (DNS rebinding), downloads or loads whatever model the body names and
fetches any image URL a chat message holds.
Arguments are passed to mlx_lm untouched. If the places patched here have moved
in the installed mlx_lm, nothing is started.
"""

from __future__ import annotations

import contextlib
import inspect
import json
import os
import re
import sys
from http.server import BaseHTTPRequestHandler
from pathlib import Path

# Any port, so an SSH tunnel keeps working.
LOOPBACK_HOST = re.compile(
    r"(?:127\.0\.0\.1|localhost|\[::1\])(?::\d{1,5})?", re.ASCII | re.IGNORECASE
)
REPO_ID = re.compile(r"(?:[A-Za-z0-9_][\w.-]*/)?[A-Za-z0-9_][\w.-]*", re.ASCII)
# How much of a refused request body is read, so the client still gets the answer.
DRAIN_LIMIT = 1024 * 1024
JSON_TYPES = ("string", "number", "integer", "boolean", "object", "array")
# Schema keywords that can make a key required although "required" omits it.
CONDITIONAL = frozenset(
    (
        "allOf",
        "anyOf",
        "oneOf",
        "not",
        "if",
        "dependentRequired",
        "dependentSchemas",
        "dependencies",
        "minProperties",
    )
)


def check_headers(method, headers, allowed_origins) -> tuple[int, str] | None:
    """``(status, reason)`` for a request to refuse, else ``None``."""
    hosts = headers.get_all("Host") or []
    if len(hosts) != 1 or not LOOPBACK_HOST.fullmatch(hosts[0].strip()):
        return 403, "Host not allowed"
    # Browsers always name a foreign page; other clients send no Origin.
    allowed = set(allowed_origins) - {"*", "null", ""}
    if any(origin not in allowed for origin in headers.get_all("Origin") or []):
        return 403, "Origin not allowed"
    if method == "POST":
        # A page can only send JSON after a preflight, which the Origin rule refuses.
        types = headers.get_all("Content-Type") or []
        if len(types) != 1 or types[0].split(";")[0].strip().lower() != "application/json":
            return 415, "Content-Type must be application/json"
    return None


def check_body(model, draft, adapters, default, is_cached) -> str | None:
    """Why a request may not name these weights, else ``None``."""
    if draft not in (None, "default_model"):
        return "draft_model is not allowed"
    if adapters is not None:
        return "adapters are not allowed"
    if model == "default_model" or (default is not None and model == default):
        return None
    # mlx_lm prefers a local path over a repo id, so a name that exists is refused.
    if (
        isinstance(model, str)
        and REPO_ID.fullmatch(model)
        and ".." not in model
        and not os.path.exists(model)
        and is_cached(model)
    ):
        return None
    return "model is not available: download it in MLX Studio first"


def check_images(messages, image_parts, image_url) -> str | None:
    """Why these chat messages may not be sent on, else ``None``.

    mlx_lm downloads an http(s) image URL before it even loads a model, so only
    ``data:`` URLs pass. Its own helpers find the images: we read what it reads.
    """
    if messages is None:
        return None
    try:
        for part in image_parts(messages):
            url = image_url(part)
            if not (isinstance(url, str) and url.startswith("data:")):
                return "image URLs are not allowed: send the image as a data: URL"
    except Exception:  # unreadable here means unchecked, so it goes no further
        return "messages are not valid"
    return None


def cached_checker(repo_to_path):
    """``is_cached(repo)`` over mlx_lm's own offline lookup, asked on every request."""

    def is_cached(repo: str) -> bool:
        try:
            snapshot = Path(repo_to_path(repo))
            if not any(snapshot.glob("model*.safetensors")):
                return False
            # An interrupted download can leave some of the indexed shards out.
            for index in snapshot.glob("model*.safetensors.index.json"):
                shards = json.loads(index.read_text("utf-8"))["weight_map"].values()
                if not all((snapshot / shard).is_file() for shard in set(shards)):
                    return False
        except Exception:  # any failure to prove it is there means it is not
            return False
        return True

    return is_cached


def served_recorder(path):
    """``record(model)``: tell MLX Studio which models this server has been given.

    One name per line, so the app can refuse to delete a model a client switched to.
    """
    seen: set[str] = set()

    def record(model: str) -> None:
        if not path or model in seen:
            return
        with contextlib.suppress(OSError):
            fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(f"{model}\n")
            seen.add(model)

    return record


def refuses_null(spec) -> bool:
    """Does this property schema clearly not allow ``null``?"""
    return (
        isinstance(spec, dict)
        and spec.get("type") in JSON_TYPES  # one type name; lists and "null" are not in it
        and spec.get("nullable") in (None, False)
        and spec.get("required") is not True
        and "anyOf" not in spec
        and "oneOf" not in spec
        and None not in spec.get("enum", ())
    )


def drop_null_arguments(name, arguments, tools):
    """A tool call's arguments without the nulls that can only mean "not given".

    A top-level key goes only if the tool's schema declares it, does not require
    it and does not allow null. A call is never emptied, since ``{}`` can mean
    something else to the tool. What cannot be proven is returned unchanged.
    """
    try:
        (schema,) = [
            tool["function"].get("parameters") for tool in tools if tool["function"]["name"] == name
        ]
        required = schema.get("required", [])
        if CONDITIONAL & schema.keys() or not isinstance(required, list):
            return arguments
        properties = schema["properties"]
        kept = {
            key: value
            for key, value in arguments.items()
            if value is not None or key in required or not refuses_null(properties.get(key))
        }
    except Exception:  # an unexpected shape: the reply goes on as the model wrote it
        return arguments
    return kept if 0 < len(kept) < len(arguments) else arguments


def code_names(code) -> set[str]:
    """Names a function uses, including inside its generator expressions."""
    inner = (code_names(const) for const in code.co_consts if inspect.iscode(const))
    return set(code.co_names).union(*inner)


def hook_problem(server) -> str | None:
    """The first thing the guard relies on that this mlx_lm no longer has."""
    try:
        handler = server.APIHandler
        post = handler.do_POST.__code__.co_names
        default = inspect.signature(server._run_http_server).parameters["handler_class"].default
        formatter = server.ToolCallFormatter
        needed = {
            "the handler no longer parses requests the standard way": (
                handler.handle_one_request is BaseHTTPRequestHandler.handle_one_request
                and callable(handler.parse_request)
            ),
            "POST no longer validates the model parameters": (
                "validate_model_parameters" in post and callable(handler.validate_model_parameters)
            ),
            "the model fields of a request were renamed": (
                {"requested_model", "requested_draft_model", "adapter"} <= set(post)
            ),
            "image URLs are no longer read by the same helpers": (
                callable(server._image_parts)
                and callable(server._image_url)
                and "_inline_image_urls" in handler.handle_chat_completions.__code__.co_names
                and {"_image_parts", "_image_url"}
                <= set(server._inline_image_urls.__code__.co_names)
            ),
            "the server no longer uses the patched handler": (
                default is handler
                and "_run_http_server" in server.run.__code__.co_names
                and "run" in server.main.__code__.co_names
            ),
            "tool calls are no longer formatted by the same method": (
                list(inspect.signature(formatter._format).parameters) == ["self", "tc"]
                and "arguments" in formatter._format.__code__.co_consts
                and "_tools" in formatter.__init__.__code__.co_names
                and "_format" in code_names(formatter.__call__.__code__)
                and "ToolCallFormatter" in code_names(handler.handle_completion.__code__)
            ),
        }
    except (AttributeError, KeyError, TypeError, ValueError) as exc:
        return f"mlx_lm.server changed ({exc})"
    return next((problem for problem, holds in needed.items() if not holds), None)


def install(server, is_cached, record=None) -> str | None:
    """Patch the mlx_lm classes in place. Returns why not, if it cannot be done."""
    problem = hook_problem(server)
    if problem:
        return problem
    # Patched, not subclassed: mlx_lm binds the class itself as its default.
    handler = server.APIHandler
    parse = handler.parse_request
    validate = handler.validate_model_parameters
    image_parts, image_url = server._image_parts, server._image_url
    formatter = server.ToolCallFormatter
    format_call = formatter._format

    def parse_request(self):
        if not parse(self):
            return False
        origins = self.response_generator.cli_args.allowed_origins
        refusal = check_headers(self.command, self.headers, origins)
        if not refusal:
            return True
        self.send_error(*refusal)
        with contextlib.suppress(OSError, ValueError):
            length = int(self.headers.get("Content-Length") or 0)
            if length > 0:
                self.connection.settimeout(2)
                self.rfile.read(min(length, DRAIN_LIMIT))
        return False

    def validate_model_parameters(self):
        validate(self)
        refusal = check_body(
            self.requested_model,
            self.requested_draft_model,
            self.adapter,
            self.response_generator.cli_args.model,
            is_cached,
        ) or check_images(self.body.get("messages"), image_parts, image_url)
        if refusal:
            raise ValueError(refusal)  # mlx_lm answers 400 with this text
        if record:
            record(self.requested_model)

    def _format(self, tc):
        # One formatter per request, so self._tools is that request's own list.
        # A call without these fields is left for mlx_lm to deal with as before.
        with contextlib.suppress(KeyError, TypeError):
            tc["arguments"] = drop_null_arguments(tc["name"], tc["arguments"], self._tools)
        return format_call(self, tc)

    formatter._format = _format
    handler.parse_request = parse_request
    handler.validate_model_parameters = validate_model_parameters
    return None


def main() -> None:
    # Before any import of huggingface_hub, which reads these once.
    os.environ["HF_HUB_OFFLINE"] = "1"
    # ModelScope would replace the downloader and ignore the offline switch.
    os.environ.pop("MLXLM_USE_MODELSCOPE", None)
    try:
        from mlx_lm import server
        from mlx_lm.utils import hf_repo_to_path

        record = served_recorder(os.environ.get("MLX_STUDIO_SERVED_FILE"))
        problem = install(server, cached_checker(hf_repo_to_path), record)
    except ImportError as exc:
        problem = f"mlx_lm cannot be imported ({exc})"
    if problem:
        sys.exit(
            f"mlx_guard: refusing to start the API server: {problem}. "
            "Install the mlx-lm version pinned by MLX Studio (Setup tab)."
        )
    server.main()


if __name__ == "__main__":
    main()
