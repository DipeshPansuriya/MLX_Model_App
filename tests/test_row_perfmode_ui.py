"""Performance mode on Configuration and Chat, and the reply stats. ui/app.js under node."""

from __future__ import annotations

import json
import re

import pytest
from test_row837_ui import ui as chat_ui
from test_row848_ui import CONFIG, PAGE, SCRIPT, ui
from test_ui_behaviour import needs_node

KEYS = ("performance_mode", "kv_bits", "kv_group_size", "prefill_step_size")
SENTENCE = (
    "Save memory uses less memory but reads new prompts about half as fast "
    "(measured on one model and one Mac)."
)


class TestMarkup:
    def test_one_plain_choice_with_its_sentence(self):
        assert 'label for="cfg-performance_mode">Performance mode<' in PAGE
        for value, words in (
            ("standard", "Standard (recommended)"),
            ("save_memory", "Save memory"),
            ("advanced", "Advanced"),
        ):
            assert re.search(f'value="{value}"[^>]*>{re.escape(words)}</option>', PAGE)
        assert SENTENCE in PAGE
        assert 'id="cfg-advanced" hidden' in PAGE

    def test_four_bit_warns(self):
        warn = PAGE[PAGE.index('id="cfg-kv4-warn"') :][:300]
        assert "broken answers in testing" in warn

    def test_chat_header_links_to_configuration(self):
        chat = PAGE[PAGE.index('id="view-chat"') : PAGE.index('id="view-models"')]
        assert 'href="#config">Change in Configuration</a>' in chat
        assert "Stop then Start, or Apply" in chat
        mode = chat[chat.index('id="chatMode"') - 40 : chat.index('id="chatGate"')]
        assert "aria-live" not in mode and 'role="status"' not in mode
        # The finished line is outside every live region.
        assert 'id="messages"></div>' in chat


MODE = """
const choose = (value) => { el("#cfg-performance_mode").value = value;
  el("#cfg-performance_mode").listeners.change(); };
const state_ = () => ({ advanced: !el("#cfg-advanced").hidden,
  warn: !el("#cfg-kv4-warn").hidden });
"""


@needs_node
class TestChoice:
    def test_advanced_reveals_the_fields_and_four_bit_warns(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + MODE
            + """
            const seen = [state_()];
            choose("advanced"); seen.push(state_());
            el("#cfg-kv_bits").value = "4"; el("#cfg-kv_bits").listeners.change();
            seen.push(state_());
            choose("save_memory"); seen.push(state_());
            return seen;
            """,
        )
        assert out == [
            {"advanced": False, "warn": False},
            {"advanced": True, "warn": False},
            {"advanced": True, "warn": True},
            {"advanced": False, "warn": False},
        ]

    def test_save_sends_words_and_numbers(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + MODE
            + """
            choose("advanced");
            type("kv_bits", "8"); type("kv_group_size", "32"); type("prefill_step_size", "512");
            await saveConfig();
            return sent.filter((s) => s[0] === "/api/server/settings");
            """,
        )
        assert out[0][1]["values"] == {
            "performance_mode": "advanced",
            "kv_bits": 8,
            "kv_group_size": 32,
            "prefill_step_size": 512,
        }

    def test_a_bad_read_step_is_not_sent(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + MODE
            + """
            choose("advanced"); type("prefill_step_size", "9000");
            await saveConfig();
            return { posted: posted(), said: el("#cfg-prefill_step_size-error").textContent };
            """,
        )
        assert out["posted"] == []
        assert "64 to 2048" in out["said"]

    def test_a_model_that_cannot_is_disabled_with_the_reason(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/config"] = (repo) => config(repo,
              { performance_mode: { value: "save_memory", own: null, inherited: "save_memory" } },
              { performance: { mode: "save_memory", reason: "it keeps a sliding window",
                blocked: { save_memory: "it keeps a sliding window",
                  kv_group_size: { 32: "x", 64: "x", 128: "x" } } } });
            routes["/api/loadtest"] = () => view();
            show(); state.config.repo = "org/custom"; showView("config"); await tick();
            return { save: el("#cfg-mode-save_memory").disabled,
              bits: el("#cfg-kv_bits").disabled,
              why: el("#cfg-performance_mode-blocked").textContent,
              first: el("#cfg-mode-default").textContent };
            """,
        )
        assert out["save"] is True and out["bits"] is True
        assert "it keeps a sliding window" in out["why"]
        assert "Standard" in out["why"]
        assert out["first"] == "Same as All models (Save memory)"


CHAT = """
const mode = (over) => srv({ running: true, ready: true, repos: ["org/one"],
  settings_pending: false, performance: { saved: "Standard",
    models: { "org/one": { saved: "Save memory", in_force: "Save memory", note: null } } },
  ...over });
const read = () => ({ now: el("#chatModeName").textContent,
  later: el("#chatModeNote").textContent });
"""


@needs_node
class TestChat:
    def test_the_header_names_the_mode_in_force(self, tmp_path):
        out = ui(
            tmp_path,
            CHAT
            + """
            show({ server: mode() }); const on = read();
            show({ server: mode({ settings_pending: true,
              performance: { saved: "Standard", models: { "org/one":
                { saved: "Standard", in_force: "Save memory", note: null } } } }) });
            const behind = read();
            show({ server: mode({ performance: { saved: "Standard", models: { "org/one":
              { saved: "Standard", in_force: null, note: null } } } }) });
            const unknown = read();
            show({ server: srv({ performance: { saved: "Save memory", models: {} } }) });
            return { on, behind, unknown, off: read() };
            """,
        )
        assert out["on"] == {"now": "Save memory", "later": ""}
        assert out["behind"]["now"] == "Save memory"
        assert "Standard is saved" in out["behind"]["later"]
        assert out["unknown"]["now"] == "not reported yet"
        assert out["off"]["now"] == "Save memory (used at the next Start)"


@needs_node
class TestStatsLine:
    @pytest.mark.parametrize(
        ("stats", "line"),
        [
            (
                {
                    "first_s": 0.4,
                    "tps": None,
                    "prompt_tokens": None,
                    "reply_tokens": None,
                    "mode": None,
                },
                "First answer after 0.4 s",
            ),
            (
                {
                    "first_s": None,
                    "tps": 20.5,
                    "prompt_tokens": 9,
                    "reply_tokens": 4,
                    "mode": "Standard",
                },
                "4 tokens · Writing 20.5 tokens/s · Prompt 9 tokens · Standard mode",
            ),
        ],
    )
    def test_unknown_figures_are_left_out(self, tmp_path, stats, line):
        done = json.dumps(["done", {"tokens": 7, "tps": 0, "stats": stats}])
        out = chat_ui(tmp_path, f'return await say([["token", "ok"], {done}]);')
        assert out["meta"][-1] == line

    def test_no_stats_at_all_adds_no_empty_line(self, tmp_path):
        out = chat_ui(tmp_path, 'return await say([["token", "ok"], ["done", { stopped: true }]]);')
        assert out["meta"][-1] == "stopped"

    def test_the_line_is_not_sent_back_to_the_model(self, tmp_path):
        out = chat_ui(
            tmp_path,
            """
            await say([["token", "ok"], DONE]);
            respond = sse([["token", "two"], DONE]);
            el("#input").value = "again"; await sendMessage();
            const asked = calls.filter((c) => c.path === "/api/chat").pop();
            return JSON.parse(asked.opts.body).messages;
            """,
        )
        assert [sorted(m) for m in out] == [["content", "role"]] * 3
        assert "tokens" not in json.dumps(out)

    def test_it_is_written_once_the_reply_is_over(self):
        reply = SCRIPT[SCRIPT.index("async function sendMessage") :]
        reply = reply[: reply.index("/* ─")]
        assert reply.count("statsLine(") == 1
        assert reply.index('ev === "done"') < reply.index("statsLine(")
