"""Rows 884 to 886: QA findings F1 to F3, thinking and context sizes (fake DOM)."""

from pathlib import Path

from test_row874_ui import SERVER
from test_ui_behaviour import needs_node, run

UI = Path(__file__).resolve().parent.parent / "ui"


def test_a_hidden_switch_stays_hidden():
    assert ".check[hidden] { display: none; }" in (UI / "style.css").read_text("utf-8")


CLIENT = """
const text = (id) => document.querySelector(id).textContent;
const seen = () => [text("#openaiSettings"), text("#claudeSettings"),
  document.querySelector("#copyKey").hidden, document.querySelector("#copyClaude").hidden,
  document.querySelector("#curlExample").hidden];
"""


@needs_node
class TestClientSettingsFollowTheRunningEngine:
    def test_optiq_running_while_mlx_is_picked(self, tmp_path):
        openai, claude, no_key, no_claude, no_curl = run(
            tmp_path,
            SERVER
            + CLIENT
            + """
            const live = { ...server, running: true, repos: ["org/a"], repo: "org/a",
              engine: "optiq", anthropic: true, ready: true };
            render(snap({ server: live }));
            state.engine = "mlx";
            state.anthropic = false;
            render(snap({ server: live }));
            return seen();
            """,
        )
        assert "press Copy key" in openai and "default_model" in openai
        assert "ANTHROPIC_BASE_URL=http://127.0.0.1:8080\n" in claude
        assert (no_key, no_claude, no_curl) == (False, False, True)

    def test_mlx_running_while_optiq_is_picked(self, tmp_path):
        openai, claude, no_key, no_claude, no_curl = run(
            tmp_path,
            SERVER
            + CLIENT
            + """
            const live = { ...server, running: true, repos: ["org/run"], repo: "org/run",
              engine: "mlx", ready: true };
            render(snap({ server: live }));
            state.engine = "optiq";
            render(snap({ server: live }));
            return seen();
            """,
        )
        assert "Model: org/run" in openai and "API key: not needed" in openai
        assert "no Anthropic route" in claude
        assert (no_key, no_claude, no_curl) == (True, True, False)


@needs_node
class TestConfigurationNamesWhatOptiqChanges:
    def test_note_shows_for_optiq_picked_or_running_only(self, tmp_path):
        out = run(
            tmp_path,
            SERVER
            + """
            const note = () => document.querySelector("#configEngine").hidden;
            render(snap({ server }));
            const seen = [note()];
            state.engine = "optiq";
            render(snap({ server }));
            seen.push(note());
            state.engine = "mlx";
            render(snap({ server: { ...server, running: true, repos: ["org/a"],
              engine: "optiq", anthropic: true, ready: true } }));
            seen.push(note());
            return seen;
            """,
        )
        assert out == [True, False, False]

    def test_note_says_what_is_ignored_and_what_differs(self):
        page = (UI / "index.html").read_text("utf-8")
        note = " ".join(page.split('id="configEngine"')[1].split("</ul>")[0].split())
        for words in (
            "Ignored: MLX buffer cache limit and Two-model context cap",
            "older text is dropped, not refused",
            "Server max reply tokens: only a default",
            "Memory limit: only sampled",
            "Prompt cache limit: empty means one eighth",
            "measured with MLX only",
        ):
            assert words in note


THINK = """
const view = (thinking) => ({ blocked: null, thinking, sizes: { min: 4096, max: 9000,
  offered: [] }, load_test: { sizes: { value: [] }, last_sizes: { value: [] } }, results: [] });
const box = () => document.querySelector("#ltThinking");
const note = () => document.querySelector("#ltThinkingNote").textContent;
render(snap({ server }));
state.readyModels = ["org/a"];
state.lt.repo = "org/a";
const seen = () => [box().disabled, box().checked, note(), ltRequest("org/a", [4096])];
"""


@needs_node
def test_thinking_is_offered_only_with_a_switch(tmp_path):
    on, off, none = run(
        tmp_path,
        SERVER
        + THINK
        + """
        state.lt.view = view({ supported: true, reason: null });
        paintThinking(false);
        const first = seen();
        state.lt.thinking = false;
        paintThinking(false);
        const second = seen();
        state.lt.view = view({ supported: false, reason: "No switch." });
        paintThinking(false);
        return [first, second, seen()];
        """,
    )
    assert on[:2] == [False, True] and on[3]["thinking"] is True
    assert "reply token budget" in on[2]
    assert off[:2] == [False, False] and off[3]["thinking"] is False
    assert none[:3] == [True, False, "No switch."] and "thinking" not in none[3]


def test_the_page_says_large_sizes_take_long():
    page = (UI / "index.html").read_text("utf-8")
    assert "Large sizes take long" in page and "3600 seconds" in page
