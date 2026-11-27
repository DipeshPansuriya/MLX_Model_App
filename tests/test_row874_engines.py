"""Row 874: selectable engines. Fakes only: no engine, model, pip or reserved port."""

import json
import subprocess
from pathlib import Path

import pytest

import engine_runtime as runtime
import engines
import optiq_engine

ROOT = Path(__file__).resolve().parent.parent
KEY = "sk-optiq-" + "k" * 43
REPO = "mlx-community/Tiny-4bit"
SECRETS = ("ANTHROPIC_API_KEY", "HF_TOKEN", "OPENAI_API_KEY", "GITHUB_TOKEN", "OPTIQ_API_KEY")


# ---- the registry -------------------------------------------------------------
def test_mlx_is_first_and_the_default():
    assert [e.id for e in engines.REGISTRY] == ["mlx", "optiq", "splash", "rapid-mlx"]
    assert engines.get(None) is engines.MLX is engines.REGISTRY[0]
    assert engines.get("nope") is None


@pytest.mark.parametrize("name", ["splash", "rapid-mlx"])
def test_a_planned_engine_has_no_command_and_no_install(name):
    engine = engines.get(name)
    assert not engine.selectable and not engine.installable
    assert engine.refusal == "Not available yet: security review pending."
    for part in ("command", "environment", "program", "install"):
        assert not hasattr(engine, part)


def test_the_optiq_port_is_one_constant():
    assert engines.OPTIQ.port == optiq_engine.PORT == 8080
    assert optiq_engine.RESERVED_PORTS == (8765,)


# ---- the child environment ----------------------------------------------------
def test_the_mlx_environment_keeps_what_it_needs_and_no_secret():
    kept = {
        "PATH": "/bin",
        "HOME": "/h",
        "HF_HOME": "/hf",
        "LC_ALL": "C",
        "MLX_STUDIO_X": "1",
        "PYTHONPATH": "/p",
    }
    env = runtime.minimal_environment({**kept, **dict.fromkeys(SECRETS, "x"), "AWS_ID": "x"})
    assert env == kept


# ---- who holds the port ---------------------------------------------------------
def _lsof(output, asked=None):
    def run(cmd, **kwargs):
        (asked if asked is not None else []).append(cmd)
        return subprocess.CompletedProcess(cmd, 0 if output else 1, output, "")

    return run


def test_every_listener_on_the_port_is_asked_for():
    asked = []
    assert runtime.listeners(18123, run=_lsof("41\n41\n42\n", asked)) == {41, 42}
    # No address: IPv4, IPv6 and wildcard sockets all count.
    assert asked == [["/usr/sbin/lsof", "-nP", "-iTCP:18123", "-sTCP:LISTEN", "-t"]]


@pytest.mark.parametrize(
    ("output", "mine"),
    [("41\n", True), ("41\n41\n", True), ("42\n", False), ("41\n42\n", False), ("", False)],
)
def test_the_key_goes_only_when_every_listener_is_ours(output, mine):
    assert runtime.listener_is(18123, 41, run=_lsof(output)) is mine


def test_an_unreadable_port_is_never_ours():
    def broken(cmd, **kwargs):
        raise OSError("no lsof")

    assert runtime.listeners(18123, run=broken) is None
    assert runtime.listener_is(18123, 41, run=broken) is False


@pytest.mark.parametrize(
    ("holders", "answers", "free"),
    [(set(), False, True), (set(), True, False), ({7}, False, False), (None, False, False)],
)
def test_a_port_is_free_only_when_nothing_holds_or_answers_it(monkeypatch, holders, answers, free):
    monkeypatch.setattr(runtime, "listeners", lambda port: holders)
    monkeypatch.setattr(runtime, "answers", lambda port: answers)
    assert (runtime.port_problem(18123) is None) is free


def test_our_running_server_may_hold_the_port_it_is_replaced_on(monkeypatch):
    monkeypatch.setattr(runtime, "listeners", lambda port: {41})
    monkeypatch.setattr(runtime, "answers", lambda port: True)
    assert runtime.port_problem(18123, ours=41) is None
    assert "18123" in runtime.port_problem(18123, ours=42)


def test_keys_are_hidden_in_text():
    assert runtime.redact(f"Authorization: Bearer {KEY}") == "Authorization: Bearer [key hidden]"


# ---- the OptiQ command ----------------------------------------------------------
def _command(**kwargs):
    base = {"settings": {}, "performance": {}, "origins": "http://127.0.0.1:8765"}
    return engines.OPTIQ.command("/v/python", "/m/snap", 8080, **{**base, **kwargs})


def test_the_anthropic_route_is_on_unless_switched_off():
    assert "--no-anthropic" not in _command()
    assert "--no-anthropic" in _command(anthropic=False)
    assert "--no-responses" in _command()


def test_no_shown_or_passed_address_says_localhost():
    assert "localhost" not in " ".join(_command())


@pytest.mark.parametrize("open_path", ["/v1/models", "/v1/chat/completions", "/v1/messages"])
def test_any_route_open_without_the_key_means_open(monkeypatch, open_path):
    seen = []

    def status(port, method, path, key=None):
        seen.append((path, key))
        return 200 if path == open_path or key else 401

    monkeypatch.setattr(runtime, "_status", status)
    assert runtime.auth_state(18123, KEY, anthropic=True) == "open"
    assert all(key is None for _, key in seen)


# ---- installing -----------------------------------------------------------------
class FakePip:
    """Stands in for ``python -m venv`` and ``pip``: writes what they would."""

    def __init__(self, fail=None, output="", check=""):
        self.calls, self.fail, self.output, self.check = [], fail, output, check

    def __call__(self, cmd, env, emit):
        self.calls.append((cmd, env))
        step = "venv" if "venv" in cmd else "check" if "check" in cmd else "install"
        if step == "venv":
            venv = Path(cmd[-1])
            (venv / "bin").mkdir(parents=True)
            (venv / "bin" / "python").write_text("")
        if step == "install" and not self.fail:
            venv = Path(cmd[0]).parent.parent
            info = f"mlx_optiq-{optiq_engine.VERSION}.dist-info"
            (venv / "lib" / "python3.15" / "site-packages" / info).mkdir(parents=True)
        lines = self.check if step == "check" else self.output if step == self.fail else ""
        for line in lines.splitlines():
            emit(line)
        return 1 if step == self.fail or (step == "check" and lines) else 0


@pytest.fixture
def python_ok(monkeypatch):
    monkeypatch.setattr(optiq_engine, "python_problem", lambda: None)


def _install(tmp_path, pip):
    engines.OPTIQ.install(tmp_path, [].append, run=pip)


def test_the_shipped_lock_is_the_reviewed_one():
    assert runtime.file_sha256(optiq_engine.LOCK_FILE) == optiq_engine.LOCK_SHA256
    assert optiq_engine.LOCK_FILE == ROOT / "packaging" / "optiq-lock.txt"


def test_install_uses_only_the_lock_in_the_apps_own_venv(tmp_path, monkeypatch, python_ok):
    for name in (*SECRETS, "PIP_INDEX_URL", "PIP_EXTRA_INDEX_URL"):
        monkeypatch.setenv(name, "inherited-value")
    pip = FakePip(check="mlx-optiq 0.8.6 requires pygame, which is not installed.")
    _install(tmp_path, pip)
    venv = tmp_path / "engines" / "optiq" / "venv"
    install = pip.calls[1][0]
    assert install == [
        str(venv / "bin" / "python"), "-I", "-m", "pip", "install", "--isolated",
        "--require-hashes", "--no-deps", "--only-binary=:all:", "--no-compile",
        "--no-cache-dir", "--index-url", "https://pypi.org/simple",
        "-r", str(optiq_engine.LOCK_FILE),
    ]  # fmt: skip
    for _, env in pip.calls:
        assert "inherited-value" not in env.values() and env["PIP_CONFIG_FILE"] == "/dev/null"
    assert engines.OPTIQ.state(tmp_path) == "installed"
    assert engines.OPTIQ.program(tmp_path) == venv / "bin" / "python"


@pytest.mark.parametrize(
    ("output", "words"),
    [
        ("THESE PACKAGES DO NOT MATCH THE HASHES FROM THE REQUIREMENTS FILE", "hash"),
        ("NewConnectionError: nodename nor servname provided", "network"),
        ("boom", "pip failed"),
    ],
)
def test_a_failed_install_is_broken_and_says_why(tmp_path, python_ok, output, words):
    with pytest.raises(runtime.Refused, match=words):
        _install(tmp_path, FakePip(fail="install", output=output))
    assert engines.OPTIQ.state(tmp_path) == "broken"
    with pytest.raises(runtime.Refused, match="not installed"):
        engines.OPTIQ.program(tmp_path)


def test_any_other_pip_check_complaint_fails_the_install(tmp_path, python_ok):
    with pytest.raises(runtime.Refused, match="check"):
        _install(tmp_path, FakePip(check="mlx 1 has requirement x, but you have y."))
    assert engines.OPTIQ.state(tmp_path) == "broken"


def test_a_changed_lock_installs_nothing(tmp_path, monkeypatch, python_ok):
    monkeypatch.setattr(optiq_engine, "LOCK_SHA256", "0" * 64)
    pip = FakePip()
    with pytest.raises(runtime.Refused, match="lock"):
        _install(tmp_path, pip)
    assert pip.calls == []


@pytest.mark.parametrize(
    ("version", "machine", "free_threaded"),
    [((3, 14, 0), "arm64", False), ((3, 15, 0), "x86_64", False), ((3, 15, 0), "arm64", True)],
)
def test_an_unsupported_python_is_named(version, machine, free_threaded):
    why = optiq_engine.python_problem(version, machine, free_threaded)
    assert "3.15" in why and f"{version[0]}.{version[1]}" in why and machine in why
    assert optiq_engine.python_problem((3, 15, 1), "arm64", False) is None


def test_remove_deletes_only_the_apps_own_folder(tmp_path, python_ok):
    _install(tmp_path, FakePip())
    engines.OPTIQ.remove(tmp_path)
    assert not (tmp_path / "engines" / "optiq").exists()
    assert engines.OPTIQ.state(tmp_path) == "not_installed"


@pytest.mark.parametrize("linked", ["engines", "engines/optiq", "engines/optiq/venv"])
def test_a_linked_engine_folder_is_neither_removed_nor_installed(tmp_path, python_ok, linked):
    outside = tmp_path / "outside"
    (outside / "optiq" / "venv").mkdir(parents=True)
    (outside / "keep").write_text("x")
    app = tmp_path / "app"
    link = app / linked
    link.parent.mkdir(parents=True)
    link.symlink_to(outside)
    pip = FakePip()
    for act in (lambda: engines.OPTIQ.remove(app), lambda: _install(app, pip)):
        with pytest.raises(runtime.Refused, match="link"):
            act()
    assert (outside / "keep").exists() and pip.calls == []


def test_a_marker_of_another_lock_means_update(tmp_path, python_ok):
    _install(tmp_path, FakePip())
    (tmp_path / "engines" / "optiq" / "venv" / optiq_engine.MARKER).write_text("old")
    assert engines.OPTIQ.state(tmp_path) == "update"
    with pytest.raises(runtime.Refused, match="Install / Update"):
        engines.OPTIQ.program(tmp_path)


def test_the_views_are_plain_data():
    views = [e.view(Path("/nowhere")) for e in engines.REGISTRY]
    assert json.loads(json.dumps(views)) == views
    assert views[0]["anthropic"] is False and views[1]["anthropic"] is True


@pytest.mark.parametrize(
    "tail", [["-m", "optiq.cli", "serve"], ["-m", "pip", "install", "--require-hashes", "-r", "x"]]
)
def test_the_suite_refuses_a_real_engine_or_install(tail):
    import portguard

    assert portguard.real_launch(["/venv/bin/python", "-I", *tail])


def test_the_mlx_guard_never_reads_or_repeats_an_authorization_header():
    """Clients that kept an OptiQ key may send it to the MLX server on the same port."""
    import importlib.util
    from email.message import Message

    source = (ROOT / "mlx_guard.py").read_text("utf-8")
    assert "authorization" not in source.lower()
    spec = importlib.util.spec_from_file_location("guard_row874", ROOT / "mlx_guard.py")
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    headers = Message()
    headers["Host"] = "evil.example"
    headers["Authorization"] = f"Bearer {KEY}"
    refusal = guard.check_headers("POST", headers, "http://127.0.0.1:8765")
    assert refusal and KEY not in str(refusal)
