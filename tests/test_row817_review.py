"""Row 817: pre-release review fixes (row 816).

The running pair's estimate and restart body, the status refresh from the request
loop and the words. No model is loaded and no process started.
"""

from __future__ import annotations

import importlib.util
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_for817", ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


limits = sibling("test_row760_limits")
guard, guards, Rig, GB = limits.guard, limits.guards, limits.Rig, limits.GB
ui, needs_node, PAIR, ASK = limits.ui, limits.needs_node, limits.PAIR, limits.ASK


# B1: beside a running server, its own memory counts as available ------------- #
@needs_node
def test_the_running_pair_counts_the_memory_it_holds(tmp_path):
    change = " estimate.held_bytes = 12e9; estimate.free_bytes = 5.6e9;"
    change += " estimate.total_bytes = 12.7e9; await show(running());"
    words = ui(tmp_path, PAIR + change + ASK)[0]
    assert "Uses about 12.7 of 17.6 GB available." in words
    assert "GB free" not in words


@needs_node
def test_more_than_is_available_is_said_so(tmp_path):
    change = " estimate.held_bytes = 4e9; estimate.free_bytes = 5e9;"
    change += " estimate.total_bytes = 12.7e9; await show(running());"
    words = ui(tmp_path, PAIR + change + ASK)[0]
    assert "Needs about 12.7 GB, 9.0 GB would be available." in words


@needs_node
@pytest.mark.parametrize("held", ["", " estimate.held_bytes = 0;"])
def test_with_nothing_held_the_free_figure_is_shown(tmp_path, held):
    words = ui(tmp_path, PAIR + held + ASK)[0]
    assert "Uses about 13.0 of 15.1 GB free." in words


@needs_node
def test_an_unsorted_running_pair_is_restarted_as_it_was(tmp_path):
    out = ui(
        tmp_path,
        'const unsorted = running({ repo: "org/c", repos: ["org/c", "org/a"],'
        " unguarded: true, background: true });"
        " await show(unsorted); await show(unsorted);"
        ' await $("#serverUnguardedRestart").listeners.click();'
        ' return [unsorted.server.repos, sent("/api/server/start")];',
    )
    assert out == [
        ["org/c", "org/a"],
        [{"repo": "org/c", "also": ["org/a"], "background": True}],
    ]


# B2: the status is refreshed from mlx_lm's request loop ---------------------- #
class Loop:
    """A two-model rig whose generator has a request loop, on a clock we move."""

    def __init__(self, tmp_path, monkeypatch):
        self.now = 1000.0
        clock = types.SimpleNamespace(monotonic=lambda: self.now, time=lambda: 0)
        monkeypatch.setattr(guard, "time", clock)
        self.queue, self.timeouts = [], []

        def next_request(generator, timeout=None):
            self.timeouts.append(timeout)
            return self.queue.pop(0) if self.queue else None

        make = guards.fake_server

        def with_loop(broken=()):
            server = make(broken)
            server.ResponseGenerator._next_request = next_request
            return server

        monkeypatch.setattr(guards, "fake_server", with_loop)
        self.rig = Rig(tmp_path)
        self.generator = self.rig.server.ResponseGenerator(self.rig.provider, self.rig.cache)
        self.rig.provider.load("org/a")

    def next(self, active):
        """One turn of the loop with ``active`` bytes in memory: (request, status)."""
        self.rig.server.mx.active = active
        self.queue.append(request := object())
        got = self.generator._next_request(timeout=0.1)
        assert got is request, "the request must reach mlx_lm"
        return self.rig.status()


def test_the_request_loop_refreshes_the_status_every_ten_seconds(tmp_path, monkeypatch):
    loop = Loop(tmp_path, monkeypatch)
    events = loop.rig.status()["events"]
    monkeypatch.setattr(guard, "free_bytes", lambda: 3 * GB)

    first = loop.next(7 * GB)
    assert (first["active_bytes"], first["budget_bytes"]) == (7 * GB, 8 * GB)
    assert first["events"] == events, "a refresh adds no event"

    loop.now += 9.9
    assert loop.next(9 * GB)["active_bytes"] == 7 * GB
    loop.now += 0.1
    later = loop.next(9 * GB)
    assert (later["active_bytes"], later["budget_bytes"]) == (9 * GB, 10 * GB)
    assert later["events"] == events
    assert loop.timeouts == [0.1, 0.1, 0.1]


def test_a_refresh_that_fails_keeps_the_request(tmp_path, monkeypatch):
    loop = Loop(tmp_path, monkeypatch)

    def unreadable():
        raise RuntimeError("no figure")

    monkeypatch.setattr(loop.rig.server.mx, "get_peak_memory", unreadable)
    loop.next(7 * GB)


# F3 and P1, P3: the words ---------------------------------------------------- #
OVERSIZE = (
    'await show(running({{ resident: ["org/c"], oversize: ["org/c"], events: [{events}] }}));'
    ' return text("#serverMultiText");'
)
END = " macOS may slow down or stop the API server. Free some memory or choose a smaller model."
START = "c was loaded on its own: it does not fit in the memory the API server may use now."


@needs_node
@pytest.mark.parametrize(
    ("events", "middle"),
    [
        pytest.param("", "", id="first load"),
        pytest.param('{ event: "alone", model: "org/c" }', "", id="nothing unloaded"),
        pytest.param(
            '{ event: "evict", model: "org/a" }',
            " The other model was unloaded.",
            id="the other was unloaded",
        ),
    ],
)
def test_the_oversize_words_say_each_thing_once(tmp_path, events, middle):
    assert ui(tmp_path, OVERSIZE.format(events=events)) == START + middle + END


@needs_node
def test_the_cache_and_refresh_words(tmp_path):
    hint, memory = ui(
        tmp_path,
        'await show(running({ resident: ["org/a"], context_tokens: 10240, memory: {'
        " active_bytes: 9e9, peak_bytes: 9e9, budget_bytes: 13.1e9 } }));"
        ' return [text("#serverPromptHint"), text("#serverMemory")];',
    )
    assert hint == (
        "Keep prompts to 10240 tokens or fewer. The prompt cache holds about 20480 tokens "
        "in total; above that, older prompts are dropped and read again."
    )
    assert memory.endswith("Measured by the API server, refreshed about every 10 s.")
