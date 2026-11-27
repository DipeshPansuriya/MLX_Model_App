"""Row 874: the app with two engines. Fake processes, ports and pip; nothing real."""

import json
import stat
import urllib.error
import urllib.request

import pytest

import engine_runtime as runtime
import engines
import optiq_engine

REPO = "mlx-community/Tiny-4bit"
OTHER = "mlx-community/Other-4bit"
PORT = 18123  # stands for the engine's port: no test touches a real one
SECRETS = ("ANTHROPIC_API_KEY", "HF_TOKEN", "OPENAI_API_KEY", "MY_PASSWORD")


class FakeProc:
    def __init__(self, cmd, **kwargs):
        self.args, self.kwargs, self.returncode = cmd, kwargs, None
        self.pid, self.stdout = 4242, iter(())

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    kill = terminate

    def wait(self, timeout=None):
        return self.returncode


class Rig:
    """The app with a cached text model, an installed engine and a fake system."""

    def __init__(self, app, monkeypatch):
        self.app, self.started, self.holders = app, [], set()
        self.auth, self.warm = "ready", True
        snapshot = app.CACHE_DIR / "models--mlx-community--Tiny-4bit" / "snapshots" / "abc"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text(json.dumps({"model_type": "qwen3"}))
        (snapshot / "model.safetensors").write_bytes(b"x")
        self.snapshot = snapshot
        app.APP_DIR.mkdir(parents=True, exist_ok=True)
        app.LOG_DIR.mkdir(parents=True, exist_ok=True)
        venv = optiq_engine.venv_dir(app.APP_DIR)
        (venv / "bin").mkdir(parents=True)
        (venv / "bin" / "python").write_text("")
        info = f"mlx_optiq-{optiq_engine.VERSION}.dist-info"
        (venv / "lib" / "python3.15" / "site-packages" / info).mkdir(parents=True)
        (venv / optiq_engine.MARKER).write_text(optiq_engine.LOCK_SHA256)
        monkeypatch.setattr(optiq_engine, "PORT", PORT)
        monkeypatch.setattr(runtime, "listeners", lambda port, **k: set(self.holders))
        monkeypatch.setattr(runtime, "answers", lambda port: False)
        monkeypatch.setattr(runtime, "auth_state", lambda *a, **k: self.auth)
        monkeypatch.setattr(runtime, "warm_up", lambda *a, **k: self.warm)
        monkeypatch.setattr(app, "_complete_models", lambda: {REPO, OTHER})
        mac = {"ram_bytes": 32 * 2**30, "chip": "test", "ram": "32 GB"}
        monkeypatch.setattr(app, "hardware", lambda: mac)
        monkeypatch.setattr(app, "environment_report", dict)
        monkeypatch.setattr(app, "_watch_server", lambda *a, **k: None)
        monkeypatch.setattr(app, "_ps_field", lambda pid, field: "then")
        monkeypatch.setattr(app.subprocess, "Popen", self.popen)
        for name in SECRETS:
            monkeypatch.setenv(name, "inherited-value")

    def popen(self, cmd, **kwargs):
        proc = FakeProc(cmd, **kwargs)
        self.started.append(proc)
        return proc

    def start(self, engine="optiq", background=False, repo=REPO):
        self.app.worker_server_start(repo, background, None, None, engine)
        return self.started[-1] if self.started else None

    def ready(self):
        proc = self.start()
        self.holders = {proc.pid}
        assert self.app._probe_direct(proc.pid) is None
        assert self.app.STATE.server_verdict == "ready"
        return proc

    def key(self):
        return (self.app.APP_DIR / "server.key").read_text()


@pytest.fixture
def rig(app_module, monkeypatch):
    return Rig(app_module, monkeypatch)


def _post(base_url, path, body, headers=None):
    request = urllib.request.Request(
        base_url + path, json.dumps(body).encode(), {"Content-Type": "application/json"}
    )
    for name, value in (headers or {}).items():
        request.add_header(name, value)
    try:
        with urllib.request.urlopen(request, timeout=10) as reply:
            return reply.status, json.load(reply)
    except urllib.error.HTTPError as error:
        return error.code, json.load(error)


# ---- MLX is what it was --------------------------------------------------------
def test_the_mlx_command_is_the_one_of_c7c3e9c(app_module):
    app = app_module
    settings = dict.fromkeys(app.SETTINGS)
    command = app._server_command(REPO, settings, 8123)
    assert command == [
        app.sys.executable, str(app.GUARD_SCRIPT), "--model", REPO,
        "--host", "127.0.0.1", "--port", "8123", "--max-tokens", "8192",
        "--allowed-origins", "http://127.0.0.1:8765,http://localhost:8765",
        "--prompt-cache-size", str(app._guard_module().CACHED_CONVERSATIONS),
    ]  # fmt: skip


def test_an_mlx_start_is_unchanged_and_gets_no_secret(rig):
    proc = rig.start("mlx")
    assert proc.args[1] == str(rig.app.GUARD_SCRIPT)
    env = proc.kwargs["env"]
    assert "inherited-value" not in env.values() and not set(SECRETS) & set(env)
    assert env["HOME"] and env["HF_HUB_OFFLINE"] == "1" and "MLX_STUDIO_SETTINGS" in env
    assert "cwd" not in proc.kwargs
    state = rig.app.STATE.snapshot()["server"]
    assert (state["engine"], state["port"]) == ("mlx", 8080)
    saved = json.loads(rig.app._server_pidfile().read_text())
    assert "engine" not in saved and saved["port"] == 8080
    assert not (rig.app.APP_DIR / "server.key").exists()
    assert rig.app._running_server() == ([REPO], False)


# ---- starting OptiQ -------------------------------------------------------------
def test_optiq_starts_its_own_command_on_the_same_port_setting(rig):
    proc = rig.start()
    app = rig.app
    assert proc.args[0] == str(optiq_engine.python_path(app.APP_DIR))
    assert proc.args[proc.args.index("--model") + 1] == str(rig.snapshot)
    assert proc.args[proc.args.index("--port") + 1] == str(PORT)
    assert "--no-anthropic" not in proc.args
    assert "--prompt-cache-bytes" in proc.args
    env = proc.kwargs["env"]
    assert "inherited-value" not in env.values() and env["OPTIQ_API_KEY"] == rig.key()
    assert proc.kwargs["cwd"] == str(app.APP_DIR / "engines" / "optiq" / "run")
    assert stat.S_IMODE((app.APP_DIR / "server.key").stat().st_mode) == 0o600
    assert rig.key() not in " ".join(proc.args)
    server = app.STATE.snapshot()["server"]
    assert server["engine"] == "optiq" and server["endpoint"] == f"http://127.0.0.1:{PORT}/v1"
    assert server["ready"] is False and server["unguarded"] is False


def test_the_anthropic_switch_reaches_the_command(rig):
    rig.app.worker_server_start(REPO, False, None, None, "optiq", False)
    assert "--no-anthropic" in rig.started[-1].args


def test_each_launch_has_a_new_key_that_is_never_logged(rig):
    rig.ready()
    first = rig.key()
    rig.app.stop_server()
    assert not (rig.app.APP_DIR / "server.key").exists()
    rig.holders = set()
    rig.ready()
    assert rig.key() != first
    app = rig.app
    app.STATE.server_lines.clear()
    proc = rig.started[-1]
    proc.stdout = iter([f"Authorization: Bearer {rig.key()}\n"])
    app._pump_output(proc)
    app.SERVER_LOG.write_text(f"key {rig.key()}\n")
    everything = json.dumps(app.STATE.snapshot()) + "".join(
        path.read_text() for path in app.LOG_DIR.iterdir() if path.name != "server.log"
    )
    assert rig.key() not in everything and first not in everything
    import io
    import zipfile

    with zipfile.ZipFile(io.BytesIO(app.build_diagnostic_bundle()[0])) as bundle:
        assert all(rig.key().encode() not in bundle.read(n) for n in bundle.namelist())


@pytest.mark.parametrize(
    ("change", "words"),
    [
        (lambda rig: (rig.snapshot / "config.json").write_text('{"vision_config": {}}'), "image"),
        (lambda rig: rig.holders.add(99), "uses port"),
        (
            lambda rig: (optiq_engine.venv_dir(rig.app.APP_DIR) / optiq_engine.MARKER).unlink(),
            "not installed",
        ),
    ],
)
def test_a_refused_optiq_start_leaves_the_running_server_alone(rig, change, words):
    old = rig.start("mlx")
    change(rig)
    rig.start("optiq")
    assert len(rig.started) == 1 and old.returncode is None
    assert words in rig.app.STATE.server_apply_notice
    assert rig.app.STATE.server_engine == "mlx"


def test_a_listener_that_appears_after_the_old_server_stopped_refuses(rig, monkeypatch):
    seen = []
    monkeypatch.setattr(
        runtime,
        "port_problem",
        lambda port, ours=None: (
            seen.append(port) or (None if len(seen) == 1 else "Another program uses port")
        ),
    )
    rig.start()
    assert rig.started == [] and "Another program" in rig.app.STATE.server_down_notice


@pytest.mark.parametrize("holders", [{4242, 99}, {99}])
def test_any_other_listener_stops_the_start_and_gets_no_key(rig, monkeypatch, holders):
    sent = []
    monkeypatch.setattr(runtime, "auth_state", lambda *a, **k: sent.append(a) or "ready")
    proc = rig.start()
    rig.holders = holders
    assert "another program" in rig.app._probe_direct(proc.pid).lower()
    assert sent == [] and rig.app.STATE.server_verdict != "ready"


def test_a_server_open_without_the_key_is_stopped(rig):
    proc = rig.start()
    rig.holders, rig.auth = {proc.pid}, "open"
    assert "without the key" in rig.app._probe_direct(proc.pid)


def test_a_failed_first_reply_is_a_failed_start(rig):
    proc = rig.start()
    rig.holders, rig.warm = {proc.pid}, False
    assert "first request" in rig.app._probe_direct(proc.pid)


def test_the_watcher_restores_the_previous_engine(app_module, monkeypatch):
    watch = app_module._watch_server
    rig = Rig(app_module, monkeypatch)
    rig.start("mlx")
    monkeypatch.setattr(app_module, "_start_verdict", _mlx_ready(app_module))
    rig.start("optiq")
    proc = rig.started[-1]
    rig.holders, rig.auth = {proc.pid}, "open"
    previous = app_module.STATE.server_fallback
    assert previous == ([REPO], False)
    watch(proc.pid, REPO, proc, previous, app_module.STATE.server_stops)
    assert proc.returncode is not None
    assert rig.started[-1].args[1] == str(app_module.GUARD_SCRIPT)
    assert "without the key" in app_module.STATE.server_apply_notice


def _mlx_ready(app):
    real = app._start_verdict
    return lambda pid, models: "ready" if app.STATE.server_engine == "mlx" else real(pid, models)


def test_going_back_restores_optiq_with_its_engine(rig):
    rig.ready()
    assert rig.app._last_good_server() == ([REPO], False, "optiq", True)
    rig.holders = set()
    with rig.app.STATE.server_lock:
        rig.app._bring_back(([REPO], False, "optiq", False), "x", rig.app.STATE.server_stops)
    assert "optiq.cli" in rig.started[-1].args
    assert "--no-anthropic" in rig.started[-1].args  # as it was before


# ---- identity and restore -------------------------------------------------------
def test_a_background_optiq_server_is_adopted_and_not_called_unguarded(rig, monkeypatch):
    proc = rig.start(background=True)
    app = rig.app
    saved = json.loads(app._server_pidfile().read_text())
    assert (saved["engine"], saved["port"]) == ("optiq", PORT)
    command = " ".join(proc.args)
    monkeypatch.setattr(app, "_pid_alive", lambda pid: pid == proc.pid)
    monkeypatch.setattr(app, "_ps_field", lambda pid, f: command if f == "command" else "then")
    app.STATE.server_proc = app.STATE.server_pid = None
    app.STATE.server_engine = "mlx"
    app.restore_server_state()
    assert (app.STATE.server_pid, app.STATE.server_engine) == (proc.pid, "optiq")
    assert app.STATE.server_port == PORT and app.STATE.server_unguarded is False


# ---- Chat and the key button ------------------------------------------------------
def test_chat_sends_the_key_only_to_our_listener(rig, monkeypatch):
    rig.ready()
    app = rig.app
    body, _port = app._chat_target(REPO, [{"role": "user", "content": "hi", "x": 1}], {})
    assert body["model"] == "default_model"
    assert body["messages"] == [{"role": "user", "content": "hi"}]
    assert app._chat_headers() == {"Authorization": f"Bearer {rig.key()}"}
    rig.holders.add(99)
    with pytest.raises(runtime.Refused, match="not sent"):
        app._chat_headers()


def test_copy_key_needs_our_origin_and_our_own_listener(rig, base_url):
    assert _post(base_url, "/api/server/key", {})[0] == 409
    rig.ready()
    assert _post(base_url, "/api/server/key", {}) == (200, {"key": rig.key()})
    assert _post(base_url, "/api/server/key", {}, {"Origin": "http://evil.example"})[0] == 403
    rig.holders.add(99)
    status, answer = _post(base_url, "/api/server/key", {})
    assert status == 409 and "key" not in answer


# ---- planned engines and the API ---------------------------------------------------
@pytest.mark.parametrize("name", ["splash", "rapid-mlx", "nope", 3])
@pytest.mark.parametrize("path", ["/api/server/start", "/api/loadtest/start"])
def test_a_planned_or_unknown_engine_never_starts(rig, base_url, name, path):
    status, answer = _post(base_url, path, {"repo": REPO, "engine": name, "sizes": [4096]})
    assert status in (400, 409) and answer["error"]
    assert rig.started == [] and rig.app.STATE.server_pending == []


@pytest.mark.parametrize("name", ["splash", "rapid-mlx"])
def test_a_planned_engine_cannot_start_even_past_the_api(rig, name):
    rig.app.worker_server_start(REPO, False, None, None, name)
    assert rig.started == []
    assert "security review pending" in rig.app.STATE.server_down_notice


@pytest.mark.parametrize("name", ["splash", "rapid-mlx", "mlx", "nope"])
def test_only_installable_engines_install(rig, base_url, name):
    for path in ("/api/engines/install", "/api/engines/remove"):
        assert _post(base_url, path, {"engine": name})[0] == 400


def test_two_models_are_refused_for_optiq(rig, base_url):
    status, _answer = _post(
        base_url, "/api/server/start", {"repo": REPO, "also": [OTHER], "engine": "optiq"}
    )
    assert status in (400, 409) and rig.started == []


def test_eligibility_says_why_per_engine(rig, base_url):
    request = urllib.request.Request(base_url + f"/api/engines?repo={REPO}")
    with urllib.request.urlopen(request, timeout=10) as reply:
        found = {e["id"]: e for e in json.load(reply)["engines"]}
    assert found["optiq"]["eligible"] is True and found["mlx"]["eligible"] is True
    assert "security review pending" in found["splash"]["reason"]
    assert found["splash"]["eligible"] is False


# ---- Setup ---------------------------------------------------------------------
def test_install_is_refused_while_the_engine_serves(rig, base_url):
    rig.ready()
    for path in ("/api/engines/install", "/api/engines/remove"):
        status, answer = _post(base_url, path, {"engine": "optiq"})
        assert status == 409 and "Stop the API server" in answer["error"]


def test_remove_deletes_the_engine(rig, base_url):
    assert _post(base_url, "/api/engines/remove", {"engine": "optiq"}) == (200, {"ok": True})
    assert not optiq_engine.engine_dir(rig.app.APP_DIR).exists()
    assert rig.app.STATE.install["running"] is False


def test_the_install_worker_reports_its_failure(rig, monkeypatch):
    def fail(app_dir, emit):
        emit("line of pip")
        raise runtime.Refused("No network, or PyPI could not be reached.")

    monkeypatch.setattr(engines.OPTIQ, "install", fail)
    assert rig.app.STATE.reserve_install("optiq")
    rig.app.worker_engine_install(engines.OPTIQ)
    assert rig.app.STATE.install["running"] is False
    assert rig.app.STATE.install["lines"][-1].startswith("No network")


def test_optiq_does_not_start_while_it_is_installed(rig):
    assert rig.app.STATE.reserve_install("optiq")
    rig.start()
    assert rig.started == [] and "install" in rig.app.STATE.server_down_notice
