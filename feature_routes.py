"""Routes and app glue for live speed, install-or-update-all and the release check.

``app`` is the running app module, handed in by the caller, so nothing here imports
it. The engine comparison and the tool "null" check need no route.
"""

from __future__ import annotations

import sys
import threading
import time
from datetime import datetime

import engine_plan
import live_stats
import mlx_loadtest
import update_check

GET_ROUTES = ("/api/install/plan",)
POST_ROUTES = ("/api/install/all", "/api/update/check")
INSTALL_KIND = "all"
PACKAGES = "mlx + mlx-lm"  # the activity log's name for the pinned packages
SERVER_BUSY = "Stop the API server first."
INSTALL_BUSY = "Another install is running. Wait for it to finish, then try again."
UP_TO_DATE = "Everything is already up to date."
# How long a saved load test's "older settings" mark may lag behind a settings change.
RUNS_CACHE_SECONDS = 30


def handle_get(app, path: str) -> tuple[int, dict] | None:
    """``(status, answer)`` for a path of ``GET_ROUTES``, else None."""
    if path == "/api/install/plan":
        return 200, install_plan(app)
    return None


def handle_post(app, path: str, body: dict) -> tuple[int, dict] | None:
    """``(status, answer)`` for a path of ``POST_ROUTES``, else None.

    Neither route takes anything from ``body``: what is installed comes from the
    app's pins, and what is asked about is the app's own version.
    """
    if path == "/api/install/all":
        return start_install_all(app)
    if path == "/api/update/check":
        return 200, update_check.check(app.APP_VERSION)
    return None


# -- install or update all -------------------------------------------------- #
def _busy(app) -> str | None:
    state = app.STATE
    if state.server_pending or state.server_proc is not None or app._pid_alive(state.server_pid):
        return SERVER_BUSY
    return app.LOADTEST_BUSY if state.testing() else None


def install_plan(app) -> dict:
    return engine_plan.build_plan(
        app.PINNED_PACKAGES,
        app.pkg_version,
        app.is_newer,
        [engine.view(app.APP_DIR) for engine in app.engines.REGISTRY],
        busy=_busy(app),
    )


def pip_install(app, kind: str, lines: list[str]) -> bool:
    """Install the pinned packages with pip. The caller holds and frees the slot."""
    app.log_activity("install", PACKAGES, "running", f"{kind} started")
    mode = "--force-reinstall" if kind == "reinstall" else "--upgrade"
    cmd = [sys.executable, "-m", "pip", "install", mode, *app.install_specs()]
    lines.append("$ " + " ".join(cmd))
    ok = False
    try:
        proc = app.subprocess.Popen(
            cmd,
            stdout=app.subprocess.PIPE,
            stderr=app.subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        if proc.stdout is None:  # pragma: no cover - PIPE guarantees a stream
            lines.append("error: no stdout stream")
            proc.kill()
        else:
            for line in proc.stdout:
                lines.append(line.rstrip())
            ok = proc.wait() == 0
        if not ok and any("externally-managed-environment" in x for x in lines):
            lines.append(
                "This Python is managed by the system and refuses pip. Create a virtual "
                "environment (python3 -m venv ~/mlx-venv) and run MLX Studio with it."
            )
    except Exception as exc:
        lines.append(f"error: {exc}")
    app.log_activity("install", PACKAGES, "success" if ok else "failed", f"{kind} finished")
    app.log_app(
        "info" if ok else "error",
        f"package {kind} {'succeeded' if ok else 'failed'}",
        tail=lines[-5:],
    )
    return ok


def engine_install(app, engine, lines: list[str]) -> bool:
    """Install one engine from the app's lock. The caller holds and frees the slot."""
    app.log_activity("install", engine.name, "running", f"{engine.version} started")
    ok = False
    try:
        engine.install(app.APP_DIR, lines.append)
        lines.append(f"{engine.name} {engine.version} is installed.")
        ok = True
    except app.engine_runtime.Refused as exc:
        lines.append(str(exc))
    except Exception as exc:
        lines.append(f"error: {exc}")
    app.log_activity("install", engine.name, "success" if ok else "failed", "install finished")
    app.log_app("info" if ok else "error", "engine install finished", engine=engine.id, ok=ok)
    return ok


def start_install_all(app) -> tuple[int, dict]:
    with app.STATE.server_lock:  # no start is in between
        plan = install_plan(app)
        if plan["blocked"]:
            return 409, {"error": plan["blocked"]}
        if not plan["needed"]:
            return 409, {"error": UP_TO_DATE}
        if not app.STATE.reserve_install(INSTALL_KIND):
            return 409, {"error": app.LOADTEST_BUSY if app.STATE.testing() else INSTALL_BUSY}
        # A start may have queued before the slot was taken; later ones are refused.
        if busy := _busy(app):
            app.STATE.install["running"] = False
            return 409, {"error": busy}
    threading.Thread(target=worker_install_all, args=(app,), daemon=True).start()
    return 200, {"ok": True}


def worker_install_all(app) -> None:
    """Run the whole plan under one reservation, then ask for the latest versions."""
    lines = app.STATE.install["lines"]

    def engine_step(name: str) -> bool:
        engine = app.engines.get(name)
        return engine is not None and engine_install(app, engine, lines)

    try:
        _, message = engine_plan.run_plan(
            lambda: install_plan(app),
            lambda names: pip_install(app, INSTALL_KIND, lines),
            engine_step,
            lines.append,
        )
        lines.append(message)
    finally:
        app.STATE.install["running"] = False
    app.worker_check_versions()


# -- live memory and speed -------------------------------------------------- #
def record_chat_speed(app, done, engine: str, pid) -> None:
    """Remember a chat reply's measured speed; an unmeasured reply changes nothing."""
    at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    if found := live_stats.chat_speed(done, engine, pid, at):
        app.STATE.last_chat_speed = found


def _saved_runs(app, repo: str, engine: str) -> list[dict]:
    """This model's saved runs on ``engine``, marked stale; kept between state polls."""
    try:
        try:
            info = app._loadtest_file().stat()
            stamp = (info.st_mtime_ns, info.st_size)
        except FileNotFoundError:
            stamp = None
        key = (stamp, repo, engine, int(time.monotonic() // RUNS_CACHE_SECONDS))
        kept = getattr(app.STATE, "speed_runs", None)
        if kept and kept[0] == key:
            return kept[1]
        now = app._loadtest_fingerprint(repo, engine)
        runs = [
            {**run, "stale": run.get("fingerprint") != now}
            for run in app._stored_runs(repo)
            if run.get("engine", "mlx") == engine
        ]
        app.STATE.speed_runs = (key, runs)
        return runs
    except Exception:  # a speed figure must never break GET /api/state
        return []


def server_stats(app, probe=mlx_loadtest.mac_probe) -> dict:
    """``memory`` and ``speed`` for the snapshot's server. Call it without the state lock."""
    state = app.STATE
    pid, repo, engine = state.server_pid, state.server_repo, state.server_engine
    if not (state.server_proc is not None or app._pid_alive(pid)):
        pid = repo = engine = None
    runs = _saved_runs(app, repo, engine) if repo else []
    return {
        "memory": live_stats.memory(probe, pid, app.hardware().get("ram_bytes")),
        "speed": live_stats.speed(getattr(state, "last_chat_speed", None), runs, repo, engine, pid),
    }
