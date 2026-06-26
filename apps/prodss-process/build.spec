# -*- mode: python ; coding: utf-8 -*-

import sys
from pathlib import Path

from PyInstaller.utils.hooks import collect_all, collect_data_files, copy_metadata


APP_NAME = "prodss-process"
PROJECT_DIR = Path(SPECPATH).resolve()


datas = [
    (str(PROJECT_DIR / "streamlit_app.py"), "."),
]
binaries = []
hiddenimports = [
    "streamlit.web.cli",
    "streamlit.runtime.scriptrunner.magic_funcs",
    "altair",
    "pydeck",
    "pandas",
    "numpy",
]

# Streamlit relies heavily on dynamic imports and runtime version introspection,
# which PyInstaller's static analysis misses. Collect everything for the visual
# packages and bundle the dependency metadata Streamlit reads at startup.
for pkg in ("streamlit", "altair", "pydeck"):
    pkg_datas, pkg_binaries, pkg_hiddenimports = collect_all(pkg)
    datas += pkg_datas
    binaries += pkg_binaries
    hiddenimports += pkg_hiddenimports

for dist in (
    "streamlit",
    "altair",
    "pydeck",
    "pandas",
    "numpy",
    "pyarrow",
    "click",
    "tornado",
):
    try:
        datas += copy_metadata(dist)
    except Exception:
        pass


a = Analysis(
    [str(PROJECT_DIR / "launcher.py")],
    pathex=[str(PROJECT_DIR)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
        bundle_identifier="edu.wisc.cfl.prodss",
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
