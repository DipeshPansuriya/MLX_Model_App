"""What a downloaded model's files say: its config, context limit and weight size.

Read by MLX Studio for its screens and the Load Test. Nothing here runs in the server.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

# A Hub model id: never an option, a path or a second slash.
REPO_PART = re.compile(r"[A-Za-z0-9_][\w.-]*", re.ASCII)
# Their mlx_lm cache is a sliding window, which cannot be compressed.
SLIDING_MODELS = ("gemma3", "gemma3_text", "gemma3n", "gemma4", "gemma4_text", "gpt_oss")
SLIDING_WORDS = "it keeps a sliding window of its memory, which mlx_lm cannot compress"
# mlx_lm's performance flags, as MLX Studio offers them. MLX can store the cache in
# other widths too, but 5, 6 and 7 bits fail in mlx_lm 0.32.2 and 1 to 3 are coarser
# than 4, which already gave broken replies.
KV_BITS = (4, 8)
KV_GROUPS = (32, 64, 128)
PREFILL_RANGE = (64, 2048)


def is_repo_id(text: str) -> bool:
    """``name`` or ``owner/name``, each part checked on its own so nothing backtracks."""
    parts = text.split("/")
    return len(parts) <= 2 and all(REPO_PART.fullmatch(part) for part in parts)


ALIAS_MAX = 64
SERVED_NAME_MAX = 96  # the Hub's own limit for a model name
# mlx_lm's own name for its model, and the folders in OptiQ's run folder.
RESERVED_NAMES = frozenset({"default_model", "home", "tmp", "hf"})
# A file name, a Hub repo name and a command line argument, all at once. No ":", which
# OptiQ reads as a variant of a model, and nothing that could name another folder.
SAFE_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?")


def alias_problem(name, longest: int = ALIAS_MAX) -> str | None:
    """Why a server cannot be given ``name`` for its model, or None."""
    if not isinstance(name, str) or not name:
        return "type a name"
    if len(name) > longest:
        return f"use at most {longest} characters"
    if not SAFE_NAME.fullmatch(name):
        return "use letters, digits, '.', '_' and '-', and start and end with a letter or a digit"
    if "--" in name or ".." in name or name.lower().endswith(".git"):
        return "'--', '..' and a name ending in '.git' are not allowed"
    if name.lower() in RESERVED_NAMES:
        return f"{name} is a reserved name"
    return None


def served_name(repo: str) -> str:
    """A readable name for ``repo``, always one that ``alias_problem`` accepts."""
    name = re.sub(r"[^A-Za-z0-9._-]+", "-", repo.rsplit("/", maxsplit=1)[-1])
    name = re.sub(r"([.-])\1+", r"\1", name)[:SERVED_NAME_MAX].strip("._-")
    name = re.sub(r"\.git$", "-git", name, flags=re.IGNORECASE)
    if name.lower() in RESERVED_NAMES:
        name = f"model-{name}"
    return name or "local-model"


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


def weights_bytes(snapshot) -> int:
    try:
        return sum(p.stat().st_size for p in Path(snapshot).glob("model*.safetensors"))
    except (OSError, ValueError):  # ValueError: a name with a NUL byte
        return 0


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
