"""Row 960: background default per engine, open-server warnings, 2.0.13 release."""

import json
from pathlib import Path

from test_row888_891_ui import CARD, CONFIRM
from test_ui_behaviour import needs_node, run

ROOT = Path(__file__).resolve().parent.parent
PAGE = " ".join((ROOT / "ui" / "index.html").read_text("utf-8").split())
WARNING = "A background MLX server has no password and keeps running until Stop."


def _remember(app, monkeypatch, engine, background):
    monkeypatch.setattr(app.STATE, "server_pid", 4242)
    monkeypatch.setattr(app.STATE, "server_repos", ["org/a"])
    monkeypatch.setattr(app.STATE, "server_engine", engine)
    monkeypatch.setattr(app.STATE, "server_background", background)
    app._remember_models(4242)


class TestSavedChoice:
    def test_nothing_saved_has_no_choice(self, app_module):
        app_module._last_models_file().unlink(missing_ok=True)
        assert app_module._saved_backgrounds() == {}

    def test_each_engine_keeps_its_own_choice(self, app_module, monkeypatch):
        app = app_module
        app._last_models_file().unlink(missing_ok=True)
        _remember(app, monkeypatch, "optiq", False)
        assert app._saved_backgrounds() == {"optiq": False}
        _remember(app, monkeypatch, "mlx", True)
        assert app._saved_backgrounds() == {"optiq": False, "mlx": True}
        assert app.STATE.snapshot()["server"]["last_background"] == app._saved_backgrounds()

    def test_an_unreadable_or_odd_value_is_no_choice(self, app_module):
        app = app_module
        app.APP_DIR.mkdir(parents=True, exist_ok=True)
        # "background" alone came from an unreleased build whose default was on.
        for text in (
            "{",
            "[]",
            json.dumps({"repos": ["org/a"], "background": True}),
            json.dumps({"backgrounds": [True]}),
            json.dumps({"backgrounds": {"mlx": "yes", "other": True}}),
        ):
            app._last_models_file().write_text(text, "utf-8")
            assert app._saved_backgrounds() == {}, text


# Picks an engine, or clicks the box, the way the page's own listeners do.
ACTIONS = """
const box = () => el("#serverBackground").checked;
const pick = (id) => { el("#engineSelect").value = id;
  el("#engineSelect").listeners.change(); };
const click = (on) => { el("#serverBackground").checked = on;
  el("#serverBackground").listeners.change(); };
wire();
"""


@needs_node
class TestBox:
    def test_nothing_saved_is_off_for_mlx_and_on_for_optiq(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + ACTIONS
            + """
draw();
const seen = [box()];
pick("optiq"); seen.push(box());
pick("mlx"); seen.push(box());
draw(); seen.push(box());
return seen;
""",
        )
        assert out == [False, True, False, False]

    def test_a_page_that_opens_on_optiq_is_on(self, tmp_path):
        out = run(
            tmp_path,
            CARD + 'draw({ last_engine: "optiq" }); return el("#serverBackground").checked;',
        )
        assert out is True

    def test_a_saved_choice_is_kept_for_its_engine(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + ACTIONS
            + """
draw({ last_background: { mlx: true, optiq: false } });
const seen = [box()];
pick("optiq"); seen.push(box());
pick("mlx"); seen.push(box());
return seen;
""",
        )
        assert out == [True, False, True]

    def test_a_click_stays_with_its_engine(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + ACTIONS
            + """
draw();
click(true);
pick("optiq"); const seen = [box()];
click(false);
pick("mlx"); seen.push(box());
pick("optiq"); seen.push(box());
return seen;
""",
        )
        assert out == [True, True, False]

    def test_a_running_server_shows_its_mode_and_the_other_engine_its_default(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + ACTIONS
            + """
draw({ running: true, pid: 7, repo: "org/custom", repos: ["org/custom"],
  engine: "optiq", background: true });
const seen = [box()];
pick("mlx"); seen.push(box());
pick("optiq"); seen.push(box(), el("#serverStartBtn").disabled);
return seen;
""",
        )
        assert out == [True, False, True, True]

    def test_use_mlx_instead_turns_it_off(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + ACTIONS
            + """
draw({ last_engine: "optiq" });
el("#serverUseMlx").listeners.click();
return box();
""",
        )
        assert out is False

    def test_the_confirm_panel_follows_the_engine(self, tmp_path):
        out = run(
            tmp_path,
            CONFIRM
            + ACTIONS
            + """
draw();
return openStartConfirm().then(() => {
  const mlxBox = el("#confirmBackground").checked;
  pick("optiq");
  return openStartConfirm().then(() => [mlxBox, el("#confirmBackground").checked]);
});
""",
        )
        assert out == [False, True]

    def test_a_choice_made_in_the_panel_stays_with_its_engine(self, tmp_path):
        out = run(
            tmp_path,
            CONFIRM
            + ACTIONS
            + """
draw();
return openStartConfirm().then(() => {
  el("#confirmBackground").checked = true;
  return confirmStart().then(() => { pick("optiq"); pick("mlx");
    return [box(), sent.at(-1)[1].background]; });
});
""",
        )
        assert out == [True, True]


@needs_node
class TestRestart:
    def test_restart_uses_the_mode_of_the_engine_that_ran(self, tmp_path):
        out = run(
            tmp_path,
            CONFIRM
            + ACTIONS
            + """
draw();
pick("optiq");
return Promise.resolve(el("#serverRestartBtn").listeners.click()).then(() => sent.at(-1)[1]);
""",
        )
        assert out == {"repo": "org/custom", "background": False}


@needs_node
class TestOpenServerWarning:
    def test_the_mlx_connect_lines_warn_and_optiq_ones_do_not(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + ACTIONS
            + """
draw();
const seen = [el("#mlxOpenWarn").hidden, el("#mlxModelHint").hidden];
pick("optiq");
return [...seen, el("#mlxOpenWarn").hidden, el("#mlxModelHint").hidden];
""",
        )
        assert out == [False, False, True, True]

    def test_the_note_under_a_blocked_cache_setting_is_a_sentence(self, tmp_path):
        out = run(
            tmp_path,
            CONFIRM
            + """
const settingsOnly = globalThis.fetch;
globalThis.fetch = (url, init) => url.startsWith("/api/config")
  ? reply({ settings, performance: { blocked: { save_memory: "it keeps a window" } } })
  : settingsOnly(url, init);
draw();
return openStartConfirm().then(() => el("#confirm-kv_bits-note").textContent);
""",
        )
        assert out == "Not available for this model: it keeps a window."


class TestPageWords:
    def test_both_background_notes_say_the_mlx_server_is_open(self):
        assert PAGE.count(WARNING) == 2

    def test_the_connect_warning_says_open_and_how_to_end_it(self):
        words = PAGE.split('id="mlxOpenWarn"')[1].split("</p>")[0]
        for part in ("no password", "website open in your browser", "Stop"):
            assert part in words


def _flat(name):
    return " ".join((ROOT / name).read_text("utf-8").split())


class TestReleaseText:
    def test_the_changelog_entry_is_dated_and_warns(self):
        entry = _flat("CHANGELOG.md").split("## [2.0.13] — 2026-11-28")[1].split("## [2.0.12]")[0]
        for part in (
            "no password",
            "A website open in your browser can also send it requests",
            "DNS rebinding",
            "no memory limit",
            "on by default for OptiQ and off for MLX",
            "Do not expose port 8080",
            "server log",
        ):
            assert part in entry, part
        assert "never been started" not in entry
        assert "has not been started with a real model" not in entry

    def test_readme_and_security_name_dns_rebinding(self):
        for name in ("README.md", "docs/SECURITY.md"):
            words = _flat(name)
            assert "possibly a web page" not in words, name
            assert "DNS rebinding" in words, name
            assert "does not stop DNS rebinding" in words, name
