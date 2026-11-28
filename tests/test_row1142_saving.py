"""Row 1142: what a saved chat keeps (row 1140 review, findings 2 and 3)."""

# ruff: noqa: F811  (the upstream fixture is imported, then asked for by name)

from __future__ import annotations

from test_row837_chat import DONE, ONE, chunk, upstream  # noqa: F401
from test_row1092_chats import chats  # noqa: F401
from test_row1092_chats_ui import ui
from test_row1142_helpers import send, user
from test_ui_behaviour import needs_node

import chat_store


def bot(text="Hello"):
    return {"role": "assistant", "content": text}


def ref(done, screen=None):
    save = {"id": done["chat"]["id"], "updated": done["chat"]["updated"]}
    return {"save": save, **({"screen": screen} if screen else {})}


def texts(chats, chat_id):
    return [m["content"] for m in chats.get(chat_id)[1]["messages"]]


# 2: every turn on screen that the chat lacks is added
def test_turns_made_while_history_was_off_are_saved_by_the_next_save(chats):
    first = send(chats, [user("one")], save={"id": None})
    chats.call("/api/ui/settings", {"save_history": "off"})
    send(chats, [user("one"), bot(), user("two")], **ref(first))
    chats.call("/api/ui/settings", {"save_history": "on"})
    screen = [user("one"), bot(), user("two"), bot(), user("three")]
    send(chats, screen, **ref(first))
    assert texts(chats, first["chat"]["id"]) == ["one", "Hello", "two", "Hello", "three", "Hello"]


def test_a_turn_whose_save_failed_is_saved_by_the_next_save(chats, monkeypatch):
    first = send(chats, [user("one")], save={"id": None})
    real = chat_store.write_private

    def full(*args):
        raise OSError("full")

    monkeypatch.setattr(chat_store, "write_private", full)
    failed = send(chats, [user("one"), bot(), user("two")], **ref(first))
    assert failed["save_error"] and "chat" not in failed
    monkeypatch.setattr(chat_store, "write_private", real)
    send(chats, [user("one"), bot(), user("two"), bot(), user("three")], **ref(first))
    assert texts(chats, first["chat"]["id"]) == ["one", "Hello", "two", "Hello", "three", "Hello"]


def test_space_around_a_saved_reply_does_not_save_it_twice(chats):
    chats.upstream.script = [chunk(reasoning="hm"), chunk(content="\n\nHello", finish="stop"), DONE]
    first = send(chats, [user("one")], save={"id": None})
    send(chats, [user("one"), bot("Hello"), user("two")], **ref(first))
    assert len(texts(chats, first["chat"]["id"])) == 4


def test_saved_turns_are_never_removed_and_the_new_one_is_always_added(chats):
    first = send(chats, [user("one")], save={"id": None})
    second = send(chats, [user("other")], **ref(first))
    send(chats, [user("one")], **ref(second))
    assert texts(chats, first["chat"]["id"])[::2] == ["one", "other", "one"]


# 3: a reply that was saved but whose "done" never reached the page
def test_a_new_chat_sent_again_is_saved_once(chats):
    send(chats, [user("one")], save={"id": None}, screen="s1")  # its answer is lost
    again = send(chats, [user("one")], save={"id": None}, screen="s1")
    assert len(chats.call("/api/chats")[1]["chats"]) == 1
    assert texts(chats, again["chat"]["id"]) == ["one", "Hello"]


def test_a_saved_chat_is_not_called_changed_after_a_lost_answer(chats):
    first = send(chats, [user("one")], save={"id": None}, screen="s1")
    old = ref(first, "s1")
    send(chats, [user("one"), bot(), user("two")], **old)  # lost
    send(chats, [user("one"), bot(), user("two")], **old)  # lost again
    done = send(chats, [user("one"), bot(), user("two")], **old)
    assert texts(chats, done["chat"]["id"]) == ["one", "Hello", "two", "Hello"]


def test_another_question_after_a_lost_answer_keeps_both(chats):
    send(chats, [user("one")], save={"id": None}, screen="s1")
    done = send(chats, [user("two")], save={"id": None}, screen="s1")
    assert texts(chats, done["chat"]["id"]) == ["one", "Hello", "two", "Hello"]


def test_another_window_is_still_told_the_chat_changed(chats):
    first = send(chats, [user("one")], save={"id": None}, screen="s1")
    send(chats, [user("one"), bot(), user("two")], **ref(first, "s1"))
    body = {"repo": ONE, "messages": [user("x")], **ref(first, "s2")}
    assert chats.call("/api/chat", body)[0] == 409
    send(chats, [user("new")], save={"id": None}, screen="s2")
    send(chats, [user("new")], save={"id": None}, screen="s3")
    assert len(chats.call("/api/chats")[1]["chats"]) == 3


def test_a_lost_chat_that_was_deleted_since_is_made_anew(chats):
    lost = send(chats, [user("one")], save={"id": None}, screen="s1")["chat"]["id"]
    chats.call("/api/chats/delete", {"id": lost, "confirm": True})
    done = send(chats, [user("one")], save={"id": None}, screen="s1")
    assert done["chat"]["id"] != lost


# The page
@needs_node
def test_a_save_warning_stays_until_a_save_succeeds(tmp_path):
    out = ui(
        tmp_path,
        """
        const answers = [stream({ chat: summary() }), stream({ save_error: "Not saved: full." }),
          Promise.reject(new Error("offline")), stream({ chat: summary({ updated: 9 }) })];
        let n = 0;
        routes["/api/chat"] = () => answers[n++];
        const seen = [];
        for (const text of ["a", "b", "c", "d"]) {
          await say(text);
          seen.push([el("#chatSaveError").textContent, el("#chatSaveState").textContent]);
        }
        return seen;
        """,
    )
    assert out[0] == ["", "Saved chat."]
    for error, state in out[1:3]:
        assert error == "Not saved: full." and state.startswith("Not saved yet")
    assert out[3] == ["", "Saved chat."]


@needs_node
def test_turns_not_saved_while_history_was_off_are_said(tmp_path):
    out = ui(
        tmp_path,
        """
        const answers = [stream({ chat: summary() }), stream({})];
        let n = 0;
        routes["/api/chat"] = () => answers[n++];
        await say("a"); paintSaveHistory("off"); await say("b"); paintSaveHistory("on");
        const stale = el("#chatSaveState").textContent;
        newChat();
        return [stale, el("#chatSaveState").textContent];
        """,
    )
    assert out[0].startswith("Not saved yet") and "missing" in out[0]
    assert "missing" not in out[1]


@needs_node
def test_each_chat_on_screen_names_itself(tmp_path):
    out = ui(
        tmp_path,
        """
        routes["/api/chat"] = () => stream({});
        routes["/api/chats/get"] = () => reply(saved());
        await say("a"); await say("b"); newChat(); await say("c");
        await openChat(ID); await say("d");
        return bodies("/api/chat").map((b) => b.screen);
        """,
    )
    assert out[0] and out[0] == out[1]
    assert len({out[0], out[2], out[3]}) == 3
