"""Review 1087 finding 4: Copy model name gives the name the running server accepts."""

import re

from test_ui_behaviour import needs_node, run

SHOW = """
const show = (server) => {
  render(snap({ server: { endpoint: "e", lines: [], tool_support: {}, ...server },
    models: [{ repo: "org/custom", alias: "new-name", path: "/c", size_h: "1 GB",
               complete: true }] }));
  return html("#modelCards");
};
"""


def copied(tmp_path, server):
    out = run(tmp_path, SHOW + f"return show({server});")
    return re.findall(r'data-copy="([^"]+)"', out)


@needs_node
def test_a_stopped_server_gives_the_saved_alias(tmp_path):
    assert copied(tmp_path, "{ running: false }") == ["new-name"]


@needs_node
def test_before_apply_the_running_servers_alias_is_copied(tmp_path):
    server = '{ running: true, repo: "org/custom", repos: ["org/custom"], alias: "old-name" }'
    assert copied(tmp_path, server) == ["old-name"]


@needs_node
def test_before_apply_a_server_with_no_alias_gives_the_model_id(tmp_path):
    server = '{ running: true, repo: "org/custom", repos: ["org/custom"], alias: null }'
    assert copied(tmp_path, server) == ["org/custom"]


@needs_node
def test_a_model_the_server_does_not_serve_gives_the_saved_alias(tmp_path):
    server = '{ running: true, repo: "org/other", repos: ["org/other"], alias: "old-name" }'
    assert copied(tmp_path, server) == ["new-name"]
