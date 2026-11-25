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
STAGE=""
TARGET="$DEST/$APP_NAME.app"

cleanup() {
    [ -n "$MOUNT" ] && hdiutil detach "$MOUNT" >/dev/null 2>&1 || true
    if [ -n "$STAGE" ]; then
        # Interrupted or failed between the two moves: put the old app back, and
        # keep the staging folder if even that does not work.
        if [ -e "$STAGE/previous.app" ] && [ ! -e "$TARGET" ]; then
            mv "$STAGE/previous.app" "$TARGET" \
                || { echo "error: previous app kept at $STAGE/previous.app" >&2; STAGE=""; }
        fi
        [ -n "$STAGE" ] && rm -rf "$STAGE"
    fi
    rm -rf "$TMP"
}
trap cleanup EXIT

say "▸ downloading $DMG"
curl -fsSL --retry 3 -o "$TMP/$DMG" "$URL" || die "download failed: $URL"

# curl does not set this, but a proxy or a copied file might have.
xattr -dr com.apple.quarantine "$TMP/$DMG" 2>/dev/null || true

# release/SHA256SUMS lists every published asset of every version, so the line is
# chosen by the exact file name. No line, no install.
say "▸ verifying checksum"
SUMS_URL="https://raw.githubusercontent.com/$REPO/main/release/SHA256SUMS"
curl -fsSL --retry 3 -o "$TMP/SHA256SUMS" "$SUMS_URL" \
    || die "could not fetch the checksum list: $SUMS_URL"
EXPECTED="$(awk -v name="$DMG" '$2 == name {print $1}' "$TMP/SHA256SUMS")"
[[ "$EXPECTED" =~ ^[0-9a-f]{64}$ ]] \
    || die "no single published checksum for $DMG; refusing to install"
ACTUAL="$(shasum -a 256 "$TMP/$DMG" | awk '{print $1}')"
if [ "$EXPECTED" != "$ACTUAL" ]; then
    die "checksum mismatch
  expected $EXPECTED
  actual   $ACTUAL"
fi
say "  sha256 $ACTUAL"

say "▸ mounting"
mkdir "$TMP/mnt"
hdiutil attach -nobrowse -readonly -mountpoint "$TMP/mnt" "$TMP/$DMG" >/dev/null \
    || die "could not mount the dmg"
MOUNT="$TMP/mnt"

[ -d "$MOUNT/$APP_NAME.app" ] || die "$APP_NAME.app not found in the dmg"

# Copy and verify next to the target first; the installed app is only replaced
# once the new copy has proven good.
say "▸ installing to $DEST"
mkdir -p "$DEST"
STAGE="$(mktemp -d "$DEST/.mlx-install.XXXXXX")" || die "cannot write to $DEST"
NEW="$STAGE/$APP_NAME.app"
cp -R "$MOUNT/$APP_NAME.app" "$NEW" || die "copy failed; nothing was changed"
xattr -dr com.apple.quarantine "$NEW" 2>/dev/null || true

# The signature is ad-hoc: it detects a damaged bundle, not who published it.
say "▸ verifying signature"
codesign --verify --deep --strict "$NEW" \
    || die "signature check failed; nothing was changed"

if [ -e "$TARGET" ]; then
    mv "$TARGET" "$STAGE/previous.app" || die "cannot replace $TARGET; nothing was changed"
fi
mv "$NEW" "$TARGET" || die "could not move the new app into place"

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
