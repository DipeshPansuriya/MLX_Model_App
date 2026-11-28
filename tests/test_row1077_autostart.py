"""Rows 689 / 1077: a default model whose API server starts by itself at Mac login.

Nothing real runs here: HOME is temporary, ``launchctl`` and ``vm_stat`` go through
a fake ``run_cmd``, the app's ``subprocess`` is replaced, and no port is touched.
"""

from __future__ import annotations

import fcntl
import json
import os
import plistlib
import stat
import subprocess
import sys
import time
import types
from pathlib import Path

import pytest

import login_agent

REPO = "mlx-community/Tiny-Model-4bit"
FAKE_PID = 4_000_000  # above macOS's highest pid: signal 0 finds nothing
GIB = 1024**3


class Proc:
    def __init__(self, cmd, **kwargs):
        self.args, self.kwargs, self.pid = cmd, kwargs, FAKE_PID
        self.returncode = None
        self.stdout = None

    def poll(self):
        return self.returncode

    def terminate(self):
        self.returncode = -15

    def kill(self):
        self.returncode = -9

    def wait(self, timeout=None):
        return self.returncode


class Mac:
    """The parts of the machine the code under test may look at."""

    def __init__(self, app, monkeypatch):
        self.app = app
        self.started: list[Proc] = []
        self.commands: list[list[str]] = []
        self.busy: str | None = None
        self.free_pages = 20 * GIB // 16384
        self.ram = 32 * GIB
        self.launchctl_ok = True

        fake = types.SimpleNamespace(
            Popen=self._popen,
            PIPE=subprocess.PIPE,
            STDOUT=subprocess.STDOUT,
            DEVNULL=subprocess.DEVNULL,
            TimeoutExpired=subprocess.TimeoutExpired,
            SubprocessError=subprocess.SubprocessError,
        )
        self.real_watcher = app._watch_server
        patch = monkeypatch.setattr
        patch(app, "subprocess", fake)
        patch(app, "run_cmd", self._run)
        patch(app, "_ps_field", lambda pid, field: "")
        patch(app, "_watch_server", lambda *args: None)
        patch(app, "_redacting_pipe", lambda log: os.open(os.devnull, os.O_WRONLY))
        patch(app, "instance_is_running", lambda: False)
        patch(app, "hardware", lambda: {"ram_bytes": self.ram})
        patch(app, "pkg_version", lambda name: "0.32.2")
        patch(app, "_complete_models", lambda: {REPO})
        patch(app.engine_runtime, "port_problem", lambda port, ours=None: self.busy)

        snapshot = app.CACHE_DIR / f"models--{REPO.replace('/', '--')}" / "snapshots" / "abc"
        snapshot.mkdir(parents=True)
        (snapshot / "config.json").write_text("{}")
        (snapshot / "model.safetensors").write_bytes(b"0" * 1000)

        optiq = app.engines.OPTIQ
        patch(optiq, "program", lambda app_dir: Path("/fake/optiq/bin/python"))
        patch(optiq, "check_model", lambda repo, folder: None)

    def _popen(self, cmd, **kwargs):
        proc = Proc(cmd, **kwargs)
        self.started.append(proc)
        return proc

    def _run(self, cmd, timeout=None):
        self.commands.append(list(cmd))
        if cmd[0].endswith("vm_stat"):
            return True, (
                "Mach Virtual Memory Statistics: (page size of 16384 bytes)\n"
                f"Pages free:                {self.free_pages}.\n"
                "Pages active:              999999.\n"
                "Pages inactive:            0.\n"
                "Pages speculative:         0.\n"
            )
        assert cmd[0] == "/bin/launchctl", cmd
        return self.launchctl_ok, "" if self.launchctl_ok else "No such process"

    def launchctl_calls(self):
        return [c for c in self.commands if c[0] == "/bin/launchctl"]


@pytest.fixture
def mac(app_module, monkeypatch):
    return Mac(app_module, monkeypatch)


@pytest.fixture
def bundle(tmp_path):
    """An installed app: ``(Resources folder, launcher)``."""
    contents = tmp_path / "Applications" / "MLX Studio.app" / "Contents"
    (contents / "Resources").mkdir(parents=True)
    (contents / "MacOS").mkdir()
    launcher = contents / "MacOS" / "MLXStudio"
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    return contents / "Resources", launcher


def switch_on(app, bundle, engine="optiq"):
    login_agent.set_default(app, REPO, engine)
    login_agent.enable(app, resources=bundle[0])


def after_first_look(action):
    """A ``sleep`` for ``login_start`` that makes ``action`` happen while it waits."""

    def sleep(seconds):
        action()

    return sleep


def ready(app):
    return after_first_look(lambda: setattr(app.STATE, "server_verdict", "ready"))


def result(app):
    return json.loads((app.APP_DIR / "login-start.json").read_text())


def mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def everything_written(app):
    """All text the app wrote, the key file and the live server key aside."""
    skip = {"autostart.key", "server.key"}
    files = [p for p in Path.home().rglob("*") if p.is_file() and p.name not in skip]
    return "\n".join(p.read_text("utf-8", errors="replace") for p in files)


# --------------------------------------------------------------------------- #
# The default-model record
# --------------------------------------------------------------------------- #
def test_off_until_the_user_switches_it_on(app_module, mac):
    view = login_agent.status(app_module)
    assert view["default"] is None and view["enabled"] is False
    login_agent.set_default(app_module, REPO)
    assert login_agent.status(app_module)["enabled"] is False
    assert not login_agent.plist_path().exists()
    assert login_agent.login_start(app_module) == 0
    assert mac.started == []


def test_default_record_is_private_and_holds_model_and_engine(app_module, mac):
    login_agent.set_default(app_module, REPO, "optiq", anthropic=False)
    record = login_agent.read_default(app_module)
    assert record == {
        "v": 1,
        "repo": REPO,
        "engine": "optiq",
        "anthropic": False,
        "autostart": False,
    }
    assert mode(login_agent.record_file(app_module)) == 0o600


@pytest.mark.parametrize(
    ("repo", "engine", "anthropic"),
    [
        ("../etc", "mlx", True),
        (None, "mlx", True),
        ("mlx-community/Not-Downloaded", "mlx", True),
        (REPO, "nope", True),
        (REPO, "splash", True),
        (REPO, ["mlx"], True),
        (REPO, "mlx", "yes"),
    ],
)
def test_a_default_that_could_not_start_is_not_saved(app_module, mac, repo, engine, anthropic):
    with pytest.raises(app_module.engine_runtime.Refused):
        login_agent.set_default(app_module, repo, engine, anthropic)
    assert not login_agent.record_file(app_module).exists()


def test_changing_the_default_keeps_the_switch(app_module, mac, bundle):
    switch_on(app_module, bundle)
    login_agent.set_default(app_module, REPO, "optiq")
    assert login_agent.read_default(app_module)["autostart"] is True
    # The new engine needs its key before the next login.
    assert login_agent.key_file(app_module).is_file()


@pytest.mark.parametrize("text", ["{", "[]", '{"v": 1, "repo": "../x"}', '{"v": 2}'])
def test_an_unreadable_record_starts_nothing(app_module, mac, text):
    login_agent.record_file(app_module).write_text(text)
    assert login_agent.read_default(app_module) is None
    assert "cannot be read" in login_agent.status(app_module)["problem"]
    assert login_agent.login_start(app_module) == 0
    assert mac.started == []


# --------------------------------------------------------------------------- #
# The LaunchAgent
# --------------------------------------------------------------------------- #
def test_switch_needs_a_default_model(app_module, mac, bundle):
    with pytest.raises(app_module.engine_runtime.Refused, match="default model"):
        login_agent.enable(app_module, resources=bundle[0])
    assert not login_agent.plist_path().exists()


def test_enable_writes_the_login_item_and_starts_nothing_now(app_module, mac, bundle):
    switch_on(app_module, bundle)
    path = login_agent.plist_path()
    assert path == Path.home() / "Library/LaunchAgents/com.mlxstudio.app.login-server.plist"
    assert str(Path.home()).startswith(str(bundle[0].parents[3]))  # the temporary HOME
    assert plistlib.loads(path.read_bytes()) == {
        "Label": "com.mlxstudio.app.login-server",
        "ProgramArguments": [str(bundle[1].resolve()), "--start-server-at-login"],
        "RunAtLoad": True,
        "KeepAlive": False,
        "AbandonProcessGroup": True,
    }
    assert mode(path) == 0o644
    assert mac.launchctl_calls() == [] and mac.started == []
    view = login_agent.status(app_module, resources=bundle[0])
    assert view["enabled"] is True and view["login_item"] == "ok"


@pytest.mark.parametrize(
    "folder",
    [
        "src/MLX_Model_App",
        "Volumes/MLX Studio/MLX Studio.app/Contents/Resources",
        "private/var/AppTranslocation/1/d/MLX Studio.app/Contents/Resources",
        "Applications/Empty.app/Contents/Resources",
    ],
)
def test_only_an_installed_app_may_add_a_login_item(app_module, mac, tmp_path, folder):
    resources = tmp_path / folder
    resources.mkdir(parents=True)
    if "Empty" not in folder and resources.name == "Resources":
        (resources.parent / "MacOS").mkdir()
        (resources.parent / "MacOS" / "MLXStudio").write_text("")
        (resources.parent / "MacOS" / "MLXStudio").chmod(0o755)
    if folder.startswith("Volumes"):
        resources = Path("/Volumes/MLX Studio/MLX Studio.app/Contents/Resources")
    login_agent.set_default(app_module, REPO)
    with pytest.raises(app_module.engine_runtime.Refused):
        login_agent.enable(app_module, resources=resources)
    assert not login_agent.plist_path().exists()
    assert login_agent.read_default(app_module)["autostart"] is False
    view = login_agent.status(app_module, resources=resources)
    assert view["available"] is False and view["unavailable_reason"]


def test_a_source_run_is_not_an_installed_app(app_module, mac):
    assert login_agent.status(app_module)["available"] is False


def test_disable_removes_the_login_item(app_module, mac, bundle):
    switch_on(app_module, bundle)
    login_agent.disable(app_module)
    assert not login_agent.plist_path().exists()
    assert login_agent.read_default(app_module)["autostart"] is False
    target = f"gui/{os.getuid()}/com.mlxstudio.app.login-server"
    assert mac.launchctl_calls() == [["/bin/launchctl", "bootout", target]]
    mac.launchctl_ok = False  # not loaded: that is fine
    login_agent.disable(app_module)


def test_clearing_the_default_switches_off(app_module, mac, bundle):
    switch_on(app_module, bundle)
    login_agent.clear_default(app_module)
    assert not login_agent.plist_path().exists()
    assert not login_agent.record_file(app_module).exists()


def test_refresh_follows_a_moved_app_only(app_module, mac, bundle):
    switch_on(app_module, bundle)
    path = login_agent.plist_path()
    path.write_bytes(plistlib.dumps({"ProgramArguments": ["/old/MLXStudio"]}))
    assert login_agent.status(app_module, resources=bundle[0])["login_item"] == "other app"
    login_agent.refresh(app_module, resources=bundle[0])
    assert login_agent.status(app_module, resources=bundle[0])["login_item"] == "ok"
    path.unlink()  # removed by the user: not put back behind their back
    login_agent.refresh(app_module, resources=bundle[0])
    assert not path.exists()
    assert login_agent.status(app_module, resources=bundle[0])["login_item"] == "missing"


def test_refresh_does_nothing_while_off(app_module, mac, bundle):
    login_agent.set_default(app_module, REPO)
    login_agent.refresh(app_module, resources=bundle[0])
    assert not login_agent.plist_path().exists()


# --------------------------------------------------------------------------- #
# The headless start
# --------------------------------------------------------------------------- #
def test_optiq_starts_with_a_stored_key_that_only_the_environment_carries(app_module, mac, bundle):
    switch_on(app_module, bundle, "optiq")
    stored = login_agent.key_file(app_module)
    assert mode(stored) == 0o600
    key = stored.read_text()
    optiq = app_module.engines.OPTIQ
    keys = []
    for _ in range(2):  # two logins: clients are set up once
        assert login_agent.login_start(app_module, sleep=ready(app_module)) == 0
        proc = mac.started[-1]
        keys.append(proc.kwargs["env"][optiq.key_env])
        assert key not in " ".join(map(str, proc.args))
        assert "--api-key" not in proc.args
        assert proc.args[proc.args.index("--host") + 1] == "127.0.0.1"
        assert (app_module.APP_DIR / "server.key").read_text() == key
        assert result(app_module)["password"] is True
        app_module.stop_server()
    assert keys == [key, key]
    assert key not in everything_written(app_module)
    assert key not in json.dumps(login_agent.status(app_module))
    assert optiq.new_key() != key  # the engine itself still makes new ones
    assert login_agent.client_key(app_module, "optiq", REPO) == key


def test_the_stored_key_is_for_the_default_model_while_switched_on(app_module, mac, bundle):
    assert login_agent.client_key(app_module, "optiq", REPO) is None
    switch_on(app_module, bundle, "optiq")
    assert login_agent.client_key(app_module, "optiq", "mlx-community/Other") is None
    assert login_agent.client_key(app_module, "mlx", REPO) is None
    login_agent.disable(app_module)
    assert login_agent.client_key(app_module, "optiq", REPO) is None
    assert not login_agent.key_file(app_module).exists()  # switching off revokes it


@pytest.mark.parametrize("off", [login_agent.disable, login_agent.clear_default])
def test_off_revokes_the_key_and_on_makes_a_new_one(app_module, mac, bundle, off):
    switch_on(app_module, bundle)
    stored = login_agent.key_file(app_module)
    first = stored.read_text()
    login_agent.enable(app_module, resources=bundle[0])
    assert stored.read_text() == first  # already on: clients stay set up
    off(app_module)
    assert not os.path.lexists(stored)
    switch_on(app_module, bundle)
    assert stored.read_text() != first and mode(stored) == 0o600


def test_a_key_left_from_before_is_not_used_again(app_module, mac, bundle):
    login_agent.set_default(app_module, REPO, "optiq")
    stored = login_agent.key_file(app_module)
    old = app_module.engines.OPTIQ.new_key()
    app_module.engine_runtime.write_key(stored, old)
    login_agent.enable(app_module, resources=bundle[0])
    assert app_module.engines.OPTIQ.read_key(stored) not in (None, old)


def test_an_engine_without_a_password_revokes_the_key(app_module, mac, bundle):
    switch_on(app_module, bundle)
    login_agent.set_default(app_module, REPO, "mlx")
    assert not login_agent.key_file(app_module).exists()


def test_a_key_that_cannot_be_removed_is_said(app_module, mac, bundle):
    switch_on(app_module, bundle)
    stored = login_agent.key_file(app_module)
    stored.unlink()
    stored.mkdir()  # unlink fails on a folder
    with pytest.raises(app_module.engine_runtime.Refused, match="could not be removed"):
        login_agent.disable(app_module)
    assert login_agent.read_default(app_module)["autostart"] is False


def test_the_screen_says_clients_need_the_new_key(app_module, mac, bundle):
    switch_on(app_module, bundle)
    note = login_agent.status(app_module, resources=bundle[0])["password_note"]
    assert "removes this key" in note and "update your tools" in note


def test_switching_off_while_the_login_start_waits_lets_it_finish(app_module, mac, bundle):
    switch_on(app_module, bundle)

    def off_then_ready():
        login_agent.disable(app_module)
        app_module.STATE.server_verdict = "ready"

    assert login_agent.login_start(app_module, sleep=after_first_look(off_then_ready)) == 0
    # bootout would end the job, its key check and its last report.
    assert mac.launchctl_calls() == []
    assert result(app_module)["result"] == "started"
    assert not login_agent.plist_path().exists()
    login_agent.disable(app_module)  # no job at work now: launchd is told
    assert [c[1] for c in mac.launchctl_calls()] == ["bootout"]


@pytest.mark.parametrize("off", [login_agent.disable, login_agent.clear_default])
def test_switching_off_during_the_checks_starts_nothing(app_module, mac, bundle, monkeypatch, off):
    switch_on(app_module, bundle)
    monkeypatch.setattr(app_module.engine_runtime, "port_problem", lambda *a, **k: off(app_module))
    assert login_agent.login_start(app_module, wait_seconds=0) == 0
    assert mac.started == []
    assert result(app_module)["result"] == "skipped"
    assert "switched off" in result(app_module)["reason"]
    assert not login_agent.key_file(app_module).exists()


@pytest.mark.parametrize("damage", ["open", "link", "junk"])
def test_a_key_file_that_is_not_private_is_refused(app_module, mac, bundle, damage):
    switch_on(app_module, bundle, "optiq")
    stored = login_agent.key_file(app_module)
    if damage == "open":
        stored.chmod(0o644)
    elif damage == "junk":
        stored.write_text("password")
    else:
        elsewhere = stored.with_name("elsewhere")
        stored.rename(elsewhere)
        stored.symlink_to(elsewhere)
    assert login_agent.login_start(app_module) == 0
    assert mac.started == []
    assert result(app_module)["result"] == "skipped"
    assert "key" in result(app_module)["reason"]


def _no_model(app, mac, monkeypatch):
    monkeypatch.setattr(app, "_complete_models", set)


def _no_engine(app, mac, monkeypatch):
    def missing(app_dir):
        raise app.engine_runtime.Refused("OptiQ is not installed.")

    monkeypatch.setattr(app.engines.OPTIQ, "program", missing)


def _port_busy(app, mac, monkeypatch):
    mac.busy = "Another program uses port 8080. Quit it, then start the server again."


def _too_large(app, mac, monkeypatch):
    monkeypatch.setattr(app.model_info, "weights_bytes", lambda folder: 40 * GIB)


def _no_free_memory(app, mac, monkeypatch):
    monkeypatch.setattr(app.model_info, "weights_bytes", lambda folder: 8 * GIB)
    mac.free_pages = 4 * GIB // 16384


def _no_size(app, mac, monkeypatch):
    monkeypatch.setattr(app.model_info, "weights_bytes", lambda folder: 0)


def _window_open(app, mac, monkeypatch):
    monkeypatch.setattr(app, "instance_is_running", lambda: True)


@pytest.mark.parametrize(
    ("break_it", "words"),
    [
        (_no_model, "not downloaded"),
        (_no_engine, "not installed"),
        (_port_busy, "port 8080"),
        (_too_large, "memory"),
        (_no_free_memory, "free"),
        (_no_size, "size"),
        (_window_open, "open"),
    ],
)
def test_a_failed_check_refuses_and_logs(app_module, mac, bundle, monkeypatch, break_it, words):
    switch_on(app_module, bundle)
    break_it(app_module, mac, monkeypatch)
    assert login_agent.login_start(app_module, wait_seconds=0) == 0
    assert mac.started == []
    assert result(app_module)["result"] == "skipped"
    assert words in result(app_module)["reason"]
    assert words in app_module.ACTIVITY_LOG.read_text()
    assert login_agent.status(app_module)["last"] == result(app_module)


def test_a_missing_optiq_engine_is_refused(app_module, mac, bundle, monkeypatch):
    switch_on(app_module, bundle, "optiq")

    def gone(app_dir):
        raise app_module.engine_runtime.Refused("The OptiQ engine is not installed completely.")

    monkeypatch.setattr(app_module.engines.OPTIQ, "program", gone)
    assert login_agent.login_start(app_module) == 0
    assert mac.started == [] and "not installed" in result(app_module)["reason"]


def test_unknown_free_memory_leaves_the_total_check(app_module, mac, bundle, monkeypatch):
    switch_on(app_module, bundle)
    monkeypatch.setattr(app_module, "run_cmd", lambda cmd, timeout=None: (False, "no"))
    login_agent.login_start(app_module, sleep=ready(app_module))
    assert result(app_module)["result"] == "started"


def test_a_server_that_is_already_up_is_left_alone(app_module, mac, bundle, monkeypatch):
    switch_on(app_module, bundle)
    monkeypatch.setattr(
        app_module, "restore_server_state", lambda: setattr(app_module.STATE, "server_pid", 123)
    )
    assert login_agent.login_start(app_module) == 0
    assert mac.started == [] and "already running" in result(app_module)["reason"]
    app_module.STATE.server_pid = None


def test_the_window_opened_during_the_checks_wins(app_module, mac, bundle, monkeypatch):
    switch_on(app_module, bundle)
    answers = iter([False, True])
    monkeypatch.setattr(app_module, "instance_is_running", lambda: next(answers))
    assert login_agent.login_start(app_module) == 0
    assert mac.started == [] and result(app_module)["result"] == "skipped"


def test_a_second_helper_does_nothing(app_module, mac, bundle):
    switch_on(app_module, bundle)
    with open(app_module.APP_DIR / "login-start.lock", "w") as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        assert login_agent.login_start(app_module) == 0
    assert mac.started == []


def test_a_leftover_login_item_is_removed_when_off(app_module, mac):
    path = login_agent.plist_path()
    path.parent.mkdir(parents=True)
    path.write_text("old")
    assert login_agent.login_start(app_module) == 0
    assert not path.exists()
    # bootout would end this very process.
    assert mac.launchctl_calls() == [] and mac.started == []


def test_a_server_that_dies_is_not_started_again(app_module, mac, bundle):
    switch_on(app_module, bundle)

    def dies():
        # What the watcher does when the process is gone.
        app_module.STATE.server_pid = None
        app_module.STATE.server_last_error = "exited with status 1"
        app_module._clear_server_pidfile()

    assert login_agent.login_start(app_module, sleep=after_first_look(dies)) == 0
    assert len(mac.started) == 1
    assert result(app_module)["result"] == "failed"
    assert "status 1" in result(app_module)["reason"]


def test_stop_from_the_app_while_starting_is_respected(app_module, mac, bundle):
    switch_on(app_module, bundle)
    # The app, another process, stops the server: all this one sees is the pidfile.
    stopped = after_first_look(app_module._clear_server_pidfile)
    assert login_agent.login_start(app_module, sleep=stopped) == 0
    assert len(mac.started) == 1
    assert mac.started[0].returncode is None  # not ours to signal any more
    assert result(app_module)["result"] == "stopped"
    assert app_module.STATE.server_stopping is True  # the watcher stays quiet
    assert login_agent.read_default(app_module)["autostart"] is True
    app_module.STATE.server_stopping = False


def test_a_login_start_opens_no_window_and_gives_the_server_its_own_session(
    app_module, mac, bundle, monkeypatch
):
    switch_on(app_module, bundle)
    monkeypatch.setitem(sys.modules, "webview", None)  # any import of it now fails
    assert login_agent.login_start(app_module, sleep=ready(app_module)) == 0
    [proc] = mac.started
    assert result(app_module)["result"] == "started"
    assert proc.kwargs["start_new_session"] is True  # it outlives the login job
    assert proc.returncode is None  # left running
    assert json.loads((app_module.APP_DIR / "server.pid").read_text())["background"] is True


def test_a_server_that_never_answers_is_stopped(app_module, mac, bundle):
    switch_on(app_module, bundle)
    assert login_agent.login_start(app_module, wait_seconds=0) == 0
    [proc] = mac.started
    assert proc.returncode is not None
    assert result(app_module)["result"] == "failed"
    assert "in time" in result(app_module)["reason"]


@pytest.mark.parametrize("error", ["refused", "crash"])
def test_a_launch_that_raises_is_recorded(app_module, mac, bundle, monkeypatch, error):
    switch_on(app_module, bundle, "optiq")
    kind = app_module.engine_runtime.Refused if error == "refused" else OSError

    def fail(*args, **kwargs):
        raise kind("no room for the engine folder")

    monkeypatch.setattr(app_module, "_launch_server", fail)
    assert login_agent.login_start(app_module) == 0
    assert result(app_module)["result"] == "failed"
    assert "no room" in result(app_module)["reason"]


def test_an_optiq_server_that_answers_without_the_key_is_stopped(
    app_module, mac, bundle, monkeypatch
):
    """With the app's own watcher: the headless start stays for its key check."""
    monkeypatch.setattr(app_module, "_watch_server", mac.real_watcher)
    monkeypatch.setattr(app_module.engine_runtime, "listeners", lambda port: {FAKE_PID})
    monkeypatch.setattr(
        app_module.engine_runtime, "auth_state", lambda port, key, anthropic=False: "open"
    )
    switch_on(app_module, bundle, "optiq")
    assert login_agent.login_start(app_module, wait_seconds=20) == 0
    [proc] = mac.started
    assert proc.returncode is not None
    assert result(app_module)["result"] == "failed"
    assert "without the key" in result(app_module)["reason"]


def open_server(app, mac, monkeypatch):
    """The app's own watcher, looking at a server that answers without the key."""
    monkeypatch.setattr(app, "_watch_server", mac.real_watcher)
    monkeypatch.setattr(app.engine_runtime, "listeners", lambda port: {FAKE_PID})
    monkeypatch.setattr(app.engine_runtime, "auth_state", lambda port, key, anthropic=False: "open")


def test_the_reason_is_read_only_when_the_watcher_has_written_it(
    app_module, mac, bundle, monkeypatch
):
    """The watcher forgets the pid a moment before it records why."""
    open_server(app_module, mac, monkeypatch)
    reset = app_module._reset_engine

    def slow_reset():
        reset()
        time.sleep(login_agent.POLL_SECONDS * 3)  # hold that moment open

    switch_on(app_module, bundle)
    monkeypatch.setattr(app_module, "_reset_engine", slow_reset)
    assert login_agent.login_start(app_module, wait_seconds=20) == 0
    assert "without the key" in result(app_module)["reason"]


def test_a_watcher_quicker_than_the_record_check_gives_its_own_reason(
    app_module, mac, bundle, monkeypatch
):
    """The watcher may stop the server and drop the pidfile before the launch returns here."""
    open_server(app_module, mac, monkeypatch)
    launch = app_module._launch_server

    def launch_then_wait(*args):
        proc = launch(*args)
        deadline = time.monotonic() + 10
        while app_module._server_pidfile().exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return proc

    switch_on(app_module, bundle)
    monkeypatch.setattr(app_module, "_launch_server", launch_then_wait)
    assert login_agent.login_start(app_module, wait_seconds=20) == 0
    assert result(app_module)["result"] == "failed"
    assert "without the key" in result(app_module)["reason"]
