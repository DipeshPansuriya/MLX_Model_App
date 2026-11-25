"""C3: the manifest comes from the exact built artifacts and any mismatch fails.
H5: tag commit, APP_VERSION and manifest must agree.

Runs a copy of release_manifest.py inside a throwaway project tree.
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import subprocess
import uuid
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "packaging" / "release_manifest.py"
VERSION = "3.1.0"
PKG = f"MLX.Studio-{VERSION}.pkg"
DMG = f"MLX.Studio-{VERSION}.dmg"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class Project:
    def __init__(self, base: Path, monkeypatch):
        for key in [k for k in os.environ if k.startswith("GIT_")]:
            monkeypatch.delenv(key)
        monkeypatch.setenv("HOME", str(base))
        monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
        monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
        for who in ("AUTHOR", "COMMITTER"):
            monkeypatch.setenv(f"GIT_{who}_NAME", "t")
            monkeypatch.setenv(f"GIT_{who}_EMAIL", "t@invalid")

        self.root = base / "project"
        for folder in ("packaging", "release", "ui", "dist"):
            (self.root / folder).mkdir(parents=True)
        shutil.copy(SCRIPT, self.root / "packaging" / "release_manifest.py")
        (self.root / ".gitignore").write_text("dist/\n", encoding="utf-8")
        (self.root / "ui" / "app.js").write_text("// ui\n", encoding="utf-8")
        self.set_version(VERSION)
        self.git("init", "-q", "-b", "main")
        self.commit("start")

        spec = importlib.util.spec_from_file_location(
            f"relman_{uuid.uuid4().hex}", self.root / "packaging" / "release_manifest.py"
        )
        self.mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.mod)
        # The payload check needs a real flat package; it has its own tests.
        self.payload_problems: list[str] = []
        monkeypatch.setattr(self.mod, "check_payload", lambda pkg: list(self.payload_problems))

    def git(self, *args: str) -> None:
        subprocess.run(["git", *args], cwd=self.root, check=True, capture_output=True)

    def commit(self, message: str) -> None:
        self.git("add", "-A")
        self.git("commit", "-q", "--allow-empty", "-m", message)

    def set_version(self, version: str) -> None:
        (self.root / "app.py").write_text(f'APP_VERSION = "{version}"\n', encoding="utf-8")

    def build(self, pkg: bytes = b"pkg-bytes", dmg: bytes = b"dmg-bytes") -> None:
        (self.root / "dist" / PKG).write_bytes(pkg)
        (self.root / "dist" / DMG).write_bytes(dmg)
        (self.root / "dist" / "SHA256SUMS").write_text(
            f"{sha(dmg)}  {DMG}\n{sha(pkg)}  {PKG}\n", encoding="utf-8"
        )

    def run(self, *args: str) -> int:
        argv = [a if a != "DIST" else str(self.root / "dist") for a in args]
        return self.mod.main(argv)

    def generate(self) -> int:
        return self.run("--dist", "DIST", "--notes", "n")

    @property
    def manifest(self) -> dict:
        return json.loads((self.root / "release" / "latest.json").read_text(encoding="utf-8"))

    def edit_manifest(self, **fields) -> None:
        data = {**self.manifest, **fields}
        (self.root / "release" / "latest.json").write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def project(tmp_path, monkeypatch):
    return Project(tmp_path, monkeypatch)


@pytest.fixture
def released(project):
    project.build()
    assert project.generate() == 0
    return project


class TestGenerate:
    def test_manifest_and_sums_describe_the_built_files(self, released):
        manifest = released.manifest
        assert manifest["version"] == VERSION
        assert manifest["sha256"] == sha(b"pkg-bytes")
        assert manifest["size"] == len(b"pkg-bytes")
        assert manifest["url"].endswith(f"/releases/download/v{VERSION}/{PKG}")
        sums = (released.root / "release" / "SHA256SUMS").read_text(encoding="utf-8")
        assert f"{sha(b'pkg-bytes')}  {PKG}\n" in sums
        assert f"{sha(b'dmg-bytes')}  {DMG}\n" in sums

    def test_keeps_checksums_of_earlier_versions(self, project):
        earlier = f"{'b' * 64}  MLX.Studio-3.0.0.dmg\n"
        (project.root / "release" / "SHA256SUMS").write_text(earlier, encoding="utf-8")
        project.build()
        assert project.generate() == 0
        sums = (project.root / "release" / "SHA256SUMS").read_text(encoding="utf-8")
        assert sums.startswith(earlier)
        assert len(sums.splitlines()) == 3

    def test_regenerating_replaces_this_version_only_once(self, released):
        released.build(pkg=b"second", dmg=b"third")
        assert released.run("--dist", "DIST", "--notes", "n", "--replace") == 0
        sums = (released.root / "release" / "SHA256SUMS").read_text(encoding="utf-8")
        assert len(sums.splitlines()) == 2
        assert released.manifest["sha256"] == sha(b"second")

    def test_refuses_artifacts_that_changed_after_the_build(self, project):
        project.build()
        (project.root / "dist" / PKG).write_bytes(b"rebuilt behind our back")
        assert project.generate() == 1
        assert not (project.root / "release" / "latest.json").exists()

    @pytest.mark.parametrize("missing", [PKG, DMG, "SHA256SUMS"])
    def test_refuses_an_incomplete_build(self, project, missing):
        project.build()
        (project.root / "dist" / missing).unlink()
        assert project.generate() == 1
        assert not (project.root / "release" / "latest.json").exists()

    def test_refuses_a_stale_package(self, project):
        project.build()
        project.payload_problems = ["app.py in the pkg differs from the source"]
        assert project.generate() == 1
        assert not (project.root / "release" / "latest.json").exists()

    def test_refuses_a_tag_that_belongs_to_another_version(self, project):
        project.set_version("3.0.9")
        project.commit("older")
        project.git("tag", f"v{VERSION}")
        project.set_version(VERSION)
        project.commit("bump")
        project.build()
        assert project.generate() == 1

    def test_refuses_when_the_tagged_source_differs(self, project):
        project.git("tag", f"v{VERSION}")
        (project.root / "ui" / "app.js").write_text("// changed\n", encoding="utf-8")
        project.build()
        assert project.generate() == 1

    def test_accepts_a_tag_on_the_built_source(self, project):
        project.git("tag", "-a", f"v{VERSION}", "-m", "release")
        project.build()
        assert project.generate() == 0


class TestCheck:
    def test_passes_after_generation(self, released):
        assert released.run("--check") == 0

    def test_passes_while_the_next_release_is_pending(self, released):
        released.set_version("3.2.0")
        assert released.run("--check") == 0

    def test_fails_when_manifest_is_ahead_of_the_source(self, released):
        released.set_version("3.0.0")
        assert released.run("--check") == 1

    @pytest.mark.parametrize(
        "fields",
        [
            {"url": "https://example.invalid/MLX.Studio-3.1.0.pkg"},
            {"sha256": "c" * 64},
            {"sha256": "short"},
            {"size": 0},
            {"version": "next"},
        ],
    )
    def test_fails_on_an_inconsistent_manifest(self, released, fields):
        released.edit_manifest(**fields)
        assert released.run("--check") == 1

    def test_accepts_a_legacy_manifest_without_sums(self, released):
        (released.root / "release" / "SHA256SUMS").unlink()
        assert released.run("--check") == 0

    def test_reports_the_tag_clash(self, released, capsys):
        released.set_version("3.0.0")
        released.commit("old source")
        released.git("tag", f"v{VERSION}")
        released.set_version(VERSION)
        assert released.run("--check") == 1
        assert f"tag v{VERSION}" in capsys.readouterr().err

    def test_pushed_tag_must_equal_app_version(self, released):
        assert released.run("--check", "--tag", f"v{VERSION}") == 0
        assert released.run("--check", "--tag", "v3.1.1") == 1


class TestVerify:
    def test_passes_on_the_exact_artifacts(self, released):
        assert released.run("--verify", "--release", "--dist", "DIST") == 0

    @pytest.mark.parametrize("name", [PKG, DMG])
    def test_fails_when_an_artifact_differs(self, released, name):
        (released.root / "dist" / name).write_bytes(b"different")
        assert released.run("--verify", "--dist", "DIST") == 1

    def test_published_download_needs_no_build_record(self, released):
        (released.root / "dist" / "SHA256SUMS").unlink()
        released.set_version("3.2.0")
        assert released.run("--verify", "--dist", "DIST") == 0
        assert released.run("--verify", "--release", "--dist", "DIST") == 1

    def test_requires_the_sums_file(self, released):
        (released.root / "release" / "SHA256SUMS").unlink()
        assert released.run("--verify", "--dist", "DIST") == 1

    def test_stale_changelog_checksum_fails(self, released):
        log = released.root / "CHANGELOG.md"
        log.write_text(f"## [{VERSION}]\npkg  sha256  {'d' * 64}\n", encoding="utf-8")
        assert released.run("--verify", "--dist", "DIST") == 1

    def test_stale_changelog_size_fails(self, released):
        log = released.root / "CHANGELOG.md"
        log.write_text(f"## [{VERSION}]\npkg  size    1 bytes\n", encoding="utf-8")
        assert released.run("--verify", "--dist", "DIST") == 1

    def test_current_changelog_passes_and_old_sections_are_ignored(self, released):
        log = released.root / "CHANGELOG.md"
        log.write_text(
            f"## [{VERSION}]\npkg  sha256  {sha(b'pkg-bytes')}\npkg  size    9 bytes\n"
            f"\n## [3.0.0]\npkg  sha256  {'d' * 64}\n",
            encoding="utf-8",
        )
        assert released.run("--verify", "--dist", "DIST") == 0


class TestInspect:
    def test_checks_build_record_and_payload(self, project):
        project.build()
        assert project.run("--inspect", "--dist", "DIST") == 0
        project.payload_problems = ["stale"]
        assert project.run("--inspect", "--dist", "DIST") == 1
