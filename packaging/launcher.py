#!/usr/bin/env python3
"""
MLX Studio launcher for the packaged .app bundle.

The bundle ships the Python sources; this stub finds a usable interpreter,
starts the app, and keeps a log so failures are diagnosable from Finder.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

BUNDLE = Path(__file__).resolve().parent.parent          # …/Contents
RESOURCES = BUNDLE / "Resources"
APP_PY = RESOURCES / "app.py"
LOG = Path.home() / ".mlx_studio" / "launch.log"


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
                )
                if probe.returncode == 0:
                    return path
            except Exception:  # noqa: BLE001
                continue
    return None


def main() -> int:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    python = find_python()
    if not python:
        LOG.write_text("No Python 3.10+ interpreter found.\n", encoding="utf-8")
        return 1

    if not APP_PY.exists():
        LOG.write_text(f"Missing app.py at {APP_PY}\n", encoding="utf-8")
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
    return proc.wait()


if __name__ == "__main__":
    sys.exit(main())
