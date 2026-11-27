"""Row 827: a model in use that turns incomplete still counts in the memory guard.

Downloading a model again while it is in use leaves partial files behind. No model is
loaded and no process started.
"""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_helpers", ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


rows = sibling("test_row713_fixes")
apps = rows.apps
cache_model, serving, A, B, CHAT, GB = (
    rows.cache_model, rows.serving, rows.A, rows.B, rows.CHAT, rows.GB,
)  # fmt: skip

RAM = 64 * GB
SHARE = int(0.52 * RAM)


@pytest.fixture
def ram(app_module, monkeypatch):
    monkeypatch.setattr(app_module, "hardware", lambda: {"ram_bytes": RAM, "ram": "64 GB"})
    app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
    app_module.LOG_DIR.mkdir(parents=True, exist_ok=True)


def entry_of(app, repo) -> Path:
    return app.CACHE_DIR / f"models--{repo.replace('/', '--')}"


def mark(app, repo, revision, files):
    wanted = {"revision": revision, "files": files}
    (entry_of(app, repo) / app.DOWNLOAD_MARKER).write_text(json.dumps(wanted), "utf-8")


def new_revision(app, repo, files):
    """A newer revision being fetched: refs/main has moved, only ``files`` have arrived."""
    entry = entry_of(app, repo)
    snapshot = entry / "snapshots" / "def"
    snapshot.mkdir()
    for name, size in files.items():
        (snapshot / name).parent.mkdir(exist_ok=True)
        with open(snapshot / name, "wb") as handle:
            handle.truncate(size)
    (entry / "refs" / "main").write_text("def", "utf-8")


def incomplete_blob(app, repo):
    blobs = entry_of(app, repo) / "blobs"
    blobs.mkdir()
    (blobs / "x.incomplete").write_bytes(b"x")


def listed_file_missing(app, repo):
    mark(app, repo, "abc", ["config.json", "model.safetensors", "tokenizer.json"])


def listed_weights_missing(app, repo):
    new_revision(app, repo, {"README.md": 10})
    mark(app, repo, "def", ["README.md", "config.json", "model.safetensors"])


def listed_shard_missing(app, repo):
    shards = [f"model-0000{i}-of-00002.safetensors" for i in (1, 2)]
    new_revision(app, repo, {shards[0]: GB})
    mark(app, repo, "def", ["config.json", *shards])


def only_other_weights(app, repo, name):
    """Files with a weight suffix have arrived, the model's weights have not."""
    new_revision(app, repo, {name: 10})
    mark(app, repo, "def", ["config.json", "model.safetensors", name])


def only_consolidated_weights(app, repo):
    only_other_weights(app, repo, "consolidated.safetensors")


def only_extra_weights(app, repo):
    only_other_weights(app, repo, "extra/optimizer.bin")


def no_revision_complete(app, repo):
    """The earlier revision has lost a file its index names; the newer one has no weights."""
    index = {"weight_map": {"a": "model.safetensors", "b": "gone.safetensors"}}
    earlier = entry_of(app, repo) / "snapshots" / "abc"
    (earlier / "model.safetensors.index.json").write_text(json.dumps(index), "utf-8")
    listed_weights_missing(app, repo)


BREAKS = [
    incomplete_blob,
    listed_file_missing,
    listed_weights_missing,
    listed_shard_missing,
    only_consolidated_weights,
    only_extra_weights,
    no_revision_complete,
]


def break_model(app, repo, how):
    how(app, repo)
    app.invalidate_models()
    assert repo not in app._complete_models()


@pytest.mark.parametrize("how", BREAKS)
class TestIncompleteModelInUse:
    @pytest.mark.parametrize("state", ["loaded_repo", "loading_repo"])
    def test_the_budget_still_deducts_the_chat_model(self, app_module, ram, how, state):
        cache_model(app_module, CHAT, weights=20 * GB)
        setattr(app_module.STATE, state, CHAT)
        break_model(app_module, CHAT, how)
        assert app_module._server_budget() == SHARE - 20 * GB

    def test_a_pair_that_did_not_fit_still_does_not(self, app_module, ram, how):
        for repo in (A, B):
            cache_model(app_module, repo, weights=6 * GB)
        cache_model(app_module, CHAT, weights=20 * GB)
        app_module.STATE.loaded_repo = CHAT
        assert app_module.server_memory_estimate([A, B])["fits"] is False
        break_model(app_module, CHAT, how)
        assert app_module.server_memory_estimate([A, B])["fits"] is False

    def test_a_served_model_keeps_its_weights_and_config(self, app_module, monkeypatch, ram, how):
        serving(app_module, monkeypatch)
        before = app_module._server_models([A, B])
        break_model(app_module, A, how)
        assert app_module._server_models([A, B]) == before
        assert before[0][1] == apps.CONFIG and before[0][2] == 4 * GB

    def test_a_chat_load_is_still_refused(self, app_module, monkeypatch, ram, how):
        serving(app_module, monkeypatch)
        # 22 GB beside the pair's 11.8 GB is over the 33.3 GB share.
        cache_model(app_module, CHAT, weights=22 * GB)
        assert "allowed" in app_module.chat_load_problem(CHAT)
        break_model(app_module, CHAT, how)
        assert "allowed" in app_module.chat_load_problem(CHAT)


def two_revisions(app, newest):
    """Complete snapshots ``abc`` (refs/main) and ``def``; ``newest`` changed last."""
    cache_model(app, A)
    new_revision(app, A, {"model.safetensors": 10})
    entry = entry_of(app, A)
    (entry / "refs" / "main").write_text("abc", "utf-8")
    for name in ("abc", "def"):
        stamp = 2_000_000 if name == newest else 1_000_000
        os.utime(entry / "snapshots" / name, (stamp, stamp))


class TestWhichSnapshot:
    @pytest.mark.parametrize("newest", ["abc", "def"])
    def test_of_two_complete_refs_main_wins_whatever_its_age(self, app_module, newest):
        two_revisions(app_module, newest)
        assert app_module._snapshot_dir(A).name == "abc"

    @pytest.mark.parametrize("newest", ["abc", "def"])
    def test_of_two_complete_without_refs_main_the_newest_wins(self, app_module, newest):
        two_revisions(app_module, newest)
        (entry_of(app_module, A) / "refs" / "main").unlink()
        assert app_module._snapshot_dir(A).name == newest

    @pytest.mark.parametrize(("arrived", "measured"), [(10, "abc"), (2000, "def")])
    def test_with_none_complete_the_most_weights_win(self, app_module, arrived, measured):
        cache_model(app_module, A, weights=1000)
        shard = "model-00001-of-00002.safetensors"
        new_revision(app_module, A, {shard: arrived})
        first = entry_of(app_module, A) / "snapshots" / "abc"
        (first / "model.safetensors").rename(first / shard)
        assert app_module._snapshot_dir(A).name == measured

    def test_a_snapshot_that_cannot_be_dated_does_not_hide_the_weights(self, app_module):
        cache_model(app_module, A)
        (entry_of(app_module, A) / "snapshots" / "dangling").symlink_to("nowhere")
        assert app_module._snapshot_dir(A).name == "abc"

    def test_a_snapshot_that_cannot_be_read_does_not_hide_the_weights(
        self, app_module, monkeypatch
    ):
        cache_model(app_module, A)

        def unreadable(snapshot):
            raise PermissionError(13, "not readable")

        monkeypatch.setattr(app_module, "_snapshot_complete", unreadable)
        assert app_module._snapshot_dir(A).name == "abc"

    def test_snapshots_that_cannot_be_listed_leave_the_folder(self, app_module, monkeypatch):
        cache_model(app_module, A)
        real = Path.glob

        def glob(path, pattern, *args, **kwargs):
            if path.name == "snapshots":
                raise PermissionError(13, "not readable")
            return real(path, pattern, *args, **kwargs)

        monkeypatch.setattr(Path, "glob", glob)
        assert app_module._snapshot_dir(A) == entry_of(app_module, A)

    def test_a_complete_newer_revision_wins(self, app_module):
        cache_model(app_module, A)
        new_revision(app_module, A, {"model.safetensors": 10})
        assert app_module._snapshot_dir(A).name == "def"

    def test_with_none_complete_refs_main_is_used(self, app_module):
        cache_model(app_module, A)
        (entry_of(app_module, A) / "snapshots" / "abc" / "model.safetensors").unlink()
        new_revision(app_module, A, {"README.md": 10})
        assert app_module._snapshot_dir(A).name == "def"

    def test_without_refs_main_a_complete_snapshot_is_used(self, app_module):
        cache_model(app_module, A)
        new_revision(app_module, A, {"README.md": 10})
        (entry_of(app_module, A) / "refs" / "main").unlink()
        assert app_module._snapshot_dir(A).name == "abc"

    def test_refs_main_is_never_followed_out_of_the_snapshots(self, app_module):
        cache_model(app_module, A)
        (entry_of(app_module, A) / "refs" / "main").write_text("../../..", "utf-8")
        assert app_module._snapshot_dir(A) == entry_of(app_module, A) / "snapshots" / "abc"

    def test_a_folder_without_snapshots_is_itself(self, app_module):
        entry_of(app_module, A).mkdir(parents=True)
        assert app_module._snapshot_dir(A) == entry_of(app_module, A)

    @pytest.mark.parametrize(
        "repo", ["org/missing", "..", "../..", "org/a/../../..", "/etc", "org/a\u0000b", ""]
    )
    def test_a_name_that_is_not_listed_leads_nowhere(self, app_module, repo):
        cache_model(app_module, A)
        assert app_module._snapshot_dir(repo) == app_module.CACHE_DIR

    def test_no_cache_folder(self, app_module):
        assert not app_module.CACHE_DIR.exists()
        assert app_module._snapshot_dir(A) == app_module.CACHE_DIR
