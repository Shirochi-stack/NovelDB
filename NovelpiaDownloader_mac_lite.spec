# -*- mode: python ; coding: utf-8 -*-
# ND42_Lite macOS — No Playwright/Chromium bundled (smaller app, no external novel support)

import os
import sys
import glob
import subprocess

block_cipher = None

# --- Homebrew dylibs for WeasyPrint (PDF) on macOS ---
brew_prefix = ''
try:
    brew_prefix = subprocess.check_output(
        ['brew', '--prefix'], text=True
    ).strip()
except Exception:
    if os.path.exists('/opt/homebrew'):
        brew_prefix = '/opt/homebrew'
    elif os.path.exists('/usr/local'):
        brew_prefix = '/usr/local'

brew_lib = os.path.join(brew_prefix, 'lib') if brew_prefix else ''

binaries = []
if brew_lib and os.path.isdir(brew_lib):
    dylib_patterns = [
        'libpango*.dylib',
        'libcairo*.dylib',
        'libgobject*.dylib',
        'libglib*.dylib',
        'libgio*.dylib',
        'libgdk_pixbuf*.dylib',
        'libffi*.dylib',
        'libharfbuzz*.dylib',
        'libfontconfig*.dylib',
        'libfreetype*.dylib',
        'libpng*.dylib',
        'libintl*.dylib',
        'librsvg*.dylib',
        'libxml2*.dylib',
        'libfribidi*.dylib',
        'libpixman*.dylib',
    ]
    for pattern in dylib_patterns:
        for dylib in glob.glob(os.path.join(brew_lib, pattern)):
            if os.path.isfile(dylib) and not os.path.islink(dylib):
                binaries.append((dylib, '.'))

icon_file = 'icon.icns' if os.path.exists('icon.icns') else None

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=binaries,
    datas=(
        [('icon.icns', '.'), ('dpi_setup.py', '.')]
        if icon_file else [('dpi_setup.py', '.')]
    ),
    hiddenimports=[
        'dpi_setup',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[
        'playwright',
        'greenlet',
        'pyee',
    ],
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
    name='ND42_Lite',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

app = BUNDLE(
    exe,
    name='ND42_Lite.app',
    icon=icon_file,
    bundle_identifier='com.novelpiadownloader.nd42lite',
    info_plist={
        'CFBundleName': 'ND42_Lite',
        'CFBundleDisplayName': 'Novelpia Downloader Lite',
        'CFBundleVersion': '3.8',
        'CFBundleShortVersionString': '3.8',
        'NSHighResolutionCapable': True,
    },
)
