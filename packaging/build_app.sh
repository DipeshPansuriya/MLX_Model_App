#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# build_app.sh — package MLX Studio as a .app bundle, a .dmg and a .pkg
#
#   ./build_app.sh              build dist/MLX Studio.app, the .dmg and the .pkg
#   ./build_app.sh --app-only   build just the .app
#   ./build_app.sh --no-sign    skip ad-hoc codesigning
#
# Output: dist/
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
DIST="$ROOT/dist"
APP_NAME="MLX Studio"
BUNDLE_ID="com.mlxstudio.app"
# One value for the plist and the compiler, checked against the binary below.
MIN_MACOS="12.0"
# Every line that assigns APP_VERSION. Checked below: there must be exactly one.
VERSION_LINE="$(sed -n '/^APP_VERSION[[:space:]]*=/p' "$ROOT/app.py")"
VERSION_RE='^APP_VERSION[[:space:]]*=[[:space:]]*"([0-9]+\.[0-9]+\.[0-9]+)"[[:space:]]*(#.*)?$'
BUILD_DATE="$(date -u +%Y-%m-%dT%H:%M:%SZ)"

APP_ONLY=0
DO_SIGN=1
for arg in "$@"; do
    case "$arg" in
        --app-only) APP_ONLY=1 ;;
        --no-sign)  DO_SIGN=0 ;;
        *) echo "unknown option: $arg"; exit 2 ;;
    esac
done

# A second line would reach the (#.*)? part of the pattern, so refuse it first.
if [[ "$VERSION_LINE" == *$'\n'* || ! "$VERSION_LINE" =~ $VERSION_RE ]]; then
    echo "Could not read APP_VERSION from app.py:" \
        'need exactly one line APP_VERSION = "X.Y.Z"' >&2
    exit 1
fi
VERSION="${BASH_REMATCH[1]}"

echo "▸ MLX Studio $VERSION"
APP="$DIST/$APP_NAME.app"
# A record of an earlier build must not survive this one.
rm -rf "$APP" "$DIST/SHA256SUMS"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Frameworks"

# ── resources: app sources + UI ──────────────────────────────────────────────
cp "$ROOT/app.py" "$APP/Contents/Resources/app.py"
cp "$ROOT/model_info.py" "$APP/Contents/Resources/model_info.py"
cp "$ROOT/mlx_loadtest.py" "$APP/Contents/Resources/mlx_loadtest.py"
cp "$ROOT/optiq_engine.py" "$APP/Contents/Resources/optiq_engine.py"
cp "$ROOT/engine_runtime.py" "$APP/Contents/Resources/engine_runtime.py"
cp "$ROOT/engines.py" "$APP/Contents/Resources/engines.py"
cp "$ROOT/login_agent.py" "$APP/Contents/Resources/login_agent.py"
cp "$ROOT/chat_store.py" "$APP/Contents/Resources/chat_store.py"
cp "$ROOT/projects.py" "$APP/Contents/Resources/projects.py"
cp "$ROOT/hub_catalog.py" "$APP/Contents/Resources/hub_catalog.py"
cp "$ROOT/live_stats.py" "$APP/Contents/Resources/live_stats.py"
cp "$ROOT/tool_null_text.py" "$APP/Contents/Resources/tool_null_text.py"
cp "$ROOT/loadtest_compare.py" "$APP/Contents/Resources/loadtest_compare.py"
cp "$ROOT/engine_plan.py" "$APP/Contents/Resources/engine_plan.py"
cp "$ROOT/update_check.py" "$APP/Contents/Resources/update_check.py"
cp "$ROOT/catalog_routes.py" "$APP/Contents/Resources/catalog_routes.py"
cp "$ROOT/project_routes.py" "$APP/Contents/Resources/project_routes.py"
# The only packages Setup may install for OptiQ, by hash.
mkdir -p "$APP/Contents/Resources/packaging"
cp "$ROOT/packaging/optiq-lock.txt" "$APP/Contents/Resources/packaging/optiq-lock.txt"
mkdir -p "$APP/Contents/Resources/ui"
cp "$ROOT/ui/index.html" "$ROOT/ui/style.css" "$ROOT/ui/app.js" "$ROOT/ui/download.js" "$ROOT/ui/download.css" "$ROOT/ui/projects.js" "$ROOT/ui/projects.css" "$APP/Contents/Resources/ui/"
cp "$HERE/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"

# ── Info.plist ───────────────────────────────────────────────────────────────
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>CFBundleName</key><string>$APP_NAME</string>
    <key>CFBundleDisplayName</key><string>$APP_NAME</string>
    <key>CFBundleIdentifier</key><string>$BUNDLE_ID</string>
    <key>CFBundleVersion</key><string>$VERSION</string>
    <key>CFBundleShortVersionString</key><string>$VERSION</string>
    <key>CFBundleExecutable</key><string>MLXStudio</string>
    <key>CFBundleIconFile</key><string>AppIcon</string>
    <key>CFBundlePackageType</key><string>APPL</string>
    <key>CFBundleSignature</key><string>????</string>
    <key>LSMinimumSystemVersion</key><string>$MIN_MACOS</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSRequiresAquaSystemAppearance</key><false/>
    <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
    <key>MLXStudioBuildDate</key><string>$BUILD_DATE</string>
</dict>
</plist>
PLIST

# ── executable: native Mach-O stub ───────────────────────────────────────────
# The executable must be a native Mach-O binary so LaunchServices registers ONE
# app identity ("MLX Studio") and produces a single Dock tile. A shell script
# stub exec'ing python3 registers TWO identities ("MLX Studio" + "Python")
# and produces duplicate Dock tiles.
command -v clang >/dev/null || { echo "clang not found: run xcode-select --install"; exit 1; }
BIN="$APP/Contents/MacOS/MLXStudio"
echo "▸ compiling launcher (macOS $MIN_MACOS+, arm64)"
clang -arch arm64 -mmacosx-version-min="$MIN_MACOS" -O2 -Wall -Wextra \
    -o "$BIN" "$HERE/launcher.c"

# The binary must run on every macOS the plist promises.
PLIST_MIN="$(/usr/libexec/PlistBuddy -c 'Print :LSMinimumSystemVersion' "$APP/Contents/Info.plist")"
BIN_MIN="$(otool -l "$BIN" | awk '$1 == "minos" {print $2; exit}')"
if [ "$BIN_MIN" != "$PLIST_MIN" ]; then
    echo "error: launcher has minos ${BIN_MIN:-unknown}, Info.plist promises $PLIST_MIN" >&2
    exit 1
fi

# ── codesign (ad-hoc by default) ─────────────────────────────────────────────
if [ "$DO_SIGN" -eq 1 ]; then
    echo "▸ ad-hoc codesign"
    # pipefail passes codesign's status through sed. An unsealed app is never packaged.
    if ! codesign --force --deep --sign - "$APP" 2>&1 | sed 's/^/    /'; then
        echo "error: codesign failed, build stopped" >&2
        exit 1
    fi
fi

echo "▸ built $APP"

if [ "$APP_ONLY" -eq 1 ]; then
    echo "done (app only)"
    exit 0
fi

# ── DMG ──────────────────────────────────────────────────────────────────────
# URL-safe asset name so release download links need no escaping.
DMG="$DIST/MLX.Studio-$VERSION.dmg"
STAGE="$DIST/.dmg-stage"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"

cat > "$STAGE/Read Me.txt" <<TXT
$APP_NAME $VERSION
=========================================

HOW TO INSTALL — follow these steps in order
=========================================

Step 1. Clear the quarantine flag on this DMG.

  A DMG downloaded in a browser carries a quarantine flag, and macOS 15+
  refuses to open the app: "Apple could not verify..." with only Done and
  Move to Bin. Clear the flag BEFORE installing.

  Open Terminal (Cmd-Space, type Terminal) and paste:

    xattr -dr com.apple.quarantine ~/Downloads/MLX.Studio-$VERSION.dmg

  No output means it worked. Check with:

    xattr -l ~/Downloads/MLX.Studio-$VERSION.dmg

  There should be no com.apple.quarantine line.

Step 2. Double-click this DMG to mount it.

  A volume named "$APP_NAME $VERSION" appears. It contains:
    $APP_NAME.app     the application
    Applications      a shortcut to your Applications folder
    Read Me.txt       this file

Step 3. Drag "$APP_NAME" onto the "Applications" shortcut.

  Finder copies the app to /Applications. The DMG window stays open.

Step 4. Eject the DMG.

  Click the eject symbol next to "$APP_NAME $VERSION" in the Finder sidebar.
  The app runs from /Applications, not from the DMG.

Step 5. Open Applications and double-click "$APP_NAME".

  The window opens at 1360x900 and the sidebar shows v$VERSION.

Step 6. Confirm it is running. In Terminal:

    curl -s http://127.0.0.1:8765/api/ping
    {"ok": true, "app": "$VERSION"}


IF YOU SKIPPED STEP 1
---------------------

macOS shows "Not Opened" with only Done and Move to Bin. The app is fine; the
flag is still attached. Clear it on the installed copy and relaunch:

    xattr -dr com.apple.quarantine "/Applications/$APP_NAME.app"
    open "/Applications/$APP_NAME.app"

Or allow it once through the GUI:
    macOS 15+   System Settings > Privacy & Security > Open Anyway
    macOS 14-   Right-click the app > Open > Open


SKIP THE DMG ENTIRELY
---------------------

This route never sets a quarantine flag, so no dialog appears:

    curl -fsSL https://raw.githubusercontent.com/DipeshPansuriya/MLX_Model_App/main/scripts/install.sh | bash


UNINSTALL
---------

    rm -rf "/Applications/$APP_NAME.app"

Model weights stay in ~/.cache/huggingface/hub/ and logs in ~/.mlx_studio/.


REQUIREMENTS
------------

  • macOS ${MIN_MACOS%.0} or newer on Apple Silicon
  • Python 3.10+  (python.org build or: brew install python)
  • MLX packages — install them from the app's Setup tab


UPDATE CHECKS
-------------

  Setup > Check for updates compares MLX / MLX-LM against PyPI.
  Set MLX_STUDIO_UPDATE_URL to a release manifest to also check app updates:
    {"version": "2.1.0", "url": "https://.../MLXStudio-2.1.0.dmg"}


LOGS
----

  ~/.mlx_studio/logs/          (folder: activity, app, launch, server)
  Activity > Export diagnostics zips it for a GitHub issue.
TXT

echo "▸ creating dmg"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" \
    -ov -format UDZO -fs HFS+ "$DMG" >/dev/null
rm -rf "$STAGE"

SIZE="$(du -h "$DMG" | cut -f1)"
echo "▸ $DMG ($SIZE)"
shasum -a 256 "$DMG" | awk '{print "  sha256 " $1}'

# ── pkg ──────────────────────────────────────────────────────────────────────
# A flat installer package installs to /Applications with one double-click and,
# unlike a browser-downloaded DMG, carries no com.apple.quarantine attribute on
# the installed bundle. This is the primary artifact from 2.0.4 on.
PKG="$DIST/MLX.Studio-$VERSION.pkg"
PKG_ROOT="$DIST/.pkg-root"
rm -rf "$PKG_ROOT" "$PKG"
mkdir -p "$PKG_ROOT/Applications"
cp -R "$APP" "$PKG_ROOT/Applications/"

echo "▸ creating pkg"
pkgbuild \
    --root "$PKG_ROOT" \
    --identifier "$BUNDLE_ID" \
    --version "$VERSION" \
    --install-location / \
    --ownership recommended \
    "$PKG" >/dev/null
rm -rf "$PKG_ROOT"

PKG_SIZE="$(du -h "$PKG" | cut -f1)"
echo "▸ $PKG ($PKG_SIZE)"
shasum -a 256 "$PKG" | awk '{print "  sha256 " $1}'

# ── build record ─────────────────────────────────────────────────────────────
# release_manifest.py refuses to publish files that differ from this record.
(cd "$DIST" && shasum -a 256 "$(basename "$DMG")" "$(basename "$PKG")" > SHA256SUMS)
echo "▸ $DIST/SHA256SUMS"
echo "done"
