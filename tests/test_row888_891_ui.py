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


class TestRow889Reasoning:
    def test_the_saved_choice_reaches_both_commands_as_a_real_flag(self, app_module):
        app = app_module
        for choice, flag in (("on", "true"), ("off", "false"), (None, None)):
            app.save_settings({"server_reasoning": choice})
            settings = app.server_settings(["org/a"])
            mlx = app._server_command("org/a", settings, 8080)
            optiq = app.engines.OPTIQ.command(
                "py",
                "snap",
                8080,
                settings=app._direct_settings(settings),
                performance={},
                origins="o",
            )
            for cmd in (mlx, optiq):
                if flag is None:
                    assert "--chat-template-args" not in cmd
                else:
                    found = cmd[cmd.index("--chat-template-args") + 1]
                    assert found == f'{{"enable_thinking":{flag}}}'
        for engine in (app.engines.MLX, app.engines.OPTIQ):
            assert "server_reasoning" in engine.setting_keys

    def test_only_on_or_off_is_accepted(self, app_module):
        check = app_module._settings_change_problem
        assert check({"values": {"server_reasoning": "on"}}) is None
        assert check({"values": {"server_reasoning": '{"x":1}'}})


# The settings view the panel reads, and every request the page sends.
CONFIRM = (
    CARD
    + """
const sent = [];
const setting = (value) => ({ value, own: value, inherited: null, min: 1, max: 1e11 });
const settings = { max_context_tokens: setting(null), server_reasoning: setting(null),
  prompt_cache_size: setting(null), prompt_cache_bytes: setting(null),
  kv_bits: setting(null), performance_mode: setting(null),
  server_max_reply_tokens: setting(4096) };
globalThis.fetch = (url, init) => {
  if (init?.method === "POST") sent.push([url, JSON.parse(init.body)]);
  return reply(url.startsWith("/api/config")
    ? { settings, performance: { blocked: {} } } : { ok: true });
};
const field = (key) => el(`#confirm-${key}`);
"""
)


@needs_node
class TestRow889Confirm:
    def test_start_shows_the_panel_and_cancel_starts_nothing(self, tmp_path):
        out = run(
            tmp_path,
            CONFIRM
            + """
draw();
return openStartConfirm().then(() => {
  const open = [el("#startConfirm").hidden, el("#confirmEngine").textContent,
    el("#confirmModel").textContent, field("max_context_tokens").disabled,
    el("#confirm-max_context_tokens-note").textContent,
    field("server_max_reply_tokens").value, el("#confirmBackground").checked];
  closeStartConfirm();
  return [...open, el("#startConfirm").hidden, sent.length];
});
""",
        )
        assert out[:4] == [False, "MLX (built in)", "org/custom", True]
        assert "MLX has no context limit" in out[4]
        assert out[5:] == ["4096", True, True, 0]
        assert "Vision" in PAGE and "Not supported by either engine" in PAGE

    def test_confirm_saves_the_edits_for_the_model_then_starts(self, tmp_path):
        out = run(
            tmp_path,
            CONFIRM
            + """
draw({ engines: [mlx, optiq, splash], last_engine: "optiq" });
return openStartConfirm().then(() => {
  const enabled = !field("max_context_tokens").disabled;
  field("max_context_tokens").value = "32768";
  field("server_reasoning").value = "off";
  field("kv_bits").value = "8";
  el("#confirmBackground").checked = false;
  return confirmStart().then(() => [enabled, sent]);
});
""",
        )
        assert out[0] is True
        assert out[1] == [
            [
                "/api/server/settings",
                {
                    "repo": "org/custom",
                    "values": {
                        "max_context_tokens": 32768,
                        "server_reasoning": "off",
                        "performance_mode": "advanced",
                        "kv_bits": 8,
                    },
                },
            ],
            [
                "/api/server/start",
                {"repo": "org/custom", "engine": "optiq", "anthropic": True, "background": False},
            ],
        ]

    def test_a_changed_card_closes_the_panel(self, tmp_path):
        out = run(
            tmp_path,
            CONFIRM
            + """
draw();
return openStartConfirm().then(() => {
  state.engine = "optiq";
  serverCardChanged();
  const hidden = el("#startConfirm").hidden;
  return confirmStart().then(() => [hidden, sent.length]);
});
""",
        )
        assert out == [True, 0]


# A ready server of one engine, and what the connect area then shows.
CONNECT = (
    CARD
    + """
const live = (over) => ({ running: true, ready: true, pid: 5, repo: "org/custom",
  repos: ["org/custom"], ...over });
"""
)


@needs_node
class TestRow888Connect:
    def test_the_area_appears_only_after_a_start(self, tmp_path):
        out = run(
            tmp_path,
            CONNECT
            + """
draw();
const before = el("#connectPanel").hidden;
draw(live({ ready: false }));
const loading = [el("#connectPanel").hidden, el("#connectState").textContent];
draw(live());
return [before, loading, el("#connectState").textContent];
""",
        )
        assert out[0] is True and out[1][0] is False
        assert "still starting" in out[1][1] and out[2] == ""

    def test_the_harness_context_window_is_the_users_own(self, tmp_path):
        out = run(
            tmp_path,
            CONNECT
            + """
draw(live({ engine: "optiq", anthropic: true }));
const empty = [el("#dshSettings").textContent, el("#copyDsh").disabled];
el("#dshContext").value = "65536";
el("#dshContext").listeners.input();
const chosen = [el("#dshSettings").textContent, el("#copyDsh").disabled];
el("#dshContext").value = "-3";
el("#dshContext").listeners.input();
return [empty, chosen, el("#copyDsh").disabled, el("#claudeSettings").textContent];
""",
        )
        empty, chosen, bad, claude = out
        assert "Base URL: http://127.0.0.1:8080/v1" in empty[0] and "default_model" in empty[0]
        assert "type a number above" in empty[0] and empty[1] is True
        assert "Context window: 65536" in chosen[0] and chosen[1] is False
        assert bad is True and "ANTHROPIC_BASE_URL=http://127.0.0.1:8080" in claude
        assert "sk-optiq" not in empty[0] + chosen[0] + claude

    def test_mlx_says_there_is_no_claude_route_and_nothing_writes_files(self, tmp_path):
        out = run(
            tmp_path,
            CONNECT + 'draw(live()); return [el("#claudeSettings").textContent];',
        )
        assert "MLX engine has no Anthropic route" in out[0]
        words = " ".join(PAGE.split())
        assert "Connect your tool" in words and "exhaust this Mac's memory" in words
        assert "does not change any other app's settings" in words
        assert 'id="dshContext"' in PAGE and 'value="' not in PAGE.split('id="dshContext"')[1][:200]


class TestRow888Messages:
    def test_a_refused_start_says_the_next_step(self, base_url):
        from test_row874_app import _post

        status, body = _post(base_url, "/api/server/start", {"repo": "org/none"})
        assert status == 400
        assert "not installed completely" in body["error"] and "Download" in body["error"]
