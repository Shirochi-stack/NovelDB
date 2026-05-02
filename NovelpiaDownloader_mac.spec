# -*- mode: python ; coding: utf-8 -*-
# Full macOS build — includes Playwright + Chromium + external novel support

import os
import sys
import glob
import subprocess

block_cipher = None

# --- Homebrew dylibs for WeasyPrint (PDF) on macOS ---
# Detect Homebrew prefix (differs between Intel and Apple Silicon)
brew_prefix = ''
try:
    brew_prefix = subprocess.check_output(
        ['brew', '--prefix'], text=True
    ).strip()
except Exception:
    # Fallback paths
    if os.path.exists('/opt/homebrew'):
        brew_prefix = '/opt/homebrew'  # Apple Silicon
    elif os.path.exists('/usr/local'):
        brew_prefix = '/usr/local'     # Intel

brew_lib = os.path.join(brew_prefix, 'lib') if brew_prefix else ''

binaries = []
if brew_lib and os.path.isdir(brew_lib):
    # Bundle essential dylibs for WeasyPrint / Pango / Cairo / GLib
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

# --- Playwright driver (node + JS package) ---
import playwright
_pw_dir = os.path.dirname(playwright.__file__)
_pw_driver = os.path.join(_pw_dir, 'driver')
if os.path.exists(_pw_driver):
    for root, dirs, files in os.walk(_pw_driver):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(root, _pw_dir)
            binaries.append((src, os.path.join('playwright', rel)))

# --- Bundle Chromium browsers from ms-playwright ---
# On macOS, browsers live in ~/Library/Caches/ms-playwright
_ms_pw_candidates = [
    os.path.join(os.path.expanduser('~'), 'Library', 'Caches', 'ms-playwright'),
    os.path.join(os.environ.get('PLAYWRIGHT_BROWSERS_PATH', ''), ''),
]
_ms_pw = None
for _candidate in _ms_pw_candidates:
    if _candidate and os.path.isdir(_candidate):
        _ms_pw = _candidate
        break

if _ms_pw:
    for entry in os.listdir(_ms_pw):
        if entry.startswith('chromium'):
            _chromium_dir = os.path.join(_ms_pw, entry)
            for root, dirs, files in os.walk(_chromium_dir):
                for f in files:
                    src = os.path.join(root, f)
                    rel = os.path.relpath(root, _ms_pw)
                    binaries.append((src, os.path.join('ms-playwright', rel)))

# Icon: expect icon.icns to be generated at build time from icon.ico
icon_file = 'icon.icns' if os.path.exists('icon.icns') else None

_datas = [('dpi_setup.py', '.')]
if icon_file:
    _datas.append(('icon.icns', '.'))
# External novel downloader JS files
for js in ['gm_stubs.js', 'bridge.js', 'rules-lib.js']:
    if os.path.exists(js):
        _datas.append((js, '.'))

a = Analysis(
    ['gui.py'],
    pathex=[],
    binaries=binaries,
    datas=_datas,
    hiddenimports=[
        'dpi_setup',
        'playwright',
        'playwright.sync_api',
        'playwright._impl',
        'playwright._impl._connection',
        'playwright._impl._driver',
        'playwright._impl._transport',
        'greenlet',
        'pyee',
    ],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
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
    name='ND42',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,  # Set via CLI: --target-arch x86_64 or arm64
    codesign_identity=None,
    entitlements_file=None,
    icon=icon_file,
)

# Create macOS .app bundle
app = BUNDLE(
    exe,
    name='ND42.app',
    icon=icon_file,
    bundle_identifier='com.novelpiadownloader.nd42',
    info_plist={
        'CFBundleName': 'ND42',
        'CFBundleDisplayName': 'Novelpia Downloader',
        'CFBundleVersion': '3.8',
        'CFBundleShortVersionString': '3.8',
        'NSHighResolutionCapable': True,
    },
)
