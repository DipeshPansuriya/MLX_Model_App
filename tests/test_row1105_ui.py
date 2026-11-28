"""Row 1105: ui/features.js under node, loaded before app.js as the page loads it."""

from __future__ import annotations

import json
import re
import shutil
import subprocess

from test_ui_behaviour import PRELUDE, ROOT, needs_node

SCRIPT = ROOT / "ui" / "features.js"
FRAGMENTS = ROOT / "docs" / "wiring" / "row1105-fragments.html"
HELPERS = """
const el = (s) => document.querySelector(s);
const text = (s) => el(s).textContent;
const tick = () => new Promise((r) => setTimeout(r, 0));
const memory = { mac_total_bytes: 64 * 2 ** 30, mac_used_percent: 41.5, pressure: "warning",
  server_bytes: 3 * 2 ** 30, server_peak_bytes: 4 * 2 ** 30 };
const speed = { chat: { write_tps: 33.25, first_token_s: 0.5, reply_tokens: 20, mode: "fast",
    at: "10:00" },
  test: { time: "yesterday", stale: true,
    rows: [{ size: 4096, read_tps: 100, write_tps: 20, first_token_s: null }] } };
const side = (over = {}) => ({ repo: "org/m", comparable: true, reasons: [],
  runs: { mlx: { status: "done", time: "t1" }, optiq: { status: "done", time: "t2" } },
  rows: [{ size: 4096, better: { write_tps: "optiq" },
    mlx: { status: "passed", write_tps: 20 }, optiq: { status: "passed", write_tps: 25 } }],
  ...over });
const plan = (over = {}) => ({ needed: true, blocked: null, steps: [], items: [
  { id: "mlx", kind: "package", current: "0.1", target: "0.2", action: "update" },
  { id: "optiq", kind: "engine", target: "9", action: "install" }], ...over });
const booted = calls.length;
const sent = () => calls.slice(booted).map((c) => `${c.opts?.method || "GET"} ${c.path}`);
"""


def ui(tmp_path, scenario):
    page = tmp_path / "page.js"
    names = ("download.js", "features.js", "app.js")  # the page's load order
    page.write_text(
        "\n".join((ROOT / "ui" / n).read_text(encoding="utf-8") for n in names),
        encoding="utf-8",
    )
    path = tmp_path / "scenario.js"
    path.write_text(PRELUDE + HELPERS + scenario, encoding="utf-8")
    done = subprocess.run(
        [shutil.which("node"), str(ROOT / "tests" / "ui_harness.js"), str(page), str(path)],
        capture_output=True,
        check=False,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


def test_every_element_the_script_uses_is_in_the_fragments():
    used = set(re.findall(r'\$\("#([A-Za-z]+)"\)', SCRIPT.read_text(encoding="utf-8")))
    present = set(re.findall(r'\sid="([A-Za-z]+)"', FRAGMENTS.read_text(encoding="utf-8")))
    assert used and used <= present, used - present


def test_the_fragments_and_styles_hold_no_inline_script_or_style():
    markup = FRAGMENTS.read_text(encoding="utf-8")
    assert not re.search(r"<script(?![^>]*\ssrc=)|\sstyle=|\son[a-z]+=", markup)
    assert (ROOT / "ui" / "features.css").read_text(encoding="utf-8").strip()


@needs_node
class TestLiveStats:
    def test_memory_and_both_speeds_are_shown_apart(self, tmp_path):
        out = ui(
            tmp_path,
            """
            renderLiveStats({ memory, speed });
            return { hidden: el("#livePanel").hidden, mac: text("#liveMacMem"),
              server: text("#liveServerMem"), pressure: text("#livePressure"),
              chat: text("#liveChat"), test: text("#liveTest") };
            """,
        )
        assert out["hidden"] is False
        assert out["mac"] == "41.5% of 64.0 GB used"
        assert out["server"] == "3.0 GB (peak 4.0 GB)"
        assert out["pressure"] == "Pressure: warning"
        assert "33.3 tok/s" in out["chat"] and "fast" in out["chat"]
        assert "4096 tokens" in out["test"] and "read 100.0 tok/s" in out["test"]
        assert "first token unknown" in out["test"] and "older settings" in out["test"]

    def test_missing_figures_say_unknown_never_zero(self, tmp_path):
        out = ui(
            tmp_path,
            """
            renderLiveStats({ memory: {}, speed: { chat: null, test: null } });
            return [text("#liveMacMem"), text("#liveServerMem"), text("#livePressure"),
              text("#liveChat"), text("#liveTest")];
            """,
        )
        assert out[:3] == ["unknown", "unknown", "Pressure: unknown"]
        assert out[3] == "No reply measured for this server yet"
        assert out[4] == "No load test measured for this model and engine"

    def test_the_panel_stays_hidden_without_figures(self, tmp_path):
        assert ui(tmp_path, 'renderLiveStats({}); return el("#livePanel").hidden;') is True


@needs_node
class TestCompare:
    def test_both_engines_by_size_with_the_winner_in_words(self, tmp_path):
        out = ui(
            tmp_path,
            """
            renderCompare(side());
            return { hidden: el("#ltComparePanel").hidden, badge: text("#ltCompareBadge"),
              runs: text("#ltCompareRuns"), table: html("#ltCompareTables") };
            """,
        )
        assert out["hidden"] is False and out["badge"] == "Same settings"
        assert out["runs"] == "MLX: done, t1. OptiQ: done, t2."
        assert "4096 tokens" in out["table"]
        assert "25.0 (better)" in out["table"] and "20.0 (better)" not in out["table"]

    def test_not_comparable_shows_the_reasons(self, tmp_path):
        out = ui(
            tmp_path,
            """
            renderCompare(side({ comparable: false, reasons: ["Different: thinking", "x"],
              runs: { mlx: { status: "cancelled" }, optiq: null }, rows: [] }));
            return [text("#ltCompareBadge"), text("#ltCompareReasons"), text("#ltCompareRuns")];
            """,
        )
        assert out == [
            "Not comparable",
            "Different: thinking. x.",
            "MLX: cancelled. OptiQ: no finished run.",
        ]

    def test_hidden_when_there_is_no_run(self, tmp_path):
        out = ui(
            tmp_path,
            """
            renderCompare(side()); renderCompare(side({ runs: { mlx: null, optiq: null } }));
            const none = el("#ltComparePanel").hidden;
            renderCompare(side()); renderCompare(undefined);
            return [none, el("#ltComparePanel").hidden];
            """,
        )
        assert out == [True, True]

    def test_stored_text_is_escaped(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const bad = '<img src=x>"';
            renderCompare(side({ rows: [{ size: bad, better: {},
              mlx: { status: bad }, optiq: null }] }));
            return html("#ltCompareTables");
            """,
        )
        assert "<img" not in out and "&lt;img" in out


@needs_node
class TestInstallPlan:
    def test_items_and_unsupported_reasons_are_listed(self, tmp_path):
        out = ui(
            tmp_path,
            """
            paintPlan(plan({ items: [...plan().items, { id: "x<b>", kind: "engine",
              target: "1", action: "unsupported", reason: "Needs <more>" }] }));
            return { list: html("#planItems"), off: el("#planBtn").disabled };
            """,
        )
        assert "mlx: update 0.1 to 0.2" in out["list"] and "optiq: install 9" in out["list"]
        assert "Needs &lt;more&gt;" in out["list"] and "<b>" not in out["list"]
        assert out["off"] is False

    def test_blocked_or_nothing_to_do_disables_the_button(self, tmp_path):
        out = ui(
            tmp_path,
            """
            paintPlan(plan({ blocked: "Stop the API server first." }));
            const blocked = [el("#planBtn").disabled, text("#planWhy")];
            paintPlan(plan({ needed: false }));
            return [blocked, [el("#planBtn").disabled, text("#planWhy")]];
            """,
        )
        assert out == [
            [True, "Stop the API server first."],
            [True, "Nothing to install or update."],
        ]

    def test_the_plan_is_asked_for_once_per_change_and_only_on_setup(self, tmp_path):
        out = ui(
            tmp_path,
            """
            respond = () => reply(plan());
            state.view = "server"; renderFeatures(snap()); const away = sent().length;
            state.view = "settings"; renderFeatures(snap()); renderFeatures(snap());
            const once = sent().filter((s) => s.includes("/api/install/plan")).length;
            renderFeatures(snap({ install: { running: true, lines: [] } })); await tick();
            return { away, once, all: sent().filter((s) => s.includes("plan")).length,
              off: el("#planBtn").disabled };
            """,
        )
        assert out == {"away": 0, "once": 1, "all": 2, "off": True}

    def test_confirm_names_the_changes_and_cancel_sends_nothing(self, tmp_path):
        out = ui(
            tmp_path,
            """
            let asked = ""; confirm = (m) => { asked = m; return false; };
            wireFeatures(); paintPlan(plan()); await el("#planBtn").onclick();
            return { asked, sent: sent() };
            """,
        )
        assert "mlx: update" in out["asked"] and "optiq: install" in out["asked"]
        assert "download" in out["asked"] and out["sent"] == []

    def test_confirmed_install_posts_once(self, tmp_path):
        out = ui(
            tmp_path,
            """
            respond = (path) => reply(path === "/api/state" ? snap() : { ok: true });
            wireFeatures(); paintPlan(plan()); await el("#planBtn").onclick();
            return sent().filter((s) => s.startsWith("POST"));
            """,
        )
        assert out == ["POST /api/install/all"]

    def test_a_refusal_is_shown(self, tmp_path):
        out = ui(
            tmp_path,
            """
            respond = () => reply({ error: "Stop the API server first." }, false);
            wireFeatures(); paintPlan(plan()); await el("#planBtn").onclick(); await tick();
            return text("#planWhy");
            """,
        )
        assert out == "Stop the API server first."


@needs_node
class TestReleaseCheck:
    def test_nothing_is_asked_until_the_button_is_pressed(self, tmp_path):
        out = ui(
            tmp_path,
            """
            respond = () => reply({});
            wireFeatures(); state.view = "settings"; renderFeatures(snap());
            return sent().filter((s) => s.includes("/api/update/check")).length;
            """,
        )
        assert out == 0

    def test_a_newer_release_offers_its_page(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const url = "https://github.com/o/r/releases/tag/v9.0.0";
            respond = () => reply({ status: "update", message: "9.0.0 <is> out", url });
            wireFeatures(); await el("#ftUpdateBtn").onclick();
            el("#ftUpdateOpen").onclick();
            return { sent: sent(), msg: text("#ftUpdateMsg"), hidden: el("#ftUpdateOpen").hidden,
              opened, busy: el("#ftUpdateBtn").disabled };
            """,
        )
        assert out["sent"] == ["POST /api/update/check"]
        assert out["msg"] == "9.0.0 <is> out" and out["hidden"] is False and out["busy"] is False
        assert out["opened"] == ["https://github.com/o/r/releases/tag/v9.0.0"]

    def test_no_other_address_is_ever_opened(self, tmp_path):
        out = ui(
            tmp_path,
            """
            respond = () => reply({ status: "update", message: "m", url: "https://evil.test/x" });
            wireFeatures(); await el("#ftUpdateBtn").onclick(); el("#ftUpdateOpen").onclick();
            return [el("#ftUpdateOpen").hidden, opened];
            """,
        )
        assert out == [True, []]

    def test_a_failure_is_shown_and_the_button_works_again(self, tmp_path):
        out = ui(
            tmp_path,
            """
            respond = () => reply({ error: "refused" }, false);
            wireFeatures(); await el("#ftUpdateBtn").onclick();
            return [text("#ftUpdateMsg"), el("#ftUpdateBtn").disabled];
            """,
        )
        assert out == ["refused", False]
