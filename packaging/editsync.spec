# PyInstaller spec for the EditSync desktop app.
#
# Build (from the repository root):
#   pyinstaller packaging/editsync.spec
#
# If ffmpeg/ffprobe binaries are placed in packaging/bin/ first, they are
# bundled into the app so end users don't need anything installed. The
# packaging/build_*.sh scripts handle downloading them.

import sys
from pathlib import Path

ROOT = Path(SPECPATH).parent

binaries = []
bin_dir = ROOT / "packaging" / "bin"
if bin_dir.is_dir():
    ext = ".exe" if sys.platform == "win32" else ""
    for tool in ("ffmpeg", "ffprobe"):
        candidate = bin_dir / f"{tool}{ext}"
        if candidate.is_file():
            binaries.append((str(candidate), "bin"))

a = Analysis(
    [str(ROOT / "packaging" / "launch_app.py")],
    pathex=[str(ROOT / "src")],
    binaries=binaries,
    datas=[
        (str(ROOT / "src" / "editsync" / "gui" / "assets"), "editsync/gui/assets"),
    ],
    hiddenimports=[],
    excludes=["tkinter", "scipy", "matplotlib", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

icon_file = None
if sys.platform == "darwin" and (ROOT / "packaging" / "icon.icns").is_file():
    icon_file = str(ROOT / "packaging" / "icon.icns")
elif sys.platform == "win32" and (ROOT / "packaging" / "icon.ico").is_file():
    icon_file = str(ROOT / "packaging" / "icon.ico")

exe = EXE(
    pyz,
    a.scripts,
    exclude_binaries=True,
    name="EditSync",
    console=False,
    icon=icon_file,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="EditSync",
)

if sys.platform == "darwin":
    app = BUNDLE(
        coll,
        name="EditSync.app",
        icon=icon_file,
        bundle_identifier="com.86autolab.editsync",
        info_plist={
            "NSHighResolutionCapable": True,
            "CFBundleShortVersionString": "1.1.0",
            "NSHumanReadableCopyright": "© 86 Auto Lab — MIT License",
        },
    )
