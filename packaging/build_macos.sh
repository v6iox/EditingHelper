#!/usr/bin/env bash
# Build EditSync.app and EditSync.dmg on macOS.
#
# Usage (from the repository root, on a Mac):
#   ./packaging/build_macos.sh
#
# Produces dist/EditSync.app and dist/EditSync.dmg with ffmpeg bundled,
# so the app has zero dependencies for end users.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "==> Installing build dependencies"
python3 -m pip install --upgrade pip >/dev/null
python3 -m pip install ".[gui]" pyinstaller >/dev/null

echo "==> Fetching static ffmpeg/ffprobe to bundle"
mkdir -p packaging/bin
fetch_tool() {
    local tool="$1"
    [ -f "packaging/bin/$tool" ] && return 0
    # evermeet.cx serves static single-binary macOS builds of ffmpeg tools
    curl -fsSL "https://evermeet.cx/ffmpeg/getrelease/${tool}/zip" -o "/tmp/${tool}.zip"
    unzip -o -q "/tmp/${tool}.zip" -d packaging/bin
    chmod +x "packaging/bin/${tool}"
}
fetch_tool ffmpeg
fetch_tool ffprobe

echo "==> Generating app icon (.icns)"
ICONSET="$(mktemp -d)/EditSync.iconset"
mkdir -p "$ICONSET"
for size in 16 32 64 128 256 512; do
    sips -z $size $size src/editsync/gui/assets/icon.png \
        --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
    sips -z $((size*2)) $((size*2)) src/editsync/gui/assets/icon.png \
        --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o packaging/icon.icns

echo "==> Building EditSync.app"
pyinstaller --noconfirm packaging/editsync.spec

# --- code signing (runs only when MACOS_SIGN_IDENTITY is set) -----------
# See docs/SIGNING.md for how to provision the certificate and secrets.
if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
    echo "==> Signing EditSync.app with: $MACOS_SIGN_IDENTITY"
    # sign every nested Mach-O binary first (dylibs, .so modules, ffmpeg),
    # then the app bundle itself with hardened runtime + entitlements
    find dist/EditSync.app/Contents -type f \
        \( -name "*.dylib" -o -name "*.so" -o -perm -u+x \) -print0 |
    while IFS= read -r -d '' bin; do
        if file -b "$bin" | grep -q "Mach-O"; then
            codesign --force --options runtime --timestamp \
                --sign "$MACOS_SIGN_IDENTITY" "$bin"
        fi
    done
    codesign --force --options runtime --timestamp \
        --entitlements packaging/entitlements.plist \
        --sign "$MACOS_SIGN_IDENTITY" dist/EditSync.app
    codesign --verify --deep --strict dist/EditSync.app
    echo "==> Signature verified"
fi

echo "==> Creating DMG"
rm -f dist/EditSync.dmg
hdiutil create -volname "EditSync" -srcfolder dist/EditSync.app \
    -ov -format UDZO dist/EditSync.dmg
if [ -n "${MACOS_SIGN_IDENTITY:-}" ]; then
    codesign --force --timestamp --sign "$MACOS_SIGN_IDENTITY" dist/EditSync.dmg
fi

# --- notarization (runs only when Apple credentials are set) ------------
if [ -n "${APPLE_ID:-}" ] && [ -n "${APPLE_TEAM_ID:-}" ] && [ -n "${APPLE_APP_PASSWORD:-}" ]; then
    echo "==> Notarizing DMG (this waits for Apple, usually 1-5 minutes)"
    xcrun notarytool submit dist/EditSync.dmg \
        --apple-id "$APPLE_ID" \
        --team-id "$APPLE_TEAM_ID" \
        --password "$APPLE_APP_PASSWORD" \
        --wait
    xcrun stapler staple dist/EditSync.dmg
    echo "==> Notarized and stapled"
fi

echo ""
echo "Done:"
echo "  dist/EditSync.app  — the application"
echo "  dist/EditSync.dmg  — the downloadable disk image"
if [ -z "${MACOS_SIGN_IDENTITY:-}" ]; then
    echo ""
    echo "Note: built UNSIGNED (no MACOS_SIGN_IDENTITY set). On first launch"
    echo "users should right-click the app and choose Open (docs/APP_GUIDE.md)."
fi
