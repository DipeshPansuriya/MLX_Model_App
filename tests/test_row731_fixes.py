"""Rows 733 and 735: the security Info items and the QA Low findings left after row 713.

(Row 731, the F9 admission rule, is tested in test_row656_guard.py.) No model is
loaded, no process started and no window or browser tab opened.
"""

from __future__ import annotations

import importlib.util
import os
import threading
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_for731", ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


fixes = sibling("test_row713_fixes")
apps, uis = fixes.apps, fixes.uis
Rig, GB, A, B, CHAT, SHARE = fixes.Rig, fixes.GB, fixes.A, fixes.B, fixes.CHAT, fixes.SHARE
cache_model, post, ram, loads = fixes.cache_model, fixes.post, fixes.ram, fixes.loads
budget_in_file = fixes.budget_in_file
ui, needs_node, HTML = fixes.ui, fixes.needs_node, fixes.HTML


# Row 733 N1: the app writes the budget file without following a link --------- #


# Row 733 N2: the guard only takes a positive number from a plain file -------- #
def budget_after_a_load(tmp_path, monkeypatch, budget: Path) -> int:
    monkeypatch.setenv("MLX_STUDIO_BUDGET_FILE", str(budget))
    rig = Rig(tmp_path)
    # A thread: opening a FIFO the plain way would wait for ever.
    loading = threading.Thread(target=rig.provider.load, args=("org/a",), daemon=True)
    loading.start()
    loading.join(5)
    assert not loading.is_alive(), "the load is stuck on the budget file"
    return rig.status()["budget_bytes"]


@pytest.mark.parametrize("content", ["-5", "0", "-" + "9" * 30])
def test_a_budget_below_one_is_ignored(tmp_path, monkeypatch, content):
    budget = tmp_path / "server.budget"
    budget.write_text(content, "utf-8")
    assert budget_after_a_load(tmp_path, monkeypatch, budget) == 18 * GB


def test_a_fifo_as_the_budget_file_does_not_block_a_load(tmp_path, monkeypatch):
    budget = tmp_path / "server.budget"
    os.mkfifo(budget)
    assert budget_after_a_load(tmp_path, monkeypatch, budget) == 18 * GB


def test_a_fifo_with_a_writer_is_not_read(tmp_path, monkeypatch):
    budget = tmp_path / "server.budget"
    os.mkfifo(budget)
    reader = os.open(budget, os.O_RDONLY | os.O_NONBLOCK)
    writer = os.open(budget, os.O_WRONLY)
    try:
        os.write(writer, str(10 * GB).encode())
        assert budget_after_a_load(tmp_path, monkeypatch, budget) == 18 * GB
    finally:
        os.close(writer)
        os.close(reader)


@pytest.mark.parametrize("kind", ["link", "folder"])
def test_a_budget_that_is_not_a_plain_file_is_ignored(tmp_path, monkeypatch, kind):
    budget = tmp_path / "server.budget"
    if kind == "link":
        (tmp_path / "other").write_text(str(10 * GB), "utf-8")
        budget.symlink_to(tmp_path / "other")
    else:
        budget.mkdir()
    assert budget_after_a_load(tmp_path, monkeypatch, budget) == 18 * GB


# Row 733 N3: the Chat tab loads installed models only ------------------------ #


# Row 735 N1: a refused Chat load stays on screen ----------------------------- #
REFUSED = """
const why = "not enough memory: the API server keeps two models. Stop the API server.";
let refuse = true;
respond = (path) => path !== "/api/load" ? reply(path === "/api/server/estimate" ? estimate
  : current) : refuse ? reply({ error: why }, false) : reply({ ok: true });
const problem = () => [text("#loadProblem"), $("#loadProblemBox").classList.contains("show"),
  $("#loadProblemDismiss").hidden];
const tick = () => new Promise((r) => setTimeout(r, 0));
await show(view());
$("#chatModelSelect").value = "org/c";
"""
WHY = "not enough memory: the API server keeps two models. Stop the API server."
BY_BUTTON = 'await $("#loadBtn").listeners.click();'
BY_MODEL_CARD = (
    'const card = $("#modelCards").querySelectorAll("[data-act]")'
    '.find((b) => b.dataset.act === "load");'
    " await card.listeners.click(); await tick();"
)
BY_OFFER_CARD = (
    'state.catalog = [{ repo: "org/c", fit: "good", installed: true }];'
    ' $("#catalogFilter").value = "all"; renderCatalog();'
    ' await $("#catalogCards").querySelectorAll("[data-cat-load]")[0].listeners.click();'
)


class Ancestors(HTMLParser):
    """Every element with an id: its attributes and those of the elements around it."""

    VOID = frozenset({"br", "hr", "img", "input", "link", "meta"})

    def __init__(self, page):
        super().__init__()
        self.stack, self.found = [], {}
        self.feed(page)

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if "id" in attrs:
            self.found[attrs["id"]] = (attrs, [a for _, a in self.stack])
        if tag not in self.VOID:
            self.stack.append((tag, attrs))

    def handle_startendtag(self, tag, attrs):
        self.handle_starttag(tag, attrs)
        if tag not in self.VOID:
            self.stack.pop()

    def handle_endtag(self, tag):
        while self.stack and self.stack.pop()[0] != tag:
            pass


PAGE = Ancestors(HTML).found


# Row 735 N2: the live region is in the page before the warning is ------------ #
def test_the_server_warnings_appear_inside_a_standing_live_region():
    attrs, around = PAGE["serverMulti"]
    assert "role" not in attrs
    region = around[-1]
    assert region.get("role") == "status" and "hidden" not in region
    assert "class" not in region  # nothing can hide or restyle it
    for part in ("serverMultiTitle", "serverMultiText"):
        assert region in PAGE[part][1]


# Row 735 N3: the estimate is read once --------------------------------------- #
def test_the_visible_estimate_is_not_read_a_second_time():
    assert PAGE["serverWhy"][0].get("aria-hidden") == "true"
    spoken = PAGE["serverStartWhy"][0]
    assert spoken.get("role") == "status" and "aria-hidden" not in spoken


# Row 735 N4: adopting a background server rewrites the budget ---------------- #
