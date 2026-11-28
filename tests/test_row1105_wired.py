"""Row 1105: the finished modules as wired into the app. No network, no pip."""

from __future__ import annotations

import json
import urllib.error
import urllib.request

import pytest

import engine_runtime

REPO = "org/model"


def _call(base, app, path, body=None, token=True):
    headers = {"Content-Type": "application/json"}
    if token:
        headers[app.TOKEN_HEADER] = app.API_TOKEN
    data = None if body is None else json.dumps(body).encode()
    request = urllib.request.Request(base + path, data=data, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, json.loads(reply.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


@pytest.fixture
def base(server):
    return f"http://127.0.0.1:{server.server_address[1]}"


def test_the_plan_route_needs_the_token(base, app_module):
    assert _call(base, app_module, "/api/install/plan", token=False)[0] == 403
    code, plan = _call(base, app_module, "/api/install/plan")
    assert code == 200 and plan["steps"][0]["id"] == "check"


@pytest.mark.parametrize("path", ["/api/install/all", "/api/update/check"])
def test_the_post_routes_need_the_token(base, app_module, path):
    assert _call(base, app_module, path, {}, token=False)[0] == 403


def test_the_update_route_answers(base, app_module, monkeypatch):
    import feature_routes

    monkeypatch.setattr(feature_routes.update_check, "check", lambda current: {"v": current})
    answer = _call(base, app_module, "/api/update/check", {})
    assert answer == (200, {"v": app_module.APP_VERSION})


def test_the_state_carries_memory_and_speed(app_module):
    server = app_module.STATE.snapshot()["server"]
    assert set(server["speed"]) == {"chat", "test"} and "pressure" in server["memory"]


def test_both_engines_keep_their_own_load_test_history(app_module):
    def store(ident, engine):
        app_module._save_loadtest(
            {"id": ident, "repo": REPO, "engine": engine, "status": "done", "rows": []}
        )

    store("o", "optiq")
    for at in range(app_module.LOADTEST_RUNS_KEPT + 1):
        store(f"m{at}", "mlx")
    assert "o" in [run["id"] for run in app_module._read_loadtest()]
    assert app_module.loadtest_view(REPO)["compare"]["runs"]["optiq"]["id"] == "o"


@pytest.mark.parametrize("engine", ["optiq", "all"])
def test_a_direct_engine_cannot_start_during_its_install(app_module, engine):
    app_module.STATE.install = {"running": True, "kind": engine, "lines": []}
    why = app_module._engine_refusal("optiq", [REPO])
    assert why and "install is running" in why


def test_the_connection_test_warns_on_a_text_null(app_module):
    call = {
        "function": {
            "name": engine_runtime.TEST_TOOL["function"]["name"],
            "arguments": json.dumps({"city": "Pune", "unit": "null"}),
        }
    }
    found = engine_runtime._judge_tool({"tool_calls": [call]})
    assert "warn" in found.values() and "unit" in json.dumps(found)
