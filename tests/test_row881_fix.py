"""Row 881: pre-release fixes. Fake processes, ports and DOM; nothing real."""

import json
import shutil
import sys
import threading
import time

import pytest
from test_row874_app import REPO, Rig, _post, rig  # noqa: F401 - the fixture
from test_row874_ui import SERVER
from test_ui_behaviour import needs_node, run

import engine_runtime as runtime
import engines
import optiq_engine

LIVE = """
const live = { ...server, running: true, pid: 7, repos: ["org/custom"], repo: "org/custom",
  engine: "optiq", anthropic: false, ready: true };
"""
CLICK = """
const sent = [];
api = async (path, options) => {
  if (path !== "/api/server/start") throw new Error("not asked here");
  sent.push(JSON.parse(options.body));
  return {};
};
const click = async () => {
  sent.length = 0;
  state.serverSending = false;
  await document.querySelector("#serverRestartBtn").listeners.click({});
  return sent[0];
};
"""


# ---- 1: the Anthropic switch and the remembered start ----------------------------
@needs_node
def test_the_switch_shows_the_running_server_until_it_is_touched(tmp_path):
    out = run(
        tmp_path,
        SERVER
        + LIVE
        + """
        render(snap({ server: live }));
        const first = [document.querySelector("#engineAnthropic").checked, serverPending(),
          serverChoice(["org/a"]).anthropic];
        state.anthropic = true;
        return [...first, serverPending()];
        """,
    )
    assert out == [False, False, False, True]


@needs_node
def test_a_stopped_card_starts_from_the_remembered_switch_and_is_on_by_default(tmp_path):
    out = run(
        tmp_path,
        SERVER
        + """
        render(snap({ server: { ...server, last_engine: "optiq" } }));
        const fresh = serverChoice(["org/a"]).anthropic;
        render(snap({ server: { ...server, last_engine: "optiq", last_anthropic: false } }));
        return [fresh, serverChoice(["org/a"]).anthropic];
        """,
    )
    assert out == [True, False]


def test_the_remembered_start_keeps_the_anthropic_switch(rig):  # noqa: F811
    app = rig.app
    assert app.STATE.snapshot()["server"]["last_anthropic"] is True
    app.worker_server_start(REPO, False, None, "optiq", False)
    app._remember_models(rig.started[-1].pid)
    assert app.STATE.snapshot()["server"]["last_anthropic"] is False
    app.stop_server()
    rig.start("mlx")
    app._remember_models(rig.started[-1].pid)
    assert app.STATE.snapshot()["server"]["last_anthropic"] is True


# ---- 3: Restart on the crash notice uses what ran --------------------------------
@needs_node
def test_restart_uses_the_engine_and_switch_that_ran(tmp_path):
    out = run(
        tmp_path,
        SERVER
        + LIVE
        + CLICK
        + """
        render(snap({ server: live }));
        // Unapplied choices are not what ran.
        state.engine = "mlx"; state.anthropic = true;
        render(snap({ server: { ...server, down_notice: "it died" } }));
        return await click();
        """,
    )
    assert (out["engine"], out["anthropic"]) == ("optiq", False)


@needs_node
def test_the_card_and_restart_keep_an_engine_that_never_reported_ready(tmp_path):
    out = run(
        tmp_path,
        SERVER
        + LIVE
        + CLICK
        + """
        render(snap({ server: { ...live, ready: false, anthropic: true } }));
        render(snap({ server: { ...server, down_notice: "it died" } }));
        const first = [engineId(), (await click()).engine];
        render(snap({ server: { ...live, pid: 8, engine: "mlx", anthropic: false } }));
        render(snap({ server: { ...server, down_notice: "it died", last_engine: "optiq" } }));
        return [...first, engineId(), (await click()).engine || "mlx"];
        """,
    )
    assert out == ["optiq", "optiq", "mlx", "mlx"]


# ---- 2: Install / Update never deletes a working engine -------------------------
def test_installing_over_a_working_engine_is_refused_and_removes_nothing(
    rig,  # noqa: F811
    base_url,
    monkeypatch,
):
    monkeypatch.setattr(optiq_engine, "python_problem", lambda: None)
    app, ran = rig.app, []
    python = optiq_engine.python_path(app.APP_DIR)
    with pytest.raises(runtime.Refused, match="already installed"):
        engines.OPTIQ.install(app.APP_DIR, ran.append, run=lambda *a: ran.append(a) or 0)
    status, reply = _post(base_url, "/api/engines/install", {"engine": "optiq"})
    assert status == 409 and "already installed" in reply["error"]
    assert python.exists() and not ran and not app.STATE.install["running"]


@needs_node
def test_install_is_off_when_installed_and_an_update_asks_first(tmp_path):
    out = run(
        tmp_path,
        SERVER
        + """
        const card = (state_) => {
          render(snap({ server: { ...server, engines: [mlx, { ...optiq, state: state_ }] } }));
          const found = html("#engineCards").match(/<button[^>]*data-action="install"[^>]*>/);
          return found[0].includes("disabled");
        };
        const buttons = ["installed", "update", "broken", "not_installed"].map(card);
        const sent = [], asked = [];
        api = async (path) => { sent.push(path); return {}; };
        confirm = (words) => { asked.push(words); return false; };
        card("update");
        await changeEngine("optiq", "install");
        card("not_installed");
        await changeEngine("optiq", "install");
        return [buttons, asked, sent.filter((path) => path === "/api/engines/install").length];
        """,
    )
    assert out[0] == [True, False, False, False]
    assert len(out[1]) == 1 and "removed before the download" in out[1][0]
    assert out[2] == 1


# ---- 5: the background log and the engine folder --------------------------------
def test_a_background_direct_server_log_has_no_key(app_module, monkeypatch):
    app, key = app_module, runtime.new_key()
    monkeypatch.setattr(app, "_watch_server", lambda *a, **k: None)
    app.LOG_DIR.mkdir(parents=True, exist_ok=True)
    app.STATE.server_engine = "optiq"
    code = f"import sys; print('auth {key}'); print('on stderr {key}', file=sys.stderr)"
    proc = app._spawn_server([sys.executable, "-c", code], {}, [REPO], True)
    assert proc.wait(timeout=10) == 0
    deadline = time.monotonic() + 10
    while (log := app.SERVER_LOG.read_text()).count("[key hidden]") < 2:
        assert time.monotonic() < deadline, log
        time.sleep(0.05)
    assert key not in log


@pytest.mark.parametrize("added", ["evil-1.0.dist-info", "flask-0.0.1.dist-info"])
def test_a_start_refuses_a_package_the_lock_does_not_name(rig, added):  # noqa: F811
    python = optiq_engine.python_path(rig.app.APP_DIR)
    site = next(python.parent.parent.glob("lib/python*/site-packages"))
    for fine in ("pip-26.0.dist-info", "argon2_cffi_bindings-26.2.0.dist-info"):
        (site / fine).mkdir()
    assert optiq_engine.python_path(rig.app.APP_DIR) == python
    (site / added).mkdir()
    with pytest.raises(runtime.Refused, match="reviewed lock"):
        optiq_engine.python_path(rig.app.APP_DIR)


def test_a_start_refuses_a_linked_engine_folder(rig, tmp_path):  # noqa: F811
    venv = optiq_engine.venv_dir(rig.app.APP_DIR)
    shutil.move(venv, tmp_path / "elsewhere")
    venv.symlink_to(tmp_path / "elsewhere")
    with pytest.raises(runtime.Refused, match="is a link"):
        optiq_engine.python_path(rig.app.APP_DIR)


# ---- 4: the watch stops a direct server ------------------------------------------
def test_the_watch_stops_it_and_says_why(app_module, monkeypatch):
    app, watch = app_module, app_module._watch_server  # the rig replaces it
    fake = Rig(app, monkeypatch)
    proc = fake.start()
    fake.holders, fake.auth = {proc.pid}, "open"
    watcher = threading.Thread(target=watch, args=(proc.pid, REPO, proc), daemon=True)
    watcher.start()
    watcher.join(10)
    stopped = proc.returncode is not None
    proc.terminate()  # so a watch that did not stop it ends too
    watcher.join(5)
    assert stopped
    assert "without the key" in app.STATE.server_down_notice


# ---- 6: what OptiQ does not use or report ----------------------------------------
def _save(app, **server):
    app.APP_DIR.joinpath("settings.json").write_text(json.dumps(server))


def test_only_settings_optiq_uses_ask_for_a_restart(rig):  # noqa: F811
    app = rig.app
    rig.ready()
    pending = lambda: app.STATE.snapshot()["server"]["settings_pending"]  # noqa: E731
    assert pending() is False
    _save(app, chat_temperature=0.5, chat_top_p=0.5, chat_max_reply_tokens=100)
    assert pending() is False
    _save(app, prompt_cache_size=7)
    assert pending() is True
    _save(app, kv_bits=8, performance_mode="advanced")
    assert pending() is True


@needs_node
def test_under_optiq_the_chat_mode_is_the_saved_one_marked_unreported(tmp_path):
    out = run(
        tmp_path,
        SERVER
        + LIVE
        + """
        const performance = { saved: "Standard", models: { "org/custom":
          { saved: "Save memory", in_force: null, note: null } } };
        document.querySelector("#chatModelSelect").value = "org/custom";
        const text = () => document.querySelector("#chatModeName").textContent;
        renderChatMode({ ...live, performance });
        const optiqText = text();
        renderChatMode({ ...live, engine: "mlx", performance });
        state.server = live;
        return [optiqText, text()];
        """,
    )
    assert out == ["Save memory (saved; not reported by OptiQ)", "not reported yet"]
