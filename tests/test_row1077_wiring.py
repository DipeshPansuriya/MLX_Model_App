"""Row 1077 wiring: the app runs login_agent, the screen offers it, OptiQ only."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

import pytest
import test_row1077_autostart as base
from test_row892_connection import SCREEN, UI, post
from test_ui_behaviour import needs_node, run

import login_agent

mac, bundle = base.mac, base.bundle
REPO, result, switch_on = base.REPO, base.result, base.switch_on
PAGE = (UI / "index.html").read_text("utf-8")


def get(base_url, path="/api/autostart"):
    try:
        with urllib.request.urlopen(urllib.request.Request(base_url + path), timeout=10) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as error:
        return error.code, json.loads(error.read())


# ───────────────────────────── a password is required ─────────────────────────


def test_the_mlx_engine_cannot_be_switched_on(app_module, mac, bundle):
    login_agent.set_default(app_module, REPO, "mlx")
    view = login_agent.status(app_module, resources=bundle[0])
    assert "password" in view["autostart_refusal"] and "OptiQ" in view["autostart_refusal"]
    with pytest.raises(app_module.engine_runtime.Refused, match="password"):
        login_agent.enable(app_module, resources=bundle[0])
    assert not login_agent.plist_path().exists()
    assert login_agent.read_default(app_module)["autostart"] is False


def test_an_mlx_default_switches_an_enabled_login_start_off(app_module, mac, bundle):
    switch_on(app_module, bundle, "optiq")
    login_agent.set_default(app_module, REPO, "mlx")
    record = login_agent.read_default(app_module)
    assert record["engine"] == "mlx" and record["autostart"] is False
    assert not login_agent.plist_path().exists()


def test_a_record_that_asks_for_mlx_at_login_starts_nothing(app_module, mac):
    record = {"v": 1, "repo": REPO, "engine": "mlx", "anthropic": True, "autostart": True}
    app_module._write_private(login_agent.record_file(app_module), json.dumps(record))
    assert login_agent.login_start(app_module) == 0
    assert mac.started == []
    assert result(app_module)["result"] == "skipped"
    assert "password" in result(app_module)["reason"]


# ───────────────────────────────── the app ────────────────────────────────────


def test_the_login_flag_starts_the_server_and_never_a_window(app_module, monkeypatch):
    seen = []
    monkeypatch.setattr(app_module.sys, "argv", ["app.py", login_agent.FLAG])
    monkeypatch.setattr(login_agent, "login_start", lambda app: seen.append(app) or 0)
    for name in ("instance_is_running", "serve", "restore_server_state"):
        monkeypatch.setattr(app_module, name, lambda *a, **k: pytest.fail("window path"))
    with pytest.raises(SystemExit) as stop:
        app_module.main()
    assert stop.value.code == 0 and seen == [app_module]


def test_a_normal_launch_refreshes_the_login_item_after_adoption(app_module, monkeypatch):
    order = []

    class Done(Exception):
        pass

    def refresh(app):
        order.append("refresh")
        raise Done

    monkeypatch.setattr(app_module.sys, "argv", ["app.py"])
    monkeypatch.setattr(app_module, "instance_is_running", lambda: False)
    monkeypatch.setattr(app_module, "serve", lambda: None)
    monkeypatch.setattr(app_module, "worker_check_versions", lambda: None)
    monkeypatch.setattr(app_module, "restore_server_state", lambda: order.append("restore"))
    monkeypatch.setattr(login_agent, "refresh", refresh)
    with pytest.raises(Done):
        app_module.main()
    assert order == ["restore", "refresh"]


@pytest.mark.parametrize("switched", [True, False])
def test_a_start_from_the_app_keeps_the_key_clients_have(app_module, mac, bundle, switched):
    switch_on(app_module, bundle, "optiq")
    stored = login_agent.key_file(app_module).read_text()
    if not switched:
        login_agent.disable(app_module)
    app_module.worker_server_start(REPO, True, None, "optiq", True)
    [proc] = mac.started
    used = proc.kwargs["env"][app_module.engines.OPTIQ.key_env]
    assert (used == stored) is switched
    assert used not in " ".join(map(str, proc.args))
    view = login_agent.status(app_module)
    assert view["key_in_use"] is switched and used not in json.dumps(view)


# ───────────────────────────────── routes ─────────────────────────────────────


@pytest.fixture
def installed(app_module, mac, bundle, monkeypatch):
    monkeypatch.setattr(login_agent, "bundle_executable", lambda app, resources=None: bundle[1])
    return mac


def test_the_routes_set_switch_and_clear(app_module, installed, base_url):
    status, view = get(base_url)
    assert status == 200 and view["default"] is None and view["enabled"] is False
    body = {"repo": REPO, "engine": "optiq", "anthropic": False}
    status, view = post(base_url, "/api/autostart/default", body)
    assert status == 200 and view["default"] == body
    status, view = post(base_url, "/api/autostart", {"enabled": True})
    assert status == 200 and view["enabled"] is True and view["login_item"] == "ok"
    key = login_agent.key_file(app_module).read_text()
    assert key not in json.dumps([view, get(base_url)[1]])
    assert post(base_url, "/api/autostart", {"enabled": False})[1]["enabled"] is False
    assert post(base_url, "/api/autostart/clear")[1]["default"] is None
    assert installed.started == []


@pytest.mark.parametrize("enabled", ["true", 1, None])
def test_the_switch_takes_a_boolean_only(app_module, installed, base_url, enabled):
    login_agent.set_default(app_module, REPO, "optiq")
    assert post(base_url, "/api/autostart", {"enabled": enabled})[0] == 400
    assert not login_agent.plist_path().exists()


@pytest.mark.parametrize("body", [{"engine": 3}, {"anthropic": "no"}])
def test_the_default_route_checks_its_types(app_module, installed, base_url, body):
    assert post(base_url, "/api/autostart/default", {"repo": REPO, **body})[0] == 400
    assert not login_agent.record_file(app_module).exists()


def test_a_refusal_is_a_409_with_its_reason(app_module, installed, base_url):
    post(base_url, "/api/autostart/default", {"repo": REPO, "engine": "mlx"})
    status, answer = post(base_url, "/api/autostart", {"enabled": True})
    assert status == 409 and "password" in answer["error"]


@pytest.mark.parametrize(
    "path", ["/api/autostart", "/api/autostart/default", "/api/autostart/clear"]
)
def test_every_route_needs_the_token(app_module, base_url, monkeypatch, path):
    monkeypatch.setattr(app_module.Handler, "_has_token", lambda self: False)
    assert post(base_url, path, {"enabled": True})[0] == 403
    assert get(base_url)[0] == 403


# ───────────────────────────────── the screen ─────────────────────────────────


def test_the_page_has_the_controls_off_by_default():
    start = PAGE.index('id="view-server"')
    server = PAGE[start : PAGE.index('<section class="view', start)]
    assert "Set as default model" in server and "when I log in" in server
    switch = re.search(r'<input[^>]*id="autostartSwitch"[^>]*>', server).group(0)
    assert 'role="switch"' in switch and "checked" not in switch
    assert "no password support" in server
    for name in ("defaultState", "autostartWhy", "autostartLast", "autostartProblem", "keyUse"):
        assert f'id="{name}"' in server


VIEW = (
    SCREEN
    + """
const auto = (over = {}) => ({ available: true, unavailable_reason: null,
  default: { repo: "org/run", engine: "optiq", anthropic: true }, enabled: false,
  login_item: "missing", password: true, password_note: "The server asks for the key.",
  autostart_refusal: null, key_in_use: false, key_file: "/k/autostart.key", last: null,
  problem: null, ...over });
const optiq = { engine: "optiq", anthropic: true,
  engines: [{ id: "optiq", name: "OptiQ", direct: true, anthropic: true }] };
const sw = () => q("#autostartSwitch");
const show = () => ({ checked: !!sw().checked, off: sw().getAttribute("aria-disabled"),
  why: q("#autostartWhy").textContent, what: q("#defaultState").textContent });
"""
)


@needs_node
class TestScreen:
    def test_the_switch_says_why_it_cannot_be_used(self, tmp_path):
        out = run(
            tmp_path,
            VIEW
            + """
            return [{ default: null },
              { autostart_refusal: "Needs a password. Choose OptiQ." },
              { available: false, unavailable_reason: "Only the installed app." },
              {}, { enabled: true, autostart_refusal: "x" }].map((over) => {
                state.autostart = auto(over); paintAutostart(); return show();
              });
            """,
        )
        none, mlx, source, off, on = out
        assert none["off"] == "true" and "default model first" in none["why"]
        assert mlx["off"] == "true" and "Choose OptiQ" in mlx["why"]
        assert source["off"] == "true" and "installed app" in source["why"]
        assert off["off"] == "false" and off["checked"] is False and "run" in off["what"]
        assert on["off"] == "false" and on["checked"] is True  # can always be switched off

    def test_the_buttons_send_the_cards_choice(self, tmp_path):
        out = run(
            tmp_path,
            VIEW
            + """
            render(live(optiq));
            respond = () => Promise.resolve({ ok: true, json: async () => auto() });
            await setDefaultModel();
            sw().checked = true; await switchAutostart();
            state.autostart = auto({ default: null }); paintAutostart();
            sw().checked = true; await switchAutostart();
            return [calls.filter((c) => c.path.startsWith("/api/autostart") && c.opts?.body)
              .map((c) => [c.path, JSON.parse(c.opts.body)]), sw().checked];
            """,
        )
        sent, checked = out
        assert sent == [
            ["/api/autostart/default", {"repo": "org/run", "engine": "optiq", "anthropic": True}],
            ["/api/autostart", {"enabled": True}],
        ]
        assert checked is False

    def test_a_refusal_and_the_last_login_start_are_shown(self, tmp_path):
        out = run(
            tmp_path,
            VIEW
            + """
            render(live());
            respond = () => Promise.resolve({ ok: false, statusText: "no",
              json: async () => ({ error: "Not from a source run." }) });
            await setDefaultModel();
            const refused = q("#autostartProblem").textContent;
            state.autostart = auto({ last: { at: "2026-11-28 09:00:00", result: "skipped",
              reason: "Another program uses port 8080." } });
            paintAutostart();
            return [refused, q("#autostartLast").textContent];
            """,
        )
        assert "source run" in out[0]
        assert "skipped" in out[1] and "port 8080" in out[1]

    def test_the_connect_instructions_say_how_the_key_is_used(self, tmp_path):
        out = run(
            tmp_path,
            VIEW
            + """
            return [false, true].map((stable) => {
              state.autostart = auto({ key_in_use: stable, enabled: stable });
              render(live(optiq));
              return [q("#dshSettings").textContent, q("#keyUse").textContent];
            });
            """,
        )
        (fresh, fresh_use), (stable, stable_use) = out
        assert "new at every start" in fresh and "new at every start" not in stable
        for words in ("Authorization: Bearer", "ANTHROPIC_AUTH_TOKEN", "DeepSeek"):
            assert words in fresh_use and words in stable_use
        assert "stays the same" in stable_use and "/k/autostart.key" in stable_use
