#!/usr/bin/env python3
"""Start ``mlx_lm server`` with request checks, in the same process and port.

One change is made to replies: a tool argument that is ``null`` where the
request's schema allows only "not given" is left out (see drop_null_arguments).

With ``--studio-max-models 2`` the server also keeps the last two models loaded
(see install_multi). The ``--studio-*`` flags are read here and not passed on.
What is loaded and the memory it takes go to ``MLX_STUDIO_RESIDENT_FILE``.

On its own the server accepts any web page's ``text/plain`` POST, answers any
Host name (DNS rebinding), downloads or loads whatever model the body names and
fetches any image URL a chat message holds.
Other arguments are passed to mlx_lm untouched. If the places patched here have moved
in the installed mlx_lm, nothing is started.
"""

from __future__ import annotations

import contextlib
import gc
import inspect
import json
import os
import re
import stat
import subprocess
import sys
import threading
import time
import traceback
import types
from collections import OrderedDict
from http.server import BaseHTTPRequestHandler
from pathlib import Path
from typing import NamedTuple

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
            flags = os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW
            fd = os.open(path, flags, 0o600)
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


# Two models at once -------------------------------------------------------- #
# The context per model never goes above KV_TOKENS, is a multiple of CONTEXT_STEP,
# and below MIN_CONTEXT two models are refused.
KV_TOKENS = 32768
MIN_CONTEXT = 8192
CONTEXT_STEP = 1024
# Reply room for two models: a follow-up turn took 3.06 times its cache above what
# was stored.
REPLY_FACTOR = 3.1
# One model: the server's memory stays under weights + MEMORY_MARGIN + this many
# times the cache bytes of its prompt tokens. Measured on ONE model and ONE Mac
# (Qwen2.5-7B-Instruct-1M-4bit, fp16 cache, Apple M6 32 GB, prompts of 0.9K to 32K
# tokens, one or two cached conversations); the tightest sample, 13.72 GB at 22,151
# tokens, needs 6.33. Every other model and Mac is calculated, not measured.
ONE_MODEL_FACTOR = 6.4
# An automatic one-model figure below this is refused, and said, not served.
MIN_AUTO_CONTEXT = 4096
# The --studio-cache-limit-mb setting: MLX's free buffer cache, in 10**6 bytes.
CACHE_LIMIT_MB_RANGE = (0, 1_000_000)
# The prompt cache holds one long conversation for each model.
CACHED_CONVERSATIONS = 2
# Cache bytes per token when a config cannot be read: a large 8B-class model.
UNKNOWN_KV_PER_TOKEN = 131072
# The server's process uses about this much outside MLX; it is part of the margin.
PROCESS_OVERHEAD = 4 * 10**8
MEMORY_MARGIN = 10**9 + PROCESS_OVERHEAD
# Left free for macOS and for other apps to grow.
FREE_RESERVE = 2 * 10**9
# Share of physical memory the models may use at most.
RAM_FRACTION = 0.52
# Memory use never goes above this share: the one-model prompt limit stops here.
MAX_RAM_FRACTION = 0.80
# vm_stat counts that macOS can hand out.
FREE_PAGES = ("Pages free", "Pages speculative", "Pages purgeable", "File-backed pages")
EVENTS_KEPT = 20
IMPORT_LOCK = threading.Lock()
# What ModelProvider._load sets: one resident model is these six values.
MEMBERS = ("model_key", "model", "tokenizer", "processor", "draft_model", "is_batchable")


class Options(NamedTuple):
    also: str | None = None
    max_models: int = 1
    budget_bytes: int | None = None
    kv_tokens: int = KV_TOKENS
    max_context: int | None = None  # the user's refusal limit, for one model
    memory_percent: int | None = None  # the user's lower memory limit
    exit_with_parent: bool = False  # exit when stdin closes (the load test's server)
    cache_limit_mb: int | None = None  # MLX's free buffer cache; None: MLX's default


class Resident(NamedTuple):
    key: object
    weights_bytes: int
    kv_reserve: int


class Plan(NamedTuple):
    evict: list  # keys, least recently used first
    trim_to_bytes: int | None  # new size of the prompt cache
    alone: bool  # does not fit even by itself; loaded anyway
    reason: str


def split_studio_args(argv) -> tuple[list[str], Options]:
    """``(arguments for mlx_lm, Options)``. A bad ``--studio-*`` flag stops the start."""
    rest: list[str] = []
    found: dict[str, str] = {}
    items = iter(argv)
    for item in items:
        if not item.startswith("--studio-"):
            rest.append(item)
            continue
        if item == "--studio-exit-with-parent":  # takes no value
            found["exit_with_parent"] = "1"
            continue
        flag, equals, value = item.partition("=")
        if not equals:
            value = next(items, "")
        name = flag.removeprefix("--studio-").replace("-", "_")
        name = "also" if name == "also_model" else name
        if name == "cache_limit_mb" and name not in found:
            # Only a tuning figure: an unusable one is ignored, not a failed start.
            if _cache_limit(value) is not None:
                found[name] = value
            continue
        if name == "also":
            valid = bool(REPO_ID.fullmatch(value))
        else:
            valid = name in Options._fields and re.fullmatch(r"[1-9]\d{0,15}", value)
            valid = valid and name != "exit_with_parent"
        if not valid or name in found:
            sys.exit(f"mlx_guard: bad or repeated option {flag}")
        found[name] = value
    opts = Options(**{k: v if k == "also" else int(v) for k, v in found.items()})
    opts = opts._replace(exit_with_parent=bool(opts.exit_with_parent))
    if opts.max_models > 2 or (opts.also and opts.max_models < 2):
        sys.exit("mlx_guard: --studio-max-models is 1 or 2, and 2 for --studio-also-model")
    return rest, opts


def _cache_limit(text: str) -> int | None:
    low, high = CACHE_LIMIT_MB_RANGE
    plain = re.fullmatch(r"0|[1-9]\d{0,15}", text)
    return int(text) if plain and low <= int(text) <= high else None


def _text_config(config) -> dict:
    """Model settings, with a multimodal model's nested text settings first."""
    if not isinstance(config, dict):
        return {}
    nested = config.get("text_config")
    return {**config, **nested} if isinstance(nested, dict) else config


def _count(value) -> int | None:
    return value if type(value) is int and value > 0 else None


def read_config(snapshot) -> dict:
    try:
        return json.loads((Path(snapshot) / "config.json").read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def context_limit(config) -> int | None:
    return _count(_text_config(config).get("max_position_embeddings"))


def kv_bytes_per_token(config) -> int | None:
    """Cache bytes one token needs (16-bit keys and values); None if unreadable."""
    text = _text_config(config)
    layers = _count(text.get("num_hidden_layers"))
    attention = _count(text.get("num_attention_heads"))
    heads = _count(text.get("num_key_value_heads")) or attention
    head_dim = _count(text.get("head_dim"))
    hidden = _count(text.get("hidden_size"))
    if not head_dim and hidden and attention:
        head_dim = hidden // attention
    if not (layers and heads and head_dim):
        return None
    return layers * heads * head_dim * 2 * 2


def kv_reserve(config, tokens: int, factor: float = REPLY_FACTOR) -> int:
    """Room for one long reply: a follow-up copies the cache and works above it."""
    per_token = kv_bytes_per_token(config) or UNKNOWN_KV_PER_TOKEN
    return int(factor * per_token * min(context_limit(config) or tokens, tokens))


def context_tokens(budget: int, weights: int, kv_per_token: int) -> int:
    """Context per model that leaves room for the cache, one reply and the margin."""
    per_token = (CACHED_CONVERSATIONS + REPLY_FACTOR) * kv_per_token
    steps = int((budget - weights - MEMORY_MARGIN) // (per_token * CONTEXT_STEP))
    return max(0, min(KV_TOKENS, steps * CONTEXT_STEP))


def free_bytes() -> int | None:
    """Memory free right now, from vm_stat; None when it cannot be read."""
    try:
        text = subprocess.run(
            ["/usr/bin/vm_stat"], capture_output=True, text=True, timeout=2, check=True
        ).stdout
        page = int(re.search(r"page size of (\d+) bytes", text)[1])
        counts = dict(line.split(":", 1) for line in text.splitlines() if ":" in line)
        return page * sum(int(counts[name].strip().rstrip(".")) for name in FREE_PAGES)
    # TypeError: no page size line. ValueError: not a number, or not text.
    except (OSError, subprocess.SubprocessError, TypeError, KeyError, ValueError):
        return None


def other_apps_bytes(pid: int | None) -> int | None:
    """Memory in use on this Mac by all but the process ``pid``; None when unreadable.

    The load test's own reading, so both apply the 80% rule to the same figure.
    """
    try:
        sample = _mac_sample(pid)
        return max(int(sample.percent / 100 * ram_bytes()) - sample.footprint, 0)
    except Exception:  # an unknown figure: the fixed share of RAM applies
        return None


def used_percent() -> int | None:
    """Share of this Mac's memory in use now, for messages; None when unreadable."""
    try:
        return round(_mac_sample(None).percent)
    except Exception:
        return None


def _mac_sample(pid: int | None):
    with IMPORT_LOCK:  # a __pycache__ in the app bundle would break its signature
        before, sys.dont_write_bytecode = sys.dont_write_bytecode, True
        try:
            from mlx_loadtest import mac_probe
        finally:
            sys.dont_write_bytecode = before
    return mac_probe(pid)


def weights_bytes(snapshot) -> int:
    try:
        return sum(p.stat().st_size for p in Path(snapshot).glob("model*.safetensors"))
    except (OSError, ValueError):  # ValueError: a name with a NUL byte
        return 0


def model_figures(repo_to_path):
    """``figures(repo, tokens) -> (weight bytes, reply room bytes)`` from the cache."""

    def figures(repo: str, tokens: int) -> tuple[int, int]:
        try:
            snapshot = Path(repo_to_path(repo))
        except Exception:  # the load itself will say what is wrong
            return 0, kv_reserve({}, tokens)
        return weights_bytes(snapshot), kv_reserve(read_config(snapshot), tokens)

    return figures


def admission_plan(resident, new, active, cache_bytes, budget, max_models) -> Plan:
    """What must go before ``new`` is loaded. ``resident`` is oldest first.

    Only one model replies at a time, so only the largest reply room counts. The
    least recently used models go first, but the prompt cache shrinks before the
    last one goes if that alone is enough. Once none is left the cache shrinks,
    and if that is still not enough ``new`` is loaded alone, as a single-model
    server would.
    """
    kept, evict = list(resident), []
    while True:
        room = max(item.kv_reserve for item in (*kept, new))
        over = active + new.weights_bytes + room + MEMORY_MARGIN - budget
        if len(kept) < max_models:
            if over <= 0:
                return Plan(evict, None, False, "")
            if kept and over <= cache_bytes:  # trim rather than drop the last model
                return Plan(
                    evict, cache_bytes - over, False, f"{over} bytes over the memory budget"
                )
        if not kept:
            break
        gone = kept.pop(0)
        evict.append(gone.key)
        active -= gone.weights_bytes
    trim = max(0, cache_bytes - over) if cache_bytes > 0 else None
    alone = over > cache_bytes
    return Plan(evict, trim, alone, f"{over} bytes over the memory budget")


def status_writer(path):
    """``write(event, model, **fields)``: what the server holds, for MLX Studio."""
    state: dict = {"pid": os.getpid()}
    events: list[dict] = []

    def write(event: str | None = None, model: str | None = None, **fields) -> None:
        state.update(fields)
        if event:
            events.append({"event": event, "model": model, "at": int(time.time())})
            del events[:-EVENTS_KEPT]
        if not path:
            return
        temp = f"{path}.tmp"
        with contextlib.suppress(OSError):
            Path(temp).unlink(missing_ok=True)
            fd = os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump({**state, "events": events}, handle)
            os.replace(temp, path)

    return write


def release(models) -> None:
    """Empty dropped models. An mlx module is a dict holding its weights.

    mlx_lm's generation thread keeps a variable pointing at the model it loaded
    last until the next load returns, so forgetting our slot would not free it.
    """
    for model in models:
        clear = getattr(model, "clear", None)
        if callable(clear):
            clear()


def read_budget(path) -> int | None:
    """The number in ``path``; None unless a plain file there holds one above zero."""
    try:
        # No link is followed, and opening a FIFO does not wait for its writer.
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        try:
            if not stat.S_ISREG(os.fstat(fd).st_mode):
                return None
            value = int(os.read(fd, 64))
        finally:
            os.close(fd)
    except (TypeError, OSError, ValueError):  # TypeError: no file was named
        return None
    return value if value > 0 else None


def ram_bytes() -> int:
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def lowered(share: float, percent: int | None) -> float:
    """A built-in share of RAM, lowered by the user's percentage; never raised."""
    return min(share, percent / 100) if percent else share


def memory_budget(opts: Options) -> int:
    return opts.budget_bytes or int(lowered(RAM_FRACTION, opts.memory_percent) * ram_bytes())


def memory_ceiling(percent: int | None = None) -> int:
    return int(lowered(MAX_RAM_FRACTION, percent) * ram_bytes())


def install_report(server, status, budget: int) -> None:
    """One model at a time: change nothing, only record what each load leaves in memory.

    This is only a meter, so nothing here may stop a load or the server.
    """
    provider_class = getattr(server, "ModelProvider", None)
    load_one = getattr(provider_class, "_load", None)
    if not callable(load_one):
        status(report=False)  # so MLX Studio does not wait for a load report
        return

    def _load(self, *args, **kwargs):
        asked = args[0] if args else kwargs.get("model_path")
        try:
            return load_one(self, *args, **kwargs)
        finally:
            with contextlib.suppress(Exception):
                key = self.model_key
                status(
                    "load" if key else "load-failed",
                    key[0] if key else asked,
                    resident=[key[0]] if key else [],
                    budget_bytes=budget,
                    active_bytes=int(server.mx.get_active_memory()),
                    peak_bytes=int(server.mx.get_peak_memory()),
                )

    provider_class._load = _load


def multi_problem(server) -> str | None:
    """Why mlx_lm cannot hold two models the way install_multi expects, else None."""
    try:
        provider, generator = server.ModelProvider, server.ResponseGenerator

        def arguments(function) -> list[str]:
            return list(inspect.signature(function).parameters)

        if arguments(provider._load) != [
            "self", "model_path", "adapter_path", "draft_model_path"
        ]:  # fmt: skip
            return "ModelProvider._load takes different arguments"
        if not {"_load", "model_key"} <= set(provider.load.__code__.co_names):
            return "ModelProvider.load no longer goes through _load"
        if not {"reset", *MEMBERS} <= set(provider._load.__code__.co_names):
            return "ModelProvider._load no longer sets the values kept per model"
        if not (callable(provider.reset) and callable(provider.load_default)):
            return "ModelProvider.reset or load_default is missing"
        loop = generator._generate.__code__
        if not (
            {"load_default", "load"} <= set(loop.co_names) and "batch_generator" in loop.co_varnames
        ):
            return "ResponseGenerator._generate loads models differently"
        init = generator.__init__
        if arguments(init) != ["self", "model_provider", "prompt_cache"] or (
            "prompt_cache" not in init.__code__.co_names
        ):
            return "ResponseGenerator no longer keeps the prompt cache"
        if not hasattr(generator, "_next_request") or not callable(
            getattr(generator, "_next_request", None)
        ):
            return "ResponseGenerator._next_request is missing"
        cache = server.LRUPromptCache
        if not (callable(getattr(cache, "trim_to", None)) and hasattr(cache, "nbytes")):
            return "LRUPromptCache.trim_to or nbytes is missing"
        if "max_bytes" not in cache.insert_cache.__code__.co_names:
            return "LRUPromptCache no longer limits its bytes"
        for name in (
            "get_active_memory", "get_peak_memory", "reset_peak_memory", "clear_cache",
            "device_info",
        ):  # fmt: skip
            if not callable(getattr(server.mx, name, None)):
                return f"mlx.core.{name} is missing"
    except (AttributeError, TypeError, ValueError) as exc:
        return f"mlx_lm.server has changed ({exc})"
    return None


def install_multi(server, opts: Options, status, figures, is_cached) -> None:
    """Keep up to ``opts.max_models`` models in mlx_lm's own ModelProvider.

    mlx_lm calls load() only when nothing is generating, so a model dropped here
    is never in the middle of a reply.
    """
    provider_class, generator = server.ModelProvider, server.ResponseGenerator
    load_one = provider_class._load
    load_default = provider_class.load_default
    start_generator = generator.__init__
    mx = server.mx
    start_budget = memory_budget(opts)
    budget_file = os.environ.get("MLX_STUDIO_BUDGET_FILE")

    unread: list[bool] = []  # set once the fallback has been said

    def budget() -> dict:
        """``budget_bytes`` for one load, with the ``free_bytes`` it was worked out from.

        The smallest of the start budget, the app's own figure (less once its chat
        model has taken more) and what this process holds plus what is free.
        """
        lower = read_budget(budget_file)
        limit = start_budget if lower is None else min(start_budget, lower)
        active = measure()  # first: it gives MLX's spare buffers back to macOS
        free = free_bytes()
        if free is not None:
            limit = max(0, min(limit, active + free - FREE_RESERVE))
        elif not unread:
            unread.append(True)
            print(
                "mlx_guard: free memory could not be read; using the fixed budget",
                file=sys.stderr,
            )
        return {"budget_bytes": limit, "free_bytes": free}

    def slots_of(provider) -> OrderedDict:
        """``model_key -> (Resident, member values, over budget)``, oldest first."""
        return provider.__dict__.setdefault("_studio_slots", OrderedDict())

    def measure() -> int:
        gc.collect()
        mx.clear_cache()
        return int(mx.get_active_memory())

    def report(provider, event: str, model: str, earlier_peak: int = 0, checked=None) -> None:
        """``checked`` is from budget(); without it the last figures stay."""
        slots = slots_of(provider)
        status(
            event,
            model,
            resident=[key[0] for key in reversed(slots)],
            oversize=[key[0] for key, slot in slots.items() if slot[2]],
            **(checked or {}),
            context_tokens=opts.kv_tokens,
            active_bytes=int(mx.get_active_memory()),
            peak_bytes=max(earlier_peak, int(mx.get_peak_memory())),
            multi=True,
            reason=None,
        )

    def _load(self, model_path, adapter_path=None, draft_model_path=None):
        key = (model_path, adapter_path, draft_model_path)
        slots = slots_of(self)
        # The peak since the last switch covers the replies that just finished.
        peak = int(mx.get_peak_memory())
        mx.reset_peak_memory()
        if key in slots:
            for name, value in zip(MEMBERS, slots[key][1], strict=True):
                setattr(self, name, value)
            slots.move_to_end(key)
            report(self, "hit", model_path, peak)
            return
        self.reset()
        new = Resident(key, *figures(model_path, opts.kv_tokens))
        cache = getattr(self, "_studio_prompt_cache", None)
        checked = budget()  # not on a hit: switching loads nothing
        while True:
            plan = admission_plan(
                [slot[0] for slot in slots.values()],
                new,
                measure(),
                cache.nbytes if cache is not None else 0,
                checked["budget_bytes"],
                opts.max_models,
            )
            if plan.evict:
                dropped = dict(zip(MEMBERS, slots.pop(plan.evict[0])[1], strict=True))
                release((dropped["model"], dropped["draft_model"]))
                del dropped
                measure()
                report(self, "evict", plan.evict[0][0], peak, checked)
            elif plan.trim_to_bytes is not None and cache.nbytes > plan.trim_to_bytes:
                # Planned again afterwards: the trim may free less than it counted.
                cache.trim_to(n_bytes=plan.trim_to_bytes)
                measure()
                report(self, "trim", model_path, peak, checked)
            else:
                break
        if plan.alone:
            print(
                f"mlx_guard: warning: {model_path} is loaded alone, {plan.reason}; "
                "macOS may stop the server",
                file=sys.stderr,
            )
            report(self, "alone", model_path, peak, checked)
        try:
            load_one(self, *key)
        except BaseException:
            report(self, "load-failed", model_path, peak, checked)
            raise
        slots[key] = (new, tuple(getattr(self, name) for name in MEMBERS), plan.alone)
        report(self, "load", model_path, peak, checked)

    def preload(self):
        """Load the first model, then the second if both fit, and end on the first."""
        try:
            load_default(self)
            preload_second(self)
        finally:
            # MLX Studio waits for this before it compares memory, however it ended.
            status(preload_done=True)

    def preload_second(self):
        repo = opts.also
        if not repo:
            return
        checked = None
        # The same rules a request's model name gets: cached, and not a local path.
        if check_body(repo, None, None, None, is_cached) is None:
            new = Resident(repo, *figures(repo, opts.kv_tokens))
            held = [slot[0] for slot in slots_of(self).values()]
            checked = budget()
            limit = checked["budget_bytes"]
            plan = admission_plan(held, new, measure(), 0, limit, opts.max_models)
            if not (plan.evict or plan.alone):
                try:
                    self.load(repo)
                except Exception:  # the server still starts with the first model
                    traceback.print_exc()
                    report(self, "preload-skipped", repo)
                self.load("default_model", None, "default_model")
                return
        report(self, "preload-skipped", repo, checked=checked)

    def __init__(self, model_provider, prompt_cache):
        # Set first: the original starts the thread that loads the models.
        model_provider._studio_prompt_cache = prompt_cache
        # mlx_lm trims to --prompt-cache-bytes only on its batch path. The cache's
        # own byte limit holds on every insert, serial requests included.
        cap = getattr(getattr(model_provider, "cli_args", None), "prompt_cache_bytes", None)
        if cap is not None:
            prompt_cache.max_bytes = cap
        # Wrap _next_request on the class before the thread starts so every call
        # runs the wrapper on the worker thread. Refresh at most every 10 s.
        # Pass no event name so the status file's 20-event cap is not consumed.
        if hasattr(generator, "_next_request"):
            original_next = generator._next_request
            last_refresh = {"t": 0.0}

            def wrapped_next(self, timeout=None):
                req = original_next(self, timeout)
                now = time.monotonic()
                if now - last_refresh["t"] >= 10.0:
                    last_refresh["t"] = now
                    with contextlib.suppress(Exception):
                        report(
                            self.model_provider,
                            None,
                            None,
                            earlier_peak=int(mx.get_peak_memory()),
                            checked=budget(),
                        )
                return req

            generator._next_request = wrapped_next
        start_generator(self, model_provider, prompt_cache)

    provider_class._load = _load
    provider_class.load_default = preload
    generator.__init__ = __init__


def start_multi(server, opts: Options, status, repo_to_path, record) -> None:
    """Turn on the second slot. If mlx_lm has moved, serve one model and say why."""
    problem = multi_problem(server)
    if problem:
        # Holding two models protects nothing, so this is not a reason to refuse.
        print(f"mlx_guard: keeping one model at a time: {problem}", file=sys.stderr)
        status(multi=False, reason=problem)
        install_report(server, status, memory_budget(opts))
        return
    install_multi(
        server,
        opts,
        status,
        figures=model_figures(repo_to_path),
        is_cached=cached_checker(repo_to_path),
    )
    if opts.also:
        record(opts.also)


# Prompt length ------------------------------------------------------------- #
def close_apps_words(running: bool = True) -> str:
    """How full the Mac is now, what to close, and how to make the server read it again."""
    used = used_percent()
    now = "" if used is None else f"Memory in use now: {used}%. "
    press = "Stop, then Start Server" if running else "Start Server"
    return (
        f"{now}Close other apps (web browsers, code editors, virtual machines, other "
        f"model apps), then on the API Server page press {press}."
    )


def low_context_words(percent: int | None = None, running: bool = True) -> str:
    """Why no prompt is accepted, and what the user can do about it."""
    share = lowered(MAX_RAM_FRACTION, percent)
    return (
        f"Its safe context is below {MIN_AUTO_CONTEXT} tokens: with it the whole Mac "
        f"would use more than {share:.0%} of this Mac's memory. {close_apps_words(running)} "
        "Or choose a smaller model."
    )


class PromptTooLong(ValueError):
    def __init__(self, tokens: int, limit: int, percent: int | None = None) -> None:
        # OpenAI's wording first: clients look for it to compact their history.
        message = (
            f"This model's maximum context length is {limit} tokens on this server. "
            f"However, your prompt is {tokens} tokens. Start a new chat, or let your "
            "client compact the conversation to fit."
        )
        self.code = "context_length_exceeded"
        if limit <= 0:  # no shorter prompt would help, so clients must not compact
            self.code = "insufficient_memory"
            message = "This server accepts no prompt for this model. " + low_context_words(percent)
        super().__init__(message)


class _Answered(BaseException):
    """The refusal has been sent. Not an Exception: mlx_lm would answer a second time."""


# A user's max context above what the memory ceiling holds is lowered to that figure.
CLAMP_USER_CONTEXT = True


def read_model_contexts(text: str | None) -> dict[str, int]:
    """``{repo: tokens}`` from MLX_STUDIO_MODEL_CONTEXTS; anything unusable is left out."""
    try:
        found = json.loads(text or "{}")
        return {repo: n for repo, n in found.items() if _count(n)}
    except (ValueError, AttributeError, RecursionError):
        return {}


def read_model_cache_limits(text: str | None) -> dict[str, int]:
    """``{repo: MB}`` from MLX_STUDIO_MODEL_CACHE_LIMITS; anything unusable is left out."""
    low, high = CACHE_LIMIT_MB_RANGE
    try:
        found = json.loads(text or "{}")
        return {repo: n for repo, n in found.items() if type(n) is int and low <= n <= high}
    except (ValueError, AttributeError, RecursionError):
        return {}


def install_cache_limit(server, mb: int | None, model_mbs: dict[str, int]) -> None:
    """Limit the free buffers MLX keeps (``mx.set_cache_limit``, for the whole process).

    A model's own figure applies once it is loaded, else ``mb``. With neither, MLX's
    default stays: nothing is called before a figure is set.
    """
    default: list[int] = []  # MLX's own, returned by the first call

    def apply(value: int | None) -> None:
        if value is None and not default:
            return
        try:
            wanted = default[0] if value is None else value * 10**6
            previous = server.mx.set_cache_limit(wanted)
        except Exception as exc:  # a tuning figure never stops the server or a load
            print(f"mlx_guard: the MLX cache limit was not set: {exc}", file=sys.stderr)
            return
        if not default:
            default.append(previous)

    apply(mb)
    if not model_mbs:
        return
    provider_class = getattr(server, "ModelProvider", None)
    load_one = getattr(provider_class, "_load", None)
    if not callable(load_one):
        print(
            "mlx_guard: no model's own MLX cache limit is applied: mlx_lm.server changed",
            file=sys.stderr,
        )
        return

    def _load(self, *args, **kwargs):
        try:
            return load_one(self, *args, **kwargs)
        finally:
            key = getattr(self, "model_key", None)
            apply(model_mbs.get(key[0], mb) if key else mb)

    provider_class._load = _load


# mlx_lm's performance flags, as MLX Studio offers them. MLX can store the cache in
# other widths too, but 5, 6 and 7 bits fail in mlx_lm 0.32.2 and 1 to 3 are coarser
# than 4, which already gave broken replies. A read step above mlx_lm's own 2048
# needs more memory than the automatic limit was measured with.
KV_BITS = (4, 8)
KV_GROUPS = (32, 64, 128)
PREFILL_RANGE = (64, 2048)
PERFORMANCE_DEFAULTS = {"kv_bits": None, "kv_group_size": 64, "prefill_step_size": 2048}
# Their mlx_lm cache is a sliding window, which cannot be compressed.
SLIDING_MODELS = ("gemma3", "gemma3_text", "gemma3n", "gemma4", "gemma4_text", "gpt_oss")
SLIDING_WORDS = "it keeps a sliding window of its memory, which mlx_lm cannot compress"


def kv_problem(config, group: int = 64) -> str | None:
    """Why ``config``'s model cannot compress its cache in groups of ``group``, or None.

    Only what the file shows: the server looks at the loaded model as well.
    """
    text = _text_config(config)
    kinds = {text.get("model_type"), config.get("model_type") if config else None}
    layers = text.get("layer_types")
    window = text.get("sliding_window") or text.get("sliding_window_size")
    if (
        kinds & set(SLIDING_MODELS)
        or (isinstance(layers, list) and "sliding_attention" in layers)
        or (_count(window) and text.get("use_sliding_window") is not False)
    ):
        return SLIDING_WORDS
    head = _count(text.get("head_dim"))
    hidden, heads = _count(text.get("hidden_size")), _count(text.get("num_attention_heads"))
    if not head and hidden and heads:
        head = hidden // heads
    if head and head % group:
        return f"its memory blocks ({head} wide) cannot be split into groups of {group}"
    return None


def _performance_value(name: str, value) -> bool:
    if type(value) is not int:
        return False
    if name == "prefill_step_size":
        return PREFILL_RANGE[0] <= value <= PREFILL_RANGE[1]
    return value in {"kv_bits": KV_BITS, "kv_group_size": KV_GROUPS}.get(name, ())


def read_model_performance(text: str | None) -> dict[str, dict[str, int]]:
    """``{repo: flags}`` from the app; an empty entry is Standard. Bad entries are left out."""
    try:
        data = json.loads(text or "{}")
        return {
            repo: flags
            for repo, flags in data.items()
            if REPO_ID.fullmatch(repo)
            and isinstance(flags, dict)
            and all(_performance_value(name, value) for name, value in flags.items())
        }
    except Exception:  # parsing can fail in more ways than ValueError
        return {}


def install_performance(server, status, model_flags: dict, repo_to_path) -> str | None:
    """Give each loaded model its own performance flags and report what is in force.

    A model without an entry in ``model_flags`` keeps the command line's. One that
    cannot compress its cache runs Standard. Returns why nothing was installed.
    """
    provider_class = getattr(server, "ModelProvider", None)
    generator = getattr(server, "ResponseGenerator", None)
    load_one = getattr(provider_class, "_load", None)
    batchable = getattr(generator, "_is_batchable", None)
    if not (callable(load_one) and callable(batchable)):
        return "mlx_lm.server changed"
    command_line: dict = {}
    in_force: dict[str, dict] = {}

    def wanted(repo, cli) -> dict:
        if not command_line:  # read before anything here changes it
            command_line.update(
                {name: getattr(cli, name, value) for name, value in PERFORMANCE_DEFAULTS.items()}
            )
        if repo in model_flags:
            return {**PERFORMANCE_DEFAULTS, **model_flags[repo]}
        return dict(command_line)

    def problem(repo: str, model, group: int) -> str | None:
        try:
            if found := kv_problem(read_config(Path(repo_to_path(repo))), group):
                return found
        except Exception:  # unread: the loaded model still says
            found = None
        try:
            caches = server.make_prompt_cache(model)
        except Exception:
            return None
        sliding = any(type(c).__name__.endswith("RotatingKVCache") for c in caches)
        return SLIDING_WORDS if sliding else None

    def _load(self, *args, **kwargs):
        cli = self.cli_args
        wanted(None, cli)
        result = load_one(self, *args, **kwargs)
        repo = self.model_key[0]
        flags = wanted(repo, cli)
        note = None
        if flags["kv_bits"] and (why := problem(repo, self.model, flags["kv_group_size"])):
            note = f"Standard is used, as this model cannot use compressed memory: {why}."
            flags = dict(PERFORMANCE_DEFAULTS)
            print(f"mlx_guard: {repo}: {note}", file=sys.stderr)
        for name, value in flags.items():
            setattr(cli, name, value)
        changed = {n: v for n, v in flags.items() if v != PERFORMANCE_DEFAULTS[n]}
        in_force[repo] = {**changed, "note": note}
        status(performance=dict(in_force))
        return result

    def _is_batchable(self, args):
        # mlx_lm asks this before it loads the requested model, while the flags are
        # still the last one's. A compressed cache cannot be batched.
        provider = self.model_provider
        name = getattr(getattr(args, "model", None), "model", None)
        repo = getattr(provider, "_model_map", {}).get(name, name)
        loaded = provider.model_key[0] if provider.model_key else None
        if repo != loaded and wanted(repo, provider.cli_args)["kv_bits"]:
            return False
        return batchable(self, args)

    provider_class._load = _load
    generator._is_batchable = _is_batchable
    return None


def exit_with_parent() -> None:
    """End this process when stdin closes, so a killed parent leaves no server."""

    def watch() -> None:
        with contextlib.suppress(OSError):
            while os.read(0, 4096):
                pass
        os._exit(0)

    threading.Thread(target=watch, daemon=True).start()


def prompt_limit(
    config,
    weights: int,
    budget: int,
    kv_tokens: int | None,
    ceiling: int,
    chosen: int | None = None,
    others: int | None = None,
) -> int | None:
    """The longest prompt to accept for a model, or None when no figure is known.

    Never above the model's own context limit. Two models: ``kv_tokens``. One model,
    with ``others`` (the bytes other apps hold) known: the tokens that fit in what
    they leave of ``ceiling``. Unknown: the tokens that fit in ``budget``, but not
    below ``KV_TOKENS`` (or the model's smaller limit) as long as they fit in
    ``ceiling``. A figure worked out here that is below ``MIN_AUTO_CONTEXT`` (and the
    model's limit) is 0: refused. ``chosen`` is the user's own figure: it takes the
    place of all of these, but is never above the model's limit or what fits in what
    ``others`` leave of ``ceiling``; a figure lowered that far follows the same
    ``MIN_AUTO_CONTEXT`` rule. For that check an unreadable cache size counts as
    ``UNKNOWN_KV_PER_TOKEN``; with unread weights the figure is also at most
    ``KV_TOKENS``.
    """
    most = context_limit(config)
    safe = chosen or kv_tokens
    per_token = kv_bytes_per_token(config)

    def fits(allowed: int) -> int:
        room = max(allowed - weights - MEMORY_MARGIN, 0)
        return int(room // (ONE_MODEL_FACTOR * (per_token or UNKNOWN_KV_PER_TOKEN)))

    least = min(most or MIN_AUTO_CONTEXT, MIN_AUTO_CONTEXT)
    if chosen and CLAMP_USER_CONTEXT and ceiling > 0:
        room = fits(ceiling - (others or 0))
        if weights <= 0:  # the room left for the cache is not known either
            room = min(room, KV_TOKENS)
        if room < chosen:
            safe = room if room >= least else 0
    if safe is None and per_token and weights > 0 and budget > 0:
        if others is None:
            floor = min(most or KV_TOKENS, KV_TOKENS)
            safe = min(max(fits(budget), floor), fits(ceiling))
        else:
            safe = fits(ceiling - others)
        if safe < least:
            safe = 0
    known = [tokens for tokens in (most, safe) if tokens is not None]
    return min(known, default=None)


def install_prompt_limits(
    server,
    status,
    repos,
    budget: int,
    kv_tokens: int | None,
    repo_to_path,
    *,
    ceiling: int,
    max_context: int | None = None,
    memory_percent: int | None = None,
    model_contexts: dict[str, int] | None = None,
    others: int | None = None,
) -> str | None:
    """Refuse a too long prompt before it is read; else why that cannot be done.

    mlx_lm's own ``_tokenize`` counts it, so the chat template and tools are included.
    """
    try:
        generator, handler = server.ResponseGenerator, server.APIHandler
        tokenize, complete = generator._tokenize, handler.handle_completion
        if list(inspect.signature(tokenize).parameters) != [
            "self", "tokenizer", "request", "args"
        ]:  # fmt: skip
            return "ResponseGenerator._tokenize takes different arguments"
        if "generate" not in complete.__code__.co_names:
            return "APIHandler.handle_completion no longer calls generate"
    except (AttributeError, TypeError, ValueError) as exc:
        return f"mlx_lm.server has changed ({exc})"

    lowered_for: set[str] = set()  # said once for each model
    refused_for: set[str] = set()
    guessed_for: set[str] = set()

    def limit(repo: str) -> int | None:
        # The model's own figure, else the global one: a switch changes which applies.
        chosen = (model_contexts or {}).get(repo, max_context)
        try:
            snapshot = Path(repo_to_path(repo))
            config, weights = read_config(snapshot), weights_bytes(snapshot)
            most = prompt_limit(config, weights, budget, kv_tokens, ceiling, chosen, others)
        except Exception:  # an unknown figure never refuses a request
            return chosen
        unread = [
            name
            for name, known in (
                ("cache size per token", kv_bytes_per_token(config)),
                ("weight size", weights > 0),
            )
            if not known
        ]
        if chosen and CLAMP_USER_CONTEXT and ceiling > 0 and unread and repo not in guessed_for:
            guessed_for.add(repo)
            print(
                f"mlx_guard: the {' and '.join(unread)} of {repo} cannot be read; the max "
                f"context of {chosen} is checked with a fallback figure; {most} tokens apply.",
                file=sys.stderr,
            )
        if most == 0 and repo not in refused_for:
            refused_for.add(repo)
            print(
                f"mlx_guard: no prompt is accepted for {repo}. "
                + low_context_words(memory_percent),
                file=sys.stderr,
            )
        wanted = min(chosen, context_limit(config) or chosen) if chosen else None
        if wanted and most and most < wanted and repo not in lowered_for:
            lowered_for.add(repo)
            share = lowered(MAX_RAM_FRACTION, memory_percent)
            print(
                f"mlx_guard: the max context of {chosen} for {repo} is more than "
                f"{share:.0%} of this Mac's memory holds with what other apps use; "
                f"{most} tokens apply. {close_apps_words()}",
                file=sys.stderr,
            )
        return most

    def cap_cache(reader, repo: str, most: int | None) -> None:
        """One model: the prompt cache holds at most two conversations of ``most``."""
        cache = getattr(reader, "prompt_cache", None)
        if cache is None:
            return
        try:
            per_token = kv_bytes_per_token(read_config(Path(repo_to_path(repo))))
        except Exception:
            per_token = None
        tokens = KV_TOKENS if most is None else most
        planned = CACHED_CONVERSATIONS * (per_token or UNKNOWN_KV_PER_TOKEN) * tokens
        # The user's --prompt-cache-bytes only lowers it.
        asked = getattr(reader.model_provider.cli_args, "prompt_cache_bytes", None)
        cache.max_bytes = min(asked or planned, planned)
        if cache.nbytes > cache.max_bytes:  # what an earlier model left behind
            cache.trim_to(n_bytes=cache.max_bytes)

    def _tokenize(self, tokenizer, request, args):
        result = tokenize(self, tokenizer, request, args)
        key = getattr(self.model_provider, "model_key", None)
        if not key:
            return result
        most = limit(key[0])
        if kv_tokens is None:  # two models: install_multi has set the planned cap
            cap_cache(self, key[0], most)
        if most is None:
            return result
        length = len(result[0])
        if length > most:
            raise PromptTooLong(length, most, memory_percent)
        # The reply is cached too: it gets what the prompt leaves, and at least one token.
        room = max(most - length, 1)
        asked = getattr(args, "max_tokens", None)
        if isinstance(asked, int) and asked > room:
            args.max_tokens = room
        return result

    def handle_completion(self, request, stop_words):
        real = self.response_generator

        def generate(*args, **kwargs):
            try:
                return real.generate(*args, **kwargs)
            except PromptTooLong as exc:
                error = {
                    "message": str(exc),
                    "type": "invalid_request_error",
                    "param": "messages",
                    "code": exc.code,
                }
                body = json.dumps({"error": error}).encode()
                self._set_completion_headers(400)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                raise _Answered from None

        # One handler serves one connection, so this stand-in is its own.
        self.response_generator = types.SimpleNamespace(cli_args=real.cli_args, generate=generate)
        try:
            complete(self, request, stop_words)
        except _Answered:
            pass
        finally:
            self.response_generator = real

    generator._tokenize = _tokenize
    handler.handle_completion = handle_completion
    known = {repo: tokens for repo in repos if (tokens := limit(repo)) is not None}
    if known:
        status(prompt_limits=known)
    return None


def main() -> None:
    # Before any import of huggingface_hub, which reads these once.
    os.environ["HF_HUB_OFFLINE"] = "1"
    # ModelScope would replace the downloader and ignore the offline switch.
    os.environ.pop("MLXLM_USE_MODELSCOPE", None)
    sys.argv[1:], opts = split_studio_args(sys.argv[1:])
    if opts.exit_with_parent:
        exit_with_parent()  # before the slow imports
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
    resident_file = os.environ.get("MLX_STUDIO_RESIDENT_FILE")
    status = status_writer(resident_file)
    if started := os.environ.get("MLX_STUDIO_SETTINGS"):
        status(started_with=started)  # so the app knows when Apply is needed
    # Asked before start_multi changes what multi_problem reads.
    two = opts.max_models > 1 and not multi_problem(server)
    if opts.max_models > 1:
        start_multi(server, opts, status, hf_repo_to_path, record)
    elif resident_file:
        install_report(server, status, memory_budget(opts))
    # After the wrappers above, which look at mlx_lm's own _load. Two models share one
    # process, so they get the start value only.
    install_cache_limit(
        server,
        opts.cache_limit_mb,
        {} if two else read_model_cache_limits(os.environ.get("MLX_STUDIO_MODEL_CACHE_LIMITS")),
    )
    performance = read_model_performance(os.environ.get("MLX_STUDIO_MODEL_PERFORMANCE"))
    # After the wrappers above too: each model's flags, and what is in force.
    unset = install_performance(server, status, performance, hf_repo_to_path)
    if unset and performance:  # the command line's flags apply either way
        print(f"mlx_guard: per-model performance is not applied: {unset}", file=sys.stderr)
    args = sys.argv[1:]
    first = args[args.index("--model") + 1 :][:1] if "--model" in args else []
    unchecked = install_prompt_limits(
        server,
        status,
        [repo for repo in (*first, opts.also) if repo],
        memory_budget(opts),
        opts.kv_tokens if two else None,
        hf_repo_to_path,
        ceiling=memory_ceiling(opts.memory_percent),
        max_context=None if two else opts.max_context,
        model_contexts=None
        if two
        else read_model_contexts(os.environ.get("MLX_STUDIO_MODEL_CONTEXTS")),
        memory_percent=opts.memory_percent,
        # Read before any model is loaded; this process's own memory is not "others".
        others=None if two else other_apps_bytes(os.getpid()),
    )
    if unchecked:  # a protection that is missing is no reason to refuse to start
        print(f"mlx_guard: long prompts are not refused: {unchecked}", file=sys.stderr)
    server.main()


if __name__ == "__main__":
    main()
