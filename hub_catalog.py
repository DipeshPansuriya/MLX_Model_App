"""The Download Model screen's data: recommended picks, Hub search and real weight sizes.

Pure logic: the caller passes the catalogue, the memory size, the fit rule and (in
tests) the network calls. Everything the Hub returns is untrusted, so each value is
type-checked, and a missing or odd one is None, never a guess.
"""

from __future__ import annotations

import datetime
import re
import threading

import model_info

HF_ORG = "mlx-community"
HUB_URL = "https://huggingface.co/"
REPO_MAX = 200
QUERY_MAX = 200
TEXT_MAX = 100
SEARCH_LIMIT = 80
SIZE_CALLS = 4  # at most this many size requests at once
SIZE_TIMEOUT_SECONDS = 15
SEARCH_FIELDS = (
    "downloads",
    "safetensors",
    "tags",
    "pipeline_tag",
    "library_name",
    "config",
    "cardData",
    "baseModels",
    "likes",
    "lastModified",
    "gated",
)
SEARCH_FAILED = "Could not reach Hugging Face. Showing recommended models only."
SEARCH_LIMITED = (
    "Hugging Face is limiting requests. Showing recommended models only; "
    "try again in a few minutes."
)
NOT_PUBLISHED = "Not published"
# First match wins: (category, tasks, other tags, model types).
CATEGORY_RULES = (
    ("Vision", ("image-text-to-text", "any-to-any"), (), ()),
    ("Speech to text", ("automatic-speech-recognition",), ("speech-to-text", "stt"), ("whisper",)),
    ("Speech and audio", ("text-to-speech", "text-to-audio", "audio-to-audio"), ("tts",), ()),
    ("Embedding", ("feature-extraction", "sentence-similarity"), (), ()),
    ("Classification", ("text-classification", "token-classification"), (), ()),
    ("Text", ("text-generation",), (), ()),
)
# Task names believed when they appear only as a tag.
OTHER_TASKS = (
    "audio-classification",
    "audio-text-to-text",
    "depth-estimation",
    "document-question-answering",
    "fill-mask",
    "image-classification",
    "image-feature-extraction",
    "image-segmentation",
    "image-to-image",
    "image-to-text",
    "image-to-video",
    "object-detection",
    "question-answering",
    "reinforcement-learning",
    "robotics",
    "summarization",
    "table-question-answering",
    "text-ranking",
    "text-to-image",
    "text-to-video",
    "text2text-generation",
    "time-series-forecasting",
    "translation",
    "video-classification",
    "video-text-to-text",
    "visual-question-answering",
    "voice-activity-detection",
    "zero-shot-classification",
    "zero-shot-image-classification",
    "zero-shot-object-detection",
)
KNOWN_TASKS = frozenset(task for rule in CATEGORY_RULES for task in rule[1]) | set(OTHER_TASKS)
TASK_NAME = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*", re.ASCII)
CHIPS = (("conversational", "Chat"), ("code", "Code"), ("reasoning", "Reasoning"))
FIT_ORDER = {"great": 0, "good": 1, "tight": 2, "too-large": 3, "unknown": 4}
PICK_TAGS = {"great": "Best for Mac", "good": "Best for Mac", "tight": "Recommended, tight"}


def hub_url(repo) -> str | None:
    """The model's page, only for an exact ``owner/name`` id; otherwise no link."""
    if not isinstance(repo, str) or len(repo) > REPO_MAX:
        return None
    parts = repo.split("/")
    if len(parts) != 2 or not all(model_info.REPO_PART.fullmatch(p) for p in parts):
        return None
    return HUB_URL + repo


# ---- reading untrusted values ----------------------------------------------------
def _text(value) -> str | None:
    ok = isinstance(value, str) and 0 < len(value) <= TEXT_MAX and value.isprintable()
    return value if ok else None


def _number(value) -> int | None:
    return value if type(value) is int and value >= 0 else None


def _field(source, key: str):
    """``key`` of a dict, or of the hub library's object for the same data."""
    return source.get(key) if isinstance(source, dict) else getattr(source, key, None)


def _tags(model) -> list[str]:
    tags = getattr(model, "tags", None)
    return [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []


def _task(value) -> str | None:
    value = _text(value)
    return value if value and TASK_NAME.fullmatch(value) else None


def category(task, tags, model_type) -> str:
    """What the model is for, from what is published only; never from its name."""
    tags = [t for t in tags if isinstance(t, str)] if isinstance(tags, list) else []
    task = _task(task)
    tasks = [task] if task else [t for t in tags if t in KNOWN_TASKS]
    for name, rule_tasks, rule_tags, model_types in CATEGORY_RULES:
        if (
            set(tasks) & set(rule_tasks)
            or set(tags) & set(rule_tags)
            or (isinstance(model_type, str) and model_type in model_types)
        ):
            return name
    return tasks[0].replace("-", " ").capitalize() if tasks else NOT_PUBLISHED


def _params(model) -> str | None:
    counts = _field(getattr(model, "safetensors", None), "parameters")
    if not isinstance(counts, dict) or not all(_number(n) is not None for n in counts.values()):
        return None
    total = sum(counts.values())
    if total >= 1_000_000_000:
        return f"{total / 1e9:.1f}".removesuffix(".0") + "B"
    if total >= 500_000:
        return f"{round(total / 1e6)}M"
    return "<1M" if total else None


def _bits(tags: list[str], config) -> int | None:
    for tag in tags:
        found = re.fullmatch(r"(\d{1,2})-bit", tag, re.ASCII)
        if found:
            return int(found[1])
    bits = _number(_field(_field(config, "quantization_config"), "bits"))
    return bits if bits and bits <= 32 else None


def _base_model(model, tags: list[str]) -> str | None:
    listed = getattr(model, "base_models", None)
    if isinstance(listed, dict):
        listed = listed.get("models")
    listed = listed if isinstance(listed, list) else [listed]
    found = [item if isinstance(item, str) else _field(item, "id") for item in listed]
    found += [t.rsplit(":", 1)[1] for t in tags if t.startswith("base_model:")]
    return next((repo for repo in found if hub_url(repo)), None)


def _published(model) -> dict:
    """The card fields that come from one search hit."""
    tags = _tags(model)
    config = getattr(model, "config", None)
    data = getattr(model, "card_data", None)
    base = _base_model(model, tags)
    updated = getattr(model, "last_modified", None)
    gated = getattr(model, "gated", None)
    if gated in ("auto", "manual"):
        gated = True
    return {
        "params": _params(model),
        "bits": _bits(tags, config),
        "category": category(
            getattr(model, "pipeline_tag", None) or _field(data, "pipeline_tag"),
            tags,
            _field(config, "model_type"),
        ),
        "chips": [label for tag, label in CHIPS if tag in tags],
        "base_model": base,
        "base_model_url": hub_url(base),
        "license": _text(_field(data, "license")),
        "library": _text(getattr(model, "library_name", None)),
        "downloads": _number(getattr(model, "downloads", None)),
        "likes": _number(getattr(model, "likes", None)),
        "updated": f"{updated:%Y-%m-%d}" if isinstance(updated, datetime.datetime) else None,
        "gated": gated if isinstance(gated, bool) else None,
    }


# ---- cards -----------------------------------------------------------------------
def _card(repo: str, entry: dict | None, model, ram_bytes: int, fit, installed) -> dict:
    """One card. ``entry`` is the recommended pick, ``model`` the search hit; either
    may be missing. ``category`` is None when the Hub was not asked about it."""
    card = {
        "repo": repo,
        "url": hub_url(repo),
        "params": None,
        "bits": None,
        "size_gb": None,
        "size_status": "unchecked",
        "fit": "unknown",
        "category": None,
        "chips": [],
        "pick": None,
        "pick_tag": None,
        "use": None,
        "base_model": None,
        "base_model_url": None,
        "license": None,
        "library": None,
        "downloads": None,
        "likes": None,
        "updated": None,
        "gated": None,
        "installed": repo in installed,
    }
    if model is not None:
        card.update(_published(model))
    if entry:
        rating = fit(entry["size_gb"], ram_bytes)
        card.update(
            params=entry["params"],
            size_gb=entry["size_gb"],  # measured from the published weight files
            size_status="ok",
            fit=rating,
            pick=entry["category"],
            pick_tag=PICK_TAGS.get(rating),
            use=entry["use"],
        )
    return card


def _by_fit(card: dict):
    return FIT_ORDER.get(card["fit"], 9), card["size_gb"]


def curated_cards(catalog: list[dict], ram_bytes: int, fit, installed=frozenset()) -> list[dict]:
    """The recommended picks, best fit first."""
    cards = [_card(m["repo"], m, None, ram_bytes, fit, installed) for m in catalog]
    return sorted(cards, key=_by_fit)


def _matches(entry: dict, words: list[str]) -> bool:
    text = f"{entry['repo']} {entry['category']} {entry['use']}".lower()
    return all(word in text for word in words)


def _list_models(**kwargs):
    from huggingface_hub import HfApi

    return HfApi().list_models(**kwargs)


def search(
    query, *, catalog: list[dict], ram_bytes: int, fit, installed=frozenset(), list_models=None
) -> dict:
    """``{"results": [...], "error": str | None}``: matching picks first, then Hub
    hits by downloads, each repo once. A Hub failure still returns the picks."""
    query = query if isinstance(query, str) else ""
    query = "".join(c for c in query if c.isprintable()).strip()[:QUERY_MAX]
    list_models = list_models or _list_models
    wanted = {"author": HF_ORG, "search": query or None, "limit": SEARCH_LIMIT, "sort": "downloads"}
    hits, error = {}, None
    try:
        try:
            found = list(list_models(**wanted, expand=list(SEARCH_FIELDS)))
        except TypeError:  # an older huggingface_hub without "expand"
            found = list(list_models(**wanted))
        for model in found:
            repo = getattr(model, "id", None)
            if hub_url(repo):
                hits.setdefault(repo, model)
    except Exception as exc:
        # The type only: a library message can hold request headers or local paths.
        print(f"search failed: {type(exc).__name__}")
        status = getattr(getattr(exc, "response", None), "status_code", None)
        hits, error = {}, SEARCH_LIMITED if status == 429 else SEARCH_FAILED
    words = query.lower().split()
    picks = [m for m in catalog if m["repo"] in hits or _matches(m, words)]
    pinned = sorted(
        (_card(m["repo"], m, hits.pop(m["repo"], None), ram_bytes, fit, installed) for m in picks),
        key=_by_fit,
    )
    others = [_card(repo, None, m, ram_bytes, fit, installed) for repo, m in hits.items()]
    others.sort(key=lambda c: c["downloads"] or 0, reverse=True)
    return {"results": pinned + others, "error": error}


# ---- real sizes ------------------------------------------------------------------
def _repo_info(**kwargs):
    from huggingface_hub import HfApi

    return HfApi().repo_info(**kwargs)


def _weight_bytes(info) -> int | None:
    """The published weight total, or None unless every weight file states its size.

    ``.npz`` counts only without ``.safetensors``, so two formats are not added up.
    """
    files = getattr(info, "siblings", None) or []
    for suffix in (".safetensors", ".npz"):
        sizes = [
            _number(getattr(f, "size", None))
            for f in files
            if str(getattr(f, "rfilename", "")).endswith(suffix)
        ]
        if sizes:
            return None if None in sizes else sum(sizes) or None
    return None


class SizeChecker:
    """Weight size of one ``mlx-community`` model, remembered while the app runs."""

    def __init__(self, fit, repo_info=None, max_cached: int = 500):
        self._fit = fit
        self._repo_info = repo_info or _repo_info
        self._max_cached = max_cached
        self._cache: dict[str, int | None] = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(SIZE_CALLS)

    def check(self, repo, ram_bytes: int) -> dict | None:
        """None for a bad id or another author. ``size_status`` is "ok",
        "not_published" or "failed"; a failure is asked again next time."""
        if not hub_url(repo) or not repo.startswith(f"{HF_ORG}/"):
            return None
        known, total = self._cached(repo)
        if not known:
            with self._slots:
                known, total = self._cached(repo)  # fetched while this call waited
                if not known:
                    try:
                        info = self._repo_info(
                            repo_id=repo,
                            repo_type="model",
                            files_metadata=True,
                            timeout=SIZE_TIMEOUT_SECONDS,
                        )
                        total = _weight_bytes(info)
                    except Exception as exc:
                        print(f"size check failed: {type(exc).__name__}")
                        return self._answer(repo, "failed", None, ram_bytes)
                    self._remember(repo, total)
        status = "not_published" if total is None else "ok"
        return self._answer(repo, status, total, ram_bytes)

    def _cached(self, repo: str) -> tuple[bool, int | None]:
        with self._lock:
            return repo in self._cache, self._cache.get(repo)

    def _remember(self, repo: str, total: int | None) -> None:
        with self._lock:
            self._cache[repo] = total
            while len(self._cache) > self._max_cached:
                del self._cache[next(iter(self._cache))]

    def _answer(self, repo: str, status: str, total: int | None, ram_bytes: int) -> dict:
        size = None if total is None else round(total / 1e9, 2)
        return {
            "repo": repo,
            "size_status": status,
            "size_gb": size,
            "weight_bytes": total,
            "fit": "unknown" if size is None else self._fit(size, ram_bytes),
        }
