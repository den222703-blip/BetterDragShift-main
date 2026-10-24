# -*- mode: python ; coding: utf-8 -*-

# PyInstaller spec. ONEFILE=1 -> portable single exe; otherwise a folder for the installer.
# PLAYER=1 -> also bundle QtMultimedia so the Trim window can play video (adds tens of MB);
#            without it Trim still works and just scrubs with still frames.
import os
from pathlib import Path
import pymupdf

pymupdf_path = os.path.dirname(pymupdf.__file__)

from PyInstaller.utils.hooks import collect_all

onefile = os.environ.get("ONEFILE") == "1"
with_player = os.environ.get("PLAYER") == "1"
heif_datas, heif_bins, heif_hidden = collect_all("pillow_heif")

# Helper programs, bundled only if they are in ./bin when you build. Otherwise the app finds them
# on PATH or in %LOCALAPPDATA%\DragShift\tools (see video.find_tool / upscale.find_realesrgan).
#   bin/ffmpeg.exe, bin/ffprobe.exe         video tools
#   bin/realesrgan/                         AI upscaler: realesrgan-ncnn-vulkan.exe, .dll files, models/
helper_datas = []
for tool in ("ffmpeg", "ffprobe"):
    for name in (tool + ".exe", tool):
        if (Path("bin") / name).is_file():
            helper_datas.append((str(Path("bin") / name), "bin"))
if (Path("bin") / "realesrgan").is_dir():
    helper_datas.append(("bin/realesrgan", "bin/realesrgan"))

excludes = ["tkinter", "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtQml",
            "PySide6.QtQuick", "PySide6.Qt3DCore", "PySide6.QtCharts",
            "PySide6.QtDataVisualization", "PySide6.QtPdf", "PySide6.QtSql", "PySide6.QtTest",
            "PySide6.QtOpenGL", "PySide6.QtSvgWidgets", "PySide6.QtDesigner", "matplotlib", "scipy"]
hiddenimports = ["win32com.client", "PySide6.QtNetwork", "AppKit", "Quartz", "objc"] + heif_hidden
DROP = ["Qt6Quick", "Qt6Qml", "Qt6Pdf", "Qt6WebEngine", "Qt63D", "Qt6Charts",
        "Qt6VirtualKeyboard", "Qt6Designer", "Qt6ShaderTools", "Qt6Sql", "Qt6Test", "opengl32sw",
        "Qt6OpenGL", "Qt6DataVisualization", "Qt6Positioning", "Qt6Sensors", "Qt6SerialPort",
        "Qt6Bluetooth", "Qt6Nfc", "Qt6RemoteObjects", "Qt6Scxml", "Qt6SpatialAudio", "Qt6TextToSpeech",
        "Qt6WebChannel", "Qt6WebSockets", "Qt6Svg", "qtquick", "qml"]
if with_player:
    hiddenimports.append("PySide6.QtMultimedia")
else:
    excludes.append("PySide6.QtMultimedia")
    DROP.append("Qt6Multimedia")

a = Analysis(
    ['run.py'],
    pathex=[],
    binaries=heif_bins,
    datas=[
        (os.path.join(pymupdf_path, 'layout', 'resources'), 'pymupdf/layout/resources')
    ] + heif_datas + helper_datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=None,
    noarchive=False,
)

a.binaries = [b for b in a.binaries if not any(d.lower() in b[0].lower() for d in DROP)]
a.datas = [d for d in a.datas if "translations" not in d[0].replace("\\", "/")]

pyz = PYZ(a.pure)

import sys
IS_MAC = sys.platform == "darwin"

common = dict(name="DragShift", console=False, upx=False)
if IS_MAC:
    common.update(icon="dragshift/assets/dragshift.icns")
else:
    common.update(icon="dragshift/assets/dragshift.ico", version="installer/version_info.txt")

if IS_MAC:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, argv_emulation=False, **common)
    coll = COLLECT(exe, a.binaries, a.datas, name="DragShift", upx=False)
    app = BUNDLE(
        coll,
        name="DragShift.app",
        icon="dragshift/assets/dragshift.icns",
        bundle_identifier="com.dragshift.app",
        info_plist={
            "CFBundleName": "DragShift",
            "CFBundleDisplayName": "DragShift",
            "CFBundleShortVersionString": "1.1.0",
            "CFBundleVersion": "1.1.0",
            "LSUIElement": True,  # menu-bar app, no Dock icon
            "LSMinimumSystemVersion": "11.0",
            "NSHighResolutionCapable": True,
            "NSRequiresAquaSystemAppearance": False,
            # show up in Finder's "Open With" for any file, and accept drops on the app icon
            "CFBundleDocumentTypes": [{
                "CFBundleTypeName": "Any file",
                "CFBundleTypeRole": "Viewer",
                "LSHandlerRank": "Alternate",
                "LSItemContentTypes": ["public.item", "public.content", "public.data"],
            }],
        },
    )
elif onefile:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.datas, name="DragShift", upx=False)