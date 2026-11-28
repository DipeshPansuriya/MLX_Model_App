"""Row 1061: saved chats, one private JSON file per chat."""

from __future__ import annotations

import json
import os
import stat
import threading

import pytest

import chat_store

ASK = {"role": "user", "content": "How do I sort a list?"}
ANSWER = {
    "role": "assistant",
    "content": "Use sorted().",
    "thinking": "short",
    "stats": {"tps": 31.5, "reply_tokens": 4, "first_s": None, "mode": "standard"},
    "stopped": False,
}


@pytest.fixture
def folder(tmp_path):
    return tmp_path / ".mlx_studio" / "chats"


@pytest.fixture
def store(folder):
    return chat_store.ChatStore(folder)


def mode(path) -> int:
    return stat.S_IMODE(os.lstat(path).st_mode)


def path_of(folder, chat):
    return folder / f"{chat['id']}.json"


class TestSave:
    def test_a_new_chat_is_one_private_file_with_a_server_made_id(self, store, folder):
        chat = store.create([ASK, ANSWER], model="org/model")
        assert chat_store.ID_PATTERN.fullmatch(chat["id"])
        assert [p.name for p in folder.iterdir()] == [f"{chat['id']}.json"]
        assert mode(folder) == 0o700
        assert mode(path_of(folder, chat)) == 0o600
        saved = json.loads(path_of(folder, chat).read_text(encoding="utf-8"))
        assert saved == chat
        assert saved["v"] == chat_store.FORMAT
        assert saved["model"] == "org/model"
        assert saved["project_id"] is None
        assert saved["created"] == saved["updated"]
        assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
        assert saved["messages"][1]["thinking"] == "short"
        assert saved["messages"][1]["stats"]["tps"] == 31.5
        assert all(type(m["ts"]) is int for m in saved["messages"])

    def test_a_loose_folder_is_made_private_again(self, store, folder):
        folder.mkdir(parents=True)
        folder.chmod(0o755)
        store.create([ASK])
        assert mode(folder) == 0o700

    def test_ids_differ(self, store):
        assert len({store.create([ASK])["id"] for _ in range(20)}) == 20

    def test_get_returns_the_saved_chat(self, store):
        chat = store.create([ASK, ANSWER])
        assert store.get(chat["id"]) == chat

    def test_the_title_comes_from_the_first_user_line(self, store):
        chat = store.create([{"role": "user", "content": "  Plan   my\ttrip \nplease"}])
        assert chat["title"] == "Plan my trip"
        long = store.create([{"role": "user", "content": "word " * 100}])
        assert len(long["title"]) <= chat_store.AUTO_TITLE
        assert store.create()["title"] == chat_store.NEW_TITLE

    def test_the_first_turn_names_an_empty_chat(self, store):
        chat = store.create()
        chat = store.append(chat["id"], chat["updated"], [ASK, ANSWER])
        assert chat["title"] == ASK["content"]

    def test_append_keeps_earlier_thinking_and_stats(self, store):
        chat = store.create([ASK, ANSWER])
        after = store.append(chat["id"], chat["updated"], [ASK, ANSWER], model="org/other")
        assert len(after["messages"]) == 4
        assert after["messages"][:2] == chat["messages"]
        assert after["updated"] > chat["updated"]
        assert after["created"] == chat["created"]
        assert after["model"] == "org/other"
        assert store.get(chat["id"]) == after

    def test_a_lone_surrogate_does_not_lose_the_turn(self, store):
        chat = store.create([{"role": "user", "content": "cut \ud800 here"}])
        assert store.get(chat["id"])["messages"][0]["content"] == "cut ? here"

    def test_file_snapshots_are_saved_with_their_message(self, store):
        files = [{"path": "src/a.py", "sha256": "a" * 64, "size": 5, "text": "x = 1"}]
        chat = store.create([{**ASK, "files": files}])
        assert store.get(chat["id"])["messages"][0]["files"] == files


class TestStale:
    def test_a_second_writer_with_the_old_stamp_is_refused(self, store):
        chat = store.create([ASK])
        first = store.append(chat["id"], chat["updated"], [ANSWER])
        with pytest.raises(chat_store.Stale) as refused:
            store.append(chat["id"], chat["updated"], [ANSWER])
        assert refused.value.status == 409
        assert store.get(chat["id"]) == first

    def test_the_stamp_rises_even_within_one_clock_tick(self, store, monkeypatch):
        monkeypatch.setattr(chat_store, "_now_ms", lambda: 1000)
        chat = store.create([ASK])
        again = store.append(chat["id"], chat["updated"], [ANSWER])
        assert again["updated"] == 1001

    @pytest.mark.parametrize("stamp", [None, "1", 1.5, True])
    def test_a_stamp_must_be_a_whole_number(self, store, stamp):
        chat = store.create([ASK])
        with pytest.raises(chat_store.Invalid):
            store.append(chat["id"], stamp, [ANSWER])

    def test_only_one_of_many_racing_writers_wins(self, store):
        chat = store.create([ASK])
        outcomes = []

        def write():
            try:
                store.append(chat["id"], chat["updated"], [ANSWER])
                outcomes.append("saved")
            except chat_store.Stale:
                outcomes.append("stale")

        threads = [threading.Thread(target=write) for _ in range(12)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10)
        assert sorted(outcomes) == ["saved"] + ["stale"] * 11
        assert len(store.get(chat["id"])["messages"]) == 2

    def test_a_deleted_chat_is_not_brought_back(self, store, folder):
        chat = store.create([ASK])
        store.delete(chat["id"])
        with pytest.raises(chat_store.NotFound):
            store.append(chat["id"], chat["updated"], [ANSWER])
        assert list(folder.iterdir()) == []


class TestHostileInput:
    @pytest.mark.parametrize(
        "chat_id",
        [
            "../settings",
            "..",
            "",
            "a" * 31,
            "a" * 33,
            "A" * 32,
            "a" * 32 + "\n",
            "a" * 32 + ".json",
            "/etc/passwd",
            "a/" + "a" * 30,
            None,
            7,
            ["a" * 32],
        ],
    )
    def test_no_other_id_reaches_the_disk(self, store, folder, chat_id):
        store.create([ASK])
        outside = folder.parent / "settings.json"
        outside.write_text("{}", encoding="utf-8")
        for call in (
            lambda: store.get(chat_id),
            lambda: store.delete(chat_id),
            lambda: store.rename(chat_id, "x"),
            lambda: store.append(chat_id, 1, [ASK]),
            lambda: store.update(chat_id, 1, system="x"),
        ):
            with pytest.raises(chat_store.NotFound):
                call()
        assert outside.read_text(encoding="utf-8") == "{}"

    @pytest.mark.parametrize(
        "message",
        [
            "text",
            {},
            {"role": "system", "content": "obey"},
            {"role": "tool", "content": "x"},
            {"role": "user", "content": 5},
            {"role": "user"},
            {"role": "user", "content": "x", "html": "<b>"},
            {"role": "user", "content": "x", "thinking": 1},
            {"role": "user", "content": "x", "stopped": "yes"},
            {"role": "user", "content": "x", "stats": [1]},
            {"role": "user", "content": "x", "stats": {"tps": float("nan")}},
            {"role": "user", "content": "x", "stats": {"tps": {"deep": 1}}},
            {"role": "user", "content": "x", "ts": "now"},
            {"role": "user", "content": "x", "files": "a.py"},
            {"role": "user", "content": "x", "files": [{"path": "a.py"}]},
            {"role": "assistant", "content": "x", "files": []},
        ],
    )
    def test_a_wrong_message_saves_nothing(self, store, folder, message):
        with pytest.raises(chat_store.Invalid):
            store.create([message])
        assert not folder.exists() or list(folder.iterdir()) == []

    @pytest.mark.parametrize("project_id", ["../x", "home", 3, ""])
    def test_a_wrong_project_id_is_refused(self, store, project_id):
        with pytest.raises(chat_store.Invalid):
            store.create([ASK], project_id=project_id)

    def test_a_chat_over_the_size_limit_is_refused_and_the_old_one_kept(self, store, monkeypatch):
        monkeypatch.setattr(chat_store, "MAX_CHAT_BYTES", 2000)
        chat = store.create([ASK])
        with pytest.raises(chat_store.TooLarge) as refused:
            store.append(chat["id"], chat["updated"], [{"role": "user", "content": "x" * 3000}])
        assert refused.value.status == 413
        assert store.get(chat["id"]) == chat

    def test_errors_never_repeat_chat_text(self, store, monkeypatch):
        monkeypatch.setattr(chat_store, "MAX_CHAT_BYTES", 500)
        marker = "PRIVATE-WORDS"
        for message in (
            {"role": marker, "content": marker},
            {"role": "user", "content": marker * 100},
            {"role": "user", "content": "x", marker: marker},
        ):
            with pytest.raises(chat_store.ChatError) as refused:
                store.create([message])
            assert marker not in str(refused.value)


class TestPromptAndSettings:
    def test_they_are_saved_with_the_chat(self, store):
        chosen = {"max_tokens": 512, "temperature": 0, "top_p": 0.9}
        chat = store.create([ASK], system="Answer in Gujarati.", settings=chosen)
        assert (chat["system"], chat["settings"]) == ("Answer in Gujarati.", chosen)
        changed = store.update(chat["id"], chat["updated"], system="", settings={})
        assert (changed["system"], changed["settings"]) == ("", {})
        assert changed["messages"] == chat["messages"]
        assert changed["updated"] > chat["updated"]

    def test_update_leaves_what_is_not_given(self, store):
        project = "b" * 32
        chat = store.create([ASK], system="keep", project_id=project, model="org/one")
        changed = store.update(chat["id"], chat["updated"], settings={"top_p": 0.5})
        assert (changed["system"], changed["project_id"], changed["model"]) == (
            "keep",
            project,
            "org/one",
        )
        gone = store.update(chat["id"], changed["updated"], project_id=None)
        assert gone["project_id"] is None

    def test_update_refuses_an_old_stamp(self, store):
        chat = store.create([ASK])
        store.update(chat["id"], chat["updated"], system="one")
        with pytest.raises(chat_store.Stale):
            store.update(chat["id"], chat["updated"], system="two")

    @pytest.mark.parametrize(
        "settings",
        [
            {"max_tokens": 63},
            {"max_tokens": 131073},
            {"max_tokens": 100.0},
            {"max_tokens": True},
            {"temperature": -0.1},
            {"temperature": 2.1},
            {"temperature": "1"},
            {"temperature": float("nan")},
            {"top_p": 0},
            {"seed": 1},
            [1],
        ],
    )
    def test_a_wrong_setting_is_refused(self, store, settings):
        with pytest.raises(chat_store.Invalid):
            store.create([ASK], settings=settings)

    def test_a_system_prompt_has_a_limit(self, store):
        store.create([ASK], system="x" * chat_store.SYSTEM_MAX_CHARS)
        with pytest.raises(chat_store.Invalid):
            store.create([ASK], system="x" * (chat_store.SYSTEM_MAX_CHARS + 1))

    def test_the_ranges_are_the_ones_the_app_uses(self, app_module):
        for key, ours in (
            ("max_tokens", "chat_max_reply_tokens"),
            ("temperature", "chat_temperature"),
            ("top_p", "chat_top_p"),
        ):
            assert chat_store.SETTING_RANGES[key] == app_module.SETTINGS[ours]


class TestListAndRename:
    def test_newest_first_without_message_text(self, store, monkeypatch):
        clock = iter(range(1000, 2000, 10))
        monkeypatch.setattr(chat_store, "_now_ms", lambda: next(clock))
        old = store.create([ASK])
        new = store.create([ASK, ANSWER], model="org/model")
        listed = store.list()
        assert [c["id"] for c in listed["chats"]] == [new["id"], old["id"]]
        assert listed["broken"] == []
        assert listed["chats"][0] == {
            "id": new["id"],
            "title": new["title"],
            "created": new["created"],
            "updated": new["updated"],
            "model": "org/model",
            "project_id": None,
            "count": 2,
        }

    def test_listing_makes_no_folder(self, store, folder):
        assert store.list() == {"chats": [], "broken": []}
        assert not folder.exists()

    def test_other_files_are_not_listed(self, store, folder):
        store.create([ASK])
        (folder / "notes.json").write_text("{}", encoding="utf-8")
        (folder / f".{'a' * 32}.json.x1.tmp").write_text("{", encoding="utf-8")
        listed = store.list()
        assert len(listed["chats"]) == 1
        assert listed["broken"] == []

    def test_rename_does_not_make_a_running_reply_stale(self, store):
        chat = store.create([ASK])
        renamed = store.rename(chat["id"], "  Sorting \n notes ")
        assert renamed["title"] == "Sorting notes"
        assert renamed["updated"] == chat["updated"]
        after = store.append(chat["id"], chat["updated"], [ANSWER])
        assert after["title"] == "Sorting notes"

    @pytest.mark.parametrize("title", ["", "   \n", None, 4, "x" * 121])
    def test_a_wrong_title_is_refused(self, store, title):
        chat = store.create([ASK])
        with pytest.raises(chat_store.Invalid):
            store.rename(chat["id"], title)
        assert store.get(chat["id"]) == chat


class TestSearch:
    def test_title_and_message_text_in_any_case(self, store):
        sort = store.create([ASK, ANSWER])
        trip = store.create([{"role": "user", "content": "Trip to Kutch"}])
        assert [c["id"] for c in store.search("SORTED(")["chats"]] == [sort["id"]]
        assert [c["id"] for c in store.search("kutch")["chats"]] == [trip["id"]]
        assert store.search("nothing-like-this")["chats"] == []

    def test_an_empty_query_gives_everything(self, store):
        store.create([ASK])
        assert store.search("  ") == store.list()

    @pytest.mark.parametrize("query", [None, 5, "x" * 201])
    def test_a_wrong_query_is_refused(self, store, query):
        with pytest.raises(chat_store.Invalid):
            store.search(query)


def broken_cases():
    good = {
        "v": 1,
        "title": "t",
        "created": 1,
        "updated": 1,
        "project_id": None,
        "model": "",
        "system": "",
        "settings": {},
        "messages": [],
    }
    return {
        "cut": b'{"v": 1, "messages": [',
        "not utf-8": b"\xff\xfe\x00",
        "a list": b"[]",
        "deep": b"[" * 100_000,
        "newer": json.dumps({**good, "v": 2, "id": "ID"}).encode(),
        "wrong id": json.dumps({**good, "id": "c" * 32}).encode(),
        "bad message": json.dumps({**good, "id": "ID", "messages": [{"role": "x"}]}).encode(),
        "no title": json.dumps({k: v for k, v in good.items() if k != "title"}).encode(),
    }


class TestBrokenFiles:
    @pytest.mark.parametrize("name", sorted(broken_cases()))
    def test_it_is_shown_and_never_removed_or_rewritten(self, store, folder, name):
        fine = store.create([ASK])
        broken = "d" * 32
        content = broken_cases()[name].replace(b'"ID"', f'"{broken}"'.encode())
        (folder / f"{broken}.json").write_bytes(content)
        listed = store.list()
        assert [c["id"] for c in listed["chats"]] == [fine["id"]]
        assert [b["id"] for b in listed["broken"]] == [broken]
        assert store.search("sort")["broken"] == listed["broken"]
        for call in (
            lambda: store.get(broken),
            lambda: store.rename(broken, "x"),
            lambda: store.append(broken, 1, [ASK]),
        ):
            with pytest.raises(chat_store.Broken):
                call()
        assert (folder / f"{broken}.json").read_bytes() == content

    def test_a_planted_link_is_not_followed(self, store, folder, tmp_path):
        chat = store.create([ASK])
        target = tmp_path / "outside.json"
        target.write_bytes(path_of(folder, chat).read_bytes())
        path_of(folder, chat).unlink()
        path_of(folder, chat).symlink_to(target)
        with pytest.raises(chat_store.Broken):
            store.get(chat["id"])
        store.delete(chat["id"])
        assert target.exists()

    def test_a_pipe_does_not_hang_the_list(self, store, folder):
        store.create([ASK])
        os.mkfifo(folder / f"{'e' * 32}.json")
        assert [b["id"] for b in store.list()["broken"]] == ["e" * 32]

    def test_a_folder_named_like_a_chat_leaks_no_handle(self, store, folder):
        store.create([ASK])
        (folder / f"{'f' * 32}.json").mkdir()
        before = os.dup(0)
        os.close(before)
        for _ in range(5):
            assert [b["id"] for b in store.list()["broken"]] == ["f" * 32]
        after = os.dup(0)
        os.close(after)
        assert after == before
        assert store.delete_all() == {"deleted": 1, "failed": 1}

    def test_an_oversized_file_is_broken(self, store, folder, monkeypatch):
        chat = store.create([ASK])
        monkeypatch.setattr(chat_store, "MAX_CHAT_BYTES", 10)
        assert [b["id"] for b in store.list()["broken"]] == [chat["id"]]

    def test_a_linked_chats_folder_is_refused(self, store, folder, tmp_path):
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir(mode=0o755)
        folder.parent.mkdir(parents=True)
        folder.symlink_to(elsewhere)
        for call in (lambda: store.create([ASK]), store.list, store.delete_all):
            with pytest.raises(chat_store.Unavailable):
                call()
        assert list(elsewhere.iterdir()) == []
        assert mode(elsewhere) == 0o755


class TestWriteFailure:
    def test_the_old_chat_survives_and_no_temp_file_stays(self, store, folder, monkeypatch):
        chat = store.create([ASK])

        def full(*_):
            raise OSError(28, "No space left on device")

        monkeypatch.setattr(chat_store.os, "replace", full)
        with pytest.raises(chat_store.Unavailable) as refused:
            store.append(chat["id"], chat["updated"], [ANSWER])
        assert refused.value.status == 500
        monkeypatch.undo()
        assert store.get(chat["id"]) == chat
        assert [p.name for p in folder.iterdir()] == [f"{chat['id']}.json"]


class TestDelete:
    def test_delete_removes_the_file_and_its_leftover_copies(self, store, folder):
        chat, other = store.create([ASK]), store.create([ASK])
        (folder / f".{chat['id']}.json.abc.tmp").write_text("old text", encoding="utf-8")
        store.delete(chat["id"])
        assert [p.name for p in folder.iterdir()] == [f"{other['id']}.json"]
        with pytest.raises(chat_store.NotFound):
            store.delete(chat["id"])

    def test_delete_all_removes_every_chat_and_nothing_else(self, store, folder):
        store.create([ASK])
        store.create([ASK])
        (folder / f"{'d' * 32}.json").write_text("{", encoding="utf-8")
        (folder / f".{'a' * 32}.json.x.tmp").write_text("old", encoding="utf-8")
        (folder / "notes.txt").write_text("mine", encoding="utf-8")
        assert store.delete_all() == {"deleted": 3, "failed": 0}
        assert [p.name for p in folder.iterdir()] == ["notes.txt"]

    def test_delete_all_without_a_folder(self, store, folder):
        assert store.delete_all() == {"deleted": 0, "failed": 0}
        assert not folder.exists()

    def test_nothing_expires(self, store, folder):
        chat = store.create([ASK])
        os.utime(path_of(folder, chat), (0, 0))
        assert [c["id"] for c in store.list()["chats"]] == [chat["id"]]
