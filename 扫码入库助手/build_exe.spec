# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller 打包配置 —— 扫码入库助手（单文件 exe）。

产物：dist/扫码入库助手.exe
运行：双击 exe，自动在 exe 同目录生成 data/（数据库、登录配置）、导出/，
      只读的前端资源（web/）打进 exe 内部，随 exe 走。

打包命令：pyinstaller build_exe.spec --noconfirm
"""
import os

ROOT = os.path.abspath(os.getcwd())
APP = os.path.join(ROOT, "app")

a = Analysis(
    [os.path.join(APP, "server.py")],
    pathex=[APP],
    binaries=[],
    # 前端资源打进 exe（_MEIPASS/web/...），改前端样式无需重新打包源码以外的文件
    datas=[(os.path.join(APP, "web"), "web"), (os.path.join(APP, "installer"), "installer")],
    hiddenimports=["download", "backup", "operations", "pda", "cryptography", "inventory", "catalog", "inbound", "jst", "store", "xlsx", "edgecookie", "cdp"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # PDA证书使用cryptography，由PyInstaller官方hook收集；排除非运行依赖
    excludes=["tkinter", "unittest", "pydoc", "doctest", "test", "distutils"],
    noarchive=False,
    optimize=1,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="扫码入库助手",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=True,          # 保留控制台：便于看运行日志与「未找到 Python/Edge」等提示
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=os.path.join(ROOT, "app.ico") if os.path.isfile(os.path.join(ROOT, "app.ico")) else None,
)
