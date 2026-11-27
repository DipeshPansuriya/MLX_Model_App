"""Row 713: fixes for the review and security findings on rows 656 and 550.

F1-F12 are the review findings, L1, L3 and I1-I5 the security ones. No model is
loaded, no process started and no window or browser tab opened.
"""

from __future__ import annotations

import http.client
import importlib.util
import json
import sys
import threading
import time
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def sibling(name: str):
    spec = importlib.util.spec_from_file_location(f"{name}_helpers", ROOT / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


apps = sibling("test_row656_app")
guards = sibling("test_row656_guard")
uis = sibling("test_row656_ui")
guard, Rig, GB = guards.guard, guards.Rig, guards.GB
cache_model, post, ram = apps.cache_model, apps.post, apps.ram
ui, needs_node, HTML = uis.ui, uis.needs_node, uis.HTML
A, B, CHAT, SHARE = apps.A, apps.B, "org/chat", int(0.52 * apps.RAM)


# F2 / L3: the Chat model and the two-model server share one memory budget ---- #
def serving(app, monkeypatch, repos=(A, B), **resident):
    """A running API server holding ``repos``, with an optional resident file."""
    for repo in repos:
        cache_model(app, repo, weights=4 * GB)
    monkeypatch.setattr(app, "_pid_alive", bool)
    app.STATE.server_pid = 4242
    app.STATE.server_repo = repos[0]
    app.STATE.server_repos = list(repos)
    # As the app records it at a start; the smallest context two models run with.
    app.STATE.server_context = 8192
    if resident:
        # As the guard leaves it once the preload is over.
        apps.resident_file(app, 4242, **{"preload_done": True, **resident})


@pytest.fixture
def loads(app_module, monkeypatch):
    seen, done = [], threading.Event()
    monkeypatch.setattr(app_module, "worker_load", lambda repo: (seen.append(repo), done.set()))
    return seen, done


def load_chat(app, base_url, weights):
    cache_model(app, CHAT, weights=weights)
    return post(base_url, "/api/load", {"repo": CHAT})


def fake_mlx_load(monkeypatch, load):
    module = types.ModuleType("mlx_lm")
    module.load = load
    monkeypatch.setitem(sys.modules, "mlx_lm", module)


def budget_in_file(app) -> int:
    return int(app._budget_file().read_text("utf-8"))


# F3: the prompt cache cap holds on every insert ----------------------------- #
class TestPromptCacheCap:
    @pytest.mark.parametrize(("cap", "expected"), [(2 * GB, 2 * GB), (None, None)])
    def test_the_cap_is_put_on_the_cache(self, cap, expected):
        server = guards.fake_server()
        opts = guard.split_studio_args(["--studio-max-models", "2"])[1]
        guard.install_multi(server, opts, guard.status_writer(None), None, None)
        provider = server.ModelProvider("org/a")
        provider.cli_args = types.SimpleNamespace(prompt_cache_bytes=cap)
        cache = guards.FakeCache()
        server.ResponseGenerator(provider, cache)
        assert getattr(cache, "max_bytes", None) == expected


# L3: a model over the budget is loaded alone, and says so ------------------- #
class TestOversize:
    def test_guard_names_it_until_it_is_gone(self, tmp_path, capsys):
        rig = Rig(tmp_path, sizes={"org/big": 30 * GB})
        rig.provider.load("org/a")
        assert rig.status()["oversize"] == []
        rig.provider.load("org/big")
        assert rig.status()["oversize"] == ["org/big"]
        warning = capsys.readouterr().err
        assert "org/big" in warning and "memory budget" in warning
        rig.provider.load("org/big")  # a hit: still loaded, still named
        assert rig.status()["oversize"] == ["org/big"]
        rig.provider.load("org/a")
        rig.provider.load("org/c")  # two slots, so the oldest goes
        assert "org/big" not in rig.status()["resident"]
        assert rig.status()["oversize"] == []

    def test_app_passes_it_on(self, app_module, monkeypatch, ram):
        serving(app_module, monkeypatch, resident=["org/big"], oversize=["org/big", 3])
        assert app_module._resident_view(4242)["oversize"] == ["org/big"]

    def test_app_without_the_field(self, app_module, monkeypatch, ram):
        serving(app_module, monkeypatch, oversize="x")
        assert app_module._resident_view(4242)["oversize"] == []


# F7, I2, I5: guard ---------------------------------------------------------- #
def test_a_failed_single_load_names_the_model(tmp_path):
    server = guards.fake_server(("org/bad",))
    path = tmp_path / "r.json"
    guard.install_report(server, guard.status_writer(str(path)), GB)
    with pytest.raises(ValueError):
        server.ModelProvider("org/a").load("org/bad")
    last = json.loads(path.read_text("utf-8"))["events"][-1]
    assert (last["event"], last["model"]) == ("load-failed", "org/bad")


@pytest.mark.parametrize("name", ["org/b", "org/a..b"])
def test_the_preload_name_gets_the_request_path_checks(tmp_path, monkeypatch, name):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "org" / "b").mkdir(parents=True)  # a folder named like the repo
    rig = Rig(tmp_path, also=name)
    rig.provider.load_default()
    assert rig.provider.loads == ["org/a"]
    assert ("preload-skipped", name) in rig.events()


def test_the_served_file_does_not_follow_a_link(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep", "utf-8")
    link = tmp_path / "server.models"
    link.symlink_to(victim)
    guard.served_recorder(str(link))("org/x")
    assert victim.read_text("utf-8") == "keep"


# I1, I4, F12: app ----------------------------------------------------------- #
@pytest.mark.parametrize("repo", ["org/no", "a\u0000b"])
def test_an_estimate_is_only_for_installed_models(app_module, base_url, ram, repo):
    status, body = post(base_url, "/api/server/estimate", {"repo": repo})
    assert status == 400 and "installed" in body["error"]


def test_a_single_estimate_still_answers(app_module, base_url, ram):
    cache_model(app_module, A)
    assert post(base_url, "/api/server/estimate", {"repo": A})[0] == 200


def test_a_nul_byte_in_a_name_is_not_a_crash(app_module, ram):
    assert app_module._guard_module().weights_bytes(app_module._snapshot_dir("a\u0000b")) == 0


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


def test_two_first_calls_never_write_bytecode(app_module, monkeypatch):
    seen = []
    inside = threading.Event()

    def exec_module(_module):
        first = not inside.is_set()
        inside.set()
        time.sleep(0.1 if first else 0.25)
        seen.append(sys.dont_write_bytecode)

    spec = types.SimpleNamespace(loader=types.SimpleNamespace(exec_module=exec_module))
    monkeypatch.setattr(sys, "dont_write_bytecode", False)
    monkeypatch.setattr(app_module.importlib.util, "spec_from_file_location", lambda *a: spec)
    monkeypatch.setattr(app_module.importlib.util, "module_from_spec", lambda s: object())
    threads = [threading.Thread(target=app_module._guard_module) for _ in range(2)]
    threads[0].start()
    assert inside.wait(5)
    threads[1].start()
    for thread in threads:
        thread.join(5)
    assert seen and all(seen)
    assert sys.dont_write_bytecode is False


# F1, U4 --------------------------------------------------------------------- #
@needs_node
@pytest.mark.parametrize(
    ("repos", "chosen"),
    [
        (["org/a", "org/b"], ["org/c", "org/a"]),
        (["org/a"], ["org/a", "org/b"]),
        (["org/a", "org/b"], ["org/a"]),
    ],
)
def test_a_choice_made_while_running_survives_polls_and_stop(tmp_path, repos, chosen):
    out = ui(
        tmp_path,
        f"const on = () => running({{ repos: {json.dumps(repos)} }});"
        f" await show(on()); editServerSet({json.dumps(chosen)}); await show(on());"
        " await show(on()); const polled = serverSet(); await show(view());"
        " return [polled, serverSet()];",
    )
    assert out == [chosen, chosen]


@needs_node
def test_a_new_server_still_fills_the_card(tmp_path):
    out = ui(tmp_path, "await show(view()); await show(running()); return serverSet();")
    assert out == ["org/a", "org/b"]


@needs_node
def test_a_late_estimate_for_an_old_pair_is_ignored(tmp_path):
    out = ui(
        tmp_path,
        "let late; const good = estimate;"
        ' respond = (path, opts) => path !== "/api/server/estimate" ? reply(current)'
        '  : JSON.parse(opts.body).also[0] === "org/b"'
        "  ? new Promise((r) => { late = r; }) : reply(good);"
        ' await pick("org/a", "org/b"); await pick("org/a", "org/c");'
        " late(await reply({ ...good, fits: false }));"
        " await new Promise((r) => setTimeout(r, 0));"
        ' return $("#serverStartBtn").disabled;',
    )
    assert out is False


# F4, L3: the notice --------------------------------------------------------- #
NOTICE = ' return [$("#serverMulti").hidden, text("#serverMultiTitle"), text("#serverMultiText")];'


def notice(tmp_path, over: str):
    return ui(tmp_path, f"await show(running({over}));" + NOTICE)


@needs_node
def test_a_model_unloaded_for_memory_is_named(tmp_path):
    hidden, title, words = notice(
        tmp_path,
        '{ resident: ["org/b"], events: [{ event: "evict", model: "org/a" },'
        ' { event: "load", model: "org/b" }] }',
    )
    assert not hidden and title == "Loaded models changed"
    assert "a was unloaded to make room" in words


@needs_node
def test_a_pair_still_loading_has_no_notice(tmp_path):
    over = '{ resident: ["org/a"], events: [{ event: "load", model: "org/a" }] }'
    assert notice(tmp_path, over)[0] is True


@needs_node
@pytest.mark.parametrize(
    ("over", "title"),
    [
        ('{ multi_off: "moved" }', "One model at a time"),
        (
            '{ resident: ["org/a"], events: [{ event: "preload-skipped", model: "org/b" }] }',
            "Second model not preloaded",
        ),
        (
            '{ resident: ["org/c", "org/a"] }',
            "Loaded models changed",
        ),
    ],
)
def test_the_notice_title_fits_what_happened(tmp_path, over, title):
    assert notice(tmp_path, over)[1] == title


@needs_node
def test_an_oversize_model_is_warned_about(tmp_path):
    hidden, title, words = notice(
        tmp_path, '{ resident: ["org/c"], oversize: ["org/c"], events: [] }'
    )
    assert not hidden and title == "Loaded without enough memory"
    assert "c was loaded on its own: it does not fit" in words
    assert "macOS may slow down or stop" in words


# F5, F6 --------------------------------------------------------------------- #
@needs_node
def test_over_budget_gives_no_context_advice(tmp_path):
    out = ui(
        tmp_path,
        "estimate = { ...estimate, fits: false };"
        ' await pick("org/a", "org/b");'
        ' return [$("#serverContextHint").hidden, text("#serverContextHint")];',
    )
    assert out == [True, ""]


@needs_node
def test_a_failed_estimate_is_asked_again_later(tmp_path):
    out = ui(
        tmp_path,
        "let now = 1000; Date.now = () => now; let fail = true;"
        ' respond = (path) => path !== "/api/server/estimate" ? reply(current)'
        '  : fail ? reply({ error: "no" }, false) : reply(estimate);'
        ' await pick("org/a", "org/b");'
        ' const asked = () => sent("/api/server/estimate").length;'
        " now += 2000; await show(current); const soon = asked();"
        " fail = false; now += 20000; await show(current); await show(current);"
        ' return [soon, asked(), text("#serverEstimate").startsWith("Two models: ")];',
    )
    assert out == [1, 2, True]


# F7, F8 --------------------------------------------------------------------- #
@needs_node
def test_the_log_never_prints_null(tmp_path):
    out = ui(
        tmp_path,
        'await show(running({ events: [{ event: "load-failed", model: null }] }));'
        ' return text("#serverLog");',
    )
    assert out == "[memory] load-failed"


MEMORY = "memory: { active_bytes: 22e9, peak_bytes: 23e9, budget_bytes: 17.9e9 }"


@needs_node
@pytest.mark.parametrize(
    "over", [f'{{ repos: ["org/a"], {MEMORY} }}', f'{{ multi_off: "x", {MEMORY} }}']
)
def test_one_model_shows_no_budget(tmp_path, over):
    out = ui(tmp_path, f'await show(running({over})); return text("#serverMemory");')
    assert "22.0 GB" in out and "budget" not in out and "Chat tab" not in out


@needs_node
def test_two_models_show_the_budget(tmp_path):
    out = ui(tmp_path, f'await show(running({{ {MEMORY} }})); return text("#serverMemory");')
    assert "17.9 GB budget" in out


# F2: the Chat tab ------------------------------------------------------------ #


# F10, F11: markup ------------------------------------------------------------ #
def test_server_alerts_come_before_the_card():
    panel = HTML.index('id="serverCard"')
    assert HTML.index('id="serverUnguarded"') < panel
    assert HTML.index('id="serverNotice"') < panel


@needs_node
def test_the_live_region_gets_the_reason(tmp_path):
    out = ui(
        tmp_path,
        'estimate = { ...estimate, fits: false, refused: "They do not fit." };'
        ' await pick("org/a", "org/b"); const over = text("#serverStartWhy");'
        ' await pick("org/a"); return [over, text("#serverStartWhy")];',
    )
    assert "do not fit" in out[0] and "do not fit" not in out[1]
