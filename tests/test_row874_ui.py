"""Row 874: the engine selector, Setup rows and client settings (node, fake DOM)."""

from test_ui_behaviour import needs_node, run

SERVER = """
const optiq = { id: "optiq", name: "OptiQ", version: "0.8.6", port: 8080, selectable: true,
  direct: true, installable: true, anthropic: true, state: "installed", reason: null,
  missing: [["80% memory limit", "no"]] };
const mlx = { id: "mlx", name: "MLX (built in)", selectable: true, direct: false,
  installable: false, anthropic: false, state: "installed", missing: [], port: null };
const splash = { id: "splash", name: "Splash", selectable: false, direct: false,
  installable: false, state: "unavailable", missing: [],
  reason: "Not available yet: security review pending." };
const server = { running: false, repos: [], lines: [], tool_support: {}, background: false,
  endpoint: "http://127.0.0.1:8080/v1", engine: "mlx", last_engine: "mlx",
  engines: [mlx, optiq, splash] };
"""


@needs_node
class TestEngines:
    def test_mlx_is_the_default_and_names_no_engine(self, tmp_path):
        out = run(
            tmp_path,
            SERVER
            + """
            render(snap({ server }));
            return [serverChoice(["org/a"]), document.querySelector("#claudeSettings").textContent,
              document.querySelector("#engineSelect").innerHTML];
            """,
        )
        assert out[0] == {"repo": "org/a"}
        assert "no Anthropic route" in out[1]
        assert "disabled" in out[2] and "security review pending" in out[2]

    def test_optiq_is_sent_by_name_with_honest_client_settings(self, tmp_path):
        out = run(
            tmp_path,
            SERVER
            + """
            render(snap({ server }));
            state.engine = "optiq";
            render(snap({ server }));
            const text = (id) => document.querySelector(id).textContent;
            return [serverChoice(["org/a"]), text("#claudeSettings"), text("#openaiSettings"),
              text("#engineMissingTitle"), document.querySelector("#copyKey").hidden];
            """,
        )
        assert out[0] == {"repo": "org/a", "engine": "optiq", "anthropic": True}
        assert "ANTHROPIC_BASE_URL=http://127.0.0.1:8080\n" in out[1]
        assert "default_model" in out[1] and "localhost" not in out[1] + out[2]
        assert "sk-optiq-" not in out[1] + out[2]
        assert "(1)" in out[3] and out[4] is True

    def test_another_engine_waits_for_apply_and_a_ready_one_offers_the_key(self, tmp_path):
        out = run(
            tmp_path,
            SERVER
            + """
            const live = { ...server, running: true, repos: ["org/a"], repo: "org/a",
              engine: "optiq", anthropic: true, ready: true };
            render(snap({ server: live }));
            const first = [serverPending(), document.querySelector("#copyKey").hidden];
            state.engine = "mlx";
            return [...first, serverPending()];
            """,
        )
        assert out == [False, False, True]

    def test_setup_offers_install_only_where_there_is_one(self, tmp_path):
        out = run(
            tmp_path,
            SERVER
            + """
            render(snap({ server }));
            return document.querySelector("#engineCards").innerHTML;
            """,
        )
        assert out.count('data-action="install"') == 1 and out.count('data-action="remove"') == 1
        assert 'data-engine="splash"' not in out and "security review pending" in out
