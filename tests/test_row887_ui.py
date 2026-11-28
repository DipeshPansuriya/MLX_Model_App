"""Row 887: the screens match the direct mlx_lm server (source checks and fake DOM)."""

import re
from pathlib import Path

from test_row874_ui import SERVER
from test_ui_behaviour import needs_node, run

UI = Path(__file__).resolve().parent.parent / "ui"
SCRIPT = (UI / "app.js").read_text("utf-8")
PAGE = (UI / "index.html").read_text("utf-8")


def test_nothing_removed_is_called_or_shown():
    for gone in (
        "/api/server/estimate",
        "/api/loadtest/apply",
        "margin_percent",
        "limit_percent",
        "mlx_cache_limit_mb",
        "two_model_context_tokens",
        "memory_limit_percent",
        "serverUnguarded",
        "serverMemory",
        "prompt_limits",
        "also:",
    ):
        assert gone not in SCRIPT + PAGE, gone
    for claim in ("Max 2", "max 2", "projected to", "request guard", "two models"):
        assert claim not in PAGE, claim


def test_the_configuration_page_has_exactly_the_backend_settings(app_module):
    fields = set(re.findall(r'(?:input|select)\b[^>]*\bid="cfg-([a-z_]+)"', PAGE))
    listed = re.search(r"const CONFIG_KEYS = \[(.*?)\];", SCRIPT, re.S).group(1)
    assert fields == set(re.findall(r'"([a-z_]+)"', listed)) == set(app_module.SETTINGS)


def test_optiq_only_and_default_only_settings_are_marked():
    assert "Max context tokens (OptiQ only)" in PAGE
    assert "MLX has no memory limit" in PAGE


def test_the_load_test_page_names_the_stops_that_remain():
    page = " ".join(PAGE.split())
    for stop in (
        "critical memory pressure",
        "swap grows by more than 0.5 GB",
        "memory cannot be read",
        "not sampled for more than 1 second",
    ):
        assert stop in page, stop


@needs_node
class TestOneModel:
    def test_mlx_client_settings_say_default_model(self, tmp_path):
        openai, curl = run(
            tmp_path,
            SERVER
            + """
            const live = { ...server, running: true, repos: ["org/run"], repo: "org/run",
              ready: true };
            render(snap({ server: live }));
            return [document.querySelector("#openaiSettings").textContent,
              document.querySelector("#curlExample").textContent];
            """,
        )
        assert "Model: default_model" in openai
        assert '"model":"default_model"' in curl

    def test_a_remembered_pair_becomes_one_model(self, tmp_path):
        out = run(
            tmp_path,
            SERVER
            + """
            render(snap({ server: { ...server, last_models: ["org/a", "org/b"] },
              models: [{ repo: "org/a" }, { repo: "org/b" }] }));
            return [serverSet(), serverChoice(), document.querySelector("#serverAddBtn").disabled,
              document.querySelector("#serverStartBtn").disabled];
            """,
        )
        assert out == [["org/a"], {"repo": "org/a"}, True, False]
