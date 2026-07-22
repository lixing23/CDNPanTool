# -*- mode: python ; coding: utf-8 -*-
import sys
from PyInstaller.utils.hooks import collect_all

datas = [("assets", "assets")]
binaries = []
hiddenimports = [
    "oss_client",
    "share_codec",
    "stego_container",
    "settings_store",
    "chunk_manager",
    "manifest_codec",
]

for name in ["flet"]:
    d, b, h = collect_all(name)
    datas += d
    binaries += b
    hiddenimports += h

a = Analysis(
    ["flet_app.py"],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "unittest", "pydoc"],
    noarchive=False,
)

pyz = PYZ(a.pure, a.zipped_data, cipher=None)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="CDNPanTool",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon="assets/CDNPanTool.ico",
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="CDNPanTool",
)
