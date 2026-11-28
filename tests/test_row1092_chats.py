"""Saved chats and per chat settings over the app's own routes (row 1092)."""

# ruff: noqa: F811  (the upstream fixture is imported, then asked for by name)

from __future__ import annotations

import io
import json
import stat
import urllib.error
import urllib.request
import zipfile

import pytest
from test_row837_chat import DONE, ONE, USAGE, chunk, events, post, running, upstream  # noqa: F401

SECRET = "zebra-question-9141"
SYSTEM = "walrus-system-5522"
READ = ("/api/chats", "/api/chats/get?id=" + "a" * 32)
CHANGE = ("search", "rename", "settings", "delete", "delete-all")


@pytest.fixture
def chats(app_module, base_url, upstream):
    running(app_module)
    upstream.script = [chunk(reasoning="hmm"), chunk(content="Hello", finish="stop"), USAGE, DONE]
    return Chats(app_module, base_url, upstream)


class Chats:
    def __init__(self, app, base, upstream):
        self.app, self.base, self.upstream = app, base, upstream
        self.folder = app.APP_DIR / "chats"

    def ask(self, text=SECRET, **extra):
        body = {"repo": ONE, "messages": [{"role": "user", "content": text}], **extra}
        return events(post(self.base, "/api/chat", body), "done")[0][1]

    def new(self, text=SECRET, **extra):
        return self.ask(text, save={"id": None}, **extra)["chat"]

    def call(self, path, body=None):
        try:
            if body is None:
                reply = urllib.request.urlopen(urllib.request.Request(self.base + path), timeout=5)
            else:
                reply = post(self.base, path, body)
            return reply.status, json.loads(reply.read())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read())

    def get(self, chat_id):
        return self.call(f"/api/chats/get?id={chat_id}")


def test_an_answered_turn_is_saved_privately_under_a_server_id(chats):
    saved = chats.new()
    assert len(saved["id"]) == 32 and saved["title"] == SECRET
    path = chats.folder / f"{saved['id']}.json"
    assert stat.S_IMODE(chats.folder.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    status, chat = chats.get(saved["id"])
    assert status == 200 and chat["model"] == ONE
    asked, reply = chat["messages"]
    assert (asked["role"], asked["content"]) == ("user", SECRET)
    assert (reply["content"], reply["thinking"], reply["stopped"]) == ("Hello", "hmm", False)
    assert reply["stats"]["reply_tokens"] == 3


def test_the_next_turn_is_appended_and_a_stale_one_is_refused_before_asking(chats):
    first = chats.new()
    second = chats.ask("again", save={"id": first["id"], "updated": first["updated"]})["chat"]
    assert second["id"] == first["id"] and second["updated"] > first["updated"]
    assert len(chats.get(first["id"])[1]["messages"]) == 4
    asked = len(chats.upstream.requests)
    body = {
        "repo": ONE,
        "messages": [{"role": "user", "content": "old window"}],
        "save": {"id": first["id"], "updated": first["updated"]},
    }
    assert chats.call("/api/chat", body)[0] == 409
    assert len(chats.upstream.requests) == asked


def test_think_tags_in_the_text_are_saved_apart(chats):
    chats.upstream.script = [chunk(content="<think>plan</think>\n\nDone", finish="stop"), DONE]
    reply = chats.get(chats.new()["id"])[1]["messages"][1]
    assert (reply["thinking"], reply["content"]) == ("plan", "Done")


def test_a_reply_with_no_answer_saves_nothing(chats):
    chats.upstream.script = [chunk(reasoning="only thought", finish="stop"), DONE]
    assert "chat" not in chats.ask(save={"id": None})
    assert not chats.folder.exists()


def test_a_temporary_chat_saves_nothing(chats):
    assert "chat" not in chats.ask()
    assert not chats.folder.exists()


def test_history_switched_off_saves_nothing(chats):
    assert chats.call("/api/ui/settings", {"save_history": "off"})[0] == 200
    assert "chat" not in chats.ask(save={"id": None})
    assert not chats.folder.exists()


def test_a_chat_deleted_during_the_reply_stays_deleted(chats, monkeypatch):
    first = chats.new()
    real = chats.app.stream_chat

    def deleting(*args, **kwargs):
        (chats.folder / f"{first['id']}.json").unlink()
        yield from real(*args, **kwargs)

    monkeypatch.setattr(chats.app, "stream_chat", deleting)
    done = chats.ask(save={"id": first["id"], "updated": first["updated"]})
    assert "chat" not in done and done["save_error"]
    assert not list(chats.folder.iterdir())


def test_system_prompt_and_settings_are_sent_and_saved(chats):
    own = {"max_tokens": 128, "temperature": 0.2, "top_p": 0.5}
    saved = chats.new(system=SYSTEM, settings=own)
    sent = chats.upstream.requests[-1][2]
    assert sent["messages"][0] == {"role": "system", "content": SYSTEM}
    assert [m["role"] for m in sent["messages"]] == ["system", "user"]
    assert {key: sent[key] for key in own} == own
    chat = chats.get(saved["id"])[1]
    assert (chat["system"], chat["settings"]) == (SYSTEM, own)
    assert [m["role"] for m in chat["messages"]] == ["user", "assistant"]
    chats.ask("more", save=dict(id=saved["id"], updated=saved["updated"]), system="")
    assert chats.get(saved["id"])[1]["system"] == ""
    assert chats.upstream.requests[-1][2]["messages"][0]["role"] == "user"


@pytest.mark.parametrize("role", ["system", "tool", "developer", ""])
def test_the_browser_may_send_only_user_and_assistant(chats, role):
    body = {"repo": ONE, "messages": [{"role": role, "content": "x"}]}
    assert chats.call("/api/chat", body)[0] == 400
    assert not chats.upstream.requests


@pytest.mark.parametrize(
    "extra",
    [
        {"system": 5},
        {"system": "x" * 8001},
        {"settings": {"model": "other"}},
        {"settings": {"max_tokens": 1}},
        {"settings": []},
        {"save": True},
        {"save": {"id": "../../x", "updated": 1}},
        {"save": {"id": "a" * 32, "updated": 1}},
    ],
)
def test_unusable_extras_are_refused_before_asking(chats, extra):
    body = {"repo": ONE, "messages": [{"role": "user", "content": "x"}], **extra}
    assert chats.call("/api/chat", body)[0] in (400, 404)
    assert not chats.upstream.requests
    assert chats.app.STATE.generating is False


def test_list_search_rename_delete_and_delete_all(chats):
    one, two = chats.new("apples"), chats.new("pears")
    listed = chats.call("/api/chats")[1]
    assert {c["id"] for c in listed["chats"]} == {one["id"], two["id"]}
    assert "messages" not in listed["chats"][0] and listed["broken"] == []
    found = chats.call("/api/chats/search", {"query": "APPL"})[1]["chats"]
    assert [c["id"] for c in found] == [one["id"]]
    status, renamed = chats.call("/api/chats/rename", {"id": one["id"], "title": "Fruit"})
    assert status == 200 and renamed["title"] == "Fruit" and "messages" not in renamed
    assert chats.call("/api/chats/delete", {"id": one["id"]})[0] == 400
    assert chats.call("/api/chats/delete", {"id": one["id"], "confirm": True})[0] == 200
    assert chats.get(one["id"])[0] == 404
    assert chats.call("/api/chats/delete-all", {})[0] == 400
    answer = chats.call("/api/chats/delete-all", {"confirm": True})
    assert answer == (200, {"deleted": 1, "failed": 0})
    assert chats.call("/api/chats")[1]["chats"] == []


def test_chat_settings_can_change_without_a_message(chats):
    saved = chats.new()
    body = {"id": saved["id"], "updated": saved["updated"], "system": SYSTEM, "settings": {}}
    status, changed = chats.call("/api/chats/settings", body)
    assert status == 200 and changed["updated"] > saved["updated"]
    assert chats.get(saved["id"])[1]["system"] == SYSTEM
    assert chats.call("/api/chats/settings", body)[0] == 409


def test_a_broken_file_is_named_and_kept(chats):
    chats.new()
    broken = chats.folder / f"{'b' * 32}.json"
    broken.write_text("{")
    assert [b["id"] for b in chats.call("/api/chats")[1]["broken"]] == ["b" * 32]
    assert chats.get("b" * 32)[0] == 422 and broken.exists()


@pytest.mark.parametrize("bad", ["", "..%2F..%2Fsettings", "A" * 32, "a" * 31])
def test_hostile_ids_find_nothing(chats, bad):
    assert chats.get(bad)[0] == 404
    assert chats.call("/api/chats/delete", {"id": bad, "confirm": True})[0] == 404


@pytest.mark.parametrize("path", [*READ, *(f"/api/chats/{name}" for name in CHANGE)])
@pytest.mark.parametrize("token", [None, "wrong"])
def test_every_chat_route_needs_the_token(server, app_module, path, token):
    # base_url is not used: it adds the token by itself.
    host, port = server.server_address[:2]
    headers = {"Content-Type": "application/json"}
    if token:
        headers[app_module.TOKEN_HEADER] = token
    data = None if path in READ else b'{"confirm": true}'
    request = urllib.request.Request(f"http://{host}:{port}{path}", data=data, headers=headers)
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=5)
    assert caught.value.code == 403


def test_chat_text_stays_out_of_logs_and_diagnostics(chats):
    saved = chats.new(system=SYSTEM)
    chats.call("/api/chats/search", {"query": SECRET})
    chats.call("/api/chats/rename", {"id": saved["id"], "title": "x" * 500 + SECRET})
    chats.call("/api/chats/settings", {"id": saved["id"], "updated": 1, "system": SYSTEM})
    bundle, _ = chats.app.build_diagnostic_bundle()
    with zipfile.ZipFile(io.BytesIO(bundle)) as archive:
        assert not [n for n in archive.namelist() if saved["id"] in n or "chats" in n]
        everything = b"".join(archive.read(n) for n in archive.namelist())
    for path in chats.app.LOG_DIR.rglob("*"):
        if path.is_file():
            everything += path.read_bytes()
    assert SECRET.encode() not in everything and SYSTEM.encode() not in everything
