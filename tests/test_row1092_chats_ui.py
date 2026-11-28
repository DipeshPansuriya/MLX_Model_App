"""Row 1100: the History and Chat settings screen. ui/app.js under node."""

from __future__ import annotations

import re

from test_ui_behaviour import ROOT, needs_node, run

PAGE = (ROOT / "ui" / "index.html").read_text(encoding="utf-8")
APP = (ROOT / "ui" / "app.js").read_text(encoding="utf-8")
ID = "0" * 32

HELPERS = r"""
const el = (s) => document.querySelector(s);
for (const s of ["#historyPanel", "#chatSettingsPanel", "#renameForm"]) el(s).hidden = true;
wire();
state.chatStatus = "ready";
el("#chatModelSelect").value = "org/custom";
const ID = "0".repeat(32);
const summary = (over = {}) => ({ id: ID, title: "First", created: 1, updated: 5,
  model: "org/custom", project_id: null, count: 2, ...over });
const saved = (over = {}) => ({ ...summary(), system: "be short",
  settings: { max_tokens: 100, temperature: 0.5 },
  messages: [{ role: "user", content: "<b>hi</b>", ts: 1 },
    { role: "assistant", content: "<img src=x>", thinking: "hmm", stats: { reply_tokens: 3 },
      stopped: true, ts: 2 }], ...over });
const stream = (done) => { let sent = false; const text = "event: token\ndata: \"ok\"\n\n"
    + `event: done\ndata: ${JSON.stringify(done)}\n\n`;
  return Promise.resolve({ ok: true, body: { getReader: () => ({ read: async () => sent
    ? { done: true } : (sent = true, { done: false, value: new TextEncoder().encode(text) }) }) } });
};
let routes = {};
respond = (p, o) => routes[p.split("?")[0]] ? routes[p.split("?")[0]](o) : new Promise(() => {});
const bodies = (path) => calls.filter((c) => c.path === path).map((c) => JSON.parse(c.opts.body));
const say = async (text) => { el("#input").value = text; await sendMessage(); };
const tick = () => new Promise((r) => setTimeout(r, 0));
const click = async (node) => { await node.listeners.click({ preventDefault() {} }); await tick(); };
const enter = async (s) => { el(s).listeners.keydown({ key: "Enter", preventDefault() {} });
  await tick(); };
const rows = (s = "#historyList") => el(s).children.map((li) => li.children);
const texts = (s) => rows(s).map((parts) => parts.map((p) => p.textContent));
"""


def ui(tmp_path, scenario):
    return run(tmp_path, HELPERS + scenario)


@needs_node
class TestSaving:
    def test_a_first_message_asks_to_save_and_later_ones_name_the_chat(self, tmp_path):
        out = ui(
            tmp_path,
            """
            let n = 0;
            routes["/api/chat"] = () => stream({ chat: summary({ updated: 5 + n++ }) });
            await say("a"); await say("b"); await say("c");
            return { sent: bodies("/api/chat"), ref: state.chatRef };
            """,
        )
        first, second, third = out["sent"]
        assert first["save"] == {"id": None}
        assert first["system"] == "" and first["settings"] == {}
        assert second["save"] == {"id": ID, "updated": 5}
        assert third["save"] == {"id": ID, "updated": 6}
        assert out["ref"] == {"id": ID, "updated": 7}
        assert [m["role"] for m in third["messages"]] == ["user", "assistant"] * 2 + ["user"]
        assert all(set(m) == {"role", "content"} for m in third["messages"])

    def test_the_system_prompt_and_settings_go_with_every_turn(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chat"] = () => stream({});
            el("#chatSystem").value = "be short"; el("#chatMaxTokens").value = "64";
            el("#chatTemp").value = "0"; el("#chatTopP").value = "0.9";
            await say("a"); await say("b");
            return bodies("/api/chat");
            """,
        )
        for body in out:
            assert body["system"] == "be short"
            assert body["settings"] == {"max_tokens": 64, "temperature": 0, "top_p": 0.9}

    def test_a_temporary_chat_sends_no_save(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chat"] = () => stream({});
            el("#chatSystem").value = "kept";
            el("#chatTemporary").checked = true; el("#chatTemporary").listeners.change();
            await say("a");
            return { body: bodies("/api/chat")[0], ref: state.chatRef,
              state: el("#chatSaveState").textContent };
            """,
        )
        assert "save" not in out["body"] and out["body"]["system"] == "kept"
        assert out["ref"] is None and "Temporary" in out["state"]

    def test_the_temporary_switch_starts_a_new_chat_both_ways(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chat"] = (o) => stream(JSON.parse(o.body).save ? { chat: summary() } : {});
            const flip = (on) => { el("#chatTemporary").checked = on;
              el("#chatTemporary").listeners.change(); };
            await say("a"); flip(true);
            const on = [state.chatRef, state.messages.length];
            await say("secret"); flip(false);
            await say("b");
            return { on, last: bodies("/api/chat")[2] };
            """,
        )
        assert out["on"] == [None, 0]
        assert out["last"]["save"] == {"id": None}
        assert [m["content"] for m in out["last"]["messages"]] == ["b"]

    def test_a_save_error_is_shown_and_cleared_by_new_chat(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chat"] = () => stream({ save_error: "This reply was not saved: full." });
            await say("a");
            const shown = el("#chatSaveError").textContent;
            await click(el("#newChatBtn"));
            return [shown, el("#chatSaveError").textContent, state.chatRef];
            """,
        )
        assert out == ["This reply was not saved: full.", "", None]

    def test_a_reply_of_a_chat_thrown_away_does_not_become_the_open_chat(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chat"] = () => { newChat(); return stream({ chat: summary() }); };
            await say("a");
            return state.chatRef;
            """,
        )
        assert out is None

    def test_a_bad_setting_stops_the_send_and_keeps_the_text(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const bad = [];
            for (const [s, v] of [["#chatMaxTokens", "63"], ["#chatMaxTokens", "64.5"],
                ["#chatTemp", "2.1"], ["#chatTopP", "0"]]) {
              el(s).value = v; el("#chatSettingsPanel").hidden = true;
              await say("a");
              bad.push([el("#input").value, el("#chatSettingsPanel").hidden,
                el(s).attrs["aria-invalid"], !!el("#chatSettingsError").textContent,
                document.activeElement.selector === s]);
              el(s).value = "";
            }
            return { bad, sent: bodies("/api/chat").length };
            """,
        )
        assert out["sent"] == 0
        assert out["bad"] == [["a", False, "true", True, True]] * 4

    def test_new_chat_empties_the_chat_settings(self, tmp_path):
        out = ui(
            tmp_path,
            """
            el("#chatSystem").value = "x"; el("#chatTopP").value = "0.5";
            await click(el("#newChatBtn"));
            return [el("#chatSystem").value, el("#chatTopP").value];
            """,
        )
        assert out == ["", ""]


@needs_node
class TestHistory:
    def test_opening_the_panel_lists_chats_and_names_broken_files(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats"] = () => reply({ chats: [summary({ title: "<img src=x>" })],
              broken: [{ id: "f".repeat(32), reason: "this chat file cannot be read" }] });
            await click(el("#historyBtn"));
            return { open: !el("#historyPanel").hidden,
              expanded: el("#historyBtn").attrs["aria-expanded"],
              focus: document.activeElement.selector, rows: texts(),
              labels: rows()[0].map((p) => p.attrs["aria-label"] ?? null),
              broken: texts("#historyBroken"), status: el("#historyStatus").textContent,
              markup: el("#historyList").innerHTML };
            """,
        )
        assert out["open"] and out["expanded"] == "true" and out["focus"] == "#historySave"
        assert out["rows"][0][0] == "<img src=x>" and out["markup"] == ""
        assert "2 messages" in out["rows"][0][1]
        assert out["labels"] == [
            "Open chat: <img src=x>",
            None,
            "Rename chat: <img src=x>",
            "Delete chat: <img src=x>",
        ]
        assert "f" * 32 + ".json" in out["broken"][0][0]
        assert "cannot be read" in out["broken"][0][0]
        assert "1 saved chat" in out["status"]

    def test_escape_closes_a_panel_and_returns_the_focus(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const result = [];
            for (const [b, p] of [["#historyBtn", "#historyPanel"],
                ["#chatSettingsBtn", "#chatSettingsPanel"]]) {
              await click(el(b));
              el(p).listeners.keydown({ key: "Escape", preventDefault() {} });
              result.push([el(p).hidden, el(b).attrs["aria-expanded"],
                document.activeElement.selector === b]);
            }
            await click(el("#historyBtn")); await click(el("#chatSettingsBtn"));
            result.push([el("#historyPanel").hidden, el("#chatSettingsPanel").hidden]);
            return result;
            """,
        )
        assert out == [[True, "false", True], [True, "false", True], [True, False]]

    def test_search_posts_the_words(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats/search"] = () => reply({ chats: [], broken: [] });
            el("#historyQuery").value = " cats "; await enter("#historyQuery");
            return [bodies("/api/chats/search"), el("#historyStatus").textContent];
            """,
        )
        assert out[0] == [{"query": "cats"}] and "No saved chat matches" in out[1]

    def test_open_draws_the_chat_as_text_and_continues_it(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats"] = () => reply({ chats: [summary()], broken: [] });
            routes["/api/chats/get"] = () => reply(saved());
            routes["/api/chat"] = () => stream({ chat: summary({ updated: 9 }) });
            el("#chatTemporary").checked = true;
            await click(el("#historyBtn")); await click(rows()[0][0]);
            const drawn = el("#messages").children.map((m) => [
              m.querySelector(".msg-body").textContent,
              m.querySelector(".msg-think-text").textContent,
              m.children.map((c) => c.textContent).join(""),
              m.innerHTML.includes("img") || m.innerHTML.includes("<b>")]);
            const fields = [el("#chatSystem").value, String(el("#chatMaxTokens").value),
              String(el("#chatTemp").value), String(el("#chatTopP").value)];
            const hidden = [el("#historyPanel").hidden, el("#chatEmpty").hidden,
              el("#chatTemporary").checked];
            await say("more");
            return { drawn, fields, hidden, body: bodies("/api/chat")[0],
              get: calls.some((c) => c.path === `/api/chats/get?id=${ID}`) };
            """,
        )
        assert out["get"]
        assert out["drawn"][0][0] == "<b>hi</b>"
        assert out["drawn"][1][:2] == ["<img src=x>", "hmm"]
        assert "3 tokens" in out["drawn"][1][2] and "stopped" in out["drawn"][1][2]
        assert [d[3] for d in out["drawn"]] == [False, False]
        assert out["fields"] == ["be short", "100", "0.5", ""]
        assert out["hidden"] == [True, True, False]
        assert out["body"]["save"] == {"id": ID, "updated": 5}
        assert out["body"]["settings"] == {"max_tokens": 100, "temperature": 0.5}
        assert [m["content"] for m in out["body"]["messages"]] == [
            "<b>hi</b>",
            "<img src=x>",
            "more",
        ]

    def test_no_chat_opens_while_a_reply_runs(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats/get"] = () => reply(saved());
            state.streaming = true; await openChat(ID);
            const first = [calls.filter((c) => c.path.startsWith("/api/chats")).length,
              el("#historyStatus").textContent];
            state.streaming = false;
            routes["/api/chats/get"] = () => { state.streaming = true; return reply(saved()); };
            await openChat(ID);
            return [first, state.chatRef, state.messages.length];
            """,
        )
        assert out[0][0] == 0 and "Stop" in out[0][1]
        assert out[1:] == [None, 0]

    def test_rename_posts_the_title_and_keeps_the_reference(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats"] = () => reply({ chats: [summary()], broken: [] });
            routes["/api/chats/rename"] = () => reply(summary({ title: "New" }));
            state.chatRef = { id: ID, updated: 5 };
            await click(el("#historyBtn")); await click(rows()[0][2]);
            const form = [el("#renameForm").hidden, el("#renameInput").value,
              document.activeElement.selector];
            el("#renameInput").value = "New"; await click(el("#renameSaveBtn"));
            return { form, sent: bodies("/api/chats/rename"), ref: state.chatRef,
              hidden: el("#renameForm").hidden, focus: document.activeElement === rows()[0][2] };
            """,
        )
        assert out["form"] == [False, "First", "#renameInput"]
        assert out["sent"] == [{"id": ID, "title": "New"}]
        assert out["ref"] == {"id": ID, "updated": 5}
        assert out["hidden"] and out["focus"]

    def test_delete_asks_first_and_clears_the_open_chat(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats"] = () => reply({ chats: [summary()], broken: [] });
            routes["/api/chats/delete"] = () => reply({ ok: true });
            state.chatRef = { id: ID, updated: 5 }; state.messages = [{ role: "user" }];
            await click(el("#historyBtn"));
            confirm = () => false; await click(rows()[0][3]);
            const refused = [bodies("/api/chats/delete").length, !!state.chatRef];
            confirm = () => true; await click(rows()[0][3]);
            return { refused, sent: bodies("/api/chats/delete"), ref: state.chatRef,
              left: state.messages.length, focus: document.activeElement.selector };
            """,
        )
        assert out["refused"] == [0, True]
        assert out["sent"] == [{"id": ID, "confirm": True}]
        assert out["ref"] is None and out["left"] == 0
        assert out["focus"] == "#historyQuery"

    def test_a_broken_file_can_be_deleted(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats"] = () => reply({ chats: [], broken: [{ id: ID, reason: "bad" }] });
            routes["/api/chats/delete"] = () => reply({ ok: true });
            await click(el("#historyBtn")); await click(rows("#historyBroken")[0][1]);
            return bodies("/api/chats/delete");
            """,
        )
        assert out == [{"id": ID, "confirm": True}]

    def test_delete_all_asks_first_and_waits_for_a_running_reply(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats"] = () => reply({ chats: [], broken: [] });
            routes["/api/chats/delete-all"] = () => reply({ deleted: 2, failed: 1 });
            state.chatRef = { id: ID, updated: 5 };
            confirm = () => false; await click(el("#historyDeleteAll"));
            confirm = () => true;
            state.streaming = true; await click(el("#historyDeleteAll"));
            const before = bodies("/api/chats/delete-all").length;
            state.streaming = false; await click(el("#historyDeleteAll"));
            return { before, sent: bodies("/api/chats/delete-all"), ref: state.chatRef,
              toast: el("#toast").textContent };
            """,
        )
        assert out["before"] == 0 and out["sent"] == [{"confirm": True}]
        assert out["ref"] is None and "1" in out["toast"] and "could not" in out["toast"]

    def test_the_save_switch_posts_and_follows_the_state(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/ui/settings"] = () => reply({ ok: true });
            render(snap({ ui: { save_history: "off" } }));
            const seen = [el("#historySave").checked, el("#chatSaveState").textContent];
            el("#historySave").checked = true;
            await el("#historySave").listeners.change(); await tick();
            routes["/api/ui/settings"] = () => reply({ error: "no" }, false);
            el("#historySave").checked = false;
            await el("#historySave").listeners.change(); await tick();
            return { seen, sent: bodies("/api/ui/settings"), after: el("#historySave").checked };
            """,
        )
        assert out["seen"][0] is False and "off" in out["seen"][1]
        assert out["sent"] == [{"save_history": "on"}, {"save_history": "off"}]
        assert out["after"] is True


@needs_node
class TestChatSettings:
    def test_save_uses_the_settings_route_and_takes_the_new_time(self, tmp_path):
        out = ui(
            tmp_path,
            """
            routes["/api/chats/settings"] = () => reply(summary({ updated: 8 }));
            state.chatRef = { id: ID, updated: 5 };
            el("#chatSystem").value = "s"; el("#chatTopP").value = "1";
            await click(el("#chatSettingsSave"));
            return { sent: bodies("/api/chats/settings"), ref: state.chatRef,
              said: el("#chatSettingsState").textContent };
            """,
        )
        assert out["sent"] == [{"id": ID, "updated": 5, "system": "s", "settings": {"top_p": 1}}]
        assert out["ref"] == {"id": ID, "updated": 8} and "Saved" in out["said"]

    def test_no_route_call_without_a_saved_chat_a_free_reply_or_saving(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const said = [];
            const go = async () => { await enter("#chatTopP");
              said.push(el("#chatSettingsState").textContent); };
            await go();
            state.chatRef = { id: ID, updated: 5 }; state.streaming = true; await go();
            state.streaming = false; render(snap({ ui: { save_history: "off" } })); await go();
            return { said, sent: bodies("/api/chats/settings").length };
            """,
        )
        assert out["sent"] == 0
        assert all("next message" in line for line in out["said"])

    def test_no_message_leaves_while_the_settings_are_being_saved(self, tmp_path):
        out = ui(
            tmp_path,
            """
            let done;
            routes["/api/chats/settings"] = () => new Promise((r) => { done = r; });
            state.chatRef = { id: ID, updated: 5 };
            const saving = saveChatSettings();
            await say("a");
            const during = [bodies("/api/chat").length, el("#input").value];
            done({ ok: false, statusText: "x", json: async () => ({ error: "changed" }) });
            await saving;
            return { during, error: el("#chatSettingsError").textContent, busy: state.chatBusy };
            """,
        )
        assert out == {"during": [0, "a"], "error": "changed", "busy": False}


class TestPage:
    def test_the_top_bar_has_the_four_controls(self):
        top = PAGE[PAGE.index('id="view-chat"') : PAGE.index('id="chatGate"')]
        for name in ("chatTemporary", "historyBtn", "chatSettingsBtn", "newChatBtn"):
            assert f'id="{name}"' in top

    def test_switches_panels_and_notes_are_named(self):
        for name in ("chatTemporary", "historySave"):
            assert re.search(rf'role="switch" id="{name}"', PAGE)
        for button, panel in (
            ("historyBtn", "historyPanel"),
            ("chatSettingsBtn", "chatSettingsPanel"),
        ):
            assert re.search(rf'id="{button}" aria-expanded="false" aria-controls="{panel}"', PAGE)
            assert re.search(rf'id="{panel}" role="region" aria-labelledby="\w+" hidden', PAGE)
        assert "plain text" in PAGE and "not a secure erase" in PAGE
        for name in ("chatSystem", "chatMaxTokens", "chatTemp", "chatTopP", "renameInput"):
            assert f'for="{name}"' in PAGE
        assert 'id="historyQuery" maxlength="200" aria-label=' in PAGE

    def test_the_limits_are_those_of_the_store(self):
        for name, limits in (
            ("chatSystem", 'maxlength="8000"'),
            ("chatMaxTokens", 'min="64" max="131072"'),
            ("chatTemp", 'min="0" max="2"'),
            ("chatTopP", 'min="0.05" max="1"'),
        ):
            assert re.search(rf'id="{name}"[^>]*{limits}', PAGE)

    def test_saved_chat_code_never_writes_markup(self):
        start = APP.index("/* ───────────── saved chats")
        block = APP[start : APP.index("/* ───────────── logs")]
        assert "innerHTML" not in block and "setHTML" not in block
