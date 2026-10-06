# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for AFT Alignment Analyzer (Windows .exe and macOS .app).

Build from the project root (the folder holding pyproject.toml):

    pyinstaller packaging/AFTAnalyzer.spec --noconfirm

Windows → dist/AFTAnalyzer/AFTAnalyzer.exe   (one-dir: fast start, fewer AV alerts)
macOS   → dist/AFTAnalyzer.app

PyInstaller cannot cross-compile: build each platform on that platform
(.github/workflows/build.yml does both on GitHub runners).
"""

import os
import sys

from PyInstaller.utils.hooks import collect_submodules

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
NAME = "AFTAnalyzer"
MAC = sys.platform == "darwin"

datas = [(os.path.join(ROOT, "aft_gui", "icon.png"), "aft_gui")]
hiddenimports = (["matplotlib.backends.backend_qtagg", "skimage.filters", "scipy.ndimage"]
                 + collect_submodules("aicspylibczi"))
excludes = ["PyQt5", "PySide2", "PySide6", "tkinter", "pytest", "IPython", "jupyter",
            "notebook", "sphinx", "PyQt6.QtWebEngineCore", "PyQt6.QtBluetooth",
            "PyQt6.QtQuick", "PyQt6.QtQml", "PyQt6.Qt3DCore", "PyQt6.QtMultimedia"]

a = Analysis(
    [os.path.join(ROOT, "aft_gui", "__main__.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=excludes,
    noarchive=False,
)
pyz = PYZ(a.pure)
exe = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name=NAME,
    console=False,
    strip=False,
    upx=False,
    icon=os.path.join(SPECPATH, "icon.icns" if MAC else "icon.ico"),
)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name=NAME)

if MAC:
    app = BUNDLE(
        coll,
        name=f"{NAME}.app",
        icon=os.path.join(SPECPATH, "icon.icns"),
        bundle_identifier="org.aftgui.analyzer",
        info_plist={
            "CFBundleDisplayName": "AFT Alignment Analyzer",
            "CFBundleShortVersionString": "0.2.0",
            "NSHighResolutionCapable": True,
        },
    )
