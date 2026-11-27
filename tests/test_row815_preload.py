"""Row 815: the context never depends on events; the guard says when preload is over.

``memory_low`` waits for ``preload_done`` in the status file. Free memory is always
injected (see conftest); no model is loaded and no process started.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_for815", ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


limits = sibling("test_row760_limits")
Rig, GB, A, B, ram, serving = limits.Rig, limits.GB, limits.A, limits.B, limits.ram, limits.serving
weigh, measured, SEVEN_B = limits.weigh, limits.measured, limits.SEVEN_B
resident_file = limits.apps.resident_file
CONTEXT = 10_240


def hit(model):
    return {"event": "hit", "model": model, "at": 0}


def pair(app, monkeypatch, events, **resident):
    """A running two-model server whose status file ends with ``events``."""
    serving(app, monkeypatch)
    # Written here: serving() would add preload_done, and these tests choose it.
    fields = {"resident": [A, B], "events": events, **resident, **measured(2 * SEVEN_B)}
    resident_file(app, 4242, **fields)
    for repo in (A, B):
        weigh(app, repo, SEVEN_B)
    app.STATE.server_context = CONTEXT


def server(app):
    app.STATE.free_read = None  # past the ten seconds
    return app.STATE.snapshot()["server"]


# The app -------------------------------------------------------------------- #
@pytest.mark.parametrize(
    "events",
    [
        pytest.param([hit(A), hit(B)], id="second model used last"),
        pytest.param([hit(A), {"event": "evict", "model": A, "at": 1}], id="eviction last"),
        pytest.param([hit(A), {"event": "trim", "model": B, "at": 1}], id="trim last"),
        pytest.param([hit(A), {"event": "load", "model": B, "at": 1}], id="load last"),
        pytest.param([], id="no events"),
    ],
)
def test_after_preload_any_last_event_keeps_both(app_module, monkeypatch, ram, free_memory, events):
    pair(app_module, monkeypatch, events, preload_done=True)
    free_memory.bytes = 0
    shown = server(app_module)
    assert shown["context_tokens"] == CONTEXT
    assert shown["memory_low"]["short_by_bytes"] > 0


@pytest.mark.parametrize(
    "done",
    [
        pytest.param({}, id="not written"),
        pytest.param({"preload_done": False}, id="false"),
        pytest.param({"preload_done": "yes"}, id="not a boolean"),
    ],
)
@pytest.mark.parametrize(
    "events",
    [
        pytest.param([{"event": "load", "model": A, "at": 0}], id="first loaded"),
        pytest.param([{"event": "load", "model": B, "at": 0}], id="second loaded"),
        pytest.param([hit(A)], id="a hit on the first"),
    ],
)
def test_while_preload_runs_only_the_notice_waits(
    app_module, monkeypatch, ram, free_memory, events, done
):
    pair(app_module, monkeypatch, events, **done)
    free_memory.bytes = 0
    shown = server(app_module)
    assert (shown["context_tokens"], shown["memory_low"]) == (CONTEXT, None)


def test_the_context_needs_no_status_file(app_module, monkeypatch, ram, free_memory):
    serving(app_module, monkeypatch)
    free_memory.bytes = 0
    shown = server(app_module)
    assert (shown["context_tokens"], shown["memory_low"]) == (8192, None)


# The guard ------------------------------------------------------------------ #
def watched(rig):
    """``(model, preload_done)`` as the status file read while each model loaded."""
    seen = []
    load = rig.server.ModelProvider.load

    def spy(self, model_path, *args):
        if rig.path.exists():
            seen.append((model_path, rig.status().get("preload_done", False)))
        return load(self, model_path, *args)

    rig.server.ModelProvider.load = spy
    return seen


def test_preload_is_not_done_while_the_second_model_loads(tmp_path):
    rig = Rig(tmp_path, also="org/b")
    seen = watched(rig)
    rig.provider.load_default()
    assert seen == [("org/b", False), ("default_model", False)]
    assert rig.status()["preload_done"] is True


@pytest.mark.parametrize(
    "kwargs",
    [
        pytest.param({"sizes": {"org/b": 30 * GB}}, id="does not fit"),
        pytest.param({"cached": {"org/a"}}, id="not installed"),
        pytest.param({"broken": ("org/b",)}, id="fails to load"),
    ],
)
def test_a_skipped_preload_is_done(tmp_path, kwargs):
    rig = Rig(tmp_path, also="org/b", **kwargs)
    rig.provider.load_default()
    assert ("preload-skipped", "org/b") in rig.events()
    assert rig.status()["preload_done"] is True


def test_no_second_model_is_done_at_once(tmp_path):
    rig = Rig(tmp_path)
    rig.provider.load_default()
    assert rig.status()["preload_done"] is True


def test_a_first_model_that_fails_ends_the_preload(tmp_path):
    rig = Rig(tmp_path, also="org/b", broken=("org/a",))
    with pytest.raises(ValueError, match="cannot load"):
        rig.provider.load_default()
    assert rig.status()["preload_done"] is True


def test_later_requests_keep_it_done(tmp_path):
    rig = Rig(tmp_path, also="org/b")
    rig.provider.load_default()
    rig.provider.load("org/b")
    rig.provider.load("org/c")
    assert rig.status()["preload_done"] is True


# Both together --------------------------------------------------------------- #
def test_the_notice_shows_after_a_preload_the_guard_skipped(
    app_module, monkeypatch, ram, free_memory, tmp_path
):
    free_memory.bytes = 100 * GB
    rig = Rig(tmp_path, also="org/b", sizes={"org/b": 30 * GB})
    rig.provider.load_default()
    serving(app_module, monkeypatch)
    app_module.STATE.server_pid = os.getpid()  # the writer of this status file
    monkeypatch.setattr(app_module, "_resident_file", lambda: rig.path)
    free_memory.bytes = 0
    shown = server(app_module)
    assert shown["context_tokens"] and shown["memory_low"]["short_by_bytes"] > 0
