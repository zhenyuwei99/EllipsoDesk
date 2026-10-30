# -*- mode: python ; coding: utf-8 -*-
# PyInstaller spec：EllipsoDesk 桌面应用（onedir；macOS arm64 / Windows x64 由各自平台构建）。
# 构建：pyinstaller packaging/launcher.spec（任意 CWD 均可，路径以 SPECPATH 锚定）→ dist/EllipsoDesk/
import os
from PyInstaller.utils.hooks import collect_all

_SPEC_DIR = os.path.abspath(SPECPATH)    # .../packaging
_ROOT_DIR = os.path.dirname(_SPEC_DIR)   # 仓库根

# 整个代码包打入（scripts/gui/stacks，含 stacks/*.yaml）；fit_app.py 运行时由 streamlit 执行
datas = [(os.path.join(_ROOT_DIR, "ellipsodesk"), "ellipsodesk")]
binaries = []
hiddenimports = []

for _pkg in ("streamlit", "plotly"):     # streamlit 静态资源 / plotly 模板必须收集齐全
    _d, _b, _h = collect_all(_pkg)
    datas += _d
    binaries += _b
    hiddenimports += _h

# 拟合引擎依赖：launcher 本体不 import 引擎（fit_app.py 以数据文件打入，静态分析看不到）
# → 显式收进闭包；numpy/pandas 随 streamlit 依赖自动带入
hiddenimports += ["scipy.optimize", "yaml", "platformdirs"]

a = Analysis(
    [os.path.join(_SPEC_DIR, "launcher.py")],
    pathex=[_ROOT_DIR],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=["tkinter", "matplotlib", "IPython", "jupyter"],   # 瘦身
    noarchive=False,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="EllipsoDesk",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,        # Windows 无黑窗
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="EllipsoDesk",   # onedir（不 onefile）：dist/EllipsoDesk/
)
