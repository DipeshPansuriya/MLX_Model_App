"""Row 1142: focus stays on the page when "Remove attached files" hides itself."""

from __future__ import annotations

from test_row1104_projects_ui import FLOW, needs_node, run


@needs_node
def test_removing_attached_files_moves_focus_to_the_message_box(tmp_path):
    out = run(
        tmp_path,
        FLOW
        + """
const button = $$$("#projectDetach");
await press($$$("#projectAttach"));
button.focus();
await press(button);
const typing = [button.hidden, document.activeElement === $$$("#input")];
await press($$$("#projectAttach"));
$$$("#input").disabled = true;
button.focus();
await press(button);
const off = document.activeElement === $$$("#historyBtn");
await press($$$("#projectAttach"));
$$$("#projectChoose").focus();
ProjectsUI.detach();
return { typing, off, kept: document.activeElement === $$$("#projectChoose") };
""",
    )
    assert out == {"typing": [True, True], "off": True, "kept": True}
