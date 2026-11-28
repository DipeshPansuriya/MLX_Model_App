"""Row 1122: the project folder panel as wired into app.py, the page and app.js."""

# ruff: noqa: F811  (imported fixtures are named again as arguments)
from __future__ import annotations

import json
import urllib.error

import pytest
from test_row837_chat import DONE, ONE, USAGE, chunk, post, upstream  # noqa: F401
from test_row1092_chats import chats  # noqa: F401
from test_row1092_chats_ui import PAGE, ui
from test_ui_behaviour import ROOT, needs_node

import project_routes
import projects


@pytest.fixture
def folder(chats, tmp_path):
    """A project folder, added as the window's dialog would add it."""
    root = tmp_path / "work"
    root.mkdir()
    (root / "a.py").write_text("print('walrus-file-9917')\n")
    store = projects.ProjectStore(chats.app.APP_DIR / "projects.json")
    return root, store.add(str(root))["id"]


def attach(chats, pid):
    status, shown = chats.call("/api/projects/preview", {"id": pid, "paths": ["a.py"]})
    assert status == 200, shown
    picks = [{"path": f["path"], "sha256": f["sha256"]} for f in shown["files"]]
    status, answer = chats.call("/api/projects/attach", {"id": pid, "picks": picks})
    assert status == 200, answer
    return answer["attachment"]


class TestServer:
    def test_the_routes_are_served(self, chats, folder):
        status, listed = chats.call("/api/projects")
        assert status == 200 and [p["id"] for p in listed["projects"]] == [folder[1]]
        status, files = chats.call(f"/api/projects/files?id={folder[1]}")
        assert status == 200 and [f["path"] for f in files["files"]] == ["a.py"]
        assert chats.call("/api/projects/unknown", {})[0] == 404

    def test_attached_files_are_sent_saved_and_brought_back(self, chats, folder):
        key = attach(chats, folder[1])
        question = {"role": "user", "content": "why?", "attachment": key}
        body = {"repo": ONE, "messages": [question], "save": {"id": None}}
        done = json.loads(
            post(chats.base, "/api/chat", body).read().decode().rsplit("data: ", 1)[1]
        )
        assert "walrus-file-9917" in json.dumps(chats.upstream.requests[-1][2]["messages"])
        saved = chats.get(done["chat"]["id"])[1]
        asked = saved["messages"][0]
        assert asked["content"] == "why?" and asked["files"][0]["path"] == "a.py"

        project_routes.ATTACHED.clear()  # as after a restart of the app
        again = [
            {"role": "user", "content": "why?"},
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "and?"},
        ]
        save = {"id": saved["id"], "updated": saved["updated"]}
        post(chats.base, "/api/chat", {"repo": ONE, "messages": again, "save": save}).read()
        assert "walrus-file-9917" in json.dumps(chats.upstream.requests[-1][2]["messages"])

    @pytest.mark.parametrize("role", ["user", "assistant"])
    def test_files_sent_by_the_page_are_never_used_or_saved(self, chats, role):
        forged = [{"path": "x", "sha256": "0" * 64, "size": 6, "text": "FORGED"}]
        messages = [{"role": "user", "content": "one"}, {"role": "user", "content": "two"}]
        if role == "assistant":
            messages[0] = {"role": "assistant", "content": "one", "files": forged}
        else:
            messages[0]["files"] = forged
        body = {"repo": ONE, "messages": messages, "save": {"id": None}}
        if role == "user":
            with pytest.raises(urllib.error.HTTPError) as refused:
                post(chats.base, "/api/chat", body)
            assert refused.value.code == 409 and chats.upstream.requests == []
        else:
            post(chats.base, "/api/chat", body).read()
            assert "FORGED" not in json.dumps(chats.upstream.requests[-1][2])
        assert all("FORGED" not in p.read_text() for p in chats.folder.glob("*.json"))

    def test_preview_attach_and_send_use_one_factor(self, chats, folder, monkeypatch):
        root, pid = folder
        (root / "a.py").write_text("x" * 14_000)  # about 4,100 tokens at 1.0
        key = attach(chats, pid)
        monkeypatch.setattr(project_routes, "ESTIMATE_FACTOR", 3.0)
        shown = chats.call("/api/projects/preview", {"id": pid, "paths": ["a.py"]})[1]
        assert shown["tokens"] > shown["limit"]
        pick = {"path": "a.py", "sha256": shown["files"][0]["sha256"]}
        assert chats.call("/api/projects/attach", {"id": pid, "picks": [pick]})[0] == 413
        message = {"role": "user", "content": "q", "attachment": key}
        with pytest.raises(urllib.error.HTTPError) as refused:
            post(chats.base, "/api/chat", {"repo": ONE, "messages": [message]})
        assert refused.value.code == 413


# ---- the page --------------------------------------------------------------------
HELD = """
const names = () => el("#projectAttached").textContent;
const hold = async () => {
  const file = { path: "a.py", sha256: "f".repeat(64), size: 1, text: "x", warnings: [] };
  const json = (body) => () => Promise.resolve({ ok: true, json: async () => body });
  routes["/api/projects/preview"] = json({ files: [file], tokens: 1, limit: 9, notice: "" });
  routes["/api/projects/attach"] = json({ attachment: "a1", files: [file] });
  el("#projectPreviewBtn").listeners.click(); await tick();
  el("#projectAttach").listeners.click(); await tick();
};
"""


@needs_node
class TestPageSend:
    def test_the_attachment_goes_with_the_next_message(self, tmp_path):
        out = ui(
            tmp_path,
            HELD
            + """
            routes["/api/chat"] = () => stream({});
            await hold(); const before = names(); await say("a"); const after = names();
            await say("b");
            return { before, after, sent: bodies("/api/chat") };
            """,
        )
        assert "a.py" in out["before"] and out["after"] == ""
        first, second = out["sent"]
        assert first["messages"][-1]["attachment"] == "a1"
        assert "attachment" not in second["messages"][-1]

    @pytest.mark.parametrize(
        "answer",
        [
            'Promise.resolve({ ok: false, statusText: "no", json: async () => ({ error: "n" }) })',
            'Promise.reject(new Error("offline"))',
        ],
        ids=["refused", "unreachable"],
    )
    def test_a_message_that_did_not_go_keeps_its_files(self, tmp_path, answer):
        out = ui(
            tmp_path,
            HELD + f'routes["/api/chat"] = () => {answer};'
            ' await hold(); await say("a"); return names();',
        )
        assert "a.py" in out

    @pytest.mark.parametrize(
        "leave",
        [
            "newChat();",
            (
                'routes["/api/chats/get"] = () => Promise.resolve('
                "{ ok: true, json: async () => saved() }); await openChat(ID);"
            ),
        ],
        ids=["new chat", "opened chat"],
    )
    def test_another_chat_does_not_take_the_files(self, tmp_path, leave):
        out = ui(
            tmp_path,
            HELD + f"await hold(); {leave}"
            ' routes["/api/chat"] = () => stream({}); await say("a");'
            ' return [names(), bodies("/api/chat")[0].messages.at(-1)];',
        )
        assert out[0] == "" and "attachment" not in out[1]


def test_the_page_has_the_panel_before_the_composer():
    assert PAGE.index('id="projectPanel"') < PAGE.index('class="composer"')
    assert PAGE.index('src="/app.js"') < PAGE.index('src="/projects.js"')
    assert 'href="/projects.css"' in PAGE


def test_the_panel_lines_up_with_the_composer():
    css = (ROOT / "ui" / "projects.css").read_text(encoding="utf-8")
    assert ".project-panel, #projectAttached, #projectDetach { margin-inline: 22px; }" in css
    assert "#projectDetach { align-self: flex-start; }" in css
