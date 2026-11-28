"""Row 827: which snapshot of a cached model is read.

Downloading a model again while it is in use leaves partial files behind. No model is
loaded and no process started.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from test_row656_app import cache_model

A = "org/a"


def entry_of(app, repo) -> Path:
    return app.CACHE_DIR / f"models--{repo.replace('/', '--')}"


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
