"""The Chat page follows the API server (row 837). ui/app.js under node."""

from __future__ import annotations

import json
import re

from test_ui_behaviour import ROOT, needs_node, run

HELPERS = """
const srv = (over = {}) => ({ running: true, ready: true, load_failed: false, starting: false,
  repos: ["org/one"], repo: "org/one", endpoint: "e", lines: [], tool_support: {}, ...over });
const off = (over = {}) => srv({ running: false, ready: false, repos: [], repo: null, ...over });
const show = (server) => render(snap({ server }));
const el = (s) => document.querySelector(s);
const chat = () => ({
  value: el("#chatModelSelect").value, options: html("#chatModelSelect"),
  select: el("#chatModelSelect").disabled, input: el("#input").disabled,
  send: el("#sendBtn").disabled, gate: !el("#chatGate").hidden,
  gateText: el("#chatGateText").textContent, go: !el("#chatGoServer").hidden,
  state: html("#chatState"), typed: el("#input").value,
});
// Answers /api/chat with these events; every other request is the same snapshot.
const sse = (frames, server = srv()) => {
  ctx = frames.map(([e, d]) => `event: ${e}\\ndata: ${JSON.stringify(d)}\\n\\n`);
  return (path) => {
    if (path !== "/api/chat") return reply(snap({ server }));
    let i = 0;
    return Promise.resolve({ ok: true, body: { getReader: () => ({ read: async () =>
      i < ctx.length ? { done: false, value: new TextEncoder().encode(ctx[i++]) }
        : { done: true } }) } });
  };
};
var ctx;
const bubble = () => el("#messages").children[1];
const part = (s) => bubble().querySelector(s);
const say = async (frames) => {
  show(srv()); respond = sse(frames); el("#input").value = "hi"; await sendMessage();
  return { body: part(".msg-body").textContent, think: part(".msg-think-text").textContent,
    thinkShown: !part(".msg-think").hidden, note: part(".msg-note").textContent,
    waiting: !part(".msg-wait").hidden, history: state.messages,
    announced: el("#chatAnnounce").textContent, bad: bubble().classList.contains("msg-error"),
    meta: bubble().children.map((c) => c.textContent) };
};
const DONE = ["done", { tokens: 3, tps: 9.5, elapsed: 1, stopped: false, truncated: false,
  stats: { first_s: 1.2, tps: 9.5, prompt_tokens: 812, reply_tokens: 3, mode: "Save memory" } }];
"""


def ui(tmp_path, scenario):
    return run(tmp_path, HELPERS + scenario)


@needs_node
class TestModelChoice:
    def test_one_served_model_is_selected_and_ready_to_type(self, tmp_path):
        out = ui(tmp_path, "show(srv()); return chat();")
        assert out["value"] == "org/one"
        assert "org/custom" not in out["options"]
        assert (out["select"], out["input"], out["send"], out["gate"]) == (False,) * 4

    def test_two_models_default_to_the_first_and_keep_the_choice(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const two = srv({ repos: ["org/a", "org/b"] });
            show(two); const first = chat().value;
            el("#chatModelSelect").value = "org/b"; el("#chatModelSelect").listeners.change?.();
            wire(); el("#chatModelSelect").listeners.change();
            show({ ...two, repos: ["org/c", "org/b"] }); const kept = chat().value;
            show(off()); show(srv({ repos: ["org/x"], ready: false })); show(two);
            const after = chat().value;
            show(srv({ repos: ["org/c", "org/d"] }));
            return { first, kept, after, gone: chat().value };
            """,
        )
        assert out == {"first": "org/a", "kept": "org/b", "after": "org/b", "gone": "org/c"}

    def test_a_stopped_server_asks_for_it_and_keeps_the_typed_text(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(srv()); el("#input").value = "half a question"; show(off());
            wire(); el("#chatGoServer").listeners.click();
            return { ...chat(), hash: location.hash,
              posted: calls.filter((c) => c.path === "/api/server/start").length };
            """,
        )
        assert out["input"] is True
        assert out["send"] is True
        assert out["select"] is True
        assert out["gate"] is True
        assert out["go"] is True
        assert "Start the API server first" in out["gateText"]
        assert out["typed"] == "half a question"
        assert out["hash"].endswith("server")
        assert out["posted"] == 0

    def test_starting_and_restarting_wait(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(off({ starting: true })); const starting = chat();
            show(srv({ starting: true })); const restarting = chat();
            show(srv({ ready: false })); const loading = chat();
            return { starting, restarting, loading };
            """,
        )
        for view in out.values():
            assert view["input"] is True
            assert view["send"] is True
            assert view["gate"] is True
            assert view["go"] is False
        assert "Starting" in out["starting"]["state"]
        assert "Restarting" in out["restarting"]["state"]
        assert "starting" in out["loading"]["gateText"]

    def test_a_failed_load_points_to_the_server_page(self, tmp_path):
        out = ui(tmp_path, "show(srv({ ready: false, load_failed: true })); return chat();")
        assert out["input"] is True
        assert out["go"] is True
        assert "could not load" in out["gateText"]
        assert "press Stop, then Start Server" in out["gateText"]

    def test_nothing_is_sent_while_the_server_is_not_ready(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(off()); el("#input").value = "hi"; await sendMessage();
            return { typed: el("#input").value,
              sent: calls.filter((c) => c.path === "/api/chat").length };
            """,
        )
        assert out == {"typed": "hi", "sent": 0}

    def test_no_load_controls_are_left(self, tmp_path):
        page = (ROOT / "ui" / "index.html").read_text("utf-8")
        script = (ROOT / "ui" / "app.js").read_text("utf-8")
        for gone in ("loadBtn", "unloadBtn", "loadState", "loadProblem", "chatServerNote"):
            assert gone not in page + script
        assert not re.search(r"/api/load(?!test)", script)
        assert "/api/unload" not in script
        out = ui(
            tmp_path,
            "render(snap({ server: srv({ repos: ['org/custom'] }) }));"
            ' state.search = null; return html("#modelCards");',
        )
        assert "served" in out
        assert ">Load<" not in out
        assert ">Unload<" not in out


@needs_node
class TestReplies:
    def test_the_chosen_model_is_asked_and_the_reply_kept(self, tmp_path):
        out = ui(
            tmp_path,
            """
            const r = await say([["token", "Hel"], ["token", "lo"], DONE]);
            const sent = JSON.parse(calls.find((c) => c.path === "/api/chat").opts.body);
            return { ...r, sent };
            """,
        )
        assert out["sent"] == {"repo": "org/one", "messages": [{"role": "user", "content": "hi"}]}
        assert out["body"] == "Hello"
        assert out["thinkShown"] is False
        assert out["history"][-1] == {"role": "assistant", "content": "Hello"}
        line = (
            "First answer after 1.2 s · Writing 9.5 tokens/s · Prompt 812 tokens · Save memory mode"
        )
        assert line in json.dumps(out["meta"], ensure_ascii=False)
        assert out["announced"] == "Reply complete"

    def test_a_reasoning_field_goes_into_the_thinking_block(self, tmp_path):
        out = ui(
            tmp_path,
            'return say([["reasoning", "let me see"], ["token", "\\n\\n42"], DONE]);',
        )
        assert (out["think"], out["thinkShown"], out["body"]) == ("let me see", True, "42")
        assert out["history"][-1]["content"] == "42"

    def test_think_tags_split_across_tokens(self, tmp_path):
        out = ui(
            tmp_path,
            'return say([["token", "<th"], ["token", "ink>hmm</th"], ["token", "ink>\\n\\n42"], DONE]);',
        )
        assert (out["think"], out["thinkShown"], out["body"]) == ("hmm", True, "42")

    def test_thinking_with_no_end_is_never_hidden(self, tmp_path):
        for frames in ('["token", "<think>all of it"]', '["reasoning", "all of it"]'):
            out = ui(tmp_path, f"return say([{frames}, DONE]);")
            assert out["body"] == "all of it", frames
            assert out["thinkShown"] is False
            assert "No answer came after the thinking" in out["note"]

    def test_thinking_alone_is_not_kept_as_the_answer(self, tmp_path):
        stop = '["done", { tokens: 2, tps: 1, stopped: true }]'
        for frames, said in (
            ('["reasoning", "all of it"], DONE', "Reply ended without an answer"),
            ('["token", "<think>all of it"], DONE', "Reply ended without an answer"),
            (f'["reasoning", "all of it"], {stop}', "Reply stopped"),
            (f'["token", "<think>all of it"], {stop}', "Reply stopped"),
        ):
            out = ui(tmp_path, f"return say([{frames}]);")
            assert out["body"] == "all of it", frames
            assert out["history"] == [], frames
            assert out["announced"] == said, frames

    def test_a_tag_inside_the_answer_is_left_alone(self, tmp_path):
        out = ui(tmp_path, 'return say([["token", "write <think> like so"], DONE]);')
        assert out["body"] == "write <think> like so"
        assert out["thinkShown"] is False

    def test_a_reply_that_looks_like_a_tag_start_is_shown(self, tmp_path):
        out = ui(tmp_path, 'return say([["token", "<"], DONE]);')
        assert out["body"] == "<"

    def test_waiting_is_shown_until_something_arrives(self, tmp_path):
        busy = ui(tmp_path, 'return say([["status", { state: "waiting" }]]);')
        assert busy["bad"] is True  # cut off: said plainly
        out = ui(
            tmp_path,
            """
            show(srv()); const seen = [];
            const frames = [["status", { state: "waiting" }],
              ["status", { state: "prompt", done: 5, total: 10 }], ["token", "ok"], DONE];
            const next = sse(frames);
            respond = async (path) => {
              const res = await next(path);
              if (path !== "/api/chat") return res;
              const reader = res.body.getReader();
              return { ok: true, body: { getReader: () => ({ read: () => {
                if (bubble()) seen.push([!part(".msg-wait").hidden, part(".msg-wait").textContent]);
                return reader.read(); } }) } };
            };
            el("#input").value = "hi"; await sendMessage();
            return seen;
            """,
        )
        assert out[1][0] is True
        assert "Waiting for the API server" in out[1][1]
        assert "5 of 10" in out[2][1]
        assert out[3][0] is False

    def test_stopped_and_length_limit_are_said(self, tmp_path):
        out = ui(
            tmp_path,
            'return say([["token", "a"], ["done", { tokens: 1, tps: 1, stopped: true }]]);',
        )
        assert "stopped" in json.dumps(out["meta"])
        assert out["history"][-1]["content"] == "a"
        out = ui(
            tmp_path,
            'return say([["token", "a"], ["done", { tokens: 1, tps: 1, truncated: true }]]);',
        )
        assert "length limit" in json.dumps(out["meta"])

    def test_stopped_before_any_reply_drops_the_turn(self, tmp_path):
        out = ui(tmp_path, 'return say([["done", { tokens: 0, tps: 0, stopped: true }]]);')
        assert out["history"] == []
        assert "Stopped before any reply" in out["note"]

    def test_an_error_keeps_the_partial_reply_and_says_why(self, tmp_path):
        out = ui(tmp_path, 'return say([["token", "half"], ["error", "server went away"]]);')
        assert out["body"] == "half"
        assert "server went away" in out["note"]
        assert out["bad"] is True
        assert out["history"] == []

    def test_a_refused_request_gives_the_text_back(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(srv()); el("#input").value = "my question";
            respond = (path) => path === "/api/chat"
              ? reply({ error: "The API server does not serve org/one." }, false)
              : reply(snap({ server: srv() }));
            await sendMessage();
            return { typed: el("#input").value, body: part(".msg-body").textContent,
              history: state.messages };
            """,
        )
        assert out["typed"] == "my question"
        assert "does not serve" in out["body"]
        assert out["history"] == []


ORDER = ["server", "chat", "models", "download", "config", "loadtest", "logs", "settings"]


class TestMenuOrder:
    PAGE = (ROOT / "ui" / "index.html").read_text("utf-8")

    def test_api_server_is_first_and_chat_second(self):
        # Source order is also the keyboard's tab order.
        assert re.findall(r'<button class="nav-item[^"]*" data-view="(\w+)"', self.PAGE) == ORDER
        assert "tabindex" not in self.PAGE.split('<nav class="nav">')[1].split("</nav>")[0]

    def test_the_page_starts_on_the_api_server(self):
        current = re.findall(
            r'<button class="nav-item active" data-view="(\w+)" aria-current', self.PAGE
        )
        assert current == ["server"]
        assert re.findall(r'<section class="view active" id="view-(\w+)"', self.PAGE) == ["server"]

    @needs_node
    def test_the_app_opens_on_the_api_server_unless_the_address_says_otherwise(self, tmp_path):
        where = "return [location.hash.replace('#', ''), VIEWS];"
        assert run(tmp_path, where) == ["server", ORDER]
        assert run(tmp_path, where, hash="#chat")[0] == "chat"
        assert run(tmp_path, where, hash="#nonsense")[0] == "server"
        assert run(tmp_path, where, hash="#t=" + "a" * 40)[0] == "server"
