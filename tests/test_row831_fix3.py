"""Row 831: Restart and the remembered set cannot launch or break anything.

No process is started and no model is loaded: Popen is a stand-in.
"""

from __future__ import annotations

import importlib.util
import json
import os
import stat
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def load(name, file):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tests" / file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fix2 = load("row831_fix2_for_fix3", "test_row831_fix2.py")
apply_tests = fix2.apply_tests
screen, needs_node = fix2.screen, fix2.needs_node
rig, ram, idle = apply_tests.rig, apply_tests.ram, fix2.idle
post = apply_tests.post
A, B = apply_tests.A, apply_tests.B


# Restart on the screen ---------------------------------------------------------- #
def restart_after(shown, left):
    """Show ``shown``, then the stopped server with only ``left`` installed; Restart."""
    return (
        f"await show({shown}); const left = {left};"
        " await show({ ...view({ down_notice: 'gone' }), models: left });"
        " await press('#serverRestartBtn'); return sent('/api/server/start');"
    )


@needs_node
@pytest.mark.parametrize("left", ["[models[0]]", "[models[0], { ...models[1], complete: false }]"])
def test_restart_leaves_out_a_model_that_cannot_be_used(tmp_path, left):
    out = screen(tmp_path, restart_after("loaded()", left))
    assert out == [{"repo": "org/a", "background": False}]


@needs_node
@pytest.mark.parametrize(
    "shown",
    [
        "view({ last_models: ['org/gone'] })",
        "view({ last_models: ['org/gone'], repo: 'org/gone' })",
    ],
)
def test_restart_starts_nothing_when_no_remembered_model_is_installed(tmp_path, shown):
    out = screen(tmp_path, restart_after(shown, "models"))
    assert out == []


# Restart on the server ---------------------------------------------------------- #
@pytest.mark.parametrize("repo", ["org/gone", "org/part", "/tmp/some/folder"])
def test_a_start_from_stopped_refuses_what_is_not_installed(idle, base_url, repo):
    fix2.cache_model(idle.app, "org/part")
    for weights in idle.app.CACHE_DIR.glob("models--org--part/**/*.safetensors"):
        weights.unlink()
    with idle.app._model_cache_lock:
        idle.app._model_cache.clear()
    status, body = post(base_url, "/api/server/start", {"repo": repo})
    assert status == 400 and "not installed completely" in body["error"]
    assert idle.made == [] and idle.app.STATE.server_pending == []


# reading the remembered set ------------------------------------------------------ #
def last_file(app):
    return app.APP_DIR / "server.last.json"


def test_a_pipe_in_its_place_never_blocks(idle):
    path = last_file(idle.app)
    os.mkfifo(path)
    got = []
    reader = threading.Thread(target=lambda: got.append(idle.app._last_models()), daemon=True)
    reader.start()
    reader.join(2)
    blocked = reader.is_alive()
    if blocked:  # let the stuck reader go
        os.close(os.open(path, os.O_WRONLY | os.O_NONBLOCK))
        reader.join(2)
    assert not blocked and got == [[]]


@pytest.mark.parametrize(
    "text",
    [
        json.dumps({"repos": [A]}) + " " * 100_000,
        json.dumps({"repos": ["org/" + "x" * 500]}),
        json.dumps({"repos": [A, ""]}),
        '{"repos": ["org/\\ud800"]}',
        '{"repos": ["org/a\\u0000"]}',
    ],
    ids=["too-big", "long-name", "empty-name", "surrogate", "control"],
)
def test_bad_content_reads_as_nothing(idle, text):
    last_file(idle.app).write_text(text, "utf-8")
    assert fix2.remembered(idle.app) == []


@pytest.mark.parametrize("error", [RecursionError, MemoryError, OverflowError])
def test_any_parse_error_reads_as_nothing(idle, monkeypatch, error):
    last_file(idle.app).write_text(json.dumps({"repos": [A]}), "utf-8")

    def fail(*args, **kwargs):
        raise error("bad")

    with monkeypatch.context() as patch:
        patch.setattr(idle.app.json, "loads", fail)
        assert idle.app._last_models() == []


# writing it ---------------------------------------------------------------------- #
def test_a_planted_temp_link_is_not_followed(idle, tmp_path):
    victim = tmp_path / "victim.txt"
    victim.write_text("keep", "utf-8")
    victim.chmod(0o644)
    path = last_file(idle.app)
    planted = path.with_name(f"{path.name}.tmp")
    planted.symlink_to(victim)
    idle.app.STATE.server_pid, idle.app.STATE.server_repos = 42, [A, B]
    idle.app._remember_models(42)
    assert victim.read_text("utf-8") == "keep"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o644
    assert not path.is_symlink() and stat.S_IMODE(path.stat().st_mode) == 0o600
    assert idle.app._last_models() == [A]  # an older pair: its first model


def test_a_failed_write_leaves_no_temp_file(idle, monkeypatch):
    def fail(*args):
        raise OSError("disk full")

    idle.app.STATE.server_pid, idle.app.STATE.server_repos = 42, [A]
    with monkeypatch.context() as patch:
        patch.setattr(idle.app.os, "replace", fail)
        idle.app._remember_models(42)
    assert list(idle.app.APP_DIR.glob("server.last*")) == []
