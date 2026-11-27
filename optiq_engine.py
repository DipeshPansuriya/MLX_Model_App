"""The direct ``optiq serve`` command: where it is, which models it may serve, how it starts.

Nothing of ours runs between the app and the server. The API key is the only gate
the server has, so it goes in through the environment, never on the command line.
"""

from __future__ import annotations

import hashlib
import json
import os
import platform
import re
import sys
import sysconfig
from pathlib import Path

import engine_runtime as runtime
from engine_runtime import HOST, Refused, new_key, write_key  # noqa: F401 - the engine's own

VERSION = "0.8.6"  # the reviewed release; any other needs a new review
PORT = 8080  # the one place; allowed with the ownership checks of review section 8
RESERVED_PORTS = (8765,)  # the app's own page
PYTHON = (3, 15)  # the lock holds wheels for this CPython on Apple silicon only
LOCK_FILE = Path(__file__).resolve().parent / "packaging" / "optiq-lock.txt"
LOCK_SHA256 = "4e7c0151282937b512cc8ac17cafbdc5821a0d5a98a3e925138854c4bc26867d"
MARKER = ".studio-lock"  # written last: holds the hash of the lock that was installed
KEY_ENV = "OPTIQ_API_KEY"
CONFIG_BYTES = 8 * 1024 * 1024

# The only repo code that may run: reviewed file by file, at one revision.
REVIEWED_CODE: dict[tuple[str, str], dict[str, str]] = {
    (
        "mlx-community/Spark-X2.5-4B-OptiQ-4bit",
        "105b628431dd078933c969a89cba78066dfa86b5",
    ): {
        "configuration_spark.py": (
            "02218597240490f490659b184052db940b08163ff9c3af7b7e59323f6f963722"
        ),
        "modeling_spark.py": ("9cf0d1ad2b54b9f7088792779ddd8b5cb4d4fe63bfea054da7f2dd16362bcaf4"),
    }
}
# A server started on a vision model opens image URLs and local paths from a request.
VISION_KEYS = ("vision_config", "image_token_id", "image_token_index", "vision_start_token_id")
VISION_SIDECAR = "optiq_vision.safetensors"
AUTO_MAP_FILES = (
    "config.json",
    "tokenizer_config.json",
    "processor_config.json",
    "preprocessor_config.json",
)
SETTING_FLAGS = (
    ("server_max_reply_tokens", "--max-tokens"),  # a default, not a cap
    ("prompt_cache_size", "--prompt-cache-size"),
    ("prompt_cache_bytes", "--prompt-cache-bytes"),
    ("max_context_tokens", "--max-context"),  # older text is dropped, not refused
)
PERFORMANCE_FLAGS = (
    ("kv_bits", "--kv-bits"),
    ("kv_group_size", "--kv-group-size"),
    ("prefill_step_size", "--prefill-step-size"),
)
KEY_SHAPE = re.compile(r"sk-optiq-[A-Za-z0-9_-]{32,128}")


# --------------------------------------------------------------------------- #
# The engine
# --------------------------------------------------------------------------- #
def venv_dir(app_dir: Path) -> Path:
    """The app's own engine folder, the only one installed from the shipped lock.

    No setting changes it: neither a file nor a request chooses which program runs.
    """
    return app_dir / "engines" / "optiq" / "venv"


def engine_dir(app_dir: Path) -> Path:
    return app_dir / "engines" / "optiq"


def python_problem(version=None, machine=None, free_threaded=None) -> str | None:
    """Why the lock cannot be installed with the app's own Python, or None."""
    version = tuple(version or sys.version_info[:3])
    machine = machine or platform.machine()
    if free_threaded is None:
        free_threaded = bool(sysconfig.get_config_var("Py_GIL_DISABLED"))
    if version[:2] == PYTHON and machine == "arm64" and not free_threaded:
        return None
    kind = " free-threaded" if free_threaded else ""
    return (
        f"OptiQ needs Python {PYTHON[0]}.{PYTHON[1]} (not free-threaded) on Apple silicon. "
        f"MLX Studio runs on{kind} Python {version[0]}.{version[1]}, {machine}."
    )


def state(app_dir: Path) -> str:
    """``installed``, ``update``, ``broken``, ``not_installed`` or ``unsupported``."""
    venv = venv_dir(app_dir)
    if not os.path.lexists(venv):
        return "unsupported" if python_problem() else "not_installed"
    try:
        marker = (venv / MARKER).read_text("ascii").strip()
    except (OSError, ValueError):
        return "broken"
    return "installed" if marker == LOCK_SHA256 else "update"


def install(app_dir: Path, emit, run=runtime.run_step) -> None:
    """Build the app's own venv from the shipped lock. A failure leaves it unmarked."""
    if why := python_problem():
        raise Refused(why)
    try:
        genuine = runtime.file_sha256(LOCK_FILE) == LOCK_SHA256
    except OSError:
        genuine = False
    if not genuine:
        raise Refused("The shipped package lock is missing or changed. Reinstall MLX Studio.")
    folder, venv = engine_dir(app_dir), venv_dir(app_dir)
    # The folder is removed before the download: never one that works.
    if state(app_dir) == "installed":
        raise Refused(runtime.ALREADY_INSTALLED)
    runtime.remove_folder(app_dir, folder)  # refuses links first
    home, tmp = folder / "install" / "home", folder / "install" / "tmp"
    for made in (home, tmp):
        made.mkdir(parents=True, mode=0o700)
    env = {
        "PATH": "/usr/bin:/bin",
        "HOME": str(home),
        "TMPDIR": str(tmp),
        "PIP_CONFIG_FILE": "/dev/null",
    }
    seen: list[str] = []

    def say(line: str) -> None:
        seen.append(line)
        emit(line)

    python = str(venv / "bin" / "python")
    say(f"Creating the engine's own Python in {venv}")
    if run([sys.executable, "-I", "-m", "venv", str(venv)], env, say):
        raise Refused("The engine's Python folder could not be created.")
    say(f"Installing the reviewed packages of OptiQ {VERSION}")
    pip = [python, "-I", "-m", "pip"]
    command = [*pip, "install", "--isolated", "--require-hashes", "--no-deps"]
    command += ["--only-binary=:all:", "--no-compile", "--no-cache-dir"]
    command += ["--index-url", "https://pypi.org/simple", "-r", str(LOCK_FILE)]
    if run(command, env, say):
        raise Refused(runtime.pip_failure(seen))
    said: list[str] = []
    failed = run([*pip, "check"], env, lambda line: (said.append(line), say(line)))
    # pygame is left out of the lock on purpose: only its absence may be reported.
    if failed and (not said or any("requires pygame, which" not in line for line in said)):
        raise Refused("The package check failed after the install; see the lines above.")
    if not list(venv.glob(f"lib/python*/site-packages/mlx_optiq-{VERSION}.dist-info")):
        raise Refused(f"OptiQ {VERSION} is not in the folder after the install.")
    (venv / MARKER).write_text(LOCK_SHA256, "ascii")


def remove(app_dir: Path) -> None:
    """Delete the engine's folder under the app's own; models stay."""
    runtime.remove_folder(app_dir, engine_dir(app_dir))


def _package(name: str, version: str) -> tuple[str, str]:
    return re.sub(r"[-_.]+", "-", name).lower(), version


def _unlocked_packages(venv: Path) -> list[str]:
    """Installed packages the shipped lock does not name at that version, pip aside.

    Names and versions only: the files inside a package are not checked.
    """
    try:
        genuine = runtime.file_sha256(LOCK_FILE) == LOCK_SHA256
        text = LOCK_FILE.read_text("utf-8")
    except (OSError, ValueError):
        genuine, text = False, ""
    if not genuine:
        raise Refused("The shipped package lock is missing or changed. Reinstall MLX Studio.")
    locked = {_package(*pin) for pin in re.findall(r"^([A-Za-z0-9_.-]+)==([^\s;\\]+)", text, re.M)}
    extra = []
    for info in venv.glob("lib/python*/site-packages/*.dist-info"):
        found = _package(*info.name.removesuffix(".dist-info").partition("-")[::2])
        if found[0] != "pip" and found not in locked:
            extra.append(info.name.removesuffix(".dist-info"))
    return sorted(extra)


def python_path(app_dir: Path) -> Path:
    """The engine's Python, after the folder is checked again for this start."""
    venv = venv_dir(app_dir)
    runtime.refuse_links(app_dir, venv)
    python = venv / "bin" / "python"
    found = sorted(venv.glob("lib/python*/site-packages/mlx_optiq-*.dist-info"))
    if not python.exists() or not found:
        raise Refused(f"The OptiQ engine is not installed in {venv}.")
    if [p.name for p in found] != [f"mlx_optiq-{VERSION}.dist-info"]:
        raise Refused(f"Only OptiQ {VERSION} has been reviewed; {venv} holds another version.")
    if state(app_dir) != "installed":
        raise Refused(
            "The OptiQ engine is not installed completely. Run Install / Update on Setup."
        )
    if extra := _unlocked_packages(venv):
        raise Refused(
            f"The OptiQ folder holds packages that are not in the reviewed lock: "
            f"{', '.join(extra[:5])}. Remove the engine on Setup and install it again."
        )
    return python


# --------------------------------------------------------------------------- #
# Models
# --------------------------------------------------------------------------- #
def _json_object(path: Path) -> dict:
    try:
        if path.stat().st_size > CONFIG_BYTES:
            raise ValueError("too large")
        data = json.loads(path.read_text("utf-8"))
        if not isinstance(data, dict):
            raise ValueError("not an object")
    except (OSError, ValueError, RecursionError) as exc:
        raise Refused(f"Not served: cannot read {path.name} of this model.") from exc
    return data


def _sha256(path: Path) -> str:
    try:
        with open(path, "rb") as handle:
            return hashlib.file_digest(handle, "sha256").hexdigest()
    except OSError as exc:
        raise Refused(f"Not served: cannot read {path.name} of this model.") from exc


def check_model(repo: str, snapshot: Path) -> None:
    """Raise ``Refused`` unless the cached ``snapshot`` of ``repo`` may be served."""
    config = _json_object(snapshot / "config.json")
    configs = [config] + [
        _json_object(snapshot / name)
        for name in AUTO_MAP_FILES[1:]
        if os.path.lexists(snapshot / name)
    ]
    if any(key in config for key in VISION_KEYS) or os.path.lexists(snapshot / VISION_SIDECAR):
        raise Refused("Not served: this model reads images, and only text models are reviewed.")
    if not any(snapshot.glob("*.safetensors")):
        raise Refused("Not served: this model has no safetensors weights.")
    code = []
    for folder, folders, names in os.walk(snapshot):
        # os.walk does not enter linked folders, so one is refused outright.
        if any(os.path.islink(os.path.join(folder, name)) for name in folders):
            raise Refused("Not served: this model holds a linked folder that may hide code.")
        code += [Path(folder, name) for name in names if name.lower().endswith(".py")]
    maps = [c["auto_map"] for c in configs if "auto_map" in c]
    if not code and not maps and "model_file" not in config:
        return
    reviewed = REVIEWED_CODE.get((repo, snapshot.name))
    found = {p.relative_to(snapshot).as_posix(): _sha256(p) for p in code} if reviewed else {}
    # "other/repo--module.Class" would fetch code that is not in this snapshot.
    remote = any("--" in json.dumps(value) for value in maps)
    if not reviewed or found != reviewed or remote or "model_file" in config:
        raise Refused(
            "Not served: this model brings its own code, and this repo and revision "
            "have not been reviewed."
        )


# --------------------------------------------------------------------------- #
# Command, environment, key
# --------------------------------------------------------------------------- #
def command(
    python,
    snapshot,
    port: int,
    *,
    settings: dict,
    performance: dict,
    origins: str,
    anthropic: bool = True,
) -> list[str]:
    if port in RESERVED_PORTS:
        raise Refused(f"Port {port} is reserved; the OptiQ server may not use it.")
    cmd = [str(python), "-I", "-B", "-u", "-m", "optiq.cli", "serve"]
    cmd += ["--model", str(snapshot), "--host", HOST, "--port", str(port)]
    cmd += ["--single-model", "--no-responses"]
    if not anthropic:  # on: /v1/messages, behind the same key
        cmd.append("--no-anthropic")
    # "exit": a dead server is reported, not restarted behind the app's back.
    cmd += ["--on-generation-death", "exit", "--max-concurrent", "1"]
    # Covers the OpenAI replies only; some of OptiQ's replies still allow any origin.
    cmd += ["--allowed-origins", origins]
    for values, flags in ((settings, SETTING_FLAGS), (performance, PERFORMANCE_FLAGS)):
        for key, flag in flags:
            if values.get(key):
                # Whole numbers only: OptiQ drops a size such as "5GB" without a word.
                cmd += [flag, str(int(values[key]))]
    return cmd


def environment(root: Path, key: str) -> dict[str, str]:
    """The child's whole environment. Nothing is inherited from the app."""
    folders = {
        "HOME": root / "home",
        "TMPDIR": root / "tmp",
        "OPTIQ_HOME": root / "home" / ".optiq",
        "HF_HOME": root / "hf",
    }
    for folder in (root, *folders.values()):
        folder.mkdir(mode=0o700, parents=True, exist_ok=True)
        folder.chmod(0o700)
    # Either file could hold a cloud login or turn request dumps on.
    for name in ("config.yaml", "state.json"):
        if os.path.lexists(folders["OPTIQ_HOME"] / name):
            raise Refused(f"Remove {folders['OPTIQ_HOME'] / name}: the engine must have none.")
    return {
        "PATH": "/usr/bin:/bin",
        **{name: str(folder) for name, folder in folders.items()},
        "HF_HUB_OFFLINE": "1",
        "TRANSFORMERS_OFFLINE": "1",
        "HF_HUB_DISABLE_TELEMETRY": "1",
        KEY_ENV: key,
    }


def read_key(path: Path) -> str | None:
    """The stored key, or None when the file is open to others or is not a key."""
    try:
        with open(path, "rb") as handle:
            if os.fstat(handle.fileno()).st_mode & 0o077:
                return None
            text = handle.read(256).decode("ascii")
    except (OSError, UnicodeDecodeError):
        return None
    return text if KEY_SHAPE.fullmatch(text) else None
