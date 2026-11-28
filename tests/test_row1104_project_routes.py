"""Project folder routes (row 1104): native picker only, preview, attach from snapshot."""

from __future__ import annotations

import sys
import types

import pytest

import chat_store
import engine_runtime
import project_routes
import projects


@pytest.fixture
def folder(tmp_path):
    root = tmp_path / "work"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.py").write_text("print('a')\n")
    (root / "big.txt").write_text("x" * 40_000)
    (root / ".env").write_text("TOKEN=abcdefgh12345")
    return root


class Window:
    def __init__(self, picked):
        self.picked, self.asked = picked, 0

    def create_file_dialog(self, kind, **kwargs):
        self.asked += 1
        return self.picked


@pytest.fixture
def app(app_module, monkeypatch, folder):
    """The app with its own window, and a running server that owns its port."""
    # The dialog is the window's; the library gives only the kind to ask for.
    fake = types.SimpleNamespace(FileDialog=types.SimpleNamespace(FOLDER=20))
    monkeypatch.setitem(sys.modules, "webview", fake)
    app_module.STATE.window = Window((str(folder),))
    app_module.STATE.server_pid = 4321
    app_module.checks = []
    app_module.owner = True

    def headers():
        app_module.checks.append(True)
        if not app_module.owner:
            raise engine_runtime.Refused("Nothing was sent: another program may listen.")
        return {}

    monkeypatch.setattr(app_module, "_chat_headers", headers)
    return app_module


def post(app, name, body):
    return project_routes.handle_post(app, name, body)


def added(app):
    code, answer = post(app, "choose", {})
    assert code == 200, answer
    return answer["project"]["id"]


def picks(app, pid, *paths):
    code, shown = post(app, "preview", {"id": pid, "paths": list(paths)})
    assert code == 200, shown
    return [{"path": f["path"], "sha256": f["sha256"]} for f in shown["files"]]


def attached(app, pid, *paths):
    code, answer = post(app, "attach", {"id": pid, "picks": picks(app, pid, *paths)})
    assert code == 200, answer
    return answer


def ask(text, **more):
    return {"role": "user", "content": text, **more}


def test_the_dialog_alone_chooses_the_folder(app, folder, tmp_path):
    for body in ({"path": str(tmp_path)}, {"root": "/"}, {"folder": str(folder)}):
        assert post(app, "choose", body)[0] == 400
    assert app.STATE.window.asked == 0
    pid = added(app)
    code, view = project_routes.handle_get(app, "/api/projects", {})
    assert code == 200 and view["picker"] is True
    assert [(p["id"], p["name"]) for p in view["projects"]] == [(pid, "work")]
    assert (app.APP_DIR / "projects.json").is_file()


def test_a_cancelled_dialog_adds_nothing(app):
    app.STATE.window = Window(None)
    assert post(app, "choose", {}) == (200, {"cancelled": True})
    assert project_routes.handle_get(app, "/api/projects", {})[1]["projects"] == []


def test_browser_mode_says_there_is_no_picker(app):
    app.STATE.window = None
    view = project_routes.handle_get(app, "/api/projects", {})[1]
    assert view["picker"] is False and "no folder picker" in view["picker_reason"]
    code, answer = post(app, "choose", {})
    assert code == 409 and "no folder picker" in answer["error"]


def test_a_second_dialog_waits_for_the_first(app):
    with project_routes.DIALOG_LOCK:
        assert post(app, "choose", {})[0] == 409
    assert app.STATE.window.asked == 0


def test_a_dialog_that_fails_is_said(app):
    app.STATE.window.create_file_dialog = lambda *a, **k: 1 / 0
    code, answer = post(app, "choose", {})
    assert code == 500 and "dialog" in answer["error"]


def test_list_and_preview_show_only_what_may_be_read(app):
    pid = added(app)
    code, listing = project_routes.handle_get(app, "/api/projects/files", {"id": [pid]})
    assert code == 200
    assert [f["path"] for f in listing["files"]] == ["big.txt", "src/a.py"]
    code, shown = post(app, "preview", {"id": pid, "paths": ["src/a.py"]})
    assert shown["files"][0]["text"] == "print('a')\n"
    assert shown["notice"] == projects.SERVER_WARNING
    code, answer = post(app, "preview", {"id": pid, "paths": [".env"]})
    assert code == 403 and answer["path"] == ".env"
    assert post(app, "preview", {"id": pid, "paths": ["../x"]})[0] == 403


def test_attach_checks_the_port_owner_first(app):
    pid = added(app)
    chosen = picks(app, pid, "src/a.py")
    app.owner = False
    code, answer = post(app, "attach", {"id": pid, "picks": chosen})
    assert code == 409 and "Nothing was sent" in answer["error"]
    app.owner, app.STATE.server_pid = True, None
    code, answer = post(app, "attach", {"id": pid, "picks": chosen})
    assert code == 409 and "Start the API server" in answer["error"]


def test_attach_gives_an_id_and_no_text(app, folder):
    pid = added(app)
    answer = attached(app, pid, "src/a.py")
    assert answer["files"] == [
        {"path": "src/a.py", "size": 11, "sha256": answer["files"][0]["sha256"]}
    ]
    assert len(answer["attachment"]) == 32 and "print" not in str(answer)


def test_a_changed_file_and_files_over_the_limit_are_refused(app, folder):
    pid = added(app)
    chosen = picks(app, pid, "src/a.py")
    (folder / "src" / "a.py").write_text("changed\n")
    code, answer = post(app, "attach", {"id": pid, "picks": chosen})
    assert code == 409 and answer["path"] == "src/a.py"
    code, answer = post(app, "attach", {"id": pid, "picks": picks(app, pid, "big.txt")})
    assert code == 413 and "limit" in answer["error"]
    assert post(app, "attach", {"id": pid, "picks": []})[0] == 400


def test_a_message_gets_the_snapshot_and_only_that(app, folder):
    pid = added(app)
    answer = attached(app, pid, "src/a.py")
    (folder / "src" / "a.py").write_text("later\n")  # what was previewed goes out
    forged = [{"path": "x", "sha256": "0" * 64, "size": 1, "text": "FORGED"}]
    messages = [ask("why?", attachment=answer["attachment"])]
    checks = len(app.checks)
    send, keep, own = project_routes.prepare(app, "r/m", messages, "be brief", {})
    assert len(app.checks) == checks + 1  # the port owner again, before the send
    assert [m["role"] for m in send] == ["user"]
    assert "print('a')" in send[0]["content"] and projects.DATA_NOTE in send[0]["content"]
    assert send[0]["content"].endswith("why?")
    assert keep[0]["content"] == "why?" and keep[0]["files"][0]["text"] == "print('a')\n"
    assert own["max_tokens"] == app.chat_settings("r/m")["max_tokens"]
    with pytest.raises(chat_store.ChatError, match="attach"):
        project_routes.prepare(app, "r/m", [ask("hi", files=forged)], "", {})
    app.owner = False
    with pytest.raises(chat_store.ChatError, match="Nothing was sent"):
        project_routes.prepare(app, "r/m", messages, "", {})


def test_a_chat_without_files_is_left_as_it_was(app):
    app.STATE.server_pid = None  # the chat route says so itself
    messages = [ask("hi")]
    assert project_routes.prepare(app, "r/m", messages, "s", {"top_p": 0.5}) == (
        messages,
        messages,
        {"top_p": 0.5},
    )
    assert app.checks == []


def test_a_saved_chat_brings_its_own_files_back(app):
    pid = added(app)
    first = attached(app, pid, "src/a.py")
    _, keep, _ = project_routes.prepare(
        app, "r/m", [ask("one", attachment=first["attachment"])], "", {}
    )
    chat = app.saved_chats().create(
        [*keep, {"role": "assistant", "content": "ok"}], model="r/m", system="", settings={}
    )
    project_routes.ATTACHED.clear()  # as after a restart of the app
    saved = project_routes.saved_messages(app, {"save": {"id": chat["id"]}})
    messages = [
        ask("one", attachment=first["attachment"], files=[{"text": "FORGED"}]),
        {"role": "assistant", "content": "ok"},
        ask("two"),
    ]
    send, keep, _ = project_routes.prepare(app, "r/m", messages, "", {}, saved)
    assert "print('a')" in send[0]["content"] and "FORGED" not in str(send)
    assert keep[0]["files"][0]["path"] == "src/a.py" and "files" not in keep[2]
    with pytest.raises(chat_store.ChatError, match="attach"):
        project_routes.prepare(app, "r/m", messages, "", {})
    assert project_routes.saved_messages(app, {"save": {"id": "nonsense"}}) == []
    assert project_routes.saved_messages(app, {}) == []


def test_remove_needs_a_known_project_and_keeps_the_folder(app, folder):
    pid = added(app)
    assert post(app, "remove", {"id": pid}) == (200, {"ok": True})
    assert post(app, "remove", {"id": pid})[0] == 404
    assert folder.is_dir()
    assert post(app, "unknown", {})[0] == 404
    assert project_routes.handle_get(app, "/api/projects/nothing", {})[0] == 404
