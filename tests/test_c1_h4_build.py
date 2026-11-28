"""C1: the launcher's minimum macOS matches the plist. H4: a fresh clone builds.

Builds in a temporary copy of the sources; the project's dist/ is never written.
"""

from __future__ import annotations

import importlib.util
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
TOOLS = ("clang", "otool", "pkgbuild", "pkgutil", "hdiutil", "codesign")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        sys.platform != "darwin" or not all(shutil.which(t) for t in TOOLS),
        reason="needs the macOS build tools",
    ),
]


def clone(target: Path) -> Path:
    """Copy only what a fresh checkout would contain (no ignored binary)."""
    (target / "packaging").mkdir(parents=True)
    (target / "ui").mkdir()
    for name in (
        "app.py",
        "model_info.py",
        "mlx_loadtest.py",
        "optiq_engine.py",
        "engine_runtime.py",
        "engines.py",
        "login_agent.py",
        "chat_store.py",
        "projects.py",
        "hub_catalog.py",
        "live_stats.py",
        "tool_null_text.py",
        "loadtest_compare.py",
        "engine_plan.py",
        "update_check.py",
        "catalog_routes.py",
        "project_routes.py",
        "feature_routes.py",
        "packaging/optiq-lock.txt",
    ):
        shutil.copy(ROOT / name, target / name)
    for name in (
        "index.html",
        "style.css",
        "app.js",
        "download.js",
        "download.css",
        "projects.js",
        "projects.css",
        "features.js",
        "features.css",
    ):
        shutil.copy(ROOT / "ui" / name, target / "ui" / name)
    for name in ("build_app.sh", "launcher.c", "release_manifest.py", "AppIcon.icns"):
        shutil.copy(ROOT / "packaging" / name, target / "packaging" / name)
    return target


def build(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", str(root / "packaging" / "build_app.sh"), *args],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )


def minos(binary: Path) -> str:
    out = subprocess.run(
        ["otool", "-l", str(binary)], capture_output=True, text=True, check=True
    ).stdout
    return next(line.split()[1] for line in out.splitlines() if line.split()[:1] == ["minos"])


def test_fresh_checkout_builds_with_the_promised_minimum(tmp_path):
    root = clone(tmp_path / "clone")
    done = build(root, "--app-only")
    assert done.returncode == 0, done.stdout + done.stderr
    contents = root / "dist" / "MLX Studio.app" / "Contents"
    info = plistlib.loads((contents / "Info.plist").read_bytes())
    assert info["LSMinimumSystemVersion"] == "12.0"
    assert minos(contents / "MacOS" / "MLXStudio") == "12.0"
    assert not (contents / "Resources" / "launcher.py").exists()
    # app.py imports this file; without it the app does not start.
    bundled = contents / "Resources" / "model_info.py"
    assert bundled.read_bytes() == (root / "model_info.py").read_bytes()


def test_build_fails_when_minimum_versions_disagree(tmp_path):
    root = clone(tmp_path / "clone")
    script = root / "packaging" / "build_app.sh"
    text = script.read_text(encoding="utf-8")
    flag = '-mmacosx-version-min="$MIN_MACOS"'
    assert flag in text
    script.write_text(text.replace(flag, "-mmacosx-version-min=13.0"), encoding="utf-8")
    done = build(root, "--app-only", "--no-sign")
    assert done.returncode != 0
    assert "minos 13.0" in done.stdout + done.stderr


def test_full_build_records_checksums_and_passes_inspection(tmp_path):
    root = clone(tmp_path / "clone")
    done = build(root)
    assert done.returncode == 0, done.stdout + done.stderr
    dist = root / "dist"
    check = subprocess.run(
        ["shasum", "-a", "256", "-c", "SHA256SUMS"],
        cwd=dist,
        capture_output=True,
        text=True,
        check=False,
    )
    assert check.returncode == 0, check.stdout + check.stderr
    assert len((dist / "SHA256SUMS").read_text(encoding="utf-8").splitlines()) == 2

    spec = importlib.util.spec_from_file_location(
        "relman_build", root / "packaging" / "release_manifest.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main(["--inspect", "--dist", str(dist)]) == 0

    # A source edit after the build makes the package stale.
    with open(root / "ui" / "app.js", "a", encoding="utf-8") as fh:
        fh.write("// later edit\n")
    assert module.main(["--inspect", "--dist", str(dist)]) == 1
