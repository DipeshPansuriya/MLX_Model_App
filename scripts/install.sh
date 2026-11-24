#!/bin/bash
# MLX Studio — one-line installer for macOS.
#
# Why this exists: the app is ad-hoc signed, not notarised by Apple. A browser
# download carries com.apple.quarantine, and macOS 15+ refuses to launch it with
# no "Open" button in the dialog. curl sets no quarantine flag, so installing
# this way skips the prompt entirely — no Apple Developer account needed.
#
#   curl -fsSL https://raw.githubusercontent.com/DipeshPansuriya/MLX_Model_App/main/scripts/install.sh | bash
#
# Options (pass after `bash -s --`):
#   --version 2.0.1   install a specific release (default: latest)
#   --dir PATH        install somewhere other than /Applications
#   --no-launch       install without opening the app

set -euo pipefail

REPO="DipeshPansuriya/MLX_Model_App"
APP_NAME="MLX Studio"
VERSION=""
DEST="/Applications"
LAUNCH=1

usage() {
    cat <<'USAGE'
MLX Studio — one-line installer for macOS.

Why this exists: the app is ad-hoc signed, not notarised by Apple. A browser
download carries com.apple.quarantine, and macOS 15+ refuses to launch it with
no "Open" button in the dialog. curl sets no quarantine flag, so installing
this way skips the prompt entirely — no Apple Developer account needed.

  curl -fsSL https://raw.githubusercontent.com/DipeshPansuriya/MLX_Model_App/main/scripts/install.sh | bash

Options (pass after `bash -s --`):
  --version 2.0.1   install a specific release (default: latest)
  --dir PATH        install somewhere other than /Applications
  --no-launch       install without opening the app
USAGE
}

while [ $# -gt 0 ]; do
    case "$1" in
        --version) VERSION="${2:?--version needs a value}"; shift 2 ;;
        --dir) DEST="${2:?--dir needs a value}"; shift 2 ;;
        --no-launch) LAUNCH=0; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "unknown option: $1" >&2; exit 2 ;;
    esac
done

say() { printf '%s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "macOS only"
[ "$(uname -m)" = "arm64" ] || die "Apple Silicon only (found $(uname -m))"
command -v curl >/dev/null || die "curl is required"

if [ -z "$VERSION" ]; then
    say "▸ resolving the latest release"
    VERSION="$(curl -fsSL "https://api.github.com/repos/$REPO/releases/latest" \
        | sed -n 's/.*"tag_name": *"v\([^"]*\)".*/\1/p' | head -1)"
    [ -n "$VERSION" ] || die "could not read the latest version from GitHub"
fi

DMG="MLX.Studio-$VERSION.dmg"
URL="https://github.com/$REPO/releases/download/v$VERSION/$DMG"
TMP="$(mktemp -d)"
MOUNT=""

cleanup() {
    [ -n "$MOUNT" ] && hdiutil detach "$MOUNT" >/dev/null 2>&1 || true
    rm -rf "$TMP"
}
trap cleanup EXIT

say "▸ downloading $DMG"
curl -fsSL --retry 3 -o "$TMP/$DMG" "$URL" || die "download failed: $URL"

# curl does not set this, but a proxy or a copied file might have.
xattr -dr com.apple.quarantine "$TMP/$DMG" 2>/dev/null || true

say "▸ verifying checksum"
EXPECTED="$(curl -fsSL "https://raw.githubusercontent.com/$REPO/main/release/latest.json" \
    | sed -n 's/.*"sha256": *"\([0-9a-f]\{64\}\)".*/\1/p' | head -1)"
ACTUAL="$(shasum -a 256 "$TMP/$DMG" | awk '{print $1}')"
if [ -n "$EXPECTED" ] && [ "$EXPECTED" != "$ACTUAL" ]; then
    die "checksum mismatch
  expected $EXPECTED
  actual   $ACTUAL"
fi
say "  sha256 $ACTUAL"

say "▸ mounting"
MOUNT="$(hdiutil attach -nobrowse -readonly "$TMP/$DMG" \
    | sed -n 's|.*\(/Volumes/.*\)|\1|p' | tail -1)"
[ -n "$MOUNT" ] || die "could not mount the dmg"

[ -d "$MOUNT/$APP_NAME.app" ] || die "$APP_NAME.app not found in the dmg"

say "▸ installing to $DEST"
mkdir -p "$DEST"
rm -rf "$DEST/$APP_NAME.app"
cp -R "$MOUNT/$APP_NAME.app" "$DEST/"
xattr -dr com.apple.quarantine "$DEST/$APP_NAME.app" 2>/dev/null || true

say "▸ verifying signature"
codesign --verify --deep --strict "$DEST/$APP_NAME.app" \
    || die "signature check failed on the installed copy"

INSTALLED="$(/usr/libexec/PlistBuddy -c 'Print :CFBundleShortVersionString' \
    "$DEST/$APP_NAME.app/Contents/Info.plist" 2>/dev/null || echo "$VERSION")"
say ""
say "✓ $APP_NAME $INSTALLED installed at $DEST/$APP_NAME.app"
say ""
say "  Logs:     ~/.mlx_studio/logs/"
say "  Share:    Activity → Export diagnostics"
say "  Uninstall: rm -rf \"$DEST/$APP_NAME.app\""

if [ "$LAUNCH" -eq 1 ]; then
    say ""
    say "▸ launching"
    open "$DEST/$APP_NAME.app"
fi
