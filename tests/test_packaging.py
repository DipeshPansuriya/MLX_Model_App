"""Packaging: what build_app.sh really produces and what install.sh really does.

The project is built once in a temporary copy, then the app, dmg and pkg are
inspected and the dmg is installed into a temporary folder. The project's dist/
and /Applications are never written, the app is never launched and no Finder
window opens.
"""

from __future__ import annotations

import hashlib
import os
import plistlib
import re
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_c1_h4_build import TOOLS, build, clone

ROOT = Path(__file__).resolve().parent.parent
BUILD = ROOT / "packaging" / "build_app.sh"
INSTALLER = ROOT / "scripts" / "install.sh"

APP = "MLX Studio.app"
VERSION = "7.8.9"  # never the real one, so a hard-coded version is caught
DMG = f"MLX.Studio-{VERSION}.dmg"
PKG = f"MLX.Studio-{VERSION}.pkg"
QUARANTINE = "com.apple.quarantine"
FLAG = "0081;00000000;Safari;"
LATEST = "https://api.github.com/repos/DipeshPansuriya/MLX_Model_App/releases/latest"
DOWNLOAD = f"https://github.com/DipeshPansuriya/MLX_Model_App/releases/download/v{VERSION}/{DMG}"
SUMS = "https://raw.githubusercontent.com/DipeshPansuriya/MLX_Model_App/main/release/SHA256SUMS"

needs_macos = pytest.mark.skipif(
    sys.platform != "darwin" or not all(shutil.which(t) for t in (*TOOLS, "xattr", "diskutil")),
    reason="needs the macOS build tools",
)


def run(*command, check=True, **kwargs) -> subprocess.CompletedProcess:
    return subprocess.run(
        [str(c) for c in command],
        check=check,
        capture_output=True,
        text=True,
        timeout=300,
        **kwargs,
    )


def files(folder: Path) -> dict[str, bytes]:
    return {str(p.relative_to(folder)): p.read_bytes() for p in folder.rglob("*") if p.is_file()}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def quarantined(path: Path) -> list[str]:
    return [str(p) for p in (path, *path.rglob("*")) if QUARANTINE in run("xattr", p).stdout]


def set_version_line(root: Path, line: str) -> None:
    """Replace the APP_VERSION line of a temporary copy with `line`."""
    source = root / "app.py"
    text, count = re.subn(
        r"^APP_VERSION = .*$",
        lambda _: line,
        source.read_text(encoding="utf-8"),
        flags=re.MULTILINE,
    )
    assert count == 1
    source.write_text(text, encoding="utf-8")


@pytest.fixture(scope="module", autouse=True)
def temporary_home(tmp_path_factory):
    """No script or tool started here sees the developer's real HOME."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setenv("HOME", str(tmp_path_factory.mktemp("home")))
        yield


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    """One full build of a temporary copy whose app.py carries VERSION."""
    root = clone(tmp_path_factory.mktemp("build") / "clone")
    set_version_line(root, f'APP_VERSION = "{VERSION}"')
    done = build(root)
    assert done.returncode == 0, done.stdout + done.stderr
    dist = root / "dist"
    return SimpleNamespace(
        dist=dist, app=dist / APP, dmg=dist / DMG, pkg=dist / PKG, out=done.stdout
    )


@pytest.fixture(scope="module")
def volume(built, tmp_path_factory):
    """The built dmg, mounted read-only without showing it in Finder."""
    mount = tmp_path_factory.mktemp("volume")
    run(
        "hdiutil", "attach", "-nobrowse", "-noautoopen", "-readonly",
        "-mountpoint", mount, built.dmg,
    )  # fmt: skip
    try:
        yield mount
    finally:
        run("hdiutil", "detach", mount, "-force", check=False)


@pytest.fixture(scope="module")
def package(built, tmp_path_factory):
    """The built pkg, unpacked (not installed)."""
    target = tmp_path_factory.mktemp("pkg") / "expanded"
    run("pkgutil", "--expand-full", built.pkg, target)
    info = ET.parse(target / "PackageInfo").getroot()  # noqa: S314 - written by pkgbuild just now
    return SimpleNamespace(info=info, payload=target / "Payload")


class TestBuildScript:
    def test_parses(self):
        assert subprocess.run(["bash", "-n", str(BUILD)], check=False).returncode == 0

    # The version is checked before anything is built, so these need no macOS tool.
    @pytest.mark.parametrize(
        "line",
        [
            "APP_VERSION = '3.0.0'",
            "",
            'APP_VERSION = "3.0"',
            'APP_VERSION = "3.0.0-rc1"',
            'APP_VERSION = ""',
            'APP_VERSION = "3.0.0" + SUFFIX',
            'APP_VERSION = "3.0.0"\nAPP_VERSION = "3.0.1"',
            'APP_VERSION = "3.0.0"\nAPP_VERSION = os.environ["V"]',
            'APP_VERSION = "3.0.0"  # old\nAPP_VERSION = "3.0.1"',
        ],
        ids=[
            "single-quotes",
            "no-line",
            "two-numbers",
            "suffix",
            "empty",
            "expression",
            "two-lines",
            "reassigned",
            "after-a-comment",
        ],
    )
    def test_stops_with_a_message_on_a_version_it_cannot_read(self, tmp_path, line):
        root = clone(tmp_path / "clone")
        set_version_line(root, line)
        done = build(root, "--app-only", "--no-sign")
        assert done.returncode == 1, done.stdout + done.stderr
        assert "Could not read APP_VERSION from app.py" in done.stderr
        assert 'APP_VERSION = "X.Y.Z"' in done.stderr
        assert not (root / "dist").exists()


@needs_macos
@pytest.mark.integration
class TestBuildOutput:
    def test_version_comes_from_app_py(self, built):
        info = plistlib.loads((built.app / "Contents" / "Info.plist").read_bytes())
        assert info["CFBundleVersion"] == VERSION
        assert info["CFBundleShortVersionString"] == VERSION
        assert built.dmg.is_file()
        assert built.pkg.is_file()

    def test_leaves_only_the_app_the_two_downloads_and_their_record(self, built):
        assert sorted(p.name for p in built.dist.iterdir()) == sorted([APP, DMG, PKG, "SHA256SUMS"])

    def test_pkg_carries_the_bundle_identifier_and_version(self, built, package):
        info = plistlib.loads((built.app / "Contents" / "Info.plist").read_bytes())
        assert info["CFBundleIdentifier"] == "com.mlxstudio.app"
        assert package.info.get("identifier") == info["CFBundleIdentifier"]
        assert package.info.get("version") == VERSION

    def test_pkg_installs_the_built_app_into_applications(self, built, package):
        assert package.info.get("install-location") == "/"
        assert [p.name for p in package.payload.iterdir()] == ["Applications"]
        assert [p.name for p in (package.payload / "Applications").iterdir()] == [APP]
        installed = package.payload / "Applications" / APP
        assert files(installed) == files(built.app)
        assert (installed / "Contents" / "MacOS" / "MLXStudio").stat().st_mode & 0o111

    def test_dmg_holds_the_app_the_shortcut_and_the_read_me(self, built, volume):
        disk = plistlib.loads(run("diskutil", "info", "-plist", volume).stdout.encode())
        assert disk["VolumeName"] == f"MLX Studio {VERSION}"
        assert files(volume / APP) == files(built.app)
        assert (volume / "Applications").is_symlink()
        assert str((volume / "Applications").readlink()) == "/Applications"
        assert (volume / "Read Me.txt").is_file()

    def test_app_is_ad_hoc_signed_by_default(self, built):
        run("codesign", "--verify", "--deep", "--strict", built.app)
        details = run("codesign", "--display", "--verbose=2", built.app).stderr
        assert "Signature=adhoc" in details
        assert "Sealed Resources version=" in details

    @pytest.mark.parametrize(
        "line",
        [
            'APP_VERSION="7.8.9"',
            'APP_VERSION = "7.8.9"  # next release',
            'APP_VERSION = "7.8.9"\r',
            'APP_VERSION_URL = "https://example.invalid/1.2.3"\nAPP_VERSION = "7.8.9"',
        ],
        ids=["no-spaces", "comment", "crlf", "longer-name-first"],
    )
    def test_reads_only_the_version_from_a_valid_line(self, tmp_path, line):
        root = clone(tmp_path / "clone")
        set_version_line(root, line)
        done = build(root, "--app-only", "--no-sign")
        assert done.returncode == 0, done.stdout + done.stderr
        info = plistlib.loads((root / "dist" / APP / "Contents" / "Info.plist").read_bytes())
        assert info["CFBundleVersion"] == VERSION
        assert info["CFBundleShortVersionString"] == VERSION

    @pytest.mark.parametrize("options", [(), ("--app-only",)], ids=["full", "app-only"])
    def test_a_signing_failure_stops_the_build(self, tmp_path, monkeypatch, options):
        root = clone(tmp_path / "clone")
        stubs = tmp_path / "bin"
        stubs.mkdir()
        (stubs / "codesign").write_text(
            "#!/bin/sh\necho 'stub: cannot sign' >&2\nexit 1\n", encoding="utf-8"
        )
        (stubs / "codesign").chmod(0o755)
        monkeypatch.setenv("PATH", f"{stubs}{os.pathsep}{os.environ['PATH']}")
        done = build(root, *options)
        assert done.returncode != 0, done.stdout
        assert "stub: cannot sign" in done.stdout  # the tool's own words are kept
        assert "codesign failed" in done.stderr
        assert "built" not in done.stdout
        # Nothing is packaged or recorded from an app that could not be sealed.
        assert [p.name for p in (root / "dist").iterdir()] == [APP]

    def test_app_only_without_signing_builds_just_an_unsealed_app(self, tmp_path):
        root = clone(tmp_path / "clone")
        done = build(root, "--app-only", "--no-sign")
        assert done.returncode == 0, done.stdout + done.stderr
        app = root / "dist" / APP
        assert [p.name for p in (root / "dist").iterdir()] == [APP]
        assert not (app / "Contents" / "_CodeSignature").exists()
        assert run("codesign", "--verify", "--strict", app, check=False).returncode != 0

    def test_prints_the_checksum_of_both_downloads(self, built):
        printed = re.findall(r"^\s*sha256 ([0-9a-f]{64})$", built.out, re.MULTILINE)
        assert printed == [sha256(built.dmg), sha256(built.pkg)]

    def test_read_me_has_six_numbered_steps(self, volume):
        text = (volume / "Read Me.txt").read_text(encoding="utf-8")
        steps = re.findall(r"^Step (\d+)\.", text, re.MULTILINE)
        assert steps == ["1", "2", "3", "4", "5", "6"]

    def test_read_me_first_step_clears_quarantine_from_the_download(self, built, volume, tmp_path):
        """Run the command exactly as a reader would paste it, in a temporary HOME."""
        text = (volume / "Read Me.txt").read_text(encoding="utf-8")
        step_one = text.partition("Step 1.")[2].partition("Step 2.")[0]
        commands = [x.strip() for x in step_one.splitlines() if x.strip().startswith("xattr -dr ")]
        assert len(commands) == 1, step_one
        # Only a plain command on a file under ~/Downloads is ever executed here.
        assert re.fullmatch(r"xattr -dr [\w.]+ ~/Downloads/[\w.-]+", commands[0]), commands[0]
        download = tmp_path / "Downloads" / built.dmg.name
        download.parent.mkdir()
        shutil.copyfile(built.dmg, download)
        run("xattr", "-w", QUARANTINE, FLAG, download)
        assert quarantined(download)
        done = run(
            "bash", "-c", commands[0],
            env={"HOME": str(tmp_path), "PATH": "/usr/bin:/bin"}, check=False,
        )  # fmt: skip
        assert done.returncode == 0, done.stderr
        assert not quarantined(download)


# Only the network and "open" are replaced; shasum, hdiutil, codesign, xattr, cp
# and mv are the real ones. Any address the stub does not know, and the two text
# files it serves, carry shell code that leaves a mark if anything ever runs it.
INSTALL_STUBS = {
    "uname": '#!/bin/sh\n[ "$1" = "-m" ] && echo arm64 || echo Darwin\n',
    "open": '#!/bin/sh\necho opened >> "$STUB_DIR/open.log"\n',
    "curl": f"""#!/bin/sh
out=""; url=""
while [ $# -gt 0 ]; do
    case "$1" in
        -o) out="$2"; shift 2 ;;
        --retry) shift 2 ;;
        -*) shift ;;
        *) url="$1"; shift ;;
    esac
done
echo "$url" >> "$STUB_DIR/urls.log"
file="$STUB_DIR/served/${{url##*/}}"
[ -f "$file" ] || file="$STUB_DIR/trap.sh"
if [ -n "$out" ]; then
    /bin/cp "$file" "$out"
    /usr/bin/xattr -w {QUARANTINE} "{FLAG}" "$out"
else
    /bin/cat "$file"
fi
""",
    # Records the flags on the downloaded image at the moment it is mounted.
    "hdiutil": """#!/bin/sh
if [ "$1" = "attach" ]; then
    shift
    for image; do :; done
    /usr/bin/xattr "$image" > "$STUB_DIR/mounted-flags.log"
    exec /usr/bin/hdiutil attach -noautoopen "$@"
fi
exec /usr/bin/hdiutil "$@"
""",
}


class Machine:
    """A temporary place to run install.sh against a published dmg."""

    def __init__(self, base: Path, dmg: Path):
        self.base = base
        self.bin = base / "bin"
        self.served = base / "served"
        self.dest = base / "Applications"
        self.mark = base / "downloaded-code-ran"
        for folder in (self.bin, self.served, self.dest):
            folder.mkdir()
        for name, body in INSTALL_STUBS.items():
            (self.bin / name).write_text(body, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        self.trap = f"touch '{self.mark}'\n"
        (base / "trap.sh").write_text(self.trap, encoding="utf-8")
        shutil.copyfile(dmg, self.served / DMG)
        self.publish(sha256(dmg))
        (self.served / "latest").write_text(
            f'{{\n  "tag_name": "v{VERSION}",\n}}\n{self.trap}', encoding="utf-8"
        )

    def publish(self, checksum: str) -> None:
        (self.served / "SHA256SUMS").write_text(f"{checksum}  {DMG}\n{self.trap}", encoding="utf-8")

    def install(self, *args: str) -> subprocess.CompletedProcess:
        return run(
            "bash", INSTALLER, "--dir", self.dest, "--no-launch", *args,
            env={
                "PATH": f"{self.bin}:/usr/bin:/bin:/usr/sbin:/sbin",
                "HOME": str(self.base),
                "STUB_DIR": str(self.base),
            },
            check=False,
        )  # fmt: skip


def repack(app: Path, work: Path, change) -> Path:
    """A dmg holding a copy of the built app after `change` has been applied to it."""
    stage = work / "stage"
    stage.mkdir()
    run("cp", "-R", app, stage / APP)
    change(stage / APP)
    image = work / "repacked.dmg"
    run("hdiutil", "create", "-srcfolder", stage, "-format", "UDZO", "-fs", "HFS+", image)
    return image


class TestInstaller:
    def test_parses(self):
        assert subprocess.run(["bash", "-n", str(INSTALLER)], check=False).returncode == 0


@needs_macos
@pytest.mark.integration
class TestInstallerRun:
    def test_installs_the_built_dmg(self, built, tmp_path):
        machine = Machine(tmp_path, built.dmg)
        done = machine.install("--version", VERSION)
        assert done.returncode == 0, done.stdout + done.stderr
        assert files(machine.dest / APP) == files(built.app)
        assert [p.name for p in machine.dest.iterdir()] == [APP]
        assert not (tmp_path / "open.log").exists()

    def test_refuses_a_dmg_that_differs_from_the_published_checksum(self, built, tmp_path):
        machine = Machine(tmp_path, built.dmg)
        machine.publish(hashlib.sha256(b"the file that was really published").hexdigest())
        done = machine.install("--version", VERSION)
        assert done.returncode != 0, done.stdout
        assert "checksum mismatch" in done.stderr
        assert list(machine.dest.iterdir()) == []
        assert not (tmp_path / "mounted-flags.log").exists()

    def test_refuses_an_app_altered_after_signing(self, built, tmp_path):
        def tamper(app: Path) -> None:
            with open(app / "Contents" / "Resources" / "app.py", "a", encoding="utf-8") as fh:
                fh.write("# altered after signing\n")

        # The checksum matches, so only the signature check can stop this one.
        machine = Machine(tmp_path, repack(built.app, tmp_path, tamper))
        done = machine.install("--version", VERSION)
        assert done.returncode != 0, done.stdout
        assert "signature check failed" in done.stderr
        assert list(machine.dest.iterdir()) == []

    def test_clears_quarantine_from_the_download_before_mounting(self, built, tmp_path):
        machine = Machine(tmp_path, built.dmg)
        done = machine.install("--version", VERSION)
        assert done.returncode == 0, done.stdout + done.stderr
        flags = (tmp_path / "mounted-flags.log").read_text(encoding="utf-8")
        assert QUARANTINE not in flags

    def test_clears_quarantine_from_the_installed_app(self, built, tmp_path):
        def flag(app: Path) -> None:
            run("xattr", "-r", "-w", QUARANTINE, FLAG, app)
            assert quarantined(app)

        machine = Machine(tmp_path, repack(built.app, tmp_path, flag))
        done = machine.install("--version", VERSION)
        assert done.returncode == 0, done.stdout + done.stderr
        assert quarantined(machine.dest / APP) == []

    def test_runs_nothing_it_downloads(self, built, tmp_path):
        machine = Machine(tmp_path, built.dmg)
        done = machine.install()  # no --version: the release lookup runs too
        assert done.returncode == 0, done.stdout + done.stderr
        assert not machine.mark.exists()
        urls = (tmp_path / "urls.log").read_text(encoding="utf-8").split()
        assert urls == [LATEST, DOWNLOAD, SUMS]
