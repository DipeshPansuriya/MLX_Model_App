"""The architecture docs describe the app as it is now: no model in its process (row 844)."""

from __future__ import annotations

import pytest
from test_ui_behaviour import ROOT

PAGES = (
    "docs/ARCHITECTURE.md",
    "docs/diagrams/state-model.md",
    "docs/diagrams/runtime.md",
)
GONE = (
    "when the user loads a model",
    "resident in that same process",
    "released builds also infer in-process",
    "mlx_lm (in-proc)",
    "stream_generate",
    "loaded.repo",
    "loaded.loading",
    "loaded.error",
    "Snapshot --> Loaded",
    "class Loaded",
    "worker_load",
    "State.model",
    "mlx_lm — in process",
    "versions, models, loaded",
)


@pytest.mark.parametrize("page", PAGES)
def test_the_in_process_chat_model_is_not_described(page):
    text = (ROOT / page).read_text("utf-8")
    assert [words for words in GONE if words in text] == []


def test_the_state_page_names_the_chats_server_fields():
    text = (ROOT / "docs" / "diagrams" / "state-model.md").read_text("utf-8")
    assert "`server.ready`" in text
    assert "`server.load_failed`" in text
