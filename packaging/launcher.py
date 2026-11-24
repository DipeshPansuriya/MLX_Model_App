#!/usr/bin/env python3
"""
MLX Studio launcher for the packaged .app bundle.

The bundle ships the Python sources; this stub finds a usable interpreter,
starts the app, and keeps a log so failures are diagnosable from Finder.
"""

from __future__ import annotations

import contextlib
import os
import subprocess
import sys
from datetime import datetime
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent  # …/Contents
RESOURCES = BUNDLE / "Resources"
APP_PY = RESOURCES / "app.py"
LOG_DIR = Path.home() / ".mlx_studio" / "logs"
LOG = LOG_DIR / "launch.log"


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


def main() -> int:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    note(f"launcher start · bundle={BUNDLE} · python={sys.version.split()[0]}")
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
