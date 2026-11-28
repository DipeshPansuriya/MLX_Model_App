"""Row 713: fixes for the review and security findings on rows 656 and 550."""

from __future__ import annotations

import http.client
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_helpers", ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


uis = sibling("test_row656_ui")
ui, needs_node = uis.ui, uis.needs_node
HTML = (ROOT / "ui" / "index.html").read_text("utf-8")


def test_a_nul_byte_in_a_name_is_not_a_crash(app_module):
    assert app_module.model_info.weights_bytes(app_module._snapshot_dir("a\u0000b")) == 0


@pytest.mark.parametrize("second", ["application/json", "text/plain"])
def test_two_content_types_are_refused(server, app_module, second):
    conn = http.client.HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.putrequest("POST", "/api/server/notice/clear")
    conn.putheader("Content-Type", "application/json")
    conn.putheader("Content-Type", second)
    conn.putheader("X-MLX-Token", app_module.API_TOKEN)
    conn.putheader("Content-Length", "2")
    conn.endheaders(b"{}")
    assert conn.getresponse().status == 415
    conn.close()


@needs_node
def test_a_choice_made_while_running_survives_polls_and_stop(tmp_path):
    out = ui(
        tmp_path,
        'await show(running()); editServerSet(["org/c"]); await show(running());'
        " await show(running()); const polled = serverSet(); await show(view());"
        " return [polled, serverSet()];",
    )
    assert out == [["org/c"], ["org/c"]]


@needs_node
def test_a_new_server_still_fills_the_card(tmp_path):
    out = ui(tmp_path, "await show(view()); await show(running()); return serverSet();")
    assert out == ["org/a"]


def test_server_alerts_come_before_the_card():
    panel = HTML.index('id="serverCard"')
    assert HTML.index('id="serverNotice"') < panel
    assert HTML.index('id="serverApplyNotice"') < panel
