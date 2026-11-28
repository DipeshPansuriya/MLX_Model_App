"""Row 1142: the reply limit of a send with files is not saved (row 1140 review, finding 1)."""

# ruff: noqa: F811  (the upstream fixture is imported, then asked for by name)

from __future__ import annotations

from collections import OrderedDict

import pytest
from test_row837_chat import ONE, upstream  # noqa: F401
from test_row1092_chats import chats  # noqa: F401
from test_row1142_helpers import send, user

import project_routes

KEY = "a" * 32
FILE = {"path": "src/a.py", "size": 2, "sha256": "0" * 64, "text": "a\n"}


# 1: the reply limit of a chat with files is for the request, not for the chat
@pytest.mark.parametrize("own", [{}, {"max_tokens": 9000}, {"temperature": 0.5}])
def test_a_send_with_files_caps_the_request_and_saves_the_users_settings(chats, monkeypatch, own):
    monkeypatch.setattr(project_routes, "ATTACHED", OrderedDict({KEY: [FILE]}))
    done = send(chats, [user("why", attachment=KEY)], save={"id": None}, settings=own)
    asked = chats.upstream.requests[-1][2]["max_tokens"]
    assert asked <= 4096
    assert asked == min(own.get("max_tokens") or chats.app.chat_settings(ONE)["max_tokens"], 4096)
    assert chats.get(done["chat"]["id"])[1]["settings"] == own
