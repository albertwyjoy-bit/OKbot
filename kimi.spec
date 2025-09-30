# -*- mode: python ; coding: utf-8 -*-

from PyInstaller.utils.hooks import collect_submodules, collect_data_files

tools = collect_submodules("kimi_cli.tools")
data_files = collect_data_files(
    "kimi_cli",
    includes=["**/*.yaml", "**/*.md"],
    excludes=["**/*.py", "**/*.pyc"],
)

a = Analysis(
    ["src/kimi_cli/__init__.py"],
    pathex=[],
    binaries=[],
    datas=data_files,
    hiddenimports=tools,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="kimi",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
