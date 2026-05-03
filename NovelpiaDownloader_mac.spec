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
# Added as datas (not binaries) to avoid install_name_tool issues
# with the bundled Node binary on arm64.
import playwright
_pw_dir = os.path.dirname(playwright.__file__)
_pw_driver = os.path.join(_pw_dir, 'driver')
_pw_datas = []
if os.path.exists(_pw_driver):
    for root, dirs, files in os.walk(_pw_driver):
        for f in files:
            src = os.path.join(root, f)
            rel = os.path.relpath(root, _pw_dir)
            _pw_datas.append((src, os.path.join('playwright', rel)))

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

# Icon: expect icon.icns to be generated at build time from icon.ico
icon_file = 'icon.icns' if os.path.exists('icon.icns') else None

_datas = [('dpi_setup.py', '.')]
if icon_file:
    _datas.append(('icon.icns', '.'))
# External novel downloader JS files
for js in ['gm_stubs.js', 'bridge.js', 'rules-lib.js']:
    if os.path.exists(js):
        _datas.append((js, '.'))
# Merge Playwright driver files collected earlier
_datas.extend(_pw_datas)

if _ms_pw:
    for entry in os.listdir(_ms_pw):
        if entry.startswith('chromium'):
            _chromium_dir = os.path.join(_ms_pw, entry)
            for root, dirs, files in os.walk(_chromium_dir):
                for f in files:
                    src = os.path.join(root, f)
                    rel = os.path.relpath(root, _ms_pw)
                    # Add as datas (not binaries) so PyInstaller copies
                    # them as-is without running install_name_tool.
                    # Chromium's __LINKEDIT segment is non-standard and
                    # causes install_name_tool to fail on arm64.
                    _datas.append((src, os.path.join('ms-playwright', rel)))



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

# ── Post-Analysis fix for arm64 ──────────────────────────────────────
# PyInstaller auto-discovers Chromium and Playwright node binaries via
# import tracing and adds them to a.binaries.  On arm64, install_name_tool
# fails on Chromium's non-standard __LINKEDIT segment.
# Move these entries from a.binaries → a.datas so they are copied as-is.
#
# We must be aggressive: TOC dest names can be bare filenames like
# "Google Chrome for Testing Framework" with no path prefix.
_chromium_keywords = (
    'ms-playwright', 'chromium', 'playwright/driver', 'playwright\\driver',
    'Chrome for Testing', 'chrome-mac', 'Google Chrome',
    'playwright/', 'playwright\\',
)
_move_to_datas = []
_keep_binaries = []
for item in a.binaries:
    # Check ALL tuple fields for any keyword match
    combined = ' '.join(str(field) for field in item)
    if any(kw in combined for kw in _chromium_keywords):
        _move_to_datas.append(item)
    else:
        _keep_binaries.append(item)

if _move_to_datas:
    print(f"[spec] Moved {len(_move_to_datas)} Playwright/Chromium entries"
          f" from binaries → datas to avoid install_name_tool failures.")
    for moved in _move_to_datas[:5]:
        print(f"  → {moved[0]}")
    if len(_move_to_datas) > 5:
        print(f"  ... and {len(_move_to_datas) - 5} more.")
a.binaries = _keep_binaries
a.datas += _move_to_datas
# ─────────────────────────────────────────────────────────────────────

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

# Use COLLECT (onedir) instead of onefile.
# In onefile mode, PyInstaller's PKG stage still runs install_name_tool
# on Mach-O binaries found inside datas, which fails on Chromium's
# non-standard __LINKEDIT segment.  Onedir avoids this entirely by
# copying files as-is to the output directory.
exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
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

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='ND42',
)

# Create macOS .app bundle
app = BUNDLE(
    coll,
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
