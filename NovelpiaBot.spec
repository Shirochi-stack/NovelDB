# -*- mode: python ; coding: utf-8 -*-

import os
import glob

# --- GTK/MSYS2 DLLs for WeasyPrint (PDF) ---
gtk_folder = os.environ.get('GTK_FOLDER', '')
msys2_bin_candidates = [
    os.path.join(gtk_folder, 'bin') if gtk_folder else '',
    r'C:\msys64\mingw64\bin',
    r'C:\msys64\ucrt64\bin',
    r'D:\a\_temp\msys64\mingw64\bin',
]
msys2_bin = None
for candidate in msys2_bin_candidates:
    if candidate and os.path.exists(candidate):
        msys2_bin = candidate
        break
if msys2_bin:
    os.environ['PATH'] = msys2_bin + os.pathsep + os.environ.get('PATH', '')

binaries = []
if msys2_bin and os.path.exists(msys2_bin):
    for dll in glob.glob(os.path.join(msys2_bin, '*.dll')):
        binaries.append((dll, '.'))

a = Analysis(
    ['bot.py'],
    pathex=[],
    binaries=binaries,
    datas=[
        ('dpi_setup.py', '.'),      # Ship source copy for reference
    ],
    hiddenimports=[
        'dpi_setup',
    ],
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
    name='NDBot',
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
