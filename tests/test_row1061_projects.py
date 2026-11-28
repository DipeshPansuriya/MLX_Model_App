"""Row 1061: project folders (read only): root, path, secret file and budget rules."""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import stat
from pathlib import Path

import pytest

import chat_store
import projects


@pytest.fixture
def home(tmp_path):
    folder = tmp_path.resolve() / "home"
    folder.mkdir()
    return folder


@pytest.fixture
def store(home):
    return projects.ProjectStore(home / ".mlx_studio" / "projects.json", home=home)


@pytest.fixture
def root(home):
    folder = home / "work" / "demo"
    (folder / "src").mkdir(parents=True)
    (folder / "README.md").write_text("# Demo\n", encoding="utf-8")
    (folder / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    return folder


@pytest.fixture
def project(store, root):
    return store.add(root)


def put(root, name: str, content: str | bytes = "plain words\n") -> str:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return name


def listed(store, project) -> list[str]:
    return [entry["path"] for entry in store.list_files(project["id"])["files"]]


def picks(store, project, *paths) -> list[dict]:
    shown = store.preview(project["id"], list(paths))["files"]
    return [{"path": f["path"], "sha256": f["sha256"]} for f in shown]


def open_handles() -> int:
    """The next free descriptor: it rises when one is left open."""
    handle = os.dup(0)
    os.close(handle)
    return handle


class TestRegistry:
    def test_a_project_is_saved_privately_with_its_resolved_root(self, store, root, home):
        project = store.add(str(root))
        assert chat_store.ID_PATTERN.fullmatch(project["id"])
        assert project == {"id": project["id"], "name": "demo", "root": str(root)}
        assert stat.S_IMODE(store.file.stat().st_mode) == 0o600
        assert stat.S_IMODE(store.file.parent.stat().st_mode) == 0o700
        saved = json.loads(store.file.read_text(encoding="utf-8"))
        assert saved == {"v": 1, "projects": [project]}
        assert store.list() == [project]
        assert store.get(project["id"]) == project

    def test_a_chat_can_carry_the_project_id(self, project, tmp_path):
        chat = chat_store.ChatStore(tmp_path / "chats").create(project_id=project["id"])
        assert chat["project_id"] == project["id"]

    def test_a_link_to_a_folder_is_stored_as_the_folder(self, store, root, home):
        (home / "shortcut").symlink_to(root)
        assert store.add(home / "shortcut")["root"] == str(root)

    def test_the_same_folder_is_one_project(self, store, root):
        assert store.add(root) == store.add(root, name="other")
        assert len(store.list()) == 1

    def test_a_name_may_be_given(self, store, root):
        assert store.add(root, name="  My   demo ")["name"] == "My demo"

    def test_removing_forgets_it_and_keeps_the_folder(self, store, project, root):
        store.remove(project["id"])
        assert store.list() == []
        assert (root / "README.md").exists()
        for call in (store.get, store.remove, store.list_files):
            with pytest.raises(projects.NotFound):
                call(project["id"])

    @pytest.mark.parametrize("project_id", [None, 3, "", "../x", "a" * 32])
    def test_an_unknown_id_is_not_found(self, store, project, project_id):
        with pytest.raises(projects.NotFound):
            store.preview(project_id, ["README.md"])

    def test_there_is_a_limit(self, store, home, monkeypatch):
        monkeypatch.setattr(projects, "PROJECTS_MAX", 2)
        for name in ("a", "b", "c"):
            (home / name).mkdir()
        store.add(home / "a")
        store.add(home / "b")
        with pytest.raises(projects.TooLarge):
            store.add(home / "c")

    @pytest.mark.parametrize(
        "content",
        [
            "{",
            "[]",
            '{"v": 2, "projects": []}',
            '{"v": 1, "projects": [{"id": "x", "name": "n", "root": "/tmp"}]}',
            '{"v": 1, "projects": [{"id": "' + "a" * 32 + '", "name": "n", "root": "rel"}]}',
        ],
    )
    def test_a_broken_list_is_reported_and_never_overwritten(self, store, root, content):
        store.file.parent.mkdir(parents=True)
        store.file.write_text(content, encoding="utf-8")
        for call in (store.list, lambda: store.add(root), lambda: store.remove("a" * 32)):
            with pytest.raises(projects.Broken):
                call()
        assert store.file.read_text(encoding="utf-8") == content

    def test_a_linked_list_file_is_not_followed(self, store, root, tmp_path):
        outside = tmp_path / "outside.json"
        outside.write_text('{"v": 1, "projects": []}', encoding="utf-8")
        store.file.parent.mkdir(parents=True)
        store.file.symlink_to(outside)
        with pytest.raises(projects.Broken):
            store.add(root)
        assert outside.read_text(encoding="utf-8") == '{"v": 1, "projects": []}'

    def test_the_default_home_is_the_users(self, tmp_path, monkeypatch):
        monkeypatch.setenv("HOME", str(tmp_path))
        store = projects.ProjectStore(tmp_path / ".mlx_studio" / "projects.json")
        with pytest.raises(projects.Refused):
            store.add(tmp_path)


class TestRefusedRoots:
    @pytest.mark.parametrize(
        "name",
        [
            "Library",
            "Library/Application Support",
            ".ssh",
            ".ssh/old",
            ".mlx_studio",
            ".mlx_studio/chats",
            ".dsh",
            ".claude",
            ".claude/projects",
            ".aws",
            "work/.git",
            "work/.GIT/hooks",
            "work/.Aws",
        ],
    )
    def test_a_private_folder(self, store, home, name):
        (home / name).mkdir(parents=True, exist_ok=True)
        with pytest.raises(projects.Refused) as refused:
            store.add(home / name)
        assert refused.value.status == 403
        assert store.list() == []

    def test_home_and_everything_above_it(self, store, home):
        for folder in (home, *home.parents, Path("/")):
            with pytest.raises(projects.Refused):
                store.add(folder)

    def test_system_folders(self, store):
        # Present on macOS only; elsewhere it is refused as a folder that is not there.
        with pytest.raises(projects.ProjectError):
            store.add("/System/Library")

    def test_another_spelling_of_a_private_folder(self, store, home):
        (home / "Library" / "x").mkdir(parents=True)
        # Refused as private where case is ignored, as missing where it is not.
        with pytest.raises(projects.ProjectError):
            store.add(home / "LIBRARY" / "x")

    def test_a_link_into_a_private_folder(self, store, home):
        (home / ".ssh").mkdir()
        (home / "innocent").symlink_to(home / ".ssh")
        with pytest.raises(projects.Refused):
            store.add(home / "innocent")

    @pytest.mark.parametrize("path", [None, 7, "", "work/demo", "~/work", "/no/such/folder", "a\0"])
    def test_something_that_is_not_a_full_folder_path(self, store, root, path):
        with pytest.raises(projects.ProjectError):
            store.add(path)

    def test_a_file(self, store, root):
        with pytest.raises(projects.Refused):
            store.add(root / "README.md")


class TestRootAfterwards:
    def check_refused(self, store, project):
        for call in (
            lambda: store.list_files(project["id"]),
            lambda: store.preview(project["id"], ["README.md"]),
            lambda: store.snapshot(project["id"], [{"path": "README.md", "sha256": "a" * 64}]),
        ):
            with pytest.raises(projects.ProjectError) as refused:
                call()
            assert refused.value.status in (403, 404)

    def test_swapped_for_a_link(self, store, project, root, home):
        secret = home / ".ssh"
        secret.mkdir()
        put(secret, "README.md", "private")
        root.rename(home / "moved")
        root.symlink_to(secret)
        self.check_refused(store, project)

    def test_a_parent_swapped_for_a_link(self, store, project, root, home):
        (home / "work").rename(home / "elsewhere")
        (home / "work").symlink_to(home / "elsewhere")
        self.check_refused(store, project)

    def test_gone(self, store, project, root, home):
        root.rename(home / "moved")
        with pytest.raises(projects.NotFound):
            store.list_files(project["id"])

    def test_now_private(self, store, home):
        # A saved list edited by hand must not open a refused folder.
        (home / ".dsh").mkdir()
        put(home / ".dsh", "a.txt")
        store.file.parent.mkdir(parents=True)
        entry = {"id": "a" * 32, "name": "x", "root": str(home / ".dsh")}
        store.file.write_text(json.dumps({"v": 1, "projects": [entry]}), encoding="utf-8")
        with pytest.raises(projects.Refused):
            store.preview("a" * 32, ["a.txt"])


SECRET_NAMES = [
    ".env",
    ".ENV",
    ".Env.local",
    ".envrc",
    "cert.pem",
    "Server.PEM",
    "tls.Key",
    "store.P12",
    "id_rsa",
    "ID_ED25519.pub",
    ".npmrc",
    ".NetRC",
    ".PyPIrc",
    "aws_Credentials",
    "CREDENTIALS.json",
    "my-SECRET.yml",
    "Secrets/readme.md",
    "deep/er/.env",
    ".SSH/config",
    ".aws/config",
    ".Git/config",
    "vendor/.git/HEAD",
]


class TestSecretFiles:
    @pytest.mark.parametrize("name", SECRET_NAMES)
    def test_it_is_neither_listed_nor_read(self, store, project, root, name):
        put(root, name)
        assert listed(store, project) == ["README.md", "src/app.py"]
        assert store.list_files(project["id"])["excluded"] >= 1
        for call in (
            lambda: store.preview(project["id"], [name]),
            lambda: store.snapshot(project["id"], [{"path": name, "sha256": "a" * 64}]),
        ):
            with pytest.raises(projects.Refused) as refused:
                call()
            assert refused.value.path == name

    @pytest.mark.parametrize(
        "name",
        [
            ".en\u200cv",
            "\uff0eenv",
            "id\uff3frsa",
            "a.\u212aey",
            ".GIT/config",
        ],
    )
    def test_look_alike_spellings(self, name):
        assert projects.is_secret(name)

    @pytest.mark.parametrize(
        "name", ["environment.md", "keys.py", "monkey", "src/pem.py", "gitignore", ".gitignore"]
    )
    def test_ordinary_names_pass(self, name):
        assert not projects.is_secret(name)


class TestPaths:
    @pytest.mark.parametrize(
        "path",
        [
            "..",
            "../demo/README.md",
            "src/../../x",
            "src/../README.md",
            "./README.md",
            "src//app.py",
            "src/",
            "/etc/hosts",
            "",
            "READ\0ME",
            "a\nb",
            "a\u2028b",
            "bad\ud800name",
            "x" * 1025,
            "/".join("d" * 20),
            None,
            5,
        ],
    )
    def test_only_a_plain_relative_path(self, store, project, path):
        with pytest.raises(projects.Refused):
            store.preview(project["id"], [path])

    def test_no_link_is_followed_wherever_it_points(self, store, project, root, home):
        outside = home / "outside"
        outside.mkdir()
        put(outside, "note.txt", "not yours")
        (root / "out.txt").symlink_to(outside / "note.txt")
        (root / "inner.txt").symlink_to(root / "README.md")
        (root / "folder").symlink_to(outside)
        (root / "loop").symlink_to(root)
        assert listed(store, project) == ["README.md", "src/app.py"]
        for path in ("out.txt", "inner.txt", "folder/note.txt", "loop/README.md"):
            with pytest.raises(projects.Refused):
                store.preview(project["id"], [path])

    def test_a_pipe_a_folder_and_a_missing_file(self, store, project, root):
        os.mkfifo(root / "pipe")
        assert "pipe" not in listed(store, project)
        for path in ("pipe", "src"):
            with pytest.raises(projects.Refused):
                store.preview(project["id"], [path])
        for path in ("gone.txt", "README.md/x"):
            with pytest.raises(projects.ProjectError):
                store.preview(project["id"], [path])

    def test_the_size_limit(self, store, project, root):
        put(root, "fits.txt", "x" * projects.FILE_MAX_BYTES)
        put(root, "big.txt", "x" * (projects.FILE_MAX_BYTES + 1))
        assert projects.FILE_MAX_BYTES == 256 * 1024
        assert "fits.txt" in listed(store, project)
        assert "big.txt" not in listed(store, project)
        assert store.preview(project["id"], ["fits.txt"])["files"][0]["size"] == 256 * 1024
        with pytest.raises(projects.TooLarge):
            store.preview(project["id"], ["big.txt"])

    @pytest.mark.parametrize("content", [b"\xff\xfe\x00a", b"ok\x00ok", b"caf\xe9"])
    def test_only_utf8_text(self, store, project, root, content):
        put(root, "blob.bin", content)
        with pytest.raises(projects.Refused):
            store.preview(project["id"], ["blob.bin"])

    def test_refusals_leave_no_handle_open_and_repeat_no_text(self, store, project, root):
        put(root, "src/blob.bin", b"PRIVATE-WORDS\x00")
        os.mkfifo(root / "src" / "pipe")
        before = open_handles()
        for path in ("src/blob.bin", "src/pipe", "src/none", "src", "src/app.py/x"):
            with pytest.raises(projects.ProjectError) as refused:
                store.preview(project["id"], [path])
            assert "PRIVATE-WORDS" not in str(refused.value)
        store.list_files(project["id"])
        store.preview(project["id"], ["src/app.py"])
        assert open_handles() == before


class TestListing:
    def test_relative_paths_with_sizes_nearest_first(self, store, project, root):
        put(root, "src/deep/z.txt", "12345")
        put(root, "zeta.txt", "1")
        found = store.list_files(project["id"])
        assert [f["path"] for f in found["files"]] == [
            "README.md",
            "zeta.txt",
            "src/app.py",
            "src/deep/z.txt",
        ]
        assert found["files"][-1]["size"] == 5
        assert (found["excluded"], found["truncated"]) == (0, False)

    @pytest.mark.parametrize("limit", ["LIST_MAX_FILES", "SCAN_MAX_ENTRIES", "DEPTH_MAX"])
    def test_a_large_folder_is_cut_and_says_so(self, store, project, root, monkeypatch, limit):
        monkeypatch.setattr(projects, limit, 1)
        put(root, "src/deep/z.txt")
        found = store.list_files(project["id"])
        assert found["truncated"] is True
        assert len(found["files"]) <= 2

    def test_nothing_is_run_in_the_folder(self):
        assert not {"subprocess", "shutil"} & set(vars(projects))


class TestPreviewAndSnapshot:
    def test_the_preview_shows_exactly_what_would_be_sent(self, store, project, root):
        shown = store.preview(project["id"], ["README.md", "src/app.py"])
        first = shown["files"][0]
        assert first == {
            "path": "README.md",
            "sha256": hashlib.sha256(b"# Demo\n").hexdigest(),
            "size": 7,
            "text": "# Demo\n",
            "warnings": [],
        }
        assert shown["tokens"] == projects.estimate_tokens(projects.file_block(shown["files"]))
        assert shown["notice"] == projects.SERVER_WARNING

    def test_the_snapshot_fits_a_saved_message(self, store, project, tmp_path):
        files = store.snapshot(project["id"], picks(store, project, "README.md"))
        assert set(files[0]) == chat_store.FILE_KEYS
        chats = chat_store.ChatStore(tmp_path / "chats")
        chat = chats.create([{"role": "user", "content": "read it", "files": files}])
        assert chats.get(chat["id"])["messages"][0]["files"] == files

    def test_a_file_changed_after_the_preview_is_not_sent(self, store, project, root):
        chosen = picks(store, project, "README.md", "src/app.py")
        put(root, "src/app.py", "changed")
        with pytest.raises(projects.Stale) as refused:
            store.snapshot(project["id"], chosen)
        assert refused.value.path == "src/app.py"

    @pytest.mark.parametrize("target", ["inside", "outside"])
    def test_a_file_swapped_for_a_link_after_the_preview(self, store, project, root, home, target):
        chosen = picks(store, project, "src/app.py")
        twin = root / "twin.txt" if target == "inside" else home / "twin.txt"
        twin.write_bytes((root / "src" / "app.py").read_bytes())
        (root / "src" / "app.py").unlink()
        (root / "src" / "app.py").symlink_to(twin)
        with pytest.raises(projects.Refused):
            store.snapshot(project["id"], chosen)

    def test_a_folder_swapped_for_a_link_after_the_preview(self, store, project, root, home):
        chosen = picks(store, project, "src/app.py")
        (root / "src").rename(home / "src")
        (root / "src").symlink_to(home / "src")
        with pytest.raises(projects.Refused):
            store.snapshot(project["id"], chosen)

    @pytest.mark.parametrize(
        "chosen",
        [
            None,
            "README.md",
            ["README.md"],
            [{"path": "README.md"}],
            [{"path": "README.md", "sha256": None}],
            [{"sha256": "a" * 64}],
            [{"path": "README.md", "sha256": "a" * 64, "text": "mine"}],
        ],
    )
    def test_nothing_is_sent_without_the_hash_that_was_shown(self, store, project, chosen):
        with pytest.raises(projects.Invalid):
            store.snapshot(project["id"], chosen)

    @pytest.mark.parametrize("paths", [None, "README.md", {"README.md": 1}])
    def test_a_preview_takes_a_list(self, store, project, paths):
        with pytest.raises(projects.Invalid):
            store.preview(project["id"], paths)

    def test_no_file_twice_and_not_too_many(self, store, project, monkeypatch):
        with pytest.raises(projects.Invalid):
            store.preview(project["id"], ["README.md", "README.md"])
        monkeypatch.setattr(projects, "FILES_PER_MESSAGE", 1)
        with pytest.raises(projects.TooLarge):
            store.preview(project["id"], ["README.md", "src/app.py"])


# Built from pieces so that the repository's own secret scan stays quiet.
KEY_LIKE = {
    "private key": "-----BEGIN " + "RSA PRIVATE KEY-----\nabc\n",
    "cloud": "id = AKIA" + "ABCDEFGHIJKLMNOP\n",
    "github": "t: ghp_" + "a1" * 18 + "\n",
    "hub": "hf_" + "Ab3" * 12 + "\n",
    "openai": "sk-" + "proj-" + "Ab3" * 10 + "\n",
    "assigned": "pass" + 'word = "hunter2hunter2"\n',
    "env style": "DB_PASS" + "WORD=hunter2hunter2\n",
    "json": '{"api_' + 'key": "hunter2hunter2"}\n',
}


class TestKeyWarnings:
    @pytest.mark.parametrize("kind", sorted(KEY_LIKE))
    def test_key_like_text_is_flagged_without_repeating_it(self, store, project, root, kind):
        put(root, "config.txt", KEY_LIKE[kind])
        warnings = store.preview(project["id"], ["config.txt"])["files"][0]["warnings"]
        assert warnings
        assert all("hunter2" not in w and "Ab3" not in w for w in warnings)

    @pytest.mark.parametrize(
        "text",
        ["token = get_token()\n", "Set your password in the app.\n", "MAX_TOKENS=4096\n", ""],
    )
    def test_ordinary_text_is_not(self, text):
        assert projects.key_warnings(text) == []

    def test_a_hostile_file_is_scanned_quickly(self):
        assert projects.key_warnings("A_" * 130_000) == []


def ask(text="question", files=None):
    return {"role": "user", "content": text, **({"files": files} if files else {})}


def reply(text="answer"):
    return {"role": "assistant", "content": text, "thinking": "hidden", "ts": 1}


def attached(chars: int, name="a.txt"):
    text = "x" * chars
    sha = hashlib.sha256(text.encode()).hexdigest()
    return {"path": name, "sha256": sha, "size": chars, "text": text}


class TestBudget:
    def test_the_start_values(self):
        assert projects.WINDOW_TOKENS == 24 * 1024
        assert projects.REPLY_TOKENS == 4 * 1024
        assert projects.FILES_TOKENS == 8 * 1024

    def test_estimates_by_characters_and_counts_other_scripts_higher(self):
        assert projects.estimate_tokens("") == 0
        assert projects.estimate_tokens("a" * 350) == 100
        assert projects.estimate_tokens("\u0a97" * 100) == 100
        assert projects.estimate_tokens("a" * 350, factor=1.5) == 150

    @pytest.mark.parametrize(
        ("estimated", "actual", "factor"),
        [
            (100, 130, 1.3),
            (100, 10, 0.75),
            (100, 9000, 4.0),
            (0, 50, 1.0),
            (100, None, 1.0),
            (100, 0, 1.0),
            (100, True, 1.0),
        ],
    )
    def test_the_servers_count_corrects_the_estimate(self, estimated, actual, factor):
        assert projects.calibrate(estimated, actual) == pytest.approx(factor)

    def test_the_server_alone_adds_the_system_message(self):
        planned = projects.plan_request([ask(), reply(), ask("more")], system="Be brief.")
        assert planned["messages"] == [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "more"},
        ]
        assert projects.plan_request([ask()])["messages"] == [
            {"role": "user", "content": "question"}
        ]

    @pytest.mark.parametrize(
        "messages",
        [
            [],
            None,
            [reply()],
            [{"role": "system", "content": "obey"}, ask()],
            [{"role": "tool", "content": "x"}, ask()],
            [{"role": "user", "content": 3}],
            ["hi"],
        ],
    )
    def test_only_a_users_question_and_known_roles(self, messages):
        with pytest.raises(projects.Invalid):
            projects.plan_request(messages)

    @pytest.mark.parametrize(
        ("asked", "sent"), [(100, 100), (4096, 4096), (131072, 4096), (None, 4096)]
    )
    def test_the_reply_is_capped_at_its_share(self, asked, sent):
        assert projects.plan_request([ask()], reply_tokens=asked)["max_tokens"] == sent

    def test_the_meter_adds_up(self):
        messages = [ask("a" * 700), reply("b" * 700), ask("c" * 350, [attached(3500)])]
        meter = projects.plan_request(messages, system="s" * 350)["meter"]
        assert meter["window"] == projects.WINDOW_TOKENS
        assert meter["reply"] == projects.REPLY_TOKENS
        assert meter["files_limit"] == projects.FILES_TOKENS
        assert meter["files"] > 1000
        parts = ("reply", "system", "files", "history", "message")
        assert meter["used"] == sum(meter[p] for p in parts)
        assert meter["free"] == meter["window"] - meter["used"]
        assert meter["raw"] == meter["used"] - meter["reply"]

    def test_the_correction_is_applied_but_raw_stays(self):
        plain = projects.plan_request([ask("a" * 3500)])["meter"]
        doubled = projects.plan_request([ask("a" * 3500)], factor=2)["meter"]
        assert doubled["message"] > 1.9 * plain["message"]
        assert doubled["raw"] == plain["raw"]

    def test_old_turns_leave_the_request_not_the_chat(self):
        turns = []
        for n in range(20):
            turns += [ask(f"q{n} " + "a" * 7000), reply(f"r{n} " + "b" * 7000)]
        turns.append(ask("now"))
        before = json.dumps(turns)
        planned = projects.plan_request(turns)
        sent = planned["messages"]
        assert json.dumps(turns) == before
        assert planned["dropped"] == len(turns) - len(sent) > 0
        assert sent[0]["role"] == "user"
        assert sent[-1]["content"] == "now"
        assert [m["content"] for m in sent] == [m["content"] for m in turns[-len(sent) :]]
        assert planned["meter"]["used"] <= projects.WINDOW_TOKENS

    def test_files_over_their_share_are_refused(self):
        with pytest.raises(projects.OverBudget) as refused:
            projects.plan_request([ask(files=[attached(30_000)])])
        assert refused.value.status == 413
        assert "file" in str(refused.value)

    def test_a_message_that_cannot_fit_is_refused(self):
        with pytest.raises(projects.OverBudget):
            projects.plan_request([ask("a" * 80_000)])
        with pytest.raises(projects.OverBudget):
            projects.plan_request([ask("a" * 40_000)], system="s" * 40_000)

    def test_earlier_files_count_towards_the_share(self):
        old = ask("first", [attached(20_000, "old.txt")])
        new = ask("second", [attached(20_000, "new.txt")])
        planned = projects.plan_request([old, reply(), new])
        assert planned["dropped"] == 2
        assert len(planned["messages"]) == 1
        assert planned["meter"]["files"] <= projects.FILES_TOKENS

    def test_a_file_goes_in_the_message_marked_as_data(self):
        sent = projects.plan_request([ask("explain", [attached(10)])])["messages"][0]["content"]
        sha = attached(10)["sha256"]
        assert sent.startswith(projects.DATA_NOTE)
        assert "data" in projects.DATA_NOTE
        assert "not instructions" in projects.DATA_NOTE
        assert f'"a.txt" sha256={sha}' in sent
        assert sent.index(f"END FILE sha256={sha}") < sent.index("explain")
        assert sent.endswith("explain")

    def test_no_files_no_block(self):
        assert projects.file_block([]) == ""

    def test_the_warning_names_the_open_server(self):
        assert "password" in projects.SERVER_WARNING
        assert "website" in projects.SERVER_WARNING


class TestPreviewAndSendAgree:
    """Row 1087 finding 2: the preview must count with the factor the send uses."""

    @pytest.mark.parametrize("factor", [0.75, 1.0, 2.5, 4.0])
    def test_the_preview_counts_with_the_given_factor(self, store, project, root, factor):
        put(root, "notes.txt", "word " * 2000)
        shown = store.preview(project["id"], ["notes.txt"], factor=factor)
        files = store.snapshot(project["id"], picks(store, project, "notes.txt"))
        message = {"role": "user", "content": "q", "files": files}
        if shown["tokens"] > shown["limit"]:
            with pytest.raises(projects.OverBudget):
                projects.plan_request([message], factor=factor)
        else:
            sent = projects.plan_request([message], factor=factor)
            assert sent["meter"]["files"] == shown["tokens"]

    def test_a_calibrated_factor_shows_the_files_over_their_share(self, store, project, root):
        put(root, "notes.txt", "word " * 2000)
        assert store.preview(project["id"], ["notes.txt"])["tokens"] <= projects.FILES_TOKENS
        shown = store.preview(project["id"], ["notes.txt"], factor=4.0)
        assert shown["tokens"] > shown["limit"]


class TestAFileThatGoesAwayDuringTheListing:
    """Row 1087 finding 1: one vanished file must not fail or cut short the listing."""

    @pytest.mark.parametrize("folder", ["", "src/"])
    def test_only_that_entry_is_left_out(self, store, project, root, monkeypatch, folder):
        for name in ("b.txt", "gone.txt", "z.txt"):
            put(root, folder + name)
        real = os.scandir

        @contextlib.contextmanager
        def scan_then_remove(handle):
            with real(handle) as entries:
                found = list(entries)
            if any(entry.name == "gone.txt" for entry in found):  # listed, now gone
                (root / f"{folder}gone.txt").unlink()
            yield iter(found)

        monkeypatch.setattr(projects.os, "scandir", scan_then_remove)
        result = store.list_files(project["id"])
        paths = {entry["path"] for entry in result["files"]}
        assert {f"{folder}b.txt", f"{folder}z.txt", "README.md", "src/app.py"} <= paths
        assert f"{folder}gone.txt" not in paths
        assert result["excluded"] == 1
