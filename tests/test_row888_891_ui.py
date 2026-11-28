"""Rows 888-891: easy server start and connect screens (fakes only)."""

import json
from pathlib import Path

from test_row874_ui import SERVER
from test_ui_behaviour import needs_node, run

UI = Path(__file__).resolve().parent.parent / "ui"
PAGE = (UI / "index.html").read_text("utf-8")

# A drawn card with one installed model; ``over`` changes the server block.
CARD = (
    SERVER
    + """
const draw = (over = {}, top = {}) =>
  render(snap({ server: { ...server, last_models: ["org/custom"], ...over }, ...top }));
const missing = (what, extra = {}) =>
  [mlx, { ...optiq, state: what, ...extra }, splash];
const el = (s) => document.querySelector(s);
"""
)


def _remember(app, data):
    app.APP_DIR.mkdir(parents=True, exist_ok=True)
    app._last_models_file().write_text(json.dumps(data), "utf-8")


class TestRow890DefaultEngine:
    def test_nothing_saved_means_optiq(self, app_module):
        app_module._last_models_file().unlink(missing_ok=True)
        assert app_module._last_engine() == "optiq"

    def test_a_saved_mlx_or_optiq_choice_is_kept(self, app_module):
        # An MLX start is saved without an engine name.
        _remember(app_module, {"repos": ["org/a"]})
        assert app_module._last_engine() == "mlx"
        _remember(app_module, {"repos": ["org/a"], "engine": "optiq"})
        assert app_module._last_engine() == "optiq"


@needs_node
class TestRow890Install:
    def test_not_installed_says_so_and_installs_with_the_setup_action(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + """
const sent = [];
globalThis.fetch = (url, init) => { sent.push([url, init?.body]); return reply({ ok: true }); };
draw({ last_engine: "optiq", engines: missing("not_installed") });
const seen = [el("#serverInstall").hidden, el("#serverInstallText").textContent,
  el("#serverStartBtn").disabled, el("#ltInstall").hidden, el("#ltEngine").value];
return installEngine("optiq").then(() => [...seen, sent.find((c) => c[0].includes("install"))]);
""",
        )
        assert out[0] is False and "OptiQ is not installed" in out[1] and out[2] is True
        assert out[3] is False and out[4] == "optiq"
        assert out[5] == ["/api/engines/install", '{"engine":"optiq"}']

    def test_progress_failure_and_impossible_are_said_with_mlx_offered(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + """
const read = () => [el("#serverInstallText").textContent, el("#serverInstallBtn").hidden,
  el("#serverInstallBtn").disabled, el("#serverUseMlx").hidden,
  el("#serverInstallLog").textContent];
const base = { last_engine: "optiq", engines: missing("not_installed") };
draw(base, { install: { running: true, kind: "optiq install", lines: ["Collecting x"] } });
const busy = read();
draw({ ...base, engines: missing("broken") },
  { install: { running: false, kind: "optiq install", lines: ["a", "error: no network"] } });
const failed = read();
draw({ ...base, engines: missing("unsupported", { unsupported: "OptiQ needs Python 3.15." }) });
const never = read();
return [busy, failed, never];
""",
        )
        busy, failed, never = out
        assert "Installing OptiQ" in busy[0] and busy[2] is True and "Collecting x" in busy[4]
        assert "did not finish" in failed[0] and "no network" in failed[0]
        assert failed[2] is False and failed[3] is False
        assert "Python 3.15" in never[0] and "MLX" in never[0]
        assert never[1] is True and never[3] is False

    def test_missing_mlx_uses_the_packages_install(self, tmp_path):
        out = run(
            tmp_path,
            CARD
            + """
const sent = [];
globalThis.fetch = (url, init) => { sent.push([url, init?.body]); return reply({ ok: true }); };
draw({}, { versions: { mlx_missing: true, mlx_lm_missing: true } });
const seen = [el("#serverInstallText").textContent, el("#serverUseMlx").hidden,
  el("#serverStartBtn").disabled];
return installEngine("mlx").then(() => [...seen, sent.find((c) => c[0].includes("install"))]);
""",
        )
        assert "MLX is not installed" in out[0] and out[1] is True and out[2] is True
        assert out[3] == ["/api/install", '{"kind":"install"}']

    def test_an_installed_engine_shows_nothing(self, tmp_path):
        out = run(tmp_path, CARD + 'draw(); return [el("#serverInstall").hidden];')
        assert out == [True]


class TestRow891Background:
    def test_the_choice_is_saved_and_on_by_default(self, app_module, monkeypatch):
        app = app_module
        app._last_models_file().unlink(missing_ok=True)
        assert app._last_background() is True
        _remember(app, {"repos": ["org/a"]})  # saved before this release
        assert app._last_background() is True
        monkeypatch.setattr(app.STATE, "server_pid", 4242)
        monkeypatch.setattr(app.STATE, "server_repos", ["org/a"])
        monkeypatch.setattr(app.STATE, "server_background", False)
        app._remember_models(4242)
        assert app._last_background() is False
        assert app.STATE.snapshot()["server"]["last_background"] is False

    def test_the_page_checks_it_and_says_what_it_does(self):
        assert 'id="serverBackground" checked' in PAGE
        words = " ".join(PAGE.split())
        assert "Run in background" in words
        assert "keeps running and holding memory until you press Stop" in words


@needs_node
class TestRow891Box:
    def test_default_on_saved_off_kept_and_a_click_stays(self, tmp_path):
        for saved, shown in ((None, True), (False, False)):
            over = "" if saved is None else "last_background: false"
            out = run(
                tmp_path,
                CARD
                + f"""
draw({{ {over} }});
const first = el("#serverBackground").checked;
el("#serverBackground").checked = !first;
draw({{ {over} }});
return [first, el("#serverBackground").checked];
""",
            )
            assert out == [shown, not shown]
