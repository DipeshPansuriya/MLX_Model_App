"""Row 867: the direct ``optiq serve`` command. Fakes only: no engine, no model."""

import hashlib
import json
import os
import stat
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

import engine_runtime as runtime
import optiq_engine as engine

SPARK = "mlx-community/Spark-X2.5-4B-OptiQ-4bit"
SPARK_REVISION = "105b628431dd078933c969a89cba78066dfa86b5"
KEY = "sk-optiq-" + "k" * 43


def _venv(root, version=engine.VERSION):
    (root / "bin").mkdir(parents=True)
    (root / "bin" / "python").write_text("")
    (root / engine.MARKER).write_text(engine.LOCK_SHA256)
    (root / "lib" / "python3.15" / "site-packages" / f"mlx_optiq-{version}.dist-info").mkdir(
        parents=True
    )
    return root


def _snapshot(tmp_path, config=None, files=None, name="abc123"):
    folder = tmp_path / "snapshots" / name
    folder.mkdir(parents=True)
    (folder / "config.json").write_text(json.dumps(config or {"model_type": "qwen3"}))
    (folder / "model.safetensors").write_bytes(b"x")
    for path, text in (files or {}).items():
        target = folder / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    return folder


# ---- where the engine is ----------------------------------------------------
def test_default_engine_folder_belongs_to_the_app(tmp_path):
    assert engine.venv_dir(tmp_path) == tmp_path / "engines" / "optiq" / "venv"


def test_a_config_file_cannot_point_the_engine_elsewhere(tmp_path):
    # Another folder would not be covered by the hash lock the app installs from.
    other = _venv(tmp_path / "elsewhere")
    (tmp_path / "engine.json").write_text(json.dumps({"optiq_venv": str(other)}))
    assert engine.venv_dir(tmp_path) == tmp_path / "engines" / "optiq" / "venv"
    with pytest.raises(engine.Refused, match="not installed"):
        engine.python_path(tmp_path)


def test_installed_engine_gives_its_python(tmp_path):
    venv = _venv(tmp_path / "engines" / "optiq" / "venv")
    assert engine.python_path(tmp_path) == venv / "bin" / "python"


def test_missing_engine_is_refused(tmp_path):
    with pytest.raises(engine.Refused, match="not installed"):
        engine.python_path(tmp_path)


def test_another_engine_version_is_refused(tmp_path):
    _venv(tmp_path / "engines" / "optiq" / "venv", version="0.9.0")
    with pytest.raises(engine.Refused, match=engine.VERSION):
        engine.python_path(tmp_path)


def test_no_user_folder_is_written_into_the_module():
    with open(engine.__file__, encoding="utf-8") as source:
        assert "/Users/" not in source.read()


# ---- which models -----------------------------------------------------------
def test_a_text_model_without_code_is_allowed(tmp_path):
    engine.check_model("org/plain", _snapshot(tmp_path))


@pytest.mark.parametrize(
    ("config", "files", "words"),
    [
        ({"auto_map": {"AutoConfig": "c.C"}}, None, "code"),
        ({"model_type": "x"}, {"c.py": "pass"}, "code"),
        ({"model_type": "x"}, {"sub/c.py": "pass"}, "code"),
        ({"model_type": "x"}, {"tokenizer_config.json": '{"auto_map": {"A": "t.T"}}'}, "code"),
        ({"model_file": "m.py"}, None, "code"),
        ({"vision_config": {}}, None, "image"),
        ({"image_token_id": 5}, None, "image"),
        ({"model_type": "x"}, {"optiq_vision.safetensors": "x"}, "image"),
        ({"model_type": "x"}, {"tokenizer_config.json": "{"}, "read"),
    ],
)
def test_models_with_code_or_images_are_refused(tmp_path, config, files, words):
    with pytest.raises(engine.Refused, match=words):
        engine.check_model("org/model", _snapshot(tmp_path, config, files))


def test_a_model_without_a_readable_config_is_refused(tmp_path):
    folder = _snapshot(tmp_path)
    (folder / "config.json").unlink()
    with pytest.raises(engine.Refused, match="read"):
        engine.check_model("org/model", folder)


def test_a_model_without_safetensors_weights_is_refused(tmp_path):
    folder = _snapshot(tmp_path)
    (folder / "model.safetensors").unlink()
    with pytest.raises(engine.Refused, match="safetensors"):
        engine.check_model("org/model", folder)


def _spark(tmp_path, monkeypatch, revision=SPARK_REVISION):
    code = {"configuration_spark.py": "A = 1\n", "modeling_spark.py": "B = 2\n"}
    hashes = {name: hashlib.sha256(text.encode()).hexdigest() for name, text in code.items()}
    monkeypatch.setitem(engine.REVIEWED_CODE, (SPARK, SPARK_REVISION), hashes)
    config = {"auto_map": {"AutoConfig": "configuration_spark.Spark2_5Config"}}
    return _snapshot(tmp_path, config, code, name=revision)


def test_the_shipped_approval_is_spark_at_the_reviewed_revision():
    assert engine.REVIEWED_CODE == {
        (SPARK, SPARK_REVISION): {
            "configuration_spark.py": (
                "02218597240490f490659b184052db940b08163ff9c3af7b7e59323f6f963722"
            ),
            "modeling_spark.py": (
                "9cf0d1ad2b54b9f7088792779ddd8b5cb4d4fe63bfea054da7f2dd16362bcaf4"
            ),
        }
    }


def test_reviewed_code_at_the_pinned_revision_is_allowed(tmp_path, monkeypatch):
    engine.check_model(SPARK, _spark(tmp_path, monkeypatch))


def test_another_revision_of_a_reviewed_model_is_refused(tmp_path, monkeypatch):
    with pytest.raises(engine.Refused, match="code"):
        engine.check_model(SPARK, _spark(tmp_path, monkeypatch, revision="f" * 40))


def test_another_repo_with_the_same_files_is_refused(tmp_path, monkeypatch):
    with pytest.raises(engine.Refused, match="code"):
        engine.check_model("someone/copy", _spark(tmp_path, monkeypatch))


@pytest.mark.parametrize("change", ["edited", "extra", "remote"])
def test_changed_reviewed_code_is_refused(tmp_path, monkeypatch, change):
    folder = _spark(tmp_path, monkeypatch)
    if change == "edited":
        (folder / "modeling_spark.py").write_text("import os\n")
    elif change == "extra":
        (folder / "extra.py").write_text("pass\n")
    else:
        (folder / "config.json").write_text(
            json.dumps({"auto_map": {"AutoConfig": "evil/repo--configuration_spark.C"}})
        )
    with pytest.raises(engine.Refused, match="code"):
        engine.check_model(SPARK, folder)


# ---- the command ------------------------------------------------------------
def _command(**kwargs):
    base = {"settings": {}, "performance": {}, "origins": "http://127.0.0.1:8765"}
    return engine.command("/v/bin/python", "/m/snap", engine.PORT, **{**base, **kwargs})


def test_the_command_is_optiq_serve_itself():
    cmd = _command()
    assert cmd[:7] == ["/v/bin/python", "-I", "-B", "-u", "-m", "optiq.cli", "serve"]
    assert cmd[7:13] == ["--model", "/m/snap", "--host", "127.0.0.1", "--port", "8080"]
    for flag in ("--single-model", "--no-responses"):
        assert flag in cmd
    assert cmd[cmd.index("--on-generation-death") + 1] == "exit"
    assert cmd[cmd.index("--max-concurrent") + 1] == "1"
    assert cmd[cmd.index("--allowed-origins") + 1] == "http://127.0.0.1:8765"


def test_the_command_never_carries_a_key_or_a_risky_flag():
    text = " ".join(_command())
    for word in ("--api-key", "--no-auth", "--adapter", "--draft", "--trust-remote-code"):
        assert word not in text
    assert "--studio" not in text


def test_settings_become_official_flags_only_when_set():
    assert "--max-context" not in _command()
    cmd = _command(
        settings={
            "server_max_reply_tokens": 512,
            "prompt_cache_size": 2,
            "prompt_cache_bytes": 1000,
            "max_context_tokens": 8192,
            "memory_limit_percent": 60,
            "mlx_cache_limit_mb": 0,
        }
    )
    for flag, value in (
        ("--max-tokens", "512"),
        ("--prompt-cache-size", "2"),
        ("--prompt-cache-bytes", "1000"),
        ("--max-context", "8192"),
    ):
        assert cmd[cmd.index(flag) + 1] == value
    assert "60" not in cmd and "0" not in cmd


def test_performance_modes_use_the_official_flags():
    cmd = _command(performance={"kv_bits": 8, "kv_group_size": 64, "prefill_step_size": 256})
    for flag, value in (("--kv-bits", "8"), ("--kv-group-size", "64")):
        assert cmd[cmd.index(flag) + 1] == value
    assert cmd[cmd.index("--prefill-step-size") + 1] == "256"
    assert "--kv-bits" not in _command()


@pytest.mark.parametrize("value", [5_368_709_120, 5368709120.0])
def test_the_cache_size_is_passed_as_plain_bytes(value):
    # OptiQ 0.8.6 silently ignores "5GB" or "5e9" and keeps about 0.5 GB.
    cmd = _command(settings={"prompt_cache_bytes": value})
    assert cmd[cmd.index("--prompt-cache-bytes") + 1] == "5368709120"


@pytest.mark.parametrize("port", [8765])
def test_reserved_ports_are_refused(port):
    with pytest.raises(engine.Refused, match=str(port)):
        engine.command("/p", "/m", port, settings={}, performance={}, origins="x")


# ---- environment and key ----------------------------------------------------
def test_the_child_environment_is_built_from_nothing(tmp_path, monkeypatch):
    for name in ("ANTHROPIC_API_KEY", "HF_TOKEN", "OPTIQ_DUMP_REQUESTS", "SECRET_THING"):
        monkeypatch.setenv(name, "inherited-value")
    env = engine.environment(tmp_path / "run", KEY)
    assert set(env) == {
        "PATH",
        "HOME",
        "TMPDIR",
        "OPTIQ_HOME",
        "HF_HOME",
        "HF_HUB_OFFLINE",
        "TRANSFORMERS_OFFLINE",
        "HF_HUB_DISABLE_TELEMETRY",
        "OPTIQ_API_KEY",
    }
    assert "inherited-value" not in env.values()
    assert env["OPTIQ_API_KEY"] == KEY
    assert env["HF_HUB_OFFLINE"] == env["TRANSFORMERS_OFFLINE"] == "1"
    for name in ("HOME", "TMPDIR", "OPTIQ_HOME", "HF_HOME"):
        folder = tmp_path / "run"
        assert env[name].startswith(str(folder))
        assert stat.S_IMODE(os.stat(env[name]).st_mode) == 0o700


@pytest.mark.parametrize("name", ["config.yaml", "state.json"])
def test_a_config_or_login_in_the_private_home_is_refused(tmp_path, name):
    home = tmp_path / "run" / "home" / ".optiq"
    home.mkdir(parents=True)
    (home / name).write_text('{"cloud_token": "t"}')
    with pytest.raises(engine.Refused, match=name):
        engine.environment(tmp_path / "run", KEY)


def test_keys_are_random_and_long():
    first, second = engine.new_key(), engine.new_key()
    assert first != second and len(first) >= 40


def test_the_key_file_is_private_even_when_replaced(tmp_path):
    path = tmp_path / "server.key"
    path.write_text("old")
    path.chmod(0o644)
    engine.write_key(path, KEY)
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert engine.read_key(path) == KEY
    assert [p.name for p in tmp_path.iterdir()] == ["server.key"]


def test_an_open_or_odd_key_file_is_not_used(tmp_path):
    path = tmp_path / "server.key"
    assert engine.read_key(path) is None
    engine.write_key(path, KEY)
    path.chmod(0o644)
    assert engine.read_key(path) is None
    engine.write_key(path, "bad key\r\nX: y")
    assert engine.read_key(path) is None


# ---- is the key really enforced ---------------------------------------------
def _fake_server(get_open=False, post_open=False):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def _answer(self, open_route):
            sent = self.headers.get("Authorization")
            seen.append((self.command, sent))
            self.send_response(200 if open_route or sent == f"Bearer {KEY}" else 401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            self._answer(get_open)

        def do_POST(self):
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            self._answer(post_open)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, seen


def test_a_server_that_enforces_the_key_is_ready():
    server, seen = _fake_server()
    try:
        assert runtime.auth_state(server.server_port, KEY) == "ready"
    finally:
        server.shutdown()
        server.server_close()
    assert seen[:2] == [("GET", None), ("POST", None)]


@pytest.mark.parametrize("opened", [{"get_open": True}, {"post_open": True}])
def test_a_server_that_answers_without_the_key_is_open_and_gets_no_key(opened):
    server, seen = _fake_server(**opened)
    try:
        assert runtime.auth_state(server.server_port, KEY) == "open"
    finally:
        server.shutdown()
        server.server_close()
    assert all(sent is None for _, sent in seen)


def test_a_server_that_refuses_the_key_is_not_ready():
    server, _ = _fake_server()
    try:
        assert runtime.auth_state(server.server_port, KEY + "x") == "starting"
    finally:
        server.shutdown()
        server.server_close()


def test_nothing_listening_is_still_starting():
    server, _ = _fake_server()
    port = server.server_port
    server.shutdown()
    server.server_close()
    assert runtime.auth_state(port, KEY) == "starting"


@pytest.mark.parametrize(
    ("output", "mine"), [("41\n", True), ("42\n", False), ("41\n42\n", False), ("", False)]
)
def test_the_key_goes_only_to_our_own_listener(output, mine):
    asked = []

    def run(cmd, **kwargs):
        asked.append(cmd)
        return type("Done", (), {"stdout": output, "returncode": 0})()

    assert runtime.listener_is(18090, 41, run=run) is mine
    assert asked[0][0] == "/usr/sbin/lsof" and "-iTCP:18090" in asked[0]
