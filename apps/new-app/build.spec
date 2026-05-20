# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, copy_metadata


APP_NAME = "new-app"
PROJECT_DIR = Path(SPECPATH).resolve()


datas = [
    (str(PROJECT_DIR / "streamlit_app.py"), "."),
]

datas += collect_data_files("streamlit")
datas += collect_data_files("altair")
datas += collect_data_files("pydeck")
datas += copy_metadata("streamlit")
datas += copy_metadata("altair")
datas += copy_metadata("pydeck")


a = Analysis(
    [str(PROJECT_DIR / "launcher.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=[],
    datas=datas,
    hiddenimports=[
        "streamlit.web.cli",
        "streamlit.runtime.scriptrunner.magic_funcs",
        "altair",
        "pydeck",
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
)

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name=APP_NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=True,
)

if sys.platform == "darwin":
    app = BUNDLE(
        exe,
        name=f"{APP_NAME}.app",
        icon=None,
        bundle_identifier="edu.wisc.cfl.newapp",
    )

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name=APP_NAME,
)
