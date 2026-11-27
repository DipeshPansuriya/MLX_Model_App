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
