"""The Configuration and Load Test pages (row 848). ui/app.js under node."""

from __future__ import annotations

import re

from test_ui_behaviour import ROOT, needs_node, run

PAGE = (ROOT / "ui" / "index.html").read_text("utf-8")
SCRIPT = (ROOT / "ui" / "app.js").read_text("utf-8")
ORDER = ["server", "chat", "models", "download", "config", "loadtest", "logs", "settings"]

# A fake backend: GET answers come from ``routes``, every POST is kept in ``sent``.
HELPERS = """
const el = (s) => document.querySelector(s);
const sent = [];
const asked = [];
const AUTO = { max_context_tokens: [4096, 100000000], server_max_reply_tokens: [64, 131072],
  chat_max_reply_tokens: [64, 131072], chat_temperature: [0, 2], chat_top_p: [0.05, 1],
  prompt_cache_size: [1, 100], prompt_cache_bytes: [100000000, 100000000000],
  performance_mode: ["standard", "advanced"], kv_bits: [4, 8], kv_group_size: [32, 128],
  prefill_step_size: [64, 2048] };
const config = (repo, over = {}, extra = {}) => ({
  repo: repo || null, applied_context: null, context_message: null, load_test: {},
  settings: Object.fromEntries(Object.entries(AUTO).map(([key, [min, max]]) => [key, {
    value: null, own: null, inherited: null, effective: "automatic (calculated)", min, max,
    decimal: key === "chat_temperature" || key === "chat_top_p",
    ...(over[key] || {}) }])),
  ...extra });
const NUMBERS = { reply_tokens: { value: 256, default: 256, min: 32, max: 1024 },
  rounds: { value: 2, default: 2, min: 1, max: 3 },
  timeout_seconds: { value: 900, default: 900, min: 60, max: 3600 } };
const view = (over = {}) => ({ repo: "org/custom", status: null, rows: [], results: [],
  blocked: null, load_test: { ...NUMBERS, sizes: { value: [4096, 8192] },
    last_sizes: { value: [] } },
  sizes: { min: 4096, max: 16384, offered: [{ size: 4096, allowed: true },
    { size: 8192, allowed: true }, { size: 32768, allowed: false }] }, ...over });
const row = (over = {}) => ({ size: 4096, status: "passed", reason: "", steps_done: 4,
  prompt_tokens: 4030, read_tps: 512.34, write_tps: 21.05, first_token_s: 7.86,
  follow_up_first_token_s: 0.41, peak_percent: 61.24, server_peak_bytes: 9400000000,
  ...over });
const runRecord = (over = {}) => ({ id: "run1", repo: "org/custom",
  time: "2026-11-27 10:00:00", status: "done", reason: "", sizes: [4096], rows: [row()],
  stale: false, flags: ["--max-tokens", "8192"],
  values: { reply_tokens: 256, rounds: 2, timeout_seconds: 900 },
  ...over });
let routes = {};
let refuse = {};
globalThis.fetch = (url, options = {}) => {
  const [path, query] = url.split("?");
  const repo = new URLSearchParamsLite(query).repo;
  if (options.method === "POST") {
    sent.push([path, JSON.parse(options.body)]);
    if (refuse[path]) {
      return Promise.resolve({ ok: false, statusText: "no",
        json: async () => ({ error: refuse[path] }) });
    }
    return reply({ ok: true });
  }
  asked.push(url);
  if (path === "/api/state") return reply(state.snapshot || snap());
  if (routes[path]) return reply(routes[path](repo));
  return new Promise(() => {});
};
function URLSearchParamsLite(query) {
  const pairs = (query || "").split("&").filter(Boolean).map((p) => p.split("="));
  return Object.fromEntries(pairs.map(([k, v]) => [k, decodeURIComponent(v)]));
}
const tick = () => new Promise((r) => setTimeout(r, 5));
const srv = (over = {}) => ({ running: false, starting: false, endpoint: "e", lines: [],
  tool_support: {}, ...over });
const posted = () => sent.map((s) => s[0]);
const show = (over = {}) => { const s = snap(over); state.snapshot = s; render(s); };
const type = (key, value) => { el(`#cfg-${key}`).value = value; };
const progress = (over = {}) => ({ active: true, repo: "org/custom", run_id: "run2",
  status: "running", reason: "", size: 8192, index: 2, of: 2, state: "running",
  step: "cold 1 of 2", done: 100, total: 8000, memory_percent: 55.5, ...over });
wire();
"""


def ui(tmp_path, scenario):
    return run(tmp_path, HELPERS + scenario)


CONFIG = """
routes["/api/config"] = (repo) => config(repo, repo ? {
  prompt_cache_size: { value: 3, own: null, inherited: 3, effective: "3" },
  chat_max_reply_tokens: { value: 500, own: 500, inherited: null, effective: "500" },
} : { prompt_cache_size: { value: 3, own: 3, effective: "3" } });
routes["/api/loadtest"] = () => view();
show(); showView("config"); await tick();
"""
LOAD = """
routes["/api/loadtest"] = (repo) => view({ repo });
show(); showView("loadtest"); await tick();
"""
PICK = """
const pick = (size, on = true) => el("#ltSizes").listeners.change(
  { target: { value: String(size), checked: on, dataset: { size: String(size) } } });
"""


class TestPage:
    def test_menu_order_and_sections(self):
        assert re.findall(r'<button class="nav-item[^"]*" data-view="(\w+)"', PAGE) == ORDER
        for name in ("config", "loadtest"):
            assert f'<section class="view" id="view-{name}"' in PAGE

    def test_the_settings_box_became_a_link(self):
        assert 'id="serverSettings"' not in PAGE and 'id="set-' not in PAGE
        card = PAGE.split('id="view-server"')[1].split("</section>")[0]
        assert 'href="#config"' in card

    def test_every_setting_has_a_labelled_field(self, app_module):
        for key in app_module.SETTINGS:
            assert f'for="cfg-{key}"' in PAGE
            assert f'id="cfg-{key}"' in PAGE
            scope = f"( cfg-{key}-(scope|help|blocked))*"  # the other hints
            first = f"(cfg-{key}-scope )?cfg-{key}-now cfg-{key}-error"
            assert re.search(f'aria-describedby="{first}{scope}"', PAGE), key
        for key in app_module.LOAD_TEST_NUMBERS:
            assert f'for="lt-{key}"' in PAGE and f'id="lt-{key}"' in PAGE

    def test_live_regions(self):
        assert re.search(r'id="ltAnnounce"[^>]*role="status"', PAGE)
        assert re.search(r'id="configError"[^>]*role="alert"', PAGE)
        assert re.search(r'id="ltError"[^>]*role="alert"', PAGE)
        # The part that changes with every poll is not read out.
        for name in ("ltStep", "ltDetail", "ltProgress"):
            tag = re.search(rf'<\w+[^>]*id="{name}"[^>]*>', PAGE).group(0)
            assert "role=" not in tag and "aria-live" not in tag

    def test_no_context_size_is_written_into_the_new_pages(self):
        start = SCRIPT.index("configuration page")
        end = SCRIPT.index("\n}\n", SCRIPT.index("async function cancelLoadTest"))
        for number in ("4096", "8192", "16384", "24576", "32768"):
            assert number not in SCRIPT[start:end]


@needs_node
class TestConfiguration:
    def test_all_models_is_first_and_installed_models_follow(self, tmp_path):
        out = ui(tmp_path, CONFIG + 'return html("#configModelSelect");')
        assert out.index("All models (defaults)") < out.index("org/custom")
        assert "org/other" in out

    def test_picking_in_the_search_list_switches_the_model(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            const choose = async (value) => { el("#configModelSelect").value = value;
              el("#configModelSelect").listeners.change({ target: el("#configModelSelect") });
              await tick(); return [state.config.repo,
                el("#configModelSelectChosen").textContent]; };
            return [await choose("org/custom"), await choose("All models (defaults)")];
            """,
        )
        assert out == [
            ["org/custom", "Selected: org/custom"],
            ["", "Selected: All models (defaults)"],
        ]

    def test_enter_in_a_field_saves(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            type("prompt_cache_size", "4"); let stopped = false;
            const key = (k) => el("#configFields").listeners.keydown(
              { key: k, preventDefault() { stopped = true; } });
            await key("Tab"); const before = sent.length;
            await key("Enter"); await tick();
            return [before, stopped, sent.length];
            """,
        )
        assert out == [0, True, 1]

    def test_global_values_and_automatic(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            return { asked: asked.filter((u) => u.startsWith("/api/config")),
              set: el("#cfg-prompt_cache_size").value,
              unset: el("#cfg-max_context_tokens").value,
              hint: el("#cfg-max_context_tokens-now").textContent,
              range: [el("#cfg-chat_top_p").min, el("#cfg-chat_top_p").max],
              step: [el("#cfg-chat_top_p").step, el("#cfg-prompt_cache_size").step] };
            """,
        )
        assert out["asked"] == ["/api/config"]
        assert (out["set"], out["unset"]) == ("3", "")
        assert out["hint"] == "Effective: automatic (calculated)"
        assert out["range"] == [0.05, 1] and out["step"] == ["any", "1"]

    def test_a_model_shows_own_and_inherited(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            await chooseConfigModel("org/custom"); await tick();
            const f = (k) => [el(`#cfg-${k}`).value, el(`#cfg-${k}-now`).textContent,
              el(`#cfg-${k}`).placeholder];
            return { url: asked.filter((u) => u.startsWith("/api/config?")),
              inherited: f("prompt_cache_size"), own: f("chat_max_reply_tokens") };
            """,
        )
        assert out["url"] == ["/api/config?repo=org%2Fcustom"]
        assert out["inherited"] == ["", "Effective: 3 (from All models)", "inherited: 3"]
        assert out["own"][:2] == ["500", "Effective: 500 (set for this model)"]

    def test_save_sends_only_what_changed(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            await saveConfig(); const nothing = sent.length;
            type("max_context_tokens", " 5000 "); type("chat_temperature", "0.7");
            type("prompt_cache_size", "");
            await saveConfig();
            await chooseConfigModel("org/custom"); await tick();
            type("chat_top_p", "0.9"); await saveConfig();
            return { nothing, sent };
            """,
        )
        assert out["nothing"] == 0
        assert out["sent"] == [
            [
                "/api/server/settings",
                {
                    "values": {
                        "max_context_tokens": 5000,
                        "chat_temperature": 0.7,
                        "prompt_cache_size": None,
                    }
                },
            ],
            ["/api/server/settings", {"values": {"chat_top_p": 0.9}, "repo": "org/custom"}],
        ]

    def test_invalid_fields_are_named_and_nothing_is_sent(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            type("max_context_tokens", "100"); type("prompt_cache_size", "2.5");
            type("chat_temperature", "abc");
            await saveConfig();
            const bad = (k) => [el(`#cfg-${k}`).getAttribute("aria-invalid"),
              el(`#cfg-${k}-error`).textContent];
            const first = { sent: sent.length, context: bad("max_context_tokens"),
              cache: bad("prompt_cache_size"), heat: bad("chat_temperature"),
              summary: el("#configError").textContent,
              focus: document.activeElement === el("#cfg-max_context_tokens") };
            type("max_context_tokens", ""); type("prompt_cache_size", "2");
            type("chat_temperature", "");
            await saveConfig();
            return { ...first, after: bad("prompt_cache_size"), sentAfter: sent.length };
            """,
        )
        assert out["sent"] == 0 and out["focus"] is True
        assert out["context"] == [
            "true",
            "Use a whole number from 4096 to 100000000, or leave it empty.",
        ]
        assert out["cache"][1] == "Use a whole number from 1 to 100, or leave it empty."
        assert out["heat"][1] == "Use a number from 0 to 2, or leave it empty."
        assert "3 fields" in out["summary"]
        assert out["after"] == [None, ""] and out["sentAfter"] == 1

    def test_the_server_refusal_is_shown(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            refuse["/api/server/settings"] = "too many model settings";
            type("prompt_cache_size", "9"); await saveConfig();
            return [el("#configError").textContent, el("#cfg-prompt_cache_size").value];
            """,
        )
        assert out == ["too many model settings", "9"]

    def test_a_load_error_goes_once_the_page_loads(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const real = globalThis.fetch;
            globalThis.fetch = () => reply({ error: "no answer" }, false);
            show(); showView("config"); await tick();
            showView("loadtest"); await tick();
            const shown = [el("#configError").textContent, el("#ltError").textContent];
            globalThis.fetch = real;
            routes["/api/config"] = () => config(""); routes["/api/loadtest"] = () => view();
            await loadConfig(); await loadLoadTest();
            return [shown, el("#configError").textContent, el("#ltError").textContent];
            """,
        )
        assert out == [["no answer", "no answer"], "", ""]

    def test_a_context_above_the_tested_sizes_is_warned(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/config"] = (repo) => config(repo, repo ? {
              max_context_tokens: { value: 90000, own: 90000, effective: "90000" } } : {});
            routes["/api/loadtest"] = () => view({ results: [runRecord({ rows: [
              row(), row({ size: 8192, status: "skipped" })] })] });
            show(); showView("config"); await tick();
            const none = el("#configTestWarn").textContent;
            await chooseConfigModel("org/custom"); await tick();
            return [none, el("#configTestWarn").textContent];
            """,
        )
        assert out[0] == ""
        assert "90000" in out[1] and "4096" in out[1] and "load test" in out[1]

    def test_reset_clears_this_entry_only(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            globalThis.confirm = () => false; await resetConfig(); const refused = sent.length;
            globalThis.confirm = () => true; await resetConfig();
            await chooseConfigModel("org/custom"); await tick(); await resetConfig();
            return [refused, sent.map((s) => s[1])];
            """,
        )
        assert out == [0, [{"reset": True}, {"reset": True, "repo": "org/custom"}]]

    def test_unsaved_edits_are_kept_until_the_user_agrees(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            type("prompt_cache_size", "7");
            showView("server"); showView("config"); await tick();
            show(); const back = el("#cfg-prompt_cache_size").value;
            globalThis.confirm = () => false;
            el("#configModelSelect").value = "org/custom";
            await chooseConfigModel("org/custom"); await tick();
            const kept = [el("#configModelSelect").value, el("#cfg-prompt_cache_size").value];
            globalThis.confirm = () => true;
            await chooseConfigModel("org/custom"); await tick();
            return [back, kept, el("#cfg-prompt_cache_size").value];
            """,
        )
        assert out == ["7", ["All models (defaults)", "7"], ""]

    def test_apply_changes_is_offered_while_the_server_is_behind(self, tmp_path):
        out = ui(
            tmp_path,
            CONFIG
            + """
            show({ server: srv() }); const off = el("#configPending").hidden;
            show({ server: srv({ running: true, repo: "org/custom", repos: ["org/custom"],
              settings_pending: true }) });
            const on = el("#configPending").hidden;
            await el("#configApplyBtn").listeners.click();
            return [off, on, posted()];
            """,
        )
        assert out == [True, False, ["/api/server/start"]]


@needs_node
class TestLoadTestControls:
    def test_boxes_come_from_the_settings_and_none_is_ticked(self, tmp_path):
        out = ui(tmp_path, LOAD + 'return [html("#ltSizes"), el("#ltStartBtn").disabled];')
        assert re.findall(r'data-size="(\d+)"', out[0]) == ["4096", "8192", "32768"]
        assert "checked" not in out[0]
        assert re.search(r'data-size="32768"[^>]*disabled', out[0])
        assert "16384" in out[0]  # why the box is off
        assert out[1] is False

    def test_start_needs_a_size(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD + 'await startLoadTest(); return [sent.length, el("#ltError").textContent];',
        )
        assert out[0] == 0 and "size" in out[1]

    def test_a_typed_size_is_added_and_sent_with_the_ticked_ones(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD
            + PICK
            + """
            el("#ltCustom").value = "12000"; addCustomSize();
            const boxes = html("#ltSizes"), cleared = el("#ltCustom").value;
            pick(8192); pick(4096); pick(4096, false);
            el("#ltCustom").value = "6000";  // typed, not added: it still counts
            el("#lt-reply_tokens").value = "128"; el("#lt-rounds").value = "";
            await startLoadTest();
            return { boxes, cleared, sent };
            """,
        )
        assert re.search(r'data-size="12000"[^>]*checked', out["boxes"])
        assert out["cleared"] == ""
        assert out["sent"] == [
            [
                "/api/server/settings",
                {
                    "load_test": {
                        "reply_tokens": 128,
                        "rounds": None,
                        "timeout_seconds": 900,
                        "last_sizes": [6000, 8192, 12000],
                    }
                },
            ],
            ["/api/loadtest/start", {"repo": "org/custom", "sizes": [6000, 8192, 12000]}],
        ]

    def test_bad_sizes_and_run_values_are_refused(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD
            + PICK
            + """
            const tried = [];
            for (const value of ["100", "20000", "5000.5", "x"]) {
              el("#ltCustom").value = value; addCustomSize();
              tried.push(el("#ltError").textContent);
            }
            const boxes = html("#ltSizes"); el("#ltCustom").value = "";
            pick(4096); el("#lt-rounds").value = "9"; await startLoadTest();
            const rounds = el("#ltError").textContent;
            el("#lt-rounds").value = "2";
            for (let n = 0; n < 13; n += 1) { el("#ltCustom").value = String(5000 + n);
              addCustomSize(); }
            await startLoadTest();
            return { tried, boxes, rounds, many: el("#ltError").textContent, sent: sent.length };
            """,
        )
        assert all("from 4096 to 16384" in text for text in out["tried"])
        assert "20000" not in out["boxes"]
        assert "from 1 to 3" in out["rounds"]
        assert "12 sizes" in out["many"]
        assert out["sent"] == 0

    def test_a_refused_start_is_explained(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD
            + PICK
            + """
            refuse["/api/loadtest/start"] = "the model is downloading"; pick(4096);
            await startLoadTest(); return el("#ltError").textContent;
            """,
        )
        assert out == "the model is downloading"

    def test_a_running_server_blocks_start_and_offers_stop(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD
            + """
            show({ server: srv({ running: true, repo: "org/custom" }) }); await tick();
            const on = [el("#ltStartBtn").disabled, el("#ltWhy").textContent,
              el("#ltStopServerBtn").hidden];
            await el("#ltStopServerBtn").listeners.click(); await tick();
            show({ server: srv() }); await tick();
            return { on, sent: posted(), off: [el("#ltStartBtn").disabled,
              el("#ltWhy").textContent, el("#ltStopServerBtn").hidden] };
            """,
        )
        assert out["on"][0] is True and "API server is running" in out["on"][1]
        assert out["on"][2] is False
        assert out["sent"] == ["/api/server/stop"]
        assert out["off"] == [False, "", True]

    def test_another_block_is_said_and_asked_again(self, tmp_path):
        out = ui(
            tmp_path,
            """
            let blocked = "an install is running";
            routes["/api/loadtest"] = () => view({ blocked });
            show(); showView("loadtest"); await tick();
            const on = [el("#ltStartBtn").disabled, el("#ltWhy").textContent];
            blocked = null; show(); await tick();
            return [on, el("#ltStartBtn").disabled];
            """,
        )
        assert out == [[True, "Cannot start now: an install is running."], False]

    def test_no_model_installed(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show({ models: [] }); showView("loadtest"); await tick();
            return [el("#ltNoModel").hidden, el("#ltStartBtn").disabled,
              el("#ltModelSelectSearch").disabled, asked.filter((u) => u.startsWith("/api/loadtest"))];
            """,
        )
        assert out == [False, True, True, []]
        assert 'href="#download"' in PAGE.split('id="ltNoModel"')[1].split("</p>")[0]


@needs_node
class TestLoadTestRun:
    def test_progress_and_cancel_only(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD
            + PICK
            + """
            show({ loadtest: progress() }); await tick();
            const off = ["#ltStartBtn", "#ltModelSelectSearch", "#ltCustom", "#ltAddBtn", "#lt-rounds"]
              .map((s) => el(s).disabled);
            const first = { off, hidden: el("#ltRunning").hidden,
              step: el("#ltStep").textContent, detail: el("#ltDetail").textContent,
              bar: [el("#ltProgress").value, el("#ltProgress").max],
              said: el("#ltAnnounce").textContent, boxes: html("#ltSizes"),
              cancel: [el("#ltCancelBtn").hidden,
                el("#ltCancelBtn").getAttribute("aria-disabled")] };
            const writes = el("#ltAnnounce").textWrites;
            show({ loadtest: progress({ done: 4000, memory_percent: 63 }) }); await tick();
            const quiet = el("#ltAnnounce").textWrites === writes;
            const moved = el("#ltDetail").textContent;
            show({ loadtest: progress({ step: "follow-up 1 of 2" }) }); await tick();
            const spoke = el("#ltAnnounce").textWrites === writes + 1;
            await el("#ltCancelBtn").listeners.click();
            show({ loadtest: progress({ status: "cancelling" }) }); await tick();
            return { ...first, quiet, moved, spoke, sent: sent.map((s) => s[0]),
              stopping: [el("#ltStep").textContent,
                el("#ltCancelBtn").getAttribute("aria-disabled")] };

            """,
        )
        assert out["off"] == [True] * 5 and "disabled" in out["boxes"]
        assert out["hidden"] is False and out["cancel"] == [False, None]
        for part in ("org/custom", "size 2 of 2", "8192", "cold 1 of 2"):
            assert part in out["step"]
        assert "100 of 8000" in out["detail"] and "55.5%" in out["detail"]
        assert out["bar"] == [100, 8000]
        assert "100 of 8000" not in out["said"] and "55.5" not in out["said"]
        assert out["quiet"] and out["spoke"] and "63%" in out["moved"]
        assert out["sent"] == ["/api/loadtest/cancel"]
        assert "Stopping" in out["stopping"][0] and out["stopping"][1] == "true"

    def test_cancel_keeps_the_focus_until_start_can_take_it(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD
            + """
            el("#ltStartBtn").focus(); show({ loadtest: progress() }); await tick();
            const onCancel = document.activeElement === el("#ltCancelBtn");
            show({ loadtest: progress({ status: "cancelling" }) });
            await el("#ltCancelBtn").listeners.click();
            const kept = [document.activeElement === el("#ltCancelBtn"),
              el("#ltCancelBtn").disabled, posted()];
            show({ loadtest: progress({ active: false, status: "cancelled" }) });
            return [onCancel, kept, document.activeElement === el("#ltStartBtn")];
            """,
        )
        assert out == [True, [True, False, []], True]

    def test_the_running_model_is_shown_and_results_are_asked_for_again(self, tmp_path):
        out = ui(
            tmp_path,
            LOAD
            + """
            show({ loadtest: progress({ repo: "org/other" }) }); await tick();
            const during = [el("#ltModelSelect").value, asked.length];
            show({ loadtest: progress({ repo: "org/other" }) }); await tick();
            const same = asked.length;
            show({ loadtest: progress({ repo: "org/other", active: false, status: "done" }) });
            await tick();
            return { during, same, after: asked.slice(same), hidden: el("#ltRunning").hidden,
              said: el("#ltAnnounce").textContent };
            """,
        )
        assert out["during"][0] == "org/other" and out["during"][1] == out["same"]
        assert "/api/loadtest?repo=org%2Fother" in out["after"]
        assert out["hidden"] is True and "finished" in out["said"]

    def test_the_results_table(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/loadtest"] = () => view({ results: [
              runRecord({ id: "old", time: "2026-11-26 09:00:00", stale: true }),
              runRecord({ status: "memory", reason: "macOS reports critical memory pressure",
                rows: [row(), row({ size: 8192, status: "skipped", read_tps: null,
                  write_tps: "n/a", first_token_s: null, follow_up_first_token_s: null,
                  peak_percent: undefined, server_peak_bytes: undefined,
                  prompt_tokens: undefined,
                  reason: "<b>memory</b>" })] })] });
            show(); showView("loadtest"); await tick();
            return [html("#ltResults"), el("#ltOutcome").hidden,
              el("#ltOutcomeTitle").textContent, el("#ltOutcomeText").textContent];
            """,
        )
        table, hidden, title, text = out
        for head in (
            "Size",
            "Read tok/s",
            "First token",
            "Write tok/s",
            "Peak memory",
            "Settings used",
        ):
            assert head in table
        for value in ("512.3", "7.9 s", "follow-up 0.4 s", "21.1", "61.2%", "9.4 GB"):
            assert value in table
        assert "reply 256 tokens, 2 rounds" in table and "--max-tokens 8192" in table
        assert "&lt;b&gt;memory" in table and "<b>memory" not in table
        # Newest first; the old run is history, tagged, and cannot be applied.
        assert table.index("2026-11-27") < table.index("Earlier runs") < table.index("2026-11-26")
        assert "stale" in table
        assert "data-apply" not in table and "%" not in text
        assert hidden is False and "memory" in title.lower()
        assert "critical memory pressure" in text and "Larger sizes were not run" in text

    def test_a_failed_run_gives_one_reason_and_a_done_run_none(self, tmp_path):
        out = ui(
            tmp_path,
            """
            let last = runRecord({ status: "failed", reason: "timeout", rows: [] });
            routes["/api/loadtest"] = () => view({ results: [last] });
            show(); showView("loadtest"); await tick();
            const failed = [el("#ltOutcomeTitle").textContent, el("#ltOutcomeText").textContent];
            last = runRecord(); await loadLoadTest();
            return [failed, el("#ltOutcome").hidden];
            """,
        )
        assert out == [["Test failed", "timeout"], True]
