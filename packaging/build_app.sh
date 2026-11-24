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
VERSION="$(grep -m1 '^APP_VERSION' "$ROOT/app.py" | sed -E 's/.*"([0-9]+\.[0-9]+\.[0-9]+)".*/\1/')"
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

if [ -z "$VERSION" ]; then
    echo "Could not read APP_VERSION from app.py"; exit 1
fi

echo "▸ MLX Studio $VERSION"
APP="$DIST/$APP_NAME.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources" "$APP/Contents/Frameworks"

# ── resources: app sources + UI ──────────────────────────────────────────────
cp "$ROOT/app.py" "$APP/Contents/Resources/app.py"
mkdir -p "$APP/Contents/Resources/ui"
cp "$ROOT/ui/index.html" "$ROOT/ui/style.css" "$ROOT/ui/app.js" "$APP/Contents/Resources/ui/"
cp "$HERE/launcher.py" "$APP/Contents/Resources/launcher.py"
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
    <key>LSMinimumSystemVersion</key><string>12.0</string>
    <key>NSHighResolutionCapable</key><true/>
    <key>NSRequiresAquaSystemAppearance</key><false/>
    <key>LSApplicationCategoryType</key><string>public.app-category.developer-tools</string>
    <key>MLXStudioBuildDate</key><string>$BUILD_DATE</string>
</dict>
</plist>
PLIST

# ── executable stub ──────────────────────────────────────────────────────────
cat > "$APP/Contents/MacOS/MLXStudio" <<'STUB'
#!/bin/bash
DIR="$(cd "$(dirname "$0")/../Resources" && pwd)"
PY=""
for c in /Library/Frameworks/Python.framework/Versions/Current/bin/python3 \
         /opt/homebrew/bin/python3 /usr/local/bin/python3 /usr/bin/python3; do
    if [ -x "$c" ] && "$c" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else 1)' 2>/dev/null; then
        PY="$c"; break
    fi
done
if [ -z "$PY" ]; then
    osascript -e 'display alert "Python 3.10+ required" message "Install Python from python.org or run: brew install python" as critical'
    exit 1
fi
exec "$PY" "$DIR/launcher.py"
STUB
chmod +x "$APP/Contents/MacOS/MLXStudio"

# ── codesign (ad-hoc by default) ─────────────────────────────────────────────
if [ "$DO_SIGN" -eq 1 ]; then
    echo "▸ ad-hoc codesign"
    codesign --force --deep --sign - "$APP" 2>&1 | sed 's/^/    /' || true
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

  • macOS 12 or newer on Apple Silicon
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
echo "done"
