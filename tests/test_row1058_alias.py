"""Row 1058: a readable served name and a per-model alias, by file layout only.

Fake cache, processes and ports; nothing is started and no real model is loaded.
"""

from __future__ import annotations

import json
import os
import stat
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from test_row874_app import REPO, Rig, _post

import engines
import model_info

OTHER = "mlx-community/Other-4bit"
SHORT = "Tiny-4bit"
ROUTE = "/api/models/alias"
FILES = ("config.json", "model.safetensors.index.json", "tokenizer_config.json")


def _cached(app, repo, revision="abc", files=FILES):
    """A model laid out as the Hub cache does it: blobs, relative links, refs/main."""
    entry = app.CACHE_DIR / f"models--{repo.replace('/', '--')}"
    snapshot = entry / "snapshots" / revision
    (entry / "blobs").mkdir(parents=True, exist_ok=True)
    (entry / "refs").mkdir(exist_ok=True)
    (entry / "refs" / "main").write_text(revision)
    snapshot.mkdir(parents=True, exist_ok=True)
    for number, name in enumerate((*files, "model.safetensors", "extra/notes.txt")):
        blob = entry / "blobs" / f"blob{number}"
        body = {"weight_map": {"w": "model.safetensors"}} if "index" in name else {}
        blob.write_text(json.dumps({"model_type": "qwen3", **body}))
        target = snapshot / name
        target.parent.mkdir(exist_ok=True)
        if not os.path.lexists(target):
            target.symlink_to(os.path.relpath(blob, target.parent))
    return snapshot


def _tree(folder):
    """Every file under ``folder`` with its content, links followed."""
    return {
        str(p.relative_to(folder)): p.read_text() for p in sorted(folder.rglob("*")) if p.is_file()
    }


@pytest.fixture
def rig(app_module, monkeypatch):
    made = Rig(app_module, monkeypatch)
    made.snapshot = _cached(app_module, REPO)
    (app_module.CACHE_DIR / "models--mlx-community--Tiny-4bit" / "refs" / "main").write_text("abc")
    _cached(app_module, OTHER)
    return made


@pytest.fixture
def cache(app_module):
    app_module.APP_DIR.mkdir(parents=True, exist_ok=True)
    _cached(app_module, REPO)
    _cached(app_module, OTHER)
    return app_module


def _arg(proc, flag="--model"):
    return proc.args[proc.args.index(flag) + 1]


def _adopt(app, monkeypatch, proc, command=None):
    """The app is opened again while ``proc`` still runs."""
    command = command or " ".join(proc.args)
    monkeypatch.setattr(app, "_pid_alive", lambda pid: pid == proc.pid)
    monkeypatch.setattr(app, "_ps_field", lambda pid, f: command if f == "command" else "then")
    app.STATE.server_proc = app.STATE.server_pid = None
    app.STATE.server_name = app.STATE.server_alias = None
    app.STATE.server_engine = "mlx"
    app.restore_server_state()


# ---- the name rules --------------------------------------------------------------
@pytest.mark.parametrize("name", ["coder", "Qwen-3.8_x.1", "a", "9", "a" * 64, "my_model-v2"])
def test_a_safe_alias_is_accepted(name):
    assert model_info.alias_problem(name) is None


@pytest.mark.parametrize(
    "name",
    [
        "",
        " a",
        "a ",
        "a b",
        "a/b",
        "../a",
        "a\\b",
        "a:think",
        "a..b",
        "a--b",
        "..",
        ".",
        "-a",
        ".a",
        "_a",
        "a-",
        "a.",
        "a_",
        "x.git",
        "x.GIT",
        "default_model",
        "DEFAULT_MODEL",
        "Home",
        "tmp",
        "HF",
        "a" * 65,
        "café",
        chr(0xFF41),  # a full width "a"
        "a\n",
        "a\x00",
        "$(id)",
        "a;b",
        None,
        7,
        ["a"],
    ],
)
def test_an_unsafe_alias_is_refused_with_a_reason(name):
    assert model_info.alias_problem(name)


@pytest.mark.parametrize(
    ("repo", "name"),
    [
        (REPO, SHORT),
        ("gpt2", "gpt2"),
        ("org/home", "model-home"),
        ("org/TMP", "model-TMP"),
        ("org/default_model", "model-default_model"),
    ],
)
def test_the_readable_name_of_a_model(repo, name):
    assert model_info.served_name(repo) == name


@pytest.mark.parametrize(
    "repo",
    ["org/-x", "org/..", "org/.", "org/a--b", "org/a..b", "org/x.git", "org/_", "o/a:b c", ""],
)
def test_the_readable_name_is_always_safe(repo):
    name = model_info.served_name(repo)
    assert model_info.alias_problem(name, longest=model_info.SERVED_NAME_MAX) is None


def test_a_long_model_name_is_kept_whole_up_to_the_hub_limit():
    assert model_info.served_name("org/" + "x" * 96) == "x" * 96
    assert model_info.served_name("org/" + "x" * 150) == "x" * 96


# ---- saving an alias ---------------------------------------------------------------
def test_an_alias_is_saved_in_the_ui_block_and_shown_with_the_real_name(cache, base_url):
    assert _post(base_url, ROUTE, {"repo": REPO, "alias": "coder"}) == (200, {"ok": True})
    saved = json.loads(cache._settings_file().read_text())
    assert saved["ui"]["models"] == {REPO: {"alias": "coder"}}
    assert stat.S_IMODE(cache._settings_file().stat().st_mode) == 0o600
    models = {m["repo"]: m for m in cache.STATE.snapshot()["models"]}
    assert (models[REPO]["alias"], models[OTHER]["alias"]) == ("coder", None)


@pytest.mark.parametrize("empty", ["", None])
def test_an_empty_alias_removes_it(cache, base_url, empty):
    cache.save_ui({"theme": "dark"})
    _post(base_url, ROUTE, {"repo": REPO, "alias": "coder"})
    assert _post(base_url, ROUTE, {"repo": REPO, "alias": empty})[0] == 200
    assert cache.model_aliases() == {}
    assert json.loads(cache._settings_file().read_text())["ui"] == {"theme": "dark"}


def test_other_saves_and_resets_keep_the_alias(cache):
    assert cache.save_alias(REPO, "coder") is None
    cache.save_ui({"theme": "dark"})
    cache.save_settings({"chat_temperature": 0.5}, REPO)
    cache.reset_settings(REPO)
    cache.reset_settings()
    assert cache.model_aliases() == {REPO: "coder"}
    assert cache.ui_settings() == {"theme": "dark", "save_history": "on"}


def test_saving_an_alias_keeps_the_rest_of_the_settings(cache):
    cache.save_settings({"chat_temperature": 0.5}, REPO)
    cache.save_ui({"theme": "light"})
    cache.save_alias(REPO, "coder")
    assert cache.user_settings(REPO)["chat_temperature"] == 0.5
    assert cache.ui_settings() == {"theme": "light", "save_history": "on"}


@pytest.mark.parametrize("name", ["coder", "CODER", "Coder"])
def test_two_models_cannot_share_an_alias(cache, base_url, name):
    cache.save_alias(REPO, "coder")
    status, answer = _post(base_url, ROUTE, {"repo": OTHER, "alias": name})
    assert status == 400 and REPO in answer["error"]
    assert cache.model_aliases() == {REPO: "coder"}


def test_a_model_may_keep_its_own_alias_in_another_case(cache):
    cache.save_alias(REPO, "coder")
    assert cache.save_alias(REPO, "Coder") is None
    assert cache.model_aliases() == {REPO: "Coder"}


@pytest.mark.parametrize(
    ("repo", "folder"),
    [
        ("openai-community/gpt2-medium", "gpt2-medium"),  # another model's short name
        ("OTHER-ORG/Llama-X", "llama-x"),  # in another case
        ("gpt2", "gpt2"),  # a real repo id with no owner
    ],
)
def test_an_alias_cannot_be_another_cached_models_name(cache, repo, folder):
    _cached(cache, repo)
    problem = cache.save_alias(REPO, folder)
    assert problem and repo in problem
    assert cache.model_aliases() == {}


def test_a_model_may_take_its_own_short_name(cache):
    assert cache.save_alias(REPO, SHORT) is None


def test_the_alias_of_a_removed_model_is_released_to_the_next(cache):
    gone = "org/Gone-4bit"
    cache.save_ui({"theme": "dark"})
    _cached(cache, gone)
    cache.save_alias(gone, "coder")
    import shutil

    shutil.rmtree(cache.CACHE_DIR / "models--org--Gone-4bit")
    assert cache.save_alias(REPO, "coder") is None
    assert cache.model_aliases() == {REPO: "coder"}
    assert cache.ui_settings() == {"theme": "dark", "save_history": "on"}


@pytest.mark.parametrize(
    "body",
    [
        {"repo": REPO, "alias": "a/b"},
        {"repo": REPO, "alias": "default_model"},
        {"repo": REPO, "alias": 7},
        {"repo": REPO},
        {"repo": "org/not-there", "alias": "coder"},
        {"repo": "../../etc", "alias": "coder"},
        {"alias": "coder"},
    ],
)
def test_a_bad_alias_request_changes_nothing(cache, base_url, body):
    status, answer = _post(base_url, ROUTE, body)
    assert status == 400 and answer["error"]
    assert not cache._settings_file().exists()


def test_the_alias_route_needs_the_apps_token(cache, server):
    host, port = server.server_address[:2]
    request = urllib.request.Request(
        f"http://{host}:{port}{ROUTE}",
        json.dumps({"repo": REPO, "alias": "coder"}).encode(),
        {"Content-Type": "application/json"},
    )
    with pytest.raises(urllib.error.HTTPError) as caught:
        urllib.request.urlopen(request, timeout=10)
    assert caught.value.code == 403
    assert cache.model_aliases() == {}


@pytest.mark.parametrize(
    "block",
    [
        "x",
        [],
        {REPO: "coder"},
        {REPO: {"alias": "../x"}},
        {REPO: {"alias": 5}},
        {"../x": {"alias": "coder"}},
    ],
)
def test_a_hand_edited_unsafe_alias_is_ignored(cache, block):
    cache._settings_file().write_text(json.dumps({"ui": {"models": block}}))
    assert cache.model_aliases() == {}
    assert cache.save_alias(OTHER, "fine") is None
    assert cache.model_aliases() == {OTHER: "fine"}


# ---- OptiQ: a readable name, by a link in its own run folder ------------------------
def _run_folder(app):
    return app.APP_DIR / "engines" / "optiq" / "run"


@pytest.mark.parametrize(("alias", "name"), [(None, SHORT), ("coder", "coder")])
def test_optiq_is_given_a_readable_name_instead_of_the_path(rig, alias, name):
    if alias:
        assert rig.app.save_alias(REPO, alias) is None
    proc = rig.start()
    assert _arg(proc) == name
    assert not [part for part in proc.args if str(rig.app.CACHE_DIR) in part]
    assert proc.kwargs["cwd"] == str(_run_folder(rig.app))
    link = _run_folder(rig.app) / name
    assert link.is_symlink() and link.resolve() == rig.snapshot.resolve()
    state = rig.app.STATE.snapshot()["server"]
    assert state["alias"] == alias


def test_a_new_alias_replaces_the_old_link_and_nothing_else(rig):
    before = _tree(rig.snapshot)
    rig.app.save_alias(REPO, "coder")
    rig.start()
    run = _run_folder(rig.app)
    (run / "keep.txt").write_text("not a link")
    rig.app.save_alias(REPO, "writer")
    rig.start()
    assert sorted(p.name for p in run.iterdir()) == ["hf", "home", "keep.txt", "tmp", "writer"]
    assert _tree(rig.snapshot) == before


@pytest.mark.parametrize("make", ["file", "folder"])
def test_something_real_in_the_way_is_refused_and_kept(rig, make):
    run = _run_folder(rig.app)
    run.mkdir(parents=True)
    if make == "file":
        (run / "coder").write_text("mine")
    else:
        (run / "coder").mkdir()
        (run / "coder" / "mine").write_text("mine")
    rig.app.save_alias(REPO, "coder")
    assert rig.start() is None
    assert "coder" in rig.app.STATE.server_down_notice
    assert (run / "coder").exists() and not (run / "coder").is_symlink()
    assert not (rig.app.APP_DIR / "server.key").exists()


@pytest.mark.parametrize("name", ["home", "tmp", "hf", "..", "a/b", "", "-x"])
def test_the_link_helper_refuses_an_unsafe_name(tmp_path, name):
    (tmp_path / "run" / "home").mkdir(parents=True)
    with pytest.raises(engines.runtime.Refused):
        engines.link_model(tmp_path / "run", name, tmp_path)
    assert [p.name for p in (tmp_path / "run").iterdir()] == ["home"]


def test_a_link_with_a_reserved_name_is_not_swept_away(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (tmp_path / "elsewhere").mkdir()
    (run / "hf").symlink_to(tmp_path / "elsewhere")
    engines.link_model(run, "coder", tmp_path / "elsewhere")
    assert (run / "hf").is_symlink()


def test_the_optiq_load_test_still_names_the_snapshot_itself(rig):
    rig.app.save_alias(REPO, "coder")
    command, _env, _fields = rig.app._direct_test_launch(
        engines.OPTIQ, REPO, rig.app.server_settings([REPO])
    )
    assert command[command.index("--model") + 1] == str(rig.snapshot)


# ---- MLX: an app-owned cache that holds only the alias -------------------------------
def test_mlx_without_an_alias_starts_as_before(rig):
    proc = rig.start("mlx")
    assert _arg(proc) == REPO
    assert proc.kwargs["cwd"] == str(rig.app.APP_DIR)
    assert proc.kwargs["env"].get("HF_HUB_CACHE") == os.environ.get("HF_HUB_CACHE")
    assert not (rig.app.APP_DIR / "engines" / "mlx").exists()
    assert rig.app.STATE.snapshot()["server"]["alias"] is None


def test_mlx_with_an_alias_gets_its_own_cache_and_an_empty_folder(rig):
    rig.app.save_alias(REPO, "coder")
    proc = rig.start("mlx")
    root = rig.app.APP_DIR / "engines" / "mlx"
    assert _arg(proc) == "coder"
    env = proc.kwargs["env"]
    assert env["HF_HUB_CACHE"] == str(root / "hub") and env["HF_HUB_OFFLINE"] == "1"
    cwd = Path(proc.kwargs["cwd"])
    assert cwd == root / "run" and list(cwd.iterdir()) == []
    assert stat.S_IMODE(cwd.stat().st_mode) == 0o700
    assert [p.name for p in (root / "hub").iterdir()] == ["models--coder"]
    assert rig.app.STATE.snapshot()["server"]["alias"] == "coder"


def test_the_alias_cache_lists_only_the_alias_as_mlx_lm_reads_it(rig):
    """What mlx_lm's /v1/models does: scan the cache, need main and three files."""
    hub_library = pytest.importorskip("huggingface_hub")
    rig.app.save_alias(REPO, "coder")
    proc = rig.start("mlx")
    hub = Path(proc.kwargs["env"]["HF_HUB_CACHE"])
    info = hub_library.scan_cache_dir(hub)
    assert list(info.warnings) == []
    listed = [
        repo.repo_id
        for repo in info.repos
        if "main" in repo.refs and set(FILES) <= {f.file_path.name for f in repo.refs["main"].files}
    ]
    assert listed == ["coder"]
    found = Path(hub_library.snapshot_download("coder", cache_dir=hub, local_files_only=True))
    assert found.name == "abc" and _tree(found) == _tree(rig.snapshot)


def test_rebuilding_the_alias_cache_never_touches_the_model(rig):
    before = _tree(rig.app.CACHE_DIR)
    for name in ("coder", "writer"):
        rig.app.save_alias(REPO, name)
        rig.start("mlx")
    hub = rig.app.APP_DIR / "engines" / "mlx" / "hub"
    assert [p.name for p in hub.iterdir()] == ["models--writer"]
    assert _tree(rig.app.CACHE_DIR) == before


def test_a_linked_alias_cache_is_refused_and_its_target_kept(rig, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep").write_text("x")
    root = rig.app.APP_DIR / "engines" / "mlx"
    root.mkdir(parents=True)
    (root / "hub").symlink_to(outside)
    rig.app.save_alias(REPO, "coder")
    assert rig.start("mlx") is None
    assert (outside / "keep").read_text() == "x"


def test_something_in_the_empty_folder_refuses_the_start(rig):
    run = rig.app.APP_DIR / "engines" / "mlx" / "run"
    run.mkdir(parents=True)
    (run / "mlx_lm.py").write_text("raise SystemExit(1)")
    rig.app.save_alias(REPO, "coder")
    assert rig.start("mlx") is None


def test_the_mlx_load_test_keeps_the_real_name(rig):
    rig.app.save_alias(REPO, "coder")
    job, env = rig.app._loadtest_job(REPO, [1024], "r1", rig.app.APP_DIR, "mlx")
    assert job["command"][job["command"].index("--model") + 1] == REPO
    assert env.get("HF_HUB_CACHE") == os.environ.get("HF_HUB_CACHE")


# ---- an alias changed while the server runs ------------------------------------------
@pytest.mark.parametrize("engine", ["mlx", "optiq"])
def test_a_changed_alias_waits_for_a_restart(rig, engine):
    rig.app.save_alias(REPO, "coder")
    rig.start(engine)
    assert rig.app.STATE.snapshot()["server"]["settings_pending"] is False
    rig.app.save_alias(REPO, "writer")
    state = rig.app.STATE.snapshot()["server"]
    assert state["settings_pending"] is True
    assert state["alias"] == "coder"  # what the running server answers to
    rig.start(engine)
    state = rig.app.STATE.snapshot()["server"]
    assert (state["alias"], state["settings_pending"]) == ("writer", False)


def test_another_models_alias_does_not_ask_for_a_restart(rig):
    rig.start("mlx")
    rig.app.save_alias(OTHER, "writer")
    assert rig.app.STATE.snapshot()["server"]["settings_pending"] is False


def test_stop_forgets_the_served_name(rig):
    rig.app.save_alias(REPO, "coder")
    rig.start("mlx")
    rig.app.stop_server()
    assert (rig.app.STATE.server_name, rig.app.STATE.server_alias) == (None, None)


# ---- process identity, pidfile, adopting ---------------------------------------------
@pytest.mark.parametrize(
    ("engine", "alias", "name"),
    [("mlx", "coder", "coder"), ("optiq", "coder", "coder"), ("optiq", None, SHORT)],
)
def test_a_server_started_with_a_name_is_adopted(rig, monkeypatch, engine, alias, name):
    if alias:
        rig.app.save_alias(REPO, alias)
    proc = rig.start(engine, background=True)
    app = rig.app
    saved = json.loads(app._server_pidfile().read_text())
    assert (saved["repo"], saved["name"], saved.get("alias")) == (REPO, name, alias)
    app.save_alias(REPO, "changed-later")
    _adopt(app, monkeypatch, proc)
    assert app.STATE.server_pid == proc.pid
    assert (app.STATE.server_repo, app.STATE.server_name) == (REPO, name)
    assert app.STATE.server_alias == alias
    assert app._still_our_server(proc.pid, REPO)
    app.STATE.server_verdict = "ready"
    assert app._chat_problem(REPO) is None


@pytest.mark.parametrize("engine", ["mlx", "optiq"])
def test_a_process_serving_another_name_is_not_ours(rig, monkeypatch, engine):
    rig.app.save_alias(REPO, "coder")
    proc = rig.start(engine, background=True)
    other = " ".join("coder2" if part == "coder" else part for part in proc.args)
    _adopt(rig.app, monkeypatch, proc, other)
    assert rig.app.STATE.server_pid is None
    assert not rig.app._server_pidfile().exists()


@pytest.mark.parametrize("engine", ["mlx", "optiq"])
def test_stop_signals_an_adopted_server_that_has_a_name(rig, monkeypatch, engine):
    rig.app.save_alias(REPO, "coder")
    proc = rig.start(engine, background=True)
    _adopt(rig.app, monkeypatch, proc)
    sent = []
    alive = [True]

    def kill(pid, number):
        sent.append((pid, number))
        alive[0] = False

    monkeypatch.setattr(rig.app, "_pid_alive", lambda pid: pid == proc.pid and alive[0])
    monkeypatch.setattr(rig.app.os, "kill", kill)
    rig.app.stop_server()
    assert sent and sent[0][0] == proc.pid


@pytest.mark.parametrize("engine", ["mlx", "optiq"])
def test_a_pidfile_from_2_0_13_is_still_adopted(rig, monkeypatch, engine):
    app = rig.app
    proc = rig.start(engine, background=True)
    saved = json.loads(app._server_pidfile().read_text())
    saved.pop("name", None)
    app._server_pidfile().write_text(json.dumps(saved))
    old = list(proc.args)
    old[old.index("--model") + 1] = REPO if engine == "mlx" else str(rig.snapshot)
    _adopt(app, monkeypatch, proc, " ".join(old))
    assert app.STATE.server_pid == proc.pid
    assert app.STATE.server_alias is None


@pytest.mark.parametrize("bad", ["x --host 0", "../x", 5, ["a"], ""])
def test_an_unsafe_name_in_the_pidfile_is_not_believed(rig, monkeypatch, bad):
    app = rig.app
    proc = rig.start("mlx", background=True)
    saved = json.loads(app._server_pidfile().read_text())
    saved.update(name=bad, alias=bad)
    app._server_pidfile().write_text(json.dumps(saved))
    _adopt(app, monkeypatch, proc)  # the real command names the repo itself
    assert app.STATE.server_pid == proc.pid
    assert (app.STATE.server_name, app.STATE.server_alias) == (None, None)


def test_the_restored_server_takes_the_alias_saved_now(rig):
    rig.app.save_alias(REPO, "coder")
    with rig.app.STATE.server_lock:
        rig.app._bring_back(([REPO], False), "x", rig.app.STATE.server_stops)
    assert _arg(rig.started[-1]) == "coder"


def test_chat_and_the_probe_still_ask_for_default_model(rig):
    rig.app.save_alias(REPO, "coder")
    rig.ready()
    body, _port = rig.app._chat_target(REPO, [{"role": "user", "content": "hi"}], {})
    assert body["model"] == "default_model"
