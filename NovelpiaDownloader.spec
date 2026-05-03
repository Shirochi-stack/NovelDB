# -*- mode: python ; coding: utf-8 -*-

import os
import glob
import sys

spec_dir = os.path.dirname(os.path.abspath(__file__))
if spec_dir not in sys.path:
    sys.path.insert(0, spec_dir)

from app_version import APP_NAME

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

# --- Playwright driver (node.exe + JS package) + Chromium browser ---
# Bundle everything so users never need to run `playwright install`.
import playwright
_pw_dir = os.path.dirname(playwright.__file__)
_pw_driver = os.path.join(_pw_dir, 'driver')
if os.path.exists(_pw_driver):
    for root, dirs, files in os.walk(_pw_driver):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(root, _pw_dir)
            binaries.append((src, os.path.join('playwright', rel)))

# Bundle Chromium browsers from ms-playwright
# Playwright uses chromium_headless_shell for headless=True and
# chromium for headless=False (Enter Browser). Bundle both.
_ms_pw = os.path.join(os.environ.get('LOCALAPPDATA', ''), 'ms-playwright')
if os.path.exists(_ms_pw):
    for entry in os.listdir(_ms_pw):
        if entry.startswith('chromium'):
            _chromium_dir = os.path.join(_ms_pw, entry)
            for root, dirs, files in os.walk(_chromium_dir):
                for f in files:
                    src = os.path.join(root, f)
                    rel = os.path.relpath(root, _ms_pw)
                    binaries.append((src, os.path.join('ms-playwright', rel)))


a = Analysis(
    ['gui.py'],  # Main entry point script
    pathex=[],
    binaries=binaries,
    datas=[
        ('icon.ico', '.'),          # Include the icon file in the root of the bundle
        ('dpi_setup.py', '.'),      # Ship source copy alongside the exe so
                                    # config.json ↔ dpi_setup stays introspectable.
        # External novel downloader (novel-downloader JS rule bundle)
        ('gm_stubs.js', '.'),       # Tampermonkey/Greasemonkey API stubs
        ('bridge.js', '.'),         # JS bridge for book/chapter parsing
        ('rules-lib.js', '.'),      # Compiled novel-downloader rule bundle
    ],
    hiddenimports=[
        'dpi_setup',                # Ensure dpi_setup is always bundled,
                                    # even if only imported conditionally.
        'playwright',
        'playwright.sync_api',
        'playwright._impl',
        'playwright._impl._connection',
        'playwright._impl._driver',
        'playwright._impl._transport',
        'greenlet',                 # Required by playwright's sync API
        'pyee',                     # Event emitter used by playwright
    ],
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
    name=APP_NAME,
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
