"""Row 1142: messages that carry files name them and say they go again (row 1139, N1)."""

from __future__ import annotations

from test_row1092_chats_ui import ui
from test_ui_behaviour import needs_node

NOTES = """
const notes = () => el("#messages").children.map((m) => m.children.map((c) => c.textContent));
"""


@needs_node
def test_a_reopened_chat_names_its_files_and_says_they_go_again(tmp_path):
    out = ui(
        tmp_path,
        NOTES
        + """
        const files = [{ path: "src/<a>.py", text: "SECRET-TEXT" }, { path: "b.txt", text: "x" }];
        routes["/api/chats/get"] = () => reply(saved({ messages: [
          { role: "user", content: "why", files }, { role: "assistant", content: "A" },
          { role: "user", content: "and" }, { role: "assistant", content: "B" }] }));
        await openChat(ID);
        return { notes: notes(), said: el("#chatAnnounce").textContent, sent: state.messages };
        """,
    )
    first, plain = out["notes"][0], out["notes"][2]
    assert len(first) == 1 and "src/<a>.py, b.txt" in first[0] and "sent again" in first[0]
    assert plain == [] and "SECRET-TEXT" not in str(out["notes"])
    assert "sent again" in out["said"]
    assert all(set(m) == {"role", "content"} for m in out["sent"])


@needs_node
def test_a_reopened_chat_without_files_says_nothing_about_them(tmp_path):
    out = ui(
        tmp_path,
        """
        routes["/api/chats/get"] = () => reply(saved());
        await openChat(ID);
        return el("#chatAnnounce").textContent;
        """,
    )
    assert out == "Opened chat: First"


@needs_node
def test_an_answered_message_with_files_names_them_and_a_failed_one_does_not(tmp_path):
    out = ui(
        tmp_path,
        NOTES
        + """
        ProjectsUI.take = () => "a1";
        ProjectsUI.names = () => ["src/a.py"];
        routes["/api/chat"] = () => stream({});
        await say("why");
        routes["/api/chat"] = () => Promise.reject(new Error("offline"));
        await say("again");
        return notes();
        """,
    )
    assert len(out[0]) == 1 and "src/a.py" in out[0][0] and "sent again" in out[0][0]
    assert out[2] == []
