"""Packaging: build script contents, installer safety and pkg structure."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "packaging" / "build_app.sh"
INSTALLER = ROOT / "scripts" / "install.sh"
LAUNCHER = ROOT / "packaging" / "launcher.py"


class TestBuildScript:
    def test_parses(self):
        assert subprocess.run(["bash", "-n", str(BUILD)], check=False).returncode == 0

    def test_reads_version_from_app_py(self):
        text = BUILD.read_text(encoding="utf-8")
        assert "APP_VERSION" in text
        assert "sed -E" in text

    def test_builds_a_pkg(self):
        text = BUILD.read_text(encoding="utf-8")
        assert "pkgbuild" in text
        assert "MLX.Studio-$VERSION.pkg" in text

    def test_pkg_installs_to_applications(self):
        text = BUILD.read_text(encoding="utf-8")
        assert "--install-location /" in text
        assert ".pkg-root" in text
        assert 'mkdir -p "$PKG_ROOT/Applications"' in text

    def test_pkg_uses_the_bundle_identifier(self):
        text = BUILD.read_text(encoding="utf-8")
        assert '--identifier "$BUNDLE_ID"' in text

    def test_still_builds_a_dmg(self):
        text = BUILD.read_text(encoding="utf-8")
        assert "hdiutil create" in text

    def test_signs_ad_hoc_by_default(self):
        text = BUILD.read_text(encoding="utf-8")
        assert "codesign --force --deep --sign -" in text

    def test_prints_both_checksums(self):
        text = BUILD.read_text(encoding="utf-8")
        assert text.count("shasum -a 256") >= 2

    def test_read_me_has_six_numbered_steps(self):
        text = BUILD.read_text(encoding="utf-8")
        steps = re.findall(r"^Step \d+\.", text, re.MULTILINE)
        assert len(steps) == 6

    def test_read_me_leads_with_quarantine(self):
        text = BUILD.read_text(encoding="utf-8")
        step_one = text.split("Step 1.")[1].split("Step 2.")[0]
        assert "xattr -dr com.apple.quarantine" in step_one


class TestInstaller:
    def test_parses(self):
        assert subprocess.run(["bash", "-n", str(INSTALLER)], check=False).returncode == 0

    def test_verifies_checksum(self):
        assert "shasum -a 256" in INSTALLER.read_text(encoding="utf-8")

    def test_verifies_signature(self):
        assert "codesign --verify" in INSTALLER.read_text(encoding="utf-8")

    def test_clears_quarantine(self):
        assert "xattr -dr com.apple.quarantine" in INSTALLER.read_text(encoding="utf-8")

    def test_never_pipes_a_download_into_a_shell(self):
        text = INSTALLER.read_text(encoding="utf-8")
        # Drop the usage() heredoc: it documents the bootstrap one-liner, which is
        # not executable code. Mirrors the SA-16 check in security_audit.sh.
        lines, in_usage = [], False
        for line in text.splitlines():
            if line.startswith("usage()"):
                in_usage = True
                continue
            if in_usage and line.strip() == "USAGE":
                in_usage = False
                continue
            if not in_usage and not line.strip().startswith("#"):
                lines.append(line)
        code = "\n".join(lines)
        assert not re.search(r"curl[^|]*\|\s*(ba)?sh", code)


class TestLauncher:
    def test_compiles(self):
        assert (
            subprocess.run(["python3", "-m", "py_compile", str(LAUNCHER)], check=False).returncode
            == 0
        )

    def test_checks_ping_before_spawning(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        assert "already_running()" in text
        assert "ask_focus()" in text

    def test_has_an_applescript_fallback(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        assert "raise_by_applescript" in text
        assert "osascript" in text

    def test_fallback_runs_when_focus_fails(self):
        text = LAUNCHER.read_text(encoding="utf-8")
        assert "if not focused:" in text


@pytest.mark.integration
class TestPkgBuild:
    def test_pkgbuild_produces_a_package(self, tmp_path):
        """Build a real .pkg from a stub bundle and inspect it."""
        if subprocess.run(["which", "pkgbuild"], check=False, capture_output=True).returncode != 0:
            pytest.skip("pkgbuild not available")

        app = tmp_path / "root" / "Applications" / "Stub.app" / "Contents"
        app.mkdir(parents=True)
        (app / "Info.plist").write_text(
            '<?xml version="1.0" encoding="UTF-8"?>'
            '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
            '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">'
            '<plist version="1.0"><dict>'
            "<key>CFBundleIdentifier</key><string>com.mlxstudio.stub</string>"
            "<key>CFBundleVersion</key><string>1.0</string>"
            "<key>CFBundleShortVersionString</key><string>1.0</string>"
            "<key>CFBundleExecutable</key><string>Stub</string>"
            "</dict></plist>",
            encoding="utf-8",
        )
        (app / "MacOS").mkdir()
        (app / "MacOS" / "Stub").write_text("#!/bin/sh\n", encoding="utf-8")
        (app / "MacOS" / "Stub").chmod(0o755)

        pkg = tmp_path / "Stub.pkg"
        result = subprocess.run(
            [
                "pkgbuild",
                "--root",
                str(tmp_path / "root"),
                "--identifier",
                "com.mlxstudio.stub",
                "--version",
                "1.0",
                "--install-location",
                "/",
                str(pkg),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert pkg.exists() and pkg.stat().st_size > 0

        listed = subprocess.run(
            ["pkgutil", "--payload-files", str(pkg)],
            capture_output=True,
            text=True,
            check=False,
        )
        assert "Stub.app" in listed.stdout
