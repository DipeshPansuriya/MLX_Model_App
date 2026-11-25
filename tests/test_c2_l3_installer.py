"""C2: the installer checks the hash of the file it downloaded. L3: it fails closed
and never destroys the installed app before the replacement is proven.

Every system tool is a stub on PATH; the install target is a temporary folder.
"""

from __future__ import annotations

import hashlib
import os
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
INSTALLER = ROOT / "scripts" / "install.sh"
VERSION = "9.9.9"
DMG = f"MLX.Studio-{VERSION}.dmg"
PAYLOAD = b"stub disk image"
GOOD = hashlib.sha256(PAYLOAD).hexdigest()
OTHER = "a" * 64

STUBS = {
    "uname": '#!/bin/sh\n[ "$1" = "-m" ] && echo arm64 || echo Darwin\n',
    "curl": """#!/bin/sh
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
file="$STUB_DIR/served/${url##*/}"
[ -f "$file" ] || exit 22
if [ -n "$out" ]; then cp "$file" "$out"; else cat "$file"; fi
""",
    "hdiutil": """#!/bin/sh
verb="$1"; shift
[ "$verb" = "attach" ] || exit 0
mount=""
while [ $# -gt 0 ]; do
    [ "$1" = "-mountpoint" ] && mount="$2"
    shift
done
[ -n "$mount" ] || exit 1
mkdir -p "$mount/MLX Studio.app/Contents"
echo new > "$mount/MLX Studio.app/Contents/marker"
""",
    "codesign": '#!/bin/sh\n[ -f "$STUB_DIR/bad-signature" ] && exit 1\nexit 0\n',
    "xattr": "#!/bin/sh\nexit 0\n",
    "open": '#!/bin/sh\necho opened >> "$STUB_DIR/open.log"\n',
}


class Box:
    def __init__(self, base: Path):
        self.base = base
        self.bin = base / "bin"
        self.served = base / "served"
        self.dest = base / "Applications"
        self.tmp = base / "tmp"
        for folder in (self.bin, self.served, self.dest, self.tmp):
            folder.mkdir()
        for name, body in STUBS.items():
            (self.bin / name).write_text(body, encoding="utf-8")
            (self.bin / name).chmod(0o755)
        (self.served / DMG).write_bytes(PAYLOAD)

    def sums(self, *lines: str) -> None:
        (self.served / "SHA256SUMS").write_text("".join(f"{x}\n" for x in lines), encoding="utf-8")

    def install_old(self) -> Path:
        marker = self.dest / "MLX Studio.app" / "Contents" / "marker"
        marker.parent.mkdir(parents=True)
        marker.write_text("old\n", encoding="utf-8")
        return marker

    def run(self) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
            "HOME": str(self.base),
            "TMPDIR": str(self.tmp),
            "STUB_DIR": str(self.base),
        }
        return subprocess.run(
            [
                "bash",
                str(INSTALLER),
                "--version",
                VERSION,
                "--dir",
                str(self.dest),
                "--no-launch",
            ],
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )


@pytest.fixture
def box(tmp_path):
    return Box(tmp_path)


def test_installs_when_the_dmg_hash_matches_its_own_entry(box):
    box.sums(f"{OTHER}  MLX.Studio-{VERSION}.pkg", f"{GOOD}  {DMG}")
    marker = box.install_old()
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert marker.read_text(encoding="utf-8") == "new\n"
    assert [p.name for p in box.dest.iterdir()] == ["MLX Studio.app"]


def test_installs_into_an_empty_folder(box):
    box.sums(f"{GOOD}  {DMG}")
    done = box.run()
    assert done.returncode == 0, done.stdout + done.stderr
    assert (box.dest / "MLX Studio.app").is_dir()


@pytest.mark.parametrize(
    "lines",
    [
        [],
        [f"{GOOD}  MLX.Studio-1.0.0.dmg"],
        [f"{GOOD}  MLX.Studio-{VERSION}.pkg"],
        [f"not-a-hash  {DMG}"],
        [f"{GOOD}  {DMG}", f"{OTHER}  {DMG}"],
    ],
    ids=["empty", "other-version", "pkg-only", "malformed", "duplicate"],
)
def test_fails_closed_without_exactly_one_checksum(box, lines):
    box.sums(*lines)
    marker = box.install_old()
    done = box.run()
    assert done.returncode != 0, done.stdout
    assert "checksum" in done.stderr
    assert marker.read_text(encoding="utf-8") == "old\n"


def test_fails_closed_when_the_checksum_file_cannot_be_fetched(box):
    marker = box.install_old()
    done = box.run()
    assert done.returncode != 0
    assert marker.read_text(encoding="utf-8") == "old\n"


def test_mismatch_keeps_the_old_app(box):
    box.sums(f"{OTHER}  {DMG}")
    marker = box.install_old()
    done = box.run()
    assert done.returncode != 0
    assert "mismatch" in done.stderr
    assert marker.read_text(encoding="utf-8") == "old\n"


def test_failed_copy_keeps_the_old_app(box):
    box.sums(f"{GOOD}  {DMG}")
    marker = box.install_old()
    (box.bin / "cp").write_text(
        '#!/bin/sh\n[ "$1" = "-R" ] && exit 1\nexec /bin/cp "$@"\n', encoding="utf-8"
    )
    (box.bin / "cp").chmod(0o755)
    done = box.run()
    assert done.returncode != 0
    assert marker.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in box.dest.iterdir()] == ["MLX Studio.app"]


def test_failed_signature_check_keeps_the_old_app(box):
    box.sums(f"{GOOD}  {DMG}")
    marker = box.install_old()
    (box.base / "bad-signature").touch()
    done = box.run()
    assert done.returncode != 0
    assert marker.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in box.dest.iterdir()] == ["MLX Studio.app"]


def test_failed_swap_restores_the_old_app(box):
    box.sums(f"{GOOD}  {DMG}")
    marker = box.install_old()
    # Fail only the move of the new bundle into place.
    (box.bin / "mv").write_text(
        "#!/bin/sh\n"
        'case "$1" in */.mlx-install.*/"MLX Studio.app") exit 1 ;; esac\n'
        'exec /bin/mv "$@"\n',
        encoding="utf-8",
    )
    (box.bin / "mv").chmod(0o755)
    done = box.run()
    assert done.returncode != 0
    assert marker.read_text(encoding="utf-8") == "old\n"
    assert [p.name for p in box.dest.iterdir()] == ["MLX Studio.app"]
