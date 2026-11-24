#!/bin/bash
# ─────────────────────────────────────────────────────────────────────────────
# build_app.sh — package MLX Studio as a .app bundle and a distributable .dmg
#
#   ./build_app.sh              build dist/MLX Studio.app and dist/MLX Studio-<ver>.dmg
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

Drag "$APP_NAME" onto the Applications folder to install.

Requirements
  • macOS 12 or newer on Apple Silicon
  • Python 3.10+  (python.org build or: brew install python)
  • MLX packages — install them from the app's Setup tab

First launch
  Right-click the app and choose Open (unsigned build), then confirm.

Update checks
  Setup → Check for updates compares MLX / MLX-LM against PyPI.
  Set MLX_STUDIO_UPDATE_URL to a release manifest to also check app updates:
    {"version": "2.1.0", "url": "https://…/MLXStudio-2.1.0.dmg"}

Logs
  ~/.mlx_studio/activity.log   (in-app activity)
  ~/.mlx_studio/launch.log     (launch failures)
TXT

echo "▸ creating dmg"
hdiutil create -volname "$APP_NAME $VERSION" -srcfolder "$STAGE" \
    -ov -format UDZO -fs HFS+ "$DMG" >/dev/null
rm -rf "$STAGE"

SIZE="$(du -h "$DMG" | cut -f1)"
echo "▸ $DMG ($SIZE)"
shasum -a 256 "$DMG" | awk '{print "  sha256 " $1}'
echo "done"
