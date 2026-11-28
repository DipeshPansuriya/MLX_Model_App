"""Row 1036: the combined Download Model screen's backend. Fake Hub responses only."""

from __future__ import annotations

import datetime
import sys
import threading
from types import SimpleNamespace

import pytest

import hub_catalog as hc

try:
    from huggingface_hub.hf_api import ModelInfo
except ImportError:  # CI installs only pytest: stand in for what the module reads

    def ModelInfo(**data):
        card = data.pop("cardData", None)
        info = data.pop("safetensors", None)
        return SimpleNamespace(
            card_data=card,
            safetensors=SimpleNamespace(**info) if info else None,
            base_models=data.pop("baseModels", None),
            last_modified=data.pop("lastModified", None),
            **data,
        )


GB = 1024**3
CATALOG = [
    {
        "repo": "mlx-community/Small-4bit",
        "size_gb": 2.0,
        "params": "3B",
        "category": "Fast",
        "use": "Quick replies",
    },
    {
        "repo": "mlx-community/Coder-4bit",
        "size_gb": 12.0,
        "params": "20B",
        "category": "Code",
        "use": "Writes programs",
    },
]


def fit(size_gb, ram_bytes):
    if ram_bytes <= 0:
        return "unknown"
    share = size_gb / (ram_bytes / GB)
    for limit, name in ((0.25, "great"), (0.44, "good"), (0.70, "tight")):
        if share <= limit:
            return name
    return "too-large"


def hit(repo="mlx-community/Other-4bit", **data):
    return ModelInfo(id=repo, **data)


def search(query, *hits, ram_gb=32, installed=(), error=None):
    seen = []

    def list_models(**kwargs):
        seen.append(kwargs)
        if error:
            raise error
        return iter(hits)

    out = hc.search(
        query,
        catalog=CATALOG,
        ram_bytes=ram_gb * GB,
        fit=fit,
        installed=set(installed),
        list_models=list_models,
    )
    return out, seen


def card(**data):
    out, _ = search("other", hit(**data))
    return out["results"][0]


# ---- categories ------------------------------------------------------------------
@pytest.mark.parametrize(
    ("task", "tags", "model_type", "expected"),
    [
        ("image-text-to-text", [], None, "Vision"),
        ("any-to-any", [], None, "Vision"),
        ("automatic-speech-recognition", [], None, "Speech to text"),
        (None, ["speech-to-text"], None, "Speech to text"),
        ("text-generation", ["stt"], None, "Speech to text"),
        (None, [], "whisper", "Speech to text"),
        ("text-to-speech", [], None, "Speech and audio"),
        ("text-to-audio", [], None, "Speech and audio"),
        ("audio-to-audio", [], None, "Speech and audio"),
        (None, ["tts"], None, "Speech and audio"),
        ("feature-extraction", [], None, "Embedding"),
        ("sentence-similarity", [], None, "Embedding"),
        ("text-classification", [], None, "Classification"),
        ("token-classification", [], None, "Classification"),
        ("text-generation", [], None, "Text"),
        ("image-text-to-text", ["tts"], None, "Vision"),
        ("translation", [], None, "Translation"),
        (None, ["mlx", "text-generation"], None, "Text"),
        (None, ["mlx", "fill-mask"], None, "Fill mask"),
        (None, ["mlx", "4-bit"], None, "Not published"),
        ("<img src=x>", [], None, "Not published"),
        (["text-generation"], None, ["whisper"], "Not published"),
    ],
)
def test_category(task, tags, model_type, expected):
    assert hc.category(task, tags, model_type) == expected


def test_task_comes_from_the_model_card_when_the_listing_has_none():
    assert card(cardData={"pipeline_tag": "text-generation"})["category"] == "Text"


def test_chips_come_only_from_tags():
    assert card(tags=["conversational", "code", "reasoning"])["chips"] == [
        "Chat",
        "Code",
        "Reasoning",
    ]
    assert card(repo="mlx-community/Other-Coder-Reasoning-Chat")["chips"] == []


# ---- card details ----------------------------------------------------------------
RECORDED = {
    "id": "mlx-community/Other-4bit",
    "downloads": 120,
    "likes": 4,
    "tags": ["mlx", "4-bit", "conversational", "base_model:quantized:org/Base"],
    "pipeline_tag": "text-generation",
    "library_name": "mlx",
    "safetensors": {"parameters": {"BF16": 1000, "U32": 8_199_999_000}, "total": 8_200_000_000},
    "config": {"model_type": "qwen3", "quantization_config": {"bits": 8}},
    "cardData": {"license": "apache-2.0", "base_model": "org/Base"},
    "baseModels": {"relation": "quantized", "models": [{"id": "org/Base"}]},
    "lastModified": "2026-11-01T10:20:30.000Z",
    "gated": False,
}


def test_a_recorded_listing_entry_fills_every_published_field():
    data = dict(RECORDED)
    if not hasattr(ModelInfo, "__mro__"):  # the stand-in takes the parsed date
        data["lastModified"] = datetime.datetime(2026, 11, 1, 10, 20, 30)
    out, _ = search("other", ModelInfo(**data), installed=[data["id"]])
    assert out == {
        "error": None,
        "results": [
            {
                "repo": "mlx-community/Other-4bit",
                "url": "https://huggingface.co/mlx-community/Other-4bit",
                "params": "8.2B",
                "bits": 4,
                "size_gb": None,
                "size_status": "unchecked",
                "fit": "unknown",
                "category": "Text",
                "chips": ["Chat"],
                "pick": None,
                "pick_tag": None,
                "use": None,
                "base_model": "org/Base",
                "base_model_url": "https://huggingface.co/org/Base",
                "license": "apache-2.0",
                "library": "mlx",
                "downloads": 120,
                "likes": 4,
                "updated": "2026-11-01",
                "gated": False,
                "installed": True,
            }
        ],
    }


def test_missing_data_is_never_guessed():
    found = card()
    for key in ("params", "bits", "size_gb", "base_model", "base_model_url"):
        assert found[key] is None
    for key in ("license", "library", "likes", "updated", "gated", "downloads"):
        assert found[key] is None
    assert found["category"] == "Not published" and found["installed"] is False


def test_wrong_types_count_as_not_published():
    odd = SimpleNamespace(
        id="mlx-community/Other",
        downloads="many",
        likes=True,
        tags="code",
        pipeline_tag=7,
        library_name={"a": 1},
        safetensors=SimpleNamespace(parameters={"BF16": "big"}),
        config=["x"],
        card_data={"license": ["mit"], "base_model": {"a": "b"}},
        base_models=5,
        last_modified="yesterday",
        gated="maybe",
    )
    out, _ = search("other", odd)
    found = out["results"][0]
    assert out["error"] is None and found["category"] == "Not published"
    assert found["chips"] == []
    for key in ("params", "bits", "license", "library", "base_model", "downloads"):
        assert found[key] is None
    assert found["likes"] is None and found["updated"] is None and found["gated"] is None


def test_long_or_control_character_text_is_dropped():
    found = card(library_name="m" * 101, cardData={"license": "mit\x00"})
    assert found["library"] is None and found["license"] is None


def test_bits_fall_back_to_the_config():
    assert card(config={"quantization_config": {"bits": 6}})["bits"] == 6
    assert card(config={"quantization_config": {"bits": 600}})["bits"] is None


@pytest.mark.parametrize("gated", ["auto", "manual", True])
def test_gated_repos_are_flagged(gated):
    assert card(gated=gated)["gated"] is True


def test_base_model_from_a_tag_and_a_bad_one_gets_nothing():
    assert card(tags=["base_model:org/Base"])["base_model"] == "org/Base"
    found = card(tags=["base_model:../../etc"])
    assert found["base_model"] is None and found["base_model_url"] is None


# ---- repo ids and links ----------------------------------------------------------
@pytest.mark.parametrize(
    "repo",
    [
        "name",
        "a/b/c",
        "../b",
        "a/..",
        "a/-b",
        "a/b?x=1",
        "a/b#x",
        "a/b c",
        "a/b\n",
        'a/b"onclick=',
        "javascript:alert(1)",
        "https://evil.example/x",
        "a/é",
        "a/" + "b" * 199,
        None,
        5,
    ],
)
def test_no_link_for_a_bad_id(repo):
    assert hc.hub_url(repo) is None


def test_link_has_a_fixed_prefix():
    assert hc.hub_url("Org_1/Model-2.5") == "https://huggingface.co/Org_1/Model-2.5"


def test_hits_with_bad_ids_are_dropped():
    out, _ = search("", hit("mlx-community/../x"), hit("<script>/x"), hit("mlx-community/Ok"))
    assert [r["repo"] for r in out["results"]][len(CATALOG) :] == ["mlx-community/Ok"]


# ---- search and merge ------------------------------------------------------------
def test_search_asks_for_the_new_fields_sorted_by_downloads():
    _, seen = search("  qwen\x00 ")
    asked = seen[0]
    assert (asked["author"], asked["search"], asked["sort"], asked["limit"]) == (
        "mlx-community",
        "qwen",
        "downloads",
        80,
    )
    assert set(asked["expand"]) == {
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
    }


def test_empty_and_overlong_queries():
    assert search("")[1][0]["search"] is None
    assert len(search("q" * 500)[1][0]["search"]) == 200
    assert search(None)[1][0]["search"] is None


def test_curated_matches_come_first_without_duplicates_then_by_downloads():
    out, _ = search(
        "4bit",
        hit("mlx-community/Low", downloads=1),
        hit("mlx-community/Coder-4bit", downloads=9, likes=3, pipeline_tag="text-generation"),
        hit("mlx-community/High", downloads=50),
        hit("mlx-community/High", downloads=50),
    )
    assert [r["repo"].split("/")[1] for r in out["results"]] == [
        "Small-4bit",
        "Coder-4bit",
        "High",
        "Low",
    ]
    coder = out["results"][1]
    assert (coder["pick"], coder["category"], coder["likes"]) == ("Code", "Text", 3)
    assert (coder["size_gb"], coder["size_status"], coder["params"]) == (12.0, "ok", "20B")


def test_curated_matching_uses_every_word_of_name_label_and_use():
    for query in ("coder", "CODE programs", "writes"):
        assert [r["repo"] for r in search(query)[0]["results"]] == ["mlx-community/Coder-4bit"]
    assert search("coder nothing")[0]["results"] == []


def test_a_curated_model_the_hub_did_not_return_claims_no_category():
    found = search("small")[0]["results"][0]
    assert found["category"] is None and found["pick"] == "Fast"


@pytest.mark.parametrize(
    ("ram_gb", "tags"),
    [
        (64, {"Small": "Best for Mac", "Coder": "Best for Mac"}),
        (32, {"Small": "Best for Mac", "Coder": "Best for Mac"}),
        (18, {"Small": "Best for Mac", "Coder": "Recommended, tight"}),
        (16, {"Small": "Best for Mac", "Coder": None}),
        (8, {"Small": "Best for Mac", "Coder": None}),
        (0, {"Small": None, "Coder": None}),
    ],
)
def test_best_for_mac_is_curated_and_fits(ram_gb, tags):
    found = hc.curated_cards(CATALOG, ram_gb * GB, fit)
    assert {c["repo"].split("/")[1].split("-")[0]: c["pick_tag"] for c in found} == tags


def test_curated_cards_sort_by_fit_then_size_and_mark_installed():
    found = hc.curated_cards(CATALOG[::-1], 16 * GB, fit, {CATALOG[0]["repo"]})
    assert [c["fit"] for c in found] == ["great", "too-large"]
    assert [c["installed"] for c in found] == [True, False]
    assert found[0]["url"] == "https://huggingface.co/mlx-community/Small-4bit"


@pytest.mark.parametrize(
    ("status", "words"), [(None, "Could not reach"), (429, "limiting requests")]
)
def test_a_failed_search_keeps_curated_matches_and_says_so(status, words, capsys):
    error = RuntimeError("secret-detail")
    error.response = SimpleNamespace(status_code=status)
    out, _ = search("small", error=error)
    assert words in out["error"] and "secret-detail" not in out["error"]
    assert "secret-detail" not in capsys.readouterr().out
    assert [r["repo"] for r in out["results"]] == ["mlx-community/Small-4bit"]


def test_a_failure_half_way_through_the_listing_is_reported():
    def list_models(**kwargs):
        yield hit()
        raise OSError("cut")

    out = hc.search("", catalog=[], ram_bytes=GB, fit=fit, list_models=list_models)
    assert out["results"] == [] and out["error"]


def test_an_older_hub_library_still_searches():
    seen = []

    def list_models(**kwargs):
        seen.append(kwargs)
        if "expand" in kwargs:
            raise TypeError("expand")
        return [hit()]

    out = hc.search("other", catalog=[], ram_bytes=GB, fit=fit, list_models=list_models)
    assert out["error"] is None and len(out["results"]) == 1 and "expand" not in seen[-1]


def test_it_fits_the_apps_own_catalogue_and_fit_rule(app_module):
    found = hc.curated_cards(app_module.MODEL_CATALOG, 32 * GB, app_module.fit_rating)
    assert len(found) == len(app_module.MODEL_CATALOG)
    assert all(c["url"] and c["size_status"] == "ok" for c in found)
    assert any(c["pick_tag"] == "Best for Mac" for c in found)


# ---- real sizes ------------------------------------------------------------------
def files(*pairs):
    return SimpleNamespace(siblings=[SimpleNamespace(rfilename=n, size=s) for n, s in pairs])


class Hub:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


REPO = "mlx-community/Other-4bit"


def test_size_is_the_exact_sum_of_the_published_weights():
    hub = Hub(
        files(
            ("model-00001-of-00002.safetensors", 15_000_000_000),
            ("model-00002-of-00002.safetensors", 4_740_000_000),
            ("tokenizer.json", 10_000_000),
            ("model.safetensors.index.json", 5000),
        )
    )
    found = hc.SizeChecker(fit, repo_info=hub).check(REPO, 32 * GB)
    assert found == {
        "repo": REPO,
        "size_status": "ok",
        "size_gb": 19.74,
        "weight_bytes": 19_740_000_000,
        "fit": "tight",
    }
    assert hub.calls[0]["repo_id"] == REPO and hub.calls[0]["files_metadata"] is True
    assert hub.calls[0]["repo_type"] == "model" and hub.calls[0]["timeout"] > 0


def test_npz_weights_count_only_when_there_are_no_safetensors():
    only = Hub(files(("weights.npz", 2_000_000_000)))
    assert hc.SizeChecker(fit, repo_info=only).check(REPO, GB)["size_gb"] == 2.0
    both = Hub(files(("weights.npz", 2_000_000_000), ("model.safetensors", 1_000_000_000)))
    assert hc.SizeChecker(fit, repo_info=both).check(REPO, GB)["size_gb"] == 1.0


@pytest.mark.parametrize(
    "info",
    [
        files(),
        files(("README.md", 10), ("model.gguf", 5)),
        files(("a.safetensors", 5), ("b.safetensors", None)),
        files(("a.safetensors", -5)),
        SimpleNamespace(siblings=None),
    ],
)
def test_unpublished_sizes_are_cached_as_not_published(info):
    hub = Hub(info)
    checker = hc.SizeChecker(fit, repo_info=hub)
    for _ in range(2):
        found = checker.check(REPO, 32 * GB)
        assert (found["size_status"], found["size_gb"], found["fit"]) == (
            "not_published",
            None,
            "unknown",
        )
    assert len(hub.calls) == 1


def test_a_failure_is_not_cached():
    hub = Hub(OSError("secret-detail"), files(("model.safetensors", 1_000_000_000)))
    checker = hc.SizeChecker(fit, repo_info=hub)
    first = checker.check(REPO, 32 * GB)
    assert (first["size_status"], first["fit"]) == ("failed", "unknown")
    assert "secret-detail" not in str(first)
    assert checker.check(REPO, 32 * GB)["size_status"] == "ok"


def test_the_cached_size_is_rated_for_the_memory_asked_about():
    hub = Hub(files(("model.safetensors", 4_000_000_000)))
    checker = hc.SizeChecker(fit, repo_info=hub)
    assert checker.check(REPO, 32 * GB)["fit"] == "great"
    assert checker.check(REPO, 4 * GB)["fit"] == "too-large"
    assert len(hub.calls) == 1


@pytest.mark.parametrize("repo", ["other/Model", "mlx-community", "mlx-community/../x", "", None])
def test_size_is_refused_for_a_bad_id_or_another_author(repo):
    hub = Hub()
    assert hc.SizeChecker(fit, repo_info=hub).check(repo, GB) is None
    assert hub.calls == []


def test_the_cache_is_bounded():
    hub = Hub(*(files(("model.safetensors", 1)) for _ in range(4)))
    checker = hc.SizeChecker(fit, repo_info=hub, max_cached=2)
    for name in ("a", "b", "c", "a"):
        checker.check(f"mlx-community/{name}", GB)
    assert len(hub.calls) == 4


def test_at_most_four_calls_run_at_once():
    lock, release, full = threading.Lock(), threading.Event(), threading.Event()
    running, peak = [0], [0]

    def repo_info(**kwargs):
        with lock:
            running[0] += 1
            peak[0] = max(peak[0], running[0])
            if running[0] == 4:
                full.set()
        release.wait(5)
        with lock:
            running[0] -= 1
        return files(("model.safetensors", 1))

    checker = hc.SizeChecker(fit, repo_info=repo_info)
    threads = [
        threading.Thread(target=checker.check, args=(f"mlx-community/m{i}", GB)) for i in range(9)
    ]
    for thread in threads:
        thread.start()
    assert full.wait(5)
    release.wait(0.1)
    release.set()
    for thread in threads:
        thread.join(5)
    assert peak[0] == 4


def test_a_tiny_published_count_is_not_called_unpublished():
    assert card(safetensors={"parameters": {"BF16": 40_000}, "total": 40_000})["params"] == "<1M"


def test_a_cached_size_does_not_wait_for_a_free_slot():
    checker = hc.SizeChecker(fit, repo_info=Hub(files(("model.safetensors", 1))))
    checker.check(REPO, GB)
    for _ in range(hc.SIZE_CALLS):
        checker._slots.acquire()
    done = threading.Event()
    threading.Thread(target=lambda: (checker.check(REPO, GB), done.set()), daemon=True).start()
    answered = done.wait(2)
    for _ in range(hc.SIZE_CALLS):
        checker._slots.release()
    assert answered


# ---- row 1094, finding F4: anonymous and time limited ------------------------------
@pytest.mark.parametrize("call", ["_list_models", "_repo_info"])
def test_hub_calls_never_send_a_saved_token(monkeypatch, call):
    made = []

    class HfApi:
        def __init__(self, **kwargs):
            made.append(kwargs)

        def list_models(self, **kwargs):
            return []

        def repo_info(self, **kwargs):
            return None

    monkeypatch.setitem(sys.modules, "huggingface_hub", SimpleNamespace(HfApi=HfApi))
    getattr(hc, call)()
    assert made == [{"token": False}]


def test_a_search_that_hangs_is_given_up_and_still_shows_the_picks(monkeypatch):
    monkeypatch.setattr(hc, "SEARCH_TIMEOUT_SECONDS", 0.05, raising=False)
    release = threading.Event()

    def list_models(**kwargs):
        release.wait(3)
        return iter(())

    try:
        out = hc.search("", catalog=CATALOG, ram_bytes=32 * GB, fit=fit, list_models=list_models)
    finally:
        release.set()
    assert out["error"] == hc.SEARCH_FAILED
    assert len(out["results"]) == len(CATALOG)
