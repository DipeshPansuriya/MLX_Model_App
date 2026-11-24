#!/usr/bin/env python3
"""
release_manifest.py — generate the update manifest that shipped apps poll.

The manifest is a small JSON document:

    {
      "version": "2.1.0",
      "url": "https://example.com/MLXStudio-2.1.0.dmg",
      "sha256": "…",
      "notes": "…",
      "published": "2026-11-24T22:00:00Z"
    }

Publish it anywhere reachable over HTTPS and point installed copies at it with
MLX_STUDIO_UPDATE_URL, or drop it next to the app and use a file:// URL.

Usage
    ./release_manifest.py --url https://example.com/MLXStudio-2.1.0.dmg \
                          --notes "Faster model loading" \
                          --out dist/latest.json

    # include the DMG checksum automatically
    ./release_manifest.py --dmg ../dist/"MLX Studio-2.0.0.dmg" \
                          --url https://example.com/MLXStudio-2.0.0.dmg \
                          --out ../dist/latest.json
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
APP_PY = HERE.parent / "app.py"


def read_version() -> str:
    text = APP_PY.read_text(encoding="utf-8")
    match = re.search(r'^APP_VERSION\s*=\s*"([^"]+)"', text, re.MULTILINE)
    if not match:
        sys.exit("could not read APP_VERSION from app.py")
    return match.group(1)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the MLX Studio update manifest")
    parser.add_argument("--version", default=None, help="override the version (default: app.py)")
    parser.add_argument("--url", required=True, help="download URL for the new DMG")
    parser.add_argument("--notes", default="", help="release notes shown to users")
    parser.add_argument("--dmg", default=None, help="local DMG to checksum")
    parser.add_argument("--out", default="latest.json", help="manifest output path")
    args = parser.parse_args()

    version = args.version or read_version()
    manifest = {
        "version": version,
        "url": args.url,
        "notes": args.notes,
        "published": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    if args.dmg:
        dmg = Path(args.dmg).expanduser().resolve()
        if not dmg.is_file():
            sys.exit(f"dmg not found: {dmg}")
        manifest["sha256"] = sha256(dmg)
        manifest["size"] = dmg.stat().st_size

    out = Path(args.out).expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(manifest, indent=2))
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
