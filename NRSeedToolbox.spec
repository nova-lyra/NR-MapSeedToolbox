# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_submodules
from PyInstaller.utils.hooks import collect_all

datas = [('engine', 'engine'), ('web', 'web'), ('lock', 'lock')]
# ★ VC++ 运行时：Python 解释器依赖它们。不打进去的话，没装过 VC 运行库的电脑会启动失败。
#   （2026-09-19 加：之前 binaries 是空的，重新打包会漏掉这两个 dll，自包含性就没了）
import os as _os
binaries = []
for _dll in ('vcruntime140.dll', 'vcruntime140_1.dll'):
    _p = _os.path.join(_os.environ.get('SystemRoot', r'C:\Windows'), 'System32', _dll)
    if _os.path.isfile(_p):
        binaries.append((_p, '.'))
hiddenimports = ['ctypes.wintypes', 'tkinter', 'tkinter.ttk']
hiddenimports += collect_submodules('ctypes')
tmp_ret = collect_all('zstandard')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('Crypto')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['toolbox.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
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
    name='NRSeedToolbox',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
