"""Start the default model's API server at Mac login, without opening the window.

Off until the user switches it on. The switch writes one user LaunchAgent that runs
the app's launcher with ``FLAG``; ``login_start`` then checks the model, engine,
memory and port and starts the server through the app's own launch code. It tries
once: nothing here, and nothing in launchd, starts a server again.

``app`` is the running ``app`` module, passed in so this file never imports it.

Keys: a login start needs a password, so only an engine with its own key support
may be started this way (the built-in MLX server has none). The key is kept in a
0600 file under the app's folder and reaches the server through the environment.
"""

from __future__ import annotations

import contextlib
import fcntl
import json
import os
import plistlib
import re
import tempfile
import time
from datetime import datetime
from pathlib import Path

LABEL = "com.mlxstudio.app.login-server"
FLAG = "--start-server-at-login"
LAUNCHCTL = "/bin/launchctl"
VM_STAT = "/usr/bin/vm_stat"
LAUNCHER = "MLXStudio"  # CFBundleExecutable, see packaging/build_app.sh
SMALL_FILE = 4096
# An estimate for what the server needs besides the weights: runtime and first prompt.
HEADROOM_BYTES = 2 * 1024**3
POLL_SECONDS = 0.5
_FIELDS = {"v": int, "repo": str, "engine": str, "anthropic": bool, "autostart": bool}


def record_file(app) -> Path:
    return app.APP_DIR / "default-model.json"


def result_file(app) -> Path:
    return app.APP_DIR / "login-start.json"


def key_file(app) -> Path:
    return app.APP_DIR / "autostart.key"


def plist_path() -> Path:
    return Path.home() / "Library" / "LaunchAgents" / f"{LABEL}.plist"


def _read_json(app, path: Path) -> dict | None:
    try:
        data = json.loads(app._read_small(path, SMALL_FILE))
    except (OSError, ValueError, RecursionError):
        return None
    return data if isinstance(data, dict) else None


# --------------------------------------------------------------------------- #
# The default model
# --------------------------------------------------------------------------- #
def read_default(app) -> dict | None:
    """The saved default model, or None when there is none or it cannot be used."""
    data = _read_json(app, record_file(app))
    if data is None or any(type(data.get(name)) is not kind for name, kind in _FIELDS.items()):
        return None
    if data["v"] != 1 or not app._valid_repo(data["repo"]):
        return None
    return {name: data[name] for name in _FIELDS}


def _save(app, record: dict) -> None:
    app._write_private(record_file(app), json.dumps(record))


def _engine_problem(app, engine_id, repo: str) -> str | None:
    """Why this engine could not serve ``repo``, the port and memory aside."""
    engine = app.engines.get(engine_id) if isinstance(engine_id, str) else None
    if engine is None or not engine.selectable:
        return "That engine cannot be started."
    if repo not in app._complete_models():
        return f"Model {repo} is not downloaded completely."
    if not engine.direct and app.pkg_version("mlx-lm") is None:
        return "mlx-lm is not installed. Run Install / Update on Setup."
    return app._engine_refusal(engine.id, [repo], port_too=False)


def _password_problem(engine) -> str | None:
    """Why this engine may not start at login: nobody is there to watch an open server."""
    if engine.direct:
        return None
    return (
        f"Start at login needs a password, and {engine.name} has no password support. "
        "Choose OptiQ and set the default model again."
    )


def set_default(app, repo: str, engine: str = "mlx", anthropic: bool = True) -> None:
    """Remember the model to serve at login. The server settings saved for it apply."""
    refused = app.engine_runtime.Refused
    if not app._valid_repo(repo) or type(anthropic) is not bool:
        raise refused("Choose a downloaded model.")
    if why := _engine_problem(app, engine, repo):
        raise refused(why)
    previous = read_default(app)
    autostart = bool(previous and previous["autostart"])
    if autostart and _password_problem(app.engines.get(engine)):
        disable(app)
        autostart = False
    if autostart:
        _prepare_key(app, engine)
    _save(
        app,
        {"v": 1, "repo": repo, "engine": engine, "anthropic": anthropic, "autostart": autostart},
    )


def clear_default(app) -> None:
    disable(app)
    record_file(app).unlink(missing_ok=True)


# --------------------------------------------------------------------------- #
# The key
# --------------------------------------------------------------------------- #
def _stored_key(app, engine) -> str:
    path = key_file(app)
    # The engine's own reader refuses a file open to others or text that is not a key.
    key = None if path.is_symlink() else engine.read_key(path)
    if not key:
        raise app.engine_runtime.Refused(
            f"The auto-start key file {path} is not a private key file. "
            "Delete it, then switch auto-start on again."
        )
    return key


def _prepare_key(app, engine_id: str) -> str:
    """The key for login starts, made once. For engines with key support only."""
    engine = app.engines.get(engine_id)
    if not os.path.lexists(key_file(app)):
        app.engine_runtime.write_key(key_file(app), engine.new_key())
    return _stored_key(app, engine)


def client_key(app, engine_id: str, repo: str) -> str | None:
    """The stored key, when a start of ``repo`` should keep the one clients already have."""
    record = read_default(app)
    if not record or not record["autostart"]:
        return None
    engine = app.engines.get(engine_id)
    if (record["engine"], record["repo"]) != (engine_id, repo) or not engine.direct:
        return None
    try:
        return _stored_key(app, engine)
    except app.engine_runtime.Refused:
        return None


def _key_in_use(app) -> bool:
    """Whether the running server has the stored key. The key itself never leaves here."""
    engine = app._running_engine()
    if not app.STATE.server_pid or not engine.direct or key_file(app).is_symlink():
        return False
    stored = engine.read_key(key_file(app))
    return bool(stored) and engine.read_key(app._key_file()) == stored


# --------------------------------------------------------------------------- #
# The LaunchAgent
# --------------------------------------------------------------------------- #
def bundle_executable(app, resources: Path | None = None) -> Path:
    """The installed app's launcher. Refused from source, a disk image or quarantine."""
    refused = app.engine_runtime.Refused
    folder = Path(resources or Path(app.__file__).parent).resolve()
    bundle = folder.parent.parent
    if folder.parts[-2:] != ("Contents", "Resources") or bundle.suffix != ".app":
        raise refused("Only the installed MLX Studio app can start at login, not a source run.")
    if folder.parts[1:2] == ("Volumes",) or "AppTranslocation" in folder.parts:
        raise refused("Move MLX Studio to the Applications folder and open it from there first.")
    launcher = folder.parent / "MacOS" / LAUNCHER
    if not launcher.is_file() or not os.access(launcher, os.X_OK):
        raise refused("The app's launcher is missing. Reinstall MLX Studio.")
    return launcher


def _arguments(launcher: Path) -> list[str]:
    return [str(launcher), FLAG]


def _write_plist(launcher: Path) -> None:
    path = plist_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = plistlib.dumps(
        {
            "Label": LABEL,
            "ProgramArguments": _arguments(launcher),
            "RunAtLoad": True,
            "KeepAlive": False,  # one try per login: never a restart loop
            "AbandonProcessGroup": True,  # the server outlives this job
        }
    )
    handle, temp = tempfile.mkstemp(prefix=f"{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(handle, "wb") as out:
            out.write(content)
            os.fchmod(out.fileno(), 0o644)  # launchd refuses a file others can write
        os.replace(temp, path)
    finally:
        with contextlib.suppress(OSError):  # already gone after a good replace
            os.unlink(temp)


def _login_item(launcher: Path | None) -> str:
    """``ok``, ``missing``, ``other app`` or ``unreadable``."""
    try:
        with open(plist_path(), "rb") as handle:
            found = plistlib.load(handle)["ProgramArguments"]
    except FileNotFoundError:
        return "missing"
    except Exception:  # plistlib raises many kinds for a damaged file
        return "unreadable"
    return "ok" if launcher and found == _arguments(launcher) else "other app"


def enable(app, resources: Path | None = None) -> None:
    """Switch auto-start on. It takes effect at the next login; nothing starts now."""
    refused = app.engine_runtime.Refused
    record = read_default(app)
    if record is None:
        raise refused("Choose a default model first.")
    if why := _password_problem(app.engines.get(record["engine"]) or app.engines.MLX):
        raise refused(why)
    launcher = bundle_executable(app, resources)
    if why := _engine_problem(app, record["engine"], record["repo"]):
        raise refused(why)
    _prepare_key(app, record["engine"])
    try:
        _write_plist(launcher)
    except OSError as exc:
        raise refused(f"The login item could not be written: {exc.strerror}.") from exc
    _save(app, {**record, "autostart": True})


def disable(app) -> None:
    """Switch auto-start off and remove the login item. A running server stays."""
    if record := read_default(app):
        _save(app, {**record, "autostart": False})
    try:
        plist_path().unlink(missing_ok=True)
    except OSError as exc:
        raise app.engine_runtime.Refused(
            f"The login item could not be removed: {exc.strerror}."
        ) from exc
    # Fails when launchd has not loaded it, which is as good.
    app.run_cmd([LAUNCHCTL, "bootout", f"gui/{os.getuid()}/{LABEL}"], timeout=10)


def refresh(app, resources: Path | None = None) -> None:
    """On a normal launch: point the login item at this copy when the app has moved.

    An item the user removed is not put back.
    """
    record = read_default(app)
    if not record or not record["autostart"]:
        return
    try:
        launcher = bundle_executable(app, resources)
        if _login_item(launcher) in ("other app", "unreadable"):
            _write_plist(launcher)
    except (app.engine_runtime.Refused, OSError) as exc:
        app.log_app("warning", "login item not refreshed", error=str(exc)[:200])


def status(app, resources: Path | None = None) -> dict:
    """What the screen shows. No key is in it."""
    record = read_default(app)
    try:
        launcher, unavailable = bundle_executable(app, resources), None
    except app.engine_runtime.Refused as exc:
        launcher, unavailable = None, str(exc)
    enabled = bool(record and record["autostart"])
    item = _login_item(launcher)
    problem = None
    if record is None and os.path.lexists(record_file(app)):
        problem = "The default model record cannot be read. Choose the default model again."
    elif enabled and item != "ok":
        problem = "Auto-start is on, but its login item is not right. Switch it off and on again."
    password = note = key_path = refusal = None
    if record and (engine := app.engines.get(record["engine"])):
        password = engine.direct
        refusal = _password_problem(engine)
        if password:
            key_path = str(key_file(app))
            note = (
                "The server asks for the key in the key file. "
                "Its /health and /v1/optiq/context pages answer without it."
            )
        else:
            note = (
                f"{engine.name} has no password: while the server runs, "
                "any program on this Mac can use it."
            )
    return {
        "available": launcher is not None,
        "unavailable_reason": unavailable,
        "default": record and {k: record[k] for k in ("repo", "engine", "anthropic")},
        "enabled": enabled,
        "login_item": item,
        "password": password,
        "password_note": note,
        "key_file": key_path,
        "key_in_use": _key_in_use(app),
        "autostart_refusal": refusal,
        "last": _read_json(app, result_file(app)),
        "problem": problem,
    }


# --------------------------------------------------------------------------- #
# The headless start
# --------------------------------------------------------------------------- #
def _free_memory(app) -> int | None:
    """Bytes macOS can hand out now, roughly; None when it cannot be told."""
    ok, text = app.run_cmd([VM_STAT], timeout=5)
    page = re.search(r"page size of (\d+) bytes", text)
    counts = re.findall(r"^Pages (?:free|inactive|speculative):\s+(\d+)", text, re.M)
    if not ok or not page or len(counts) != 3:
        return None
    return int(page.group(1)) * sum(map(int, counts))


def _memory_problem(app, repo: str) -> str | None:
    weights = app.model_info.weights_bytes(app._snapshot_dir(repo))
    ram = app.hardware().get("ram_bytes") or 0
    if not weights or not ram:
        return "The model's size or this Mac's memory cannot be read."
    if app.fit_rating(weights / 1024**3, ram) == "too-large":
        return f"{repo} is too large for this Mac's memory."
    free = _free_memory(app)
    need = weights + HEADROOM_BYTES
    if free is not None and need > free:
        return (
            f"Not enough free memory: about {need / 1024**3:.1f} GB needed, "
            f"{free / 1024**3:.1f} GB free."
        )
    return None


def _report(app, record: dict | None, outcome: str, reason: str | None) -> None:
    repo = record["repo"] if record else "-"
    engine = app.engines.get(record["engine"]) if record else None
    payload = {
        "at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "result": outcome,
        "reason": reason,
        "repo": repo,
        "engine": record["engine"] if record else None,
        "password": bool(engine and engine.direct),
    }
    try:
        app._write_private(result_file(app), json.dumps(payload))
    except OSError as exc:
        app.log_app("warning", "login start result not saved", error=str(exc)[:200])
    level = {"started": "success", "starting": "running", "stopped": "info"}.get(outcome, "failed")
    detail = f"login start {outcome}" + (f": {reason}" if reason else "")
    app.log_activity("server", repo, level, detail)


def _pidfile_owner(app) -> int | None:
    data = _read_json(app, app._server_pidfile())
    return data.get("pid") if data else None


def _wait(app, pid: int, seconds: float, sleep) -> tuple[str, str | None]:
    """Stay until the app's watcher has judged the server: it checks a keyed one's gate."""
    deadline = time.monotonic() + seconds
    while True:
        # The pidfile first: the watcher forgets the pid before it removes the file.
        owner = _pidfile_owner(app)
        if app.STATE.server_pid != pid:
            return "failed", app.STATE.server_last_error or "The server stopped while starting."
        if owner != pid:
            # Another process, the app, stopped or replaced it: leave that alone.
            app.STATE.server_stopping = True
            return "stopped", "The server was stopped or replaced while it was starting."
        if app.STATE.server_verdict == "ready":
            return "started", None
        if time.monotonic() >= deadline:
            app.stop_server()
            return "failed", "The server did not answer in time, so it was stopped."
        sleep(POLL_SECONDS)


def _start(app, record: dict, seconds: float, sleep) -> tuple[str, str | None]:
    window = "skipped", "MLX Studio is open; start the server there."
    if app.instance_is_running():
        return window
    app.restore_server_state()
    if app.STATE.server_pid:
        return "skipped", "An API server is already running."
    repo = record["repo"]
    if why := _engine_problem(app, record["engine"], repo):
        return "skipped", why
    engine = app.engines.get(record["engine"])
    if why := (
        _password_problem(engine)
        or _memory_problem(app, repo)
        or app.engine_runtime.port_problem(engine.port)
    ):
        return "skipped", why
    try:
        _prepare_key(app, engine.id)
    except app.engine_runtime.Refused as exc:
        return "skipped", str(exc)
    if app.instance_is_running():  # opened during the checks: the window decides
        return window
    _report(app, record, "starting", None)
    with app.STATE.server_lock:  # the launch asks ``client_key`` for the stored key
        proc = app._launch_server(repo, True, None, engine.id, record["anthropic"])
    if proc is None:
        why = app.STATE.server_last_error or app.STATE.server_down_notice
        return "failed", why or "The server could not be started."
    if _pidfile_owner(app) != proc.pid:
        app.stop_server()  # the app could neither find nor stop it later
        return "failed", "The server could not be recorded, so it was stopped."
    return _wait(app, proc.pid, seconds, sleep)


def login_start(app, wait_seconds: float | None = None, sleep=time.sleep) -> int:
    """What the LaunchAgent runs. One try, recorded; always exit status 0."""
    # Longer than the watcher's own limit, so its reason is the one reported.
    seconds = app.START_WATCH_SECONDS + 30 if wait_seconds is None else wait_seconds
    with open(app.APP_DIR / "login-start.lock", "a", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return 0  # another login start is at work
        record = read_default(app)
        if record is None and os.path.lexists(record_file(app)):
            _report(app, None, "skipped", "The default model record cannot be read.")
        elif record is None or not record["autostart"]:
            # A leftover item. No bootout: that would end this very process.
            with contextlib.suppress(OSError):
                plist_path().unlink(missing_ok=True)
        else:
            try:
                outcome, reason = _start(app, record, seconds, sleep)
            except Exception as exc:  # recorded, never retried
                outcome, reason = "failed", str(exc)[:300]
            _report(app, record, outcome, reason)
    return 0
