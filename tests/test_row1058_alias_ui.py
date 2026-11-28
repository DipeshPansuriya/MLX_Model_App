"""Row 1058: the alias and the actual model name on every screen. ui/app.js under node."""

from __future__ import annotations

import re

from test_ui_behaviour import ROOT, needs_node, run

HELPERS = """
const el = (s) => document.querySelector(s);
const models = [
  { repo: "org/custom", alias: "coder", path: "/c/custom", size_h: "4.2 GB", complete: true },
  { repo: "org/other", alias: null, path: "/c/other", size_h: "1.0 GB", complete: true }];
const srv = (over = {}) => ({ running: true, ready: true, load_failed: false, starting: false,
  repos: ["org/custom"], repo: "org/custom", endpoint: "e", lines: [], tool_support: {},
  alias: "coder", engine: "mlx", port: 8080, ...over });
const engineViews = [
  { id: "mlx", name: "MLX (built in)", selectable: true, direct: false, state: "installed",
    anthropic: false, port: null, missing: [] },
  { id: "optiq", name: "OptiQ", selectable: true, direct: true, state: "installed",
    anthropic: true, port: 8080, missing: [] }];
const show = (server = srv(), list = models) =>
  render(snap({ server: { engines: engineViews, ...server }, models: list }));
const text = (s) => el(s).textContent;
const sent = (path) => calls.filter((c) => c.path === path).map((c) => JSON.parse(c.opts.body));
const answer = (ok = true, body = { ok: true }) => (path) =>
  path === "/api/models/alias" ? reply(body, ok) : reply(snap({ models }));
"""


def ui(tmp_path, scenario):
    return run(tmp_path, HELPERS + scenario)


@needs_node
class TestModelsScreen:
    def test_a_card_shows_the_alias_and_the_actual_name(self, tmp_path):
        out = ui(tmp_path, 'show(); return html("#modelCards");')
        first, second = out.split('class="card"')[1:3]
        assert re.search(r'class="card-name">coder<', first)
        assert re.search(r'class="card-sub">org/custom<', first)
        assert re.search(r'class="card-name">other<', second)

    def test_every_installed_card_offers_the_alias_button(self, tmp_path):
        out = ui(tmp_path, 'show(); return html("#modelCards");')
        assert re.findall(r'data-alias="([^"]+)"', out) == ["org/custom", "org/other"]
        assert "Change alias" in out and "Set alias" in out

    def test_the_alias_is_escaped(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(srv(), [{ ...models[0], alias: '<img src=x>"' }]);
            return html("#modelCards");
            """,
        )
        assert "<img" not in out and "&lt;img" in out

    def test_copy_model_name_copies_what_clients_send(self, tmp_path):
        out = ui(tmp_path, 'show(); return html("#modelCards");')
        assert re.findall(r'data-copy="([^"]+)"', out) == ["coder", "org/other"]

    def test_the_button_opens_the_editor_for_that_model(self, tmp_path):
        out = ui(
            tmp_path,
            """
            wire(); show();
            el("#modelCards").querySelectorAll("[data-alias]")[0].listeners.click();
            return { hidden: el("#aliasPanel").hidden, repo: text("#aliasRepo"),
              value: el("#aliasInput").value, remove: el("#aliasRemoveBtn").hidden,
              focus: document.activeElement.selector };
            """,
        )
        assert out == {
            "hidden": False,
            "repo": "org/custom",
            "value": "coder",
            "remove": False,
            "focus": "#aliasInput",
        }

    def test_typed_text_survives_a_poll(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(); openAlias("org/other"); el("#aliasInput").value = "wri";
            show(); show(srv({ ready: false }));
            return [el("#aliasInput").value, el("#aliasPanel").hidden,
              el("#aliasRemoveBtn").hidden];
            """,
        )
        assert out == ["wri", False, True]

    def test_save_sends_the_name_and_closes(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(); respond = answer(); openAlias("org/other");
            el("#aliasInput").value = "  writer "; await saveAlias();
            return { sent: sent("/api/models/alias"), hidden: el("#aliasPanel").hidden };
            """,
        )
        assert out == {"sent": [{"repo": "org/other", "alias": "writer"}], "hidden": True}

    def test_remove_sends_an_empty_name(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(); respond = answer(); openAlias("org/custom"); await saveAlias("");
            return sent("/api/models/alias");
            """,
        )
        assert out == [{"repo": "org/custom", "alias": ""}]

    def test_a_refusal_is_said_and_the_text_stays(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(); respond = answer(false, { error: "already used by org/custom" });
            openAlias("org/other"); el("#aliasInput").value = "coder"; await saveAlias();
            return [el("#aliasPanel").hidden, text("#aliasError"), el("#aliasInput").value];
            """,
        )
        assert out == [False, "already used by org/custom", "coder"]

    def test_enter_saves_and_escape_cancels(self, tmp_path):
        out = ui(
            tmp_path,
            """
            wire(); show(); respond = answer(); openAlias("org/other");
            const key = (name) => el("#aliasInput").listeners.keydown(
              { key: name, preventDefault() {} });
            el("#aliasInput").value = "writer"; await key("Enter");
            openAlias("org/other"); await key("Escape");
            return [sent("/api/models/alias").length, el("#aliasPanel").hidden];
            """,
        )
        assert out == [1, True]

    def test_the_editor_closes_when_its_model_is_gone(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(); openAlias("org/other"); show(srv(), [models[0]]);
            return el("#aliasPanel").hidden;
            """,
        )
        assert out is True


@needs_node
class TestAliasEverywhere:
    def test_pickers_show_both_names_and_keep_the_repo_as_value(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(off()); return ["#serverModelSelect", "#configModelSelect", "#ltModelSelect"]
              .map(html);
            function off() { return srv({ running: false, ready: false, repos: [], repo: null,
              alias: null }); }
            """,
        )
        for options in out:
            assert '<option value="org/custom">coder (org/custom)</option>' in options
            assert '<option value="org/other">org/other</option>' in options

    def test_a_new_alias_redraws_the_pickers(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(); show(srv(), [{ ...models[0], alias: "writer" }, models[1]]);
            return html("#configModelSelect");
            """,
        )
        assert "writer (org/custom)" in out and "coder" not in out

    def test_the_searchable_list_matches_the_alias(self, tmp_path):
        out = ui(
            tmp_path,
            """
            wire(); show();
            const box = el("#configModelSelectSearch");
            box.value = "coder"; box.listeners.input();
            return html("#configModelSelectList");
            """,
        )
        assert "coder (org/custom)" in out and "org/other" not in out

    def test_chat_the_header_and_the_start_card_show_it(self, tmp_path):
        out = ui(
            tmp_path,
            """
            show(); return { chat: html("#chatModelSelect"), header: text("#globalStatus"),
              card: html("#serverModelList") };
            """,
        )
        assert '<option value="org/custom">coder (org/custom)</option>' in out["chat"]
        assert out["header"] == "coder"
        assert "coder" in out["card"] and "org/custom" in out["card"]

    def test_a_download_card_shows_it_too(self, tmp_path):
        out = ui(
            tmp_path,
            """
            state.catalog = [{ repo: "org/custom", fit: "good" }]; show(); renderCatalog();
            return html("#catalogCards");
            """,
        )
        assert re.search(r'class="card-name">coder<', out) and "org/custom" in out


@needs_node
class TestConnectSettings:
    BOXES = """
      return { dsh: text("#dshSettings"), openai: text("#openaiSettings"),
        claude: text("#claudeSettings"), curl: text("#curlExample"),
        hint: text("#mlxModelHint") };
    """

    def test_optiq_settings_name_the_running_alias(self, tmp_path):
        out = ui(tmp_path, 'show(srv({ engine: "optiq", anthropic: true }));' + self.BOXES)
        assert "Model: coder\n" in out["dsh"] and "Model: coder\n" in out["openai"]
        assert "ANTHROPIC_MODEL=coder\n" in out["claude"]
        assert "ANTHROPIC_DEFAULT_HAIKU_MODEL=coder\n" in out["claude"]

    def test_mlx_settings_name_the_running_alias(self, tmp_path):
        out = ui(tmp_path, "show();" + self.BOXES)
        assert "Model: coder\n" in out["dsh"] and '"model":"coder"' in out["curl"]
        assert "coder" in out["hint"] and "default_model" in out["hint"]

    def test_without_an_alias_they_say_default_model(self, tmp_path):
        out = ui(tmp_path, "show(srv({ alias: null }));" + self.BOXES)
        assert "Model: default_model\n" in out["dsh"]
        assert "load that model instead" in out["hint"]

    def test_a_running_server_keeps_the_name_it_was_started_with(self, tmp_path):
        out = ui(
            tmp_path,
            'show(srv({ alias: "coder" }), [{ ...models[0], alias: "writer" }]);' + self.BOXES,
        )
        assert "Model: coder\n" in out["dsh"] and "writer" not in out["dsh"]

    def test_a_stopped_server_shows_the_alias_of_the_chosen_model(self, tmp_path):
        out = ui(
            tmp_path,
            """
            state.engine = "mlx";
            show(srv({ running: false, ready: false, repos: [], repo: null, alias: null,
              last_models: ["org/custom"] }));
            state.serverDraft = ["org/custom"]; paintServerCard();
            """
            + self.BOXES,
        )
        assert '"model":"coder"' in out["curl"]


def test_the_page_has_a_labelled_alias_editor():
    page = (ROOT / "ui" / "index.html").read_text("utf-8")
    assert re.search(r'<label[^>]*for="aliasInput"', page)
    assert re.search(r'id="aliasInput"[^>]*maxlength="64"', page)
    assert re.search(r'id="aliasError"[^>]*role="alert"', page)
    for button in ("aliasSaveBtn", "aliasRemoveBtn", "aliasCancelBtn"):
        assert re.search(rf'<button[^>]*type="button"[^>]*id="{button}"', page)
