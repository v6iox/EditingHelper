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

echo "==> Building EditSync.app"
pyinstaller --noconfirm packaging/editsync.spec

echo "==> Creating DMG"
rm -f dist/EditSync.dmg
hdiutil create -volname "EditSync" -srcfolder dist/EditSync.app \
    -ov -format UDZO dist/EditSync.dmg

echo ""
echo "Done:"
echo "  dist/EditSync.app  — the application"
echo "  dist/EditSync.dmg  — the downloadable disk image"
echo ""
echo "Note: the app is unsigned. On first launch users should right-click"
echo "the app and choose Open (see docs/APP_GUIDE.md)."
