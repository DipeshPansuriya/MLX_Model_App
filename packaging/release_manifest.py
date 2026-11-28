#!/usr/bin/env python3
"""
release_manifest.py — generate and verify the release manifest.

Two tracked files describe a release:

    release/latest.json   what shipped apps poll (version, url, sha256, size of
                          the pkg, notes, published)
    release/SHA256SUMS    checksum of every published asset, all versions; the
                          one-line installer selects its line by file name

Both are generated from the exact files build_app.sh produced. Never edit them
by hand.

scripts/install.sh reads SHA256SUMS from main, so --check fails until that file
holds exactly one line for the manifest version's dmg. The one exception is a
manifest listed in NEVER_PUBLISHED, while APP_VERSION is newer: there is no
asset to list, so SHA256SUMS must not name that version at all.

Usage (from the repository root)
    ./packaging/release_manifest.py --dist dist --notes "…"
        write both files. Refuses artifacts that changed since the build, a pkg
        that does not match the source, a clashing tag, or a checksum that
        differs from the one already recorded for this version. --replace
        overrides the last one, for a version that was never published.

    ./packaging/release_manifest.py --backfill --dist DIR --dmg-sha256 HEX
        add the published release that latest.json describes to SHA256SUMS. DIR
        holds that release's downloaded dmg and pkg. HEX is the digest GitHub
        shows for the published dmg. latest.json is not touched.

    ./packaging/release_manifest.py --check [--tag vX.Y.Z]
        APP_VERSION, manifest, SHA256SUMS and git tags agree (no artifacts).

    ./packaging/release_manifest.py --verify --dist DIR
        --check, then DIR (a build or a download of the published assets) must
        match both files and the CHANGELOG checksums.

    ./packaging/release_manifest.py --verify --release --dist dist
        the same, plus every check that generation makes.

    ./packaging/release_manifest.py --inspect --dist dist
        the build matches its record and the source (used by CI).

Exit codes: 0 ok, 1 a check failed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import plistlib
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_PY = ROOT / "app.py"
MANIFEST = ROOT / "release" / "latest.json"
SUMS = ROOT / "release" / "SHA256SUMS"
CHANGELOG = ROOT / "CHANGELOG.md"
INSTALLER = ROOT / "scripts" / "install.sh"
REPO = "DipeshPansuriya/MLX_Model_App"
BUILD_RECORD = "SHA256SUMS"
# Sources that end up in the bundle; a release tag must hold exactly these.
SHIPPED = (
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
    "ui",
    "packaging",
)
BUNDLED = (
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
    "ui/index.html",
    "ui/style.css",
    "ui/app.js",
    "ui/projects.js",
    "ui/projects.css",
    "ui/download.js",
    "ui/download.css",
    "ui/features.js",
    "ui/features.css",
)

VERSION_RE = re.compile(r"\d+\.\d+\.\d+")
APP_VERSION_RE = re.compile(r'^APP_VERSION\s*=\s*"([^"]+)"', re.MULTILINE)
HASH_RE = re.compile(r"[0-9a-f]{64}")
# Manifests merged for a release that has no tag and no assets on GitHub, pinned
# to the manifest sha256. Add an entry only after checking GitHub for both.
NEVER_PUBLISHED = {
    "2.0.7": "d77d46c85399a90e448c6f3886db8fb06ae85c395bb97a1f8c155a35d51dfb69",
}


def asset_names(version: str) -> tuple[str, str]:
    """(dmg, pkg) as build_app.sh names them."""
    return f"MLX.Studio-{version}.dmg", f"MLX.Studio-{version}.pkg"


def release_url(version: str) -> str:
    pkg = asset_names(version)[1]
    return f"https://github.com/{REPO}/releases/download/v{version}/{pkg}"


def parse_version(text: str) -> str | None:
    match = APP_VERSION_RE.search(text)
    return match.group(1) if match else None


def read_version() -> str:
    version = parse_version(APP_PY.read_text(encoding="utf-8"))
    if not version:
        sys.exit("could not read APP_VERSION from app.py")
    return version


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_sums(path: Path) -> dict[str, str]:
    """Parse `shasum -a 256` output into {file name: hash}."""
    sums: dict[str, str] = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            parts = line.split()
            if len(parts) == 2:
                sums[parts[1]] = parts[0]
    return sums


def render_sums(sums: dict[str, str]) -> str:
    return "".join(f"{v}  {k}\n" for k, v in sums.items())


# ── git ──────────────────────────────────────────────────────────────────────
def git(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(ROOT), *args], capture_output=True, text=True, check=False
    )


def tag_commit(version: str) -> str | None:
    done = git("rev-parse", "--quiet", "--verify", f"refs/tags/v{version}^{{commit}}")
    return done.stdout.strip() if done.returncode == 0 else None


def tag_version_problems(version: str) -> list[str]:
    """A tag cannot be moved, so vX.Y.Z must hold the source of X.Y.Z."""
    commit = tag_commit(version)
    if not commit:
        return []
    tagged = parse_version(git("show", f"{commit}:app.py").stdout)
    if tagged == version:
        return []
    clash = f"tag v{version} points at {commit[:7]} whose APP_VERSION is {tagged}"
    return [f"{clash}: choose a new version number"]


def tag_source_problems(version: str) -> list[str]:
    commit = tag_commit(version)
    if not commit:
        return []
    if git("diff", "--quiet", commit, "--", *SHIPPED).returncode == 0:
        return []
    return [f"tag v{version} ({commit[:7]}) differs from the source in {', '.join(SHIPPED)}"]


# ── checks ───────────────────────────────────────────────────────────────────
def load_manifest() -> tuple[dict, list[str]]:
    try:
        data = json.loads(MANIFEST.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {}, [f"cannot read {MANIFEST.name}: {exc}"]
    if not isinstance(data, dict):
        return {}, [f"{MANIFEST.name} is not a JSON object"]
    return data, []


def is_never_published(manifest: dict) -> bool:
    pinned = NEVER_PUBLISHED.get(str(manifest.get("version")))
    return pinned is not None and pinned == manifest.get("sha256")


def unpublished_problems(version: str) -> list[str]:
    """Nothing was published for `version`, so any checksum for it is invented."""
    names = asset_names(version)
    text = SUMS.read_text(encoding="utf-8") if SUMS.is_file() else ""
    if any(len(p) == 2 and p[1] in names for p in map(str.split, text.splitlines())):
        return [f"{version} was never published: remove its lines from {SUMS.name}"]
    print(f"note: {version} was never published; {SUMS.name} starts with the next release")
    return []


def installer_problems(version: str) -> list[str]:
    """The installer on main refuses to install without its line on main."""
    if not INSTALLER.is_file():
        return []
    if f"release/{SUMS.name}" not in INSTALLER.read_text(encoding="utf-8"):
        return []
    dmg = asset_names(version)[0]
    text = SUMS.read_text(encoding="utf-8") if SUMS.is_file() else ""
    # Count raw lines, as install.sh does: read_sums() keeps only the last one.
    found = [p[0] for p in map(str.split, text.splitlines()) if len(p) == 2 and p[1] == dmg]
    if len(found) == 1 and HASH_RE.fullmatch(found[0]):
        return []
    problem = f"scripts/install.sh needs exactly one valid {SUMS.name} line for {dmg}"
    return [f"{problem} (found {len(found)}): publish release/{SUMS.name} first, see --backfill"]


def check_consistency(pushed_tag: str | None = None) -> list[str]:
    """APP_VERSION, manifest, SHA256SUMS and tags agree. No artifacts needed."""
    app_version = read_version()
    problems = tag_version_problems(app_version)
    if pushed_tag is not None and pushed_tag != f"v{app_version}":
        problems.append(f"tag {pushed_tag} does not match APP_VERSION {app_version}")

    manifest, errors = load_manifest()
    if errors:
        return problems + errors
    version = manifest.get("version")
    if not isinstance(version, str) or not VERSION_RE.fullmatch(version):
        return [*problems, f"manifest version is not X.Y.Z: {version!r}"]

    def key(value: str) -> tuple[int, ...]:
        return tuple(int(n) for n in re.findall(r"\d+", value)[:3])

    if key(version) > key(app_version):
        problems.append(f"manifest {version} is ahead of APP_VERSION {app_version}")
    if version != app_version:
        problems += tag_version_problems(version)
    if manifest.get("url") != release_url(version):
        problems.append(f"manifest url must be {release_url(version)}")
    digest = manifest.get("sha256")
    if not isinstance(digest, str) or not HASH_RE.fullmatch(digest):
        problems.append("manifest sha256 is not 64 hex characters")
    size = manifest.get("size")
    if not isinstance(size, int) or size <= 0:
        problems.append("manifest size is not a positive integer")
    if SUMS.is_file():
        dmg, pkg = asset_names(version)
        sums = read_sums(SUMS)
        if sums.get(pkg) != digest:
            problems.append(f"{SUMS.name} and manifest disagree about {pkg}")
        if not HASH_RE.fullmatch(sums.get(dmg, "")):
            problems.append(f"{SUMS.name} has no checksum for {dmg}")
    if is_never_published(manifest) and key(app_version) > key(version):
        return problems + unpublished_problems(version)
    return problems + installer_problems(version)


def check_build_record(dist: Path, version: str) -> list[str]:
    """The artifacts are the ones build_app.sh produced."""
    record = read_sums(dist / BUILD_RECORD)
    if not record:
        return [f"no build record {dist / BUILD_RECORD}: run packaging/build_app.sh"]
    problems = []
    for name in asset_names(version):
        path = dist / name
        if not path.is_file():
            problems.append(f"missing artifact {path}")
        elif record.get(name) != sha256(path):
            problems.append(f"{name} changed since the build")
    return problems


def check_payload(pkg: Path) -> list[str]:
    """The pkg installs the current source (macOS only, because of pkgutil)."""
    if not shutil.which("pkgutil"):
        return ["pkgutil not found: the package can only be inspected on macOS"]
    with tempfile.TemporaryDirectory() as tmp:
        out = Path(tmp) / "expanded"
        done = subprocess.run(
            ["pkgutil", "--expand-full", str(pkg), str(out)],
            capture_output=True,
            text=True,
            check=False,
        )
        if done.returncode != 0:
            return [f"cannot expand {pkg.name}: {done.stderr.strip()}"]
        contents = out / "Payload" / "Applications" / "MLX Studio.app" / "Contents"
        problems = []
        for name in BUNDLED:
            shipped = contents / "Resources" / name
            if not shipped.is_file() or shipped.read_bytes() != (ROOT / name).read_bytes():
                problems.append(f"{name} in {pkg.name} differs from the source: rebuild")
        try:
            info = plistlib.loads((contents / "Info.plist").read_bytes())
        except (OSError, ValueError) as exc:
            return [*problems, f"cannot read Info.plist in {pkg.name}: {exc}"]
        if info.get("CFBundleShortVersionString") != read_version():
            problems.append(f"{pkg.name} carries the wrong bundle version")
        return problems


def check_release_build(dist: Path) -> list[str]:
    """Everything that must hold before the manifest may describe a build."""
    version = read_version()
    problems = tag_version_problems(version) + check_build_record(dist, version)
    if not problems:
        problems += check_payload(dist / asset_names(version)[1])
    return problems + tag_source_problems(version)


def check_changelog(version: str, hashes: set[str], sizes: set[int]) -> list[str]:
    """Checksums quoted in this version's changelog section are the real ones."""
    if not CHANGELOG.is_file():
        return []
    match = re.search(
        rf"^## \[{re.escape(version)}\].*?(?=^## \[|\Z)",
        CHANGELOG.read_text(encoding="utf-8"),
        re.MULTILINE | re.DOTALL,
    )
    if not match:
        return []
    section = match.group(0)
    problems = [
        f"CHANGELOG {version} quotes a checksum of another build: {found[:12]}…"
        for found in re.findall(r"\b[0-9a-f]{64}\b", section)
        if found not in hashes
    ]
    problems += [
        f"CHANGELOG {version} quotes a size of another build: {found} bytes"
        for found in re.findall(r"size\s+(\d+)\s+bytes", section)
        if int(found) not in sizes
    ]
    return problems


def check_artifacts(directory: Path) -> list[str]:
    """Files in `directory` match latest.json, SHA256SUMS and the changelog."""
    manifest, errors = load_manifest()
    if errors:
        return errors
    version = str(manifest.get("version"))
    sums = read_sums(SUMS)
    problems, hashes, sizes = [], set(), set()
    for name in asset_names(version):
        path = directory / name
        if not path.is_file():
            problems.append(f"missing artifact {path}")
            continue
        digest, size = sha256(path), path.stat().st_size
        hashes.add(digest)
        sizes.add(size)
        if sums.get(name) != digest:
            problems.append(f"{name} does not match {SUMS.name}")
        if name.endswith(".pkg") and (
            manifest.get("sha256") != digest or manifest.get("size") != size
        ):
            problems.append(f"{name} does not match {MANIFEST.name} (sha256 or size)")
    if not problems:
        problems += check_changelog(version, hashes, sizes)
    return problems


# ── generate ─────────────────────────────────────────────────────────────────
def clashing_sums(new: dict[str, str]) -> list[str]:
    """Names SHA256SUMS already lists with another checksum, on any line."""
    text = SUMS.read_text(encoding="utf-8") if SUMS.is_file() else ""
    listed = [p for p in map(str.split, text.splitlines()) if len(p) == 2]
    return [
        f"{SUMS.name} already has another checksum for {name}"
        for name, digest in new.items()
        if any(other == name and found != digest for found, other in listed)
    ]


def write_release(dist: Path, notes: str, replace: bool = False) -> list[str]:
    version = read_version()
    dmg, pkg = (dist / name for name in asset_names(version))
    new = {path.name: sha256(path) for path in (dmg, pkg)}
    if not replace:
        # A published checksum must keep matching its asset.
        problems = clashing_sums(new)
        previous = load_manifest()[0]
        if previous.get("version") == version and previous.get("sha256") != new[pkg.name]:
            problems.append(f"{MANIFEST.name} already describes another {pkg.name}")
        if problems:
            hint = f"if {version} was never published, run again with --replace"
            return [*problems, hint]
    manifest = {
        "version": version,
        "url": release_url(version),
        "notes": notes,
        "published": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "sha256": new[pkg.name],
        "size": pkg.stat().st_size,
    }
    # Earlier versions keep their lines so `install.sh --version` can verify them.
    sums = {k: v for k, v in read_sums(SUMS).items() if k not in new} | new
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    SUMS.write_text(render_sums(sums), encoding="utf-8")
    MANIFEST.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"wrote {MANIFEST}\nwrote {SUMS}\n\nCHANGELOG checksum block for {version}:\n")
    print(f"pkg  sha256  {new[pkg.name]}\npkg  size    {manifest['size']} bytes")
    print(f"dmg  sha256  {new[dmg.name]}\ndmg  size    {dmg.stat().st_size} bytes")
    return []


def backfill_sums(directory: Path, dmg_sha256: str) -> list[str]:
    """List an already published release. Nothing is written if a check fails."""
    manifest, errors = load_manifest()
    if errors:
        return errors
    version = str(manifest.get("version"))
    if is_never_published(manifest):
        return [f"{version} was never published: there is nothing to backfill"]
    dmg, pkg = (directory / name for name in asset_names(version))
    problems = [f"missing artifact {path}" for path in (dmg, pkg) if not path.is_file()]
    if problems:
        return problems
    new = {path.name: sha256(path) for path in (dmg, pkg)}
    # latest.json records the published pkg, which tells a local rebuild apart.
    if (manifest.get("sha256"), manifest.get("size")) != (new[pkg.name], pkg.stat().st_size):
        return [f"{pkg.name} is not the published file in {MANIFEST.name} (sha256 or size)"]
    # latest.json says nothing about the dmg, so its checksum comes from the release.
    expected = dmg_sha256.strip().lower().removeprefix("sha256:")
    if expected != new[dmg.name]:
        needed = "--dmg-sha256 must be the digest GitHub shows for the published"
        return [f"{needed} {dmg.name}, and the file in --dist must match it"]
    problems = clashing_sums(new)
    if not problems:
        SUMS.write_text(render_sums(read_sums(SUMS) | new), encoding="utf-8")
        print(f"wrote {SUMS} for {version}")
    return problems


def report(problems: list[str]) -> int:
    for problem in problems:
        print(f"release check failed: {problem}", file=sys.stderr)
    return 1 if problems else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate and verify the MLX Studio release manifest"
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true", help="versions, manifest and tags agree")
    mode.add_argument("--verify", action="store_true", help="artifacts match the manifest")
    mode.add_argument("--inspect", action="store_true", help="the build matches the source")
    mode.add_argument(
        "--backfill", action="store_true", help="list the published release in SHA256SUMS"
    )
    parser.add_argument("--release", action="store_true", help="with --verify: all release checks")
    parser.add_argument("--tag", help="with --check: the pushed tag, e.g. v2.1.0")
    parser.add_argument("--dist", help="folder holding the dmg and pkg")
    parser.add_argument("--notes", default="", help="release notes shown to users")
    parser.add_argument(
        "--dmg-sha256", default="", help="with --backfill: the published dmg digest"
    )
    parser.add_argument(
        "--replace", action="store_true", help="overwrite checksums of an unpublished build"
    )
    args = parser.parse_args(argv)

    if args.check:
        status = report(check_consistency(args.tag))
        if not status:
            print("release check passed: versions, manifest and tags agree")
        return status

    if not args.dist:
        parser.error("--dist is required")
    dist = Path(args.dist).expanduser().resolve()

    if args.backfill:
        return report(backfill_sums(dist, args.dmg_sha256) or check_consistency())

    if args.inspect:
        version = read_version()
        problems = check_build_record(dist, version)
        if not problems:
            problems = check_payload(dist / asset_names(version)[1])
        status = report(problems)
        if not status:
            print(f"release check passed: build {version} matches its record and the source")
        return status

    if args.verify:
        problems = check_consistency() + check_artifacts(dist)
        if args.release:
            manifest = load_manifest()[0]
            if manifest.get("version") != read_version():
                problems.append("manifest version differs from APP_VERSION")
            problems += check_release_build(dist)
        status = report(list(dict.fromkeys(problems)))
        if not status:
            print("release check passed: artifacts match the manifest")
        return status

    problems = check_release_build(dist) or write_release(dist, args.notes, args.replace)
    if problems:
        status = report(problems)
        print("nothing written", file=sys.stderr)
        return status
    # Read back what was written. A stale CHANGELOG is reported by --verify, which
    # runs after the block above has been pasted into it.
    return report(check_consistency())


if __name__ == "__main__":
    sys.exit(main())
