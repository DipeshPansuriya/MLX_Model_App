"""Project folder screen (row 1104), run against the DOM stub with app.js."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from html.parser import HTMLParser
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
UI = ROOT / "ui"
NODE = shutil.which("node")

SETUP = """
const $$$ = (s) => document.querySelector(s);
const picker = { value: true };
const sent = [];
const reply = (body, ok = true) => Promise.resolve({ ok, statusText: "no", json: async () => body });
const file = { path: "src/<a>.py", sha256: "f".repeat(64), size: 12 };
const over = { value: false };
respond = (path, opts) => {
  sent.push([path, opts && opts.body ? JSON.parse(opts.body) : null]);
  if (path === "/api/projects") return reply({ projects: [{ id: "p1", name: "<work>", root: "/w" }],
    picker: picker.value, picker_reason: picker.value ? null : "Browser mode has no folder picker.",
    max_files: 50 });
  if (path.startsWith("/api/projects/files")) return reply({ files: [file], excluded: 2, truncated: false });
  if (path === "/api/projects/preview") return reply({ files: [{ ...file, text: "<script>x</script>",
    warnings: ["looks like it holds a service token"] }], tokens: over.value ? 9000 : 10, limit: 8192,
    notice: "NOTICE no password" });
  if (path === "/api/projects/attach") return reply({ attachment: "a1", files: [file], tokens: 10 });
  return reply({}, false);
};
const tick = () => new Promise((r) => setTimeout(r, 5));
const press = async (el) => { el.listeners.click(); await tick(); };
const one = (host, kind) => $$$(host).querySelectorAll(`[data-${kind}]`)[0];
"""


def run(tmp_path, scenario):
    script = tmp_path / "both.js"
    script.write_text((UI / "app.js").read_text() + "\n" + (UI / "projects.js").read_text())
    (tmp_path / "s.js").write_text(SETUP.replace("respond =", "globalThis.respond =") + scenario)
    done = subprocess.run(
        [NODE, str(ROOT / "tests" / "ui_harness.js"), str(script), str(tmp_path / "s.js")],
        check=False,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert done.returncode == 0, done.stderr
    return json.loads(done.stdout)


needs_node = pytest.mark.skipif(NODE is None, reason="node is not installed")

FLOW = """
await ProjectsUI.load(); await tick();
await press(one("#projectList", "open"));
await press(one("#projectFiles", "pick"));
const before = $$$("#projectAttach").disabled;
await press($$$("#projectPreviewBtn"));
"""


@needs_node
def test_pick_preview_attach_and_hand_over(tmp_path):
    out = run(
        tmp_path,
        FLOW
        + """
const shown = $$$("#projectPreview").innerHTML;
const ready = !$$$("#projectAttach").disabled;
const warning = $$$("#projectWarning").textContent;
await press($$$("#projectAttach"));
const attached = $$$("#projectAttached").textContent;
const first = ProjectsUI.take();
return { before, shown, ready, warning, attached, first, second: ProjectsUI.take(),
  list: $$$("#projectList").innerHTML, bodies: sent.filter((s) => s[1]) };
""",
    )
    assert out["before"] is True and out["ready"] is True
    assert "&lt;script&gt;" in out["shown"] and "<script>" not in out["shown"]
    assert "service token" in out["shown"] and "NOTICE" in out["warning"]
    assert "&lt;work&gt;" in out["list"]
    assert "src/<a>.py" in out["attached"]
    assert out["first"] == "a1" and out["second"] is None
    assert out["bodies"] == [
        ["/api/projects/preview", {"id": "p1", "paths": ["src/<a>.py"]}],
        [
            "/api/projects/attach",
            {"id": "p1", "picks": [{"path": "src/<a>.py", "sha256": "f" * 64}]},
        ],
    ]


@needs_node
def test_over_the_limit_or_a_changed_choice_cannot_be_attached(tmp_path):
    out = run(
        tmp_path,
        "over.value = true;"
        + FLOW
        + """
const tooBig = $$$("#projectAttach").disabled;
const words = $$$("#projectError").textContent;
over.value = false;
await press($$$("#projectPreviewBtn"));
await press(one("#projectFiles", "pick"));
return { tooBig, words, afterChange: $$$("#projectAttach").disabled,
  shown: $$$("#projectPreview").innerHTML };
""",
    )
    assert out["tooBig"] is True and "limit" in out["words"]
    assert out["afterChange"] is True and out["shown"] == ""


@needs_node
def test_browser_mode_shows_there_is_no_picker(tmp_path):
    out = run(
        tmp_path,
        """
picker.value = false;
await ProjectsUI.load(); await tick();
return { off: $$$("#projectChoose").disabled, note: $$$("#projectNote").textContent };
""",
    )
    assert out == {"off": True, "note": "Browser mode has no folder picker."}


class Page(HTMLParser):
    def __init__(self):
        super().__init__()
        self.tags: list[tuple[str, dict]] = []

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_the_fragment_has_every_element_and_nothing_the_page_policy_refuses():
    page = Page()
    page.feed((UI / "projects.html").read_text())
    ids = {a.get("id") for _, a in page.tags}
    script = (UI / "projects.js").read_text()
    wanted = set(re.findall(r'"#(project[A-Za-z]+)"', script))
    assert wanted and wanted <= ids
    for tag, attrs in page.tags:
        assert tag not in ("script", "form", "style", "iframe")
        assert not [name for name in attrs if name.startswith("on") or name == "style"]
        if tag == "button":
            assert attrs.get("type") == "button"
