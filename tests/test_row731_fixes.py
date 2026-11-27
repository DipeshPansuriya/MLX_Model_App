"""Rows 733 and 735: the security Info items and the QA Low findings left after row 713.

(Row 731, the F9 admission rule, is tested in test_row656_guard.py.) No model is
loaded, no process started and no window or browser tab opened.
"""

from __future__ import annotations

import importlib.util
import os
import stat
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
def test_a_link_planted_at_the_budget_temp_file_is_not_followed(app_module, tmp_path, ram):
    victim = tmp_path / "victim"
    victim.write_text("keep", "utf-8")
    temp = app_module._budget_file().with_suffix(".budget.tmp")
    temp.symlink_to(victim)
    app_module._publish_server_budget()
    assert victim.read_text("utf-8") == "keep"
    written = app_module._budget_file()
    assert not written.is_symlink() and budget_in_file(app_module) == SHARE
    assert stat.S_IMODE(written.stat().st_mode) == 0o600
    assert not temp.is_symlink() and not temp.exists()


def test_a_budget_that_cannot_be_written_is_ignored(app_module, ram):
    app_module._budget_file().with_suffix(".budget.tmp").mkdir()
    app_module._publish_server_budget()
    assert not app_module._budget_file().exists()


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


def test_a_chat_model_over_the_whole_share_still_leaves_no_room(app_module, monkeypatch, ram):
    cache_model(app_module, CHAT, weights=SHARE + GB)
    app_module.STATE.loaded_repo = CHAT
    app_module._publish_server_budget()
    # 1, not 0: the guard ignores anything below 1, which would give all the room back.
    assert budget_in_file(app_module) == 1
    monkeypatch.setenv("MLX_STUDIO_BUDGET_FILE", str(app_module._budget_file()))
    rig = Rig(app_module.APP_DIR)
    rig.provider.load("org/a")
    assert rig.status()["budget_bytes"] == 1


# Row 733 N3: the Chat tab loads installed models only ------------------------ #
@pytest.mark.parametrize("repo", ["org/absent", "/tmp/folder", "../x", "a\u0000b"])
def test_a_load_of_a_model_that_is_not_installed_is_refused(app_module, base_url, ram, loads, repo):
    status, body = post(base_url, "/api/load", {"repo": repo})
    assert status == 400 and "not installed" in body["error"]
    assert loads[0] == [] and app_module.STATE.loading_repo is None


def test_a_load_of_a_partial_download_is_refused(app_module, base_url, ram, loads):
    cache_model(app_module, CHAT)
    entry = app_module.CACHE_DIR / "models--org--chat"
    (entry / "blobs").mkdir()
    (entry / "blobs" / "x.incomplete").write_bytes(b"x")
    status, body = post(base_url, "/api/load", {"repo": CHAT})
    assert status == 400 and "not installed completely" in body["error"]
    assert loads[0] == []


def test_a_load_of_an_installed_model_still_starts(app_module, base_url, ram, loads):
    cache_model(app_module, CHAT)
    assert post(base_url, "/api/load", {"repo": CHAT}) == (200, {"ok": True, "loading": CHAT})
    assert loads[1].wait(5) and loads[0] == [CHAT]


def test_the_model_already_in_memory_needs_no_files(app_module, base_url, ram, loads):
    app_module.STATE.loaded_repo = "org/gone"
    status, body = post(base_url, "/api/load", {"repo": "org/gone"})
    assert (status, body.get("already")) == (200, True)


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


@needs_node
@pytest.mark.parametrize("load", [BY_BUTTON, BY_MODEL_CARD, BY_OFFER_CARD])
def test_a_refused_load_stays_visible_after_later_polls(tmp_path, load):
    out = ui(
        tmp_path,
        REFUSED + load + " await show(view()); await show(running()); await show(view());"
        ' return [problem(), text("#toast")];',
    )
    assert out == [[WHY, True, False], ""]


@needs_node
def test_the_next_load_clears_the_refusal(tmp_path):
    out = ui(
        tmp_path,
        REFUSED + BY_BUTTON + " refuse = false; " + BY_BUTTON + " return problem();",
    )
    assert out == ["", False, True]


@needs_node
def test_the_same_refusal_is_written_again(tmp_path):
    out = ui(
        tmp_path,
        REFUSED
        + BY_BUTTON
        + ' const before = $("#loadProblem").textWrites; '
        + BY_BUTTON
        + ' return [$("#loadProblem").textWrites - before, problem()[0]];',
    )
    # Emptied, then written: a screen reader says it again.
    assert out == [2, WHY]


@needs_node
def test_dismiss_clears_it_and_keeps_the_focus_in_the_page(tmp_path):
    out = ui(
        tmp_path,
        REFUSED + BY_BUTTON + ' let focused = false; $(".nav-item.active").focus = () => {'
        ' focused = true; }; $("#loadProblemDismiss").listeners.click();'
        " return [problem(), focused];",
    )
    assert out == [["", False, True], True]


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


def test_the_refusal_is_an_alert_outside_every_view():
    attrs, around = PAGE["loadProblem"]
    assert attrs.get("role") == "alert" and "hidden" not in attrs
    # Loads start on the Chat and on the Models page, so no view may hide it.
    for outer in around:
        assert "hidden" not in outer and "view" not in outer.get("class", "").split()
    assert PAGE["loadProblemDismiss"][0].get("type") == "button"
    css = (ROOT / "ui" / "style.css").read_text("utf-8")
    assert ".load-problem.show" in css


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
    assert PAGE["serverEstimate"][0].get("aria-hidden") == "true"
    spoken = PAGE["serverStartWhy"][0]
    assert spoken.get("role") == "status" and "aria-hidden" not in spoken


# Row 735 N4: adopting a background server rewrites the budget ---------------- #
def test_adopting_a_server_replaces_the_budget_of_the_last_session(app_module, monkeypatch, ram):
    app_module._budget_file().write_text("1", "utf-8")
    apps.adopt(app_module, monkeypatch, repos=[A, B])
    assert budget_in_file(app_module) == SHARE
