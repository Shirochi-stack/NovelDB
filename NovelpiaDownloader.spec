# -*- mode: python ; coding: utf-8 -*-

block_cipher = None

a = Analysis(
    ['gui.py'],  # Main entry point script
    pathex=[],
    binaries=[],
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
    name='NovelpiaDownloader',
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