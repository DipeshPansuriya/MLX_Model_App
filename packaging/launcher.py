#!/usr/bin/env python3
"""
MLX Studio launcher for the packaged .app bundle.

The bundle ships the Python sources; this stub finds a usable interpreter,
starts the app, and keeps a log so failures are diagnosable from Finder.
"""

from __future__ import annotations

import contextlib
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent  # …/Contents
RESOURCES = BUNDLE / "Resources"
APP_PY = RESOURCES / "app.py"
LOG_DIR = Path.home() / ".mlx_studio" / "logs"
LOG = LOG_DIR / "launch.log"

HOST = "127.0.0.1"
PORT = 8765


def note(message: str) -> None:
    """Append a timestamped line so a failed launch is diagnosable from Finder."""
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as fh:
            fh.write(f"{datetime.now():%Y-%m-%d %H:%M:%S} {message}\n")
        with contextlib.suppress(OSError):
            LOG.chmod(0o600)
    except OSError:
        pass


def find_python() -> str | None:
    candidates = [
        "/Library/Frameworks/Python.framework/Versions/Current/bin/python3",
        "/opt/homebrew/bin/python3",
        "/usr/local/bin/python3",
        "/usr/bin/python3",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                probe = subprocess.run(
                    [path, "-c", "import sys; assert sys.version_info >= (3, 10)"],
                    capture_output=True,
                    timeout=20,
                    check=False,
                )
                if probe.returncode == 0:
                    return path
            except Exception as exc:
                note(f"interpreter probe failed for {path}: {exc}")
                continue
    return None


def already_running() -> bool:
    """True when an MLX Studio already answers on the app port."""
    try:
        with urllib.request.urlopen(f"http://{HOST}:{PORT}/api/ping", timeout=2) as resp:
            return bool(json.loads(resp.read().decode()).get("ok"))
    except Exception:
        return False


def ask_focus() -> bool:
    """Ask the running instance to raise its window instead of starting a rival."""
    try:
        req = urllib.request.Request(
            f"http://{HOST}:{PORT}/api/focus",
            data=b"{}",
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=3) as resp:
            return bool(json.loads(resp.read().decode()).get("ok"))
    except Exception:
        return False


def raise_by_applescript() -> bool:
    """Fallback for an older instance that has no /api/focus endpoint.

    A 2.0.2 server answers /api/ping but 404s /api/focus, so the request above
    returns False. Without this fallback the launcher exited 0 and the user saw
    nothing happen at all.
    """
    script = 'tell application "System Events" to tell process "Python" to set frontmost to true'
    try:
        proc = subprocess.run(
            ["osascript", "-e", script],
            capture_output=True,
            timeout=8,
            check=False,
        )
        return proc.returncode == 0
    except Exception:
        return False


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    note(f"launcher start · bundle={BUNDLE} · python={sys.version.split()[0]}")

    # A second double-click must raise the window that already exists. Without
    # this the new process dies on "Address already in use" and the user sees
    # nothing happen.
    if already_running():
        focused = ask_focus()
        if not focused:
            # Older instance without /api/focus: raise it the AppKit way.
            focused = raise_by_applescript()
            note(f"focus endpoint unavailable; applescript fallback ok={focused}")
        note(f"already running; focus requested (ok={focused})")
        if not focused:
            note("could not raise the running window; leaving it as is")
        return 0

    python = find_python()
    if not python:
        note("no Python 3.10+ interpreter found; aborting")
        return 1
    note(f"interpreter={python}")

    if not APP_PY.exists():
        note(f"missing app.py at {APP_PY}; aborting")
        return 1

    with open(LOG, "a", encoding="utf-8") as fh:
        fh.write(f"\n=== launch {python} {APP_PY} ===\n")
        fh.flush()
        proc = subprocess.Popen(
            [python, str(APP_PY)],
            cwd=str(RESOURCES),
            stdout=fh,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    code = proc.wait()
    note(f"app exited with status {code}")
    return code


if __name__ == "__main__":
    sys.exit(main())
