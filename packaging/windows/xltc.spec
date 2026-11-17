# -*- mode: python ; coding: utf-8 -*-
# 小财通 v0.1.0-preview Windows onedir build.
# Build from the release worktree, not the dirty development tree.
import os
from pathlib import Path
from PyInstaller.utils.hooks import collect_all

ROOT = Path(SPECPATH).resolve().parent.parent
datas = [(str(ROOT / "ui"), "ui"),
         (str(ROOT / "assets" / "brand"), "assets/brand")]
binaries = []
hiddenimports = []

# 这些包含平台模块/原生文件/动态导入，交给 PyInstaller 的 collect_all。
for package in ("webview", "pythonnet", "akshare", "py_mini_racer", "curl_cffi"):
    try:
        pkg_datas, pkg_binaries, pkg_hidden = collect_all(package)
        datas += pkg_datas
        binaries += pkg_binaries
        hiddenimports += pkg_hidden
    except Exception as exc:
        print(f"[spec] collect_all({package}) skipped: {exc}")

hiddenimports += ["clr", "bottle", "proxy_tools", "pandas", "numpy", "dateutil"]

a = Analysis(
    [str(ROOT / "packaging/windows/launcher.py")],
    pathex=[str(ROOT)],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "PyQt5", "PyQt6", "PySide2", "PySide6", "PIL"],
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="XiaoLiCaiTong",
    icon=str(ROOT / "assets" / "brand" / "xltc.ico"),
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=os.environ.get("XLTC_CONSOLE", "0") == "1",
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    name="XiaoLiCaiTong",
)
