# -*- mode: python ; coding: utf-8 -*-

import os
import glob

block_cipher = None

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
    ['gui.py'],  # Main entry point script
    pathex=[],
    binaries=binaries,
    datas=[
        ('icon.ico', '.')  # Include the icon file in the root of the bundle
    ],
    hiddenimports=[],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    win_no_prefer_redirects=False,
    win_private_assemblies=False,
    cipher=block_cipher,
    noarchive=False,
)
pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    [],
    name='ND37',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True, # Compress the executable using UPX if available
    upx_exclude=[],
    runtime_tmpdir=None,
    console=False, # Set to False to hide the terminal window (GUI mode)
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon='icon.ico' # Application icon for the Windows executable
)