# -*- mode: python ; coding: utf-8 -*-
# One spec for every desktop build. scripts/build_desktop.py sets:
#   NOVELDB_VARIANT       full | lite | turbolite
#   NOVELDB_MODE          onefile (Windows exe) | onedir (zip folder, macOS .app, Linux AppImage)
#   NOVELDB_PW_BROWSERS   isolated Playwright browser folder (Full only; exactly one revision)
#   NOVELDB_TARGET_ARCH   macOS only: arm64 | x86_64

import glob
import os
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(SPECPATH, os.pardir))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import app_version
from desktop_build.variants import VARIANTS, WEBENGINE_DROP, WEBENGINE_LOCALES

VARIANT = VARIANTS[os.environ.get("NOVELDB_VARIANT", "full")]
MODE = os.environ.get("NOVELDB_MODE", "onefile" if sys.platform == "win32" else "onedir")
NAME = app_version.app_name(VARIANT.name)
IS_WIN, IS_MAC = sys.platform == "win32", sys.platform == "darwin"
IS_LINUX = not IS_WIN and not IS_MAC


def here(*parts):
    return os.path.join(ROOT, *parts)


# -- data files -------------------------------------------------------------------------------
datas = [(here("icon.ico"), "."), (here("dpi_setup.py"), "."), (here("assets", "app-icon.png"), "assets")]
if VARIANT.webengine:  # the Catalog page's bridge script (shared with the mobile apps)
    datas.append((here("noveldb", "qt", "catalog", "catalog-bridge.js"), os.path.join("noveldb", "qt", "catalog")))
for name in ("gm_stubs.js", "bridge.js", "rules-lib.js"):
    datas.append((here(name), "."))
for name in ("webtoepub-lib.js", "manifest.json", "LICENSE.md"):  # the WebToEpub engine
    datas.append((here("webtoepub", name), "webtoepub"))
datas += [
    (here("data", "fonts"), os.path.join("data", "fonts")),
    (here("data", "zh_char_frequency.txt"), "data"),
    (here("data", "qdn_verified_maps.json"), "data"),
]
if IS_WIN:  # floo VIP image reader renders reference glyphs with Windows GDI+
    datas.append((here("data", "floo_glyph_atlas.ps1"), "data"))
icns = here("icon.icns")
if IS_MAC and os.path.exists(icns):
    datas.append((icns, "."))

# -- native libraries for WeasyPrint (PDF) ----------------------------------------------------
binaries = []
if IS_WIN:
    gtk = os.environ.get("GTK_FOLDER", "")
    for candidate in (os.path.join(gtk, "bin") if gtk else "", r"C:\msys64\ucrt64\bin", r"C:\msys64\mingw64\bin"):
        # The first folder holding a coherent Pango set (as downloader_core looks it up at runtime).
        if candidate and all(os.path.isfile(os.path.join(candidate, dll))
                             for dll in ("libpango-1.0-0.dll", "libharfbuzz-0.dll", "libgobject-2.0-0.dll")):
            # Only what WeasyPrint loads and the DLLs those need (about 15 MB, not the whole folder).
            from PyInstaller.depend import bindepend

            found, todo = {}, ["libgobject-2.0-0.dll", "libpango-1.0-0.dll", "libpangoft2-1.0-0.dll",
                               "libharfbuzz-0.dll", "libharfbuzz-subset-0.dll", "libfontconfig-1.dll"]
            while todo:
                name = todo.pop()
                path = os.path.join(candidate, name)
                if name.lower() in found or not os.path.isfile(path):
                    continue  # already taken, or a Windows system DLL
                found[name.lower()] = path
                todo.extend(os.path.basename(dep[0]) for dep in bindepend.get_imports(path))
            binaries += [(path, ".") for path in found.values()]
            break
    else:
        raise SystemExit("No Pango DLLs found for PDF output: install MSYS2 pango or set GTK_FOLDER.")
elif IS_MAC:
    try:
        prefix = subprocess.check_output(["brew", "--prefix"], text=True).strip()
    except Exception:
        prefix = "/opt/homebrew" if os.path.exists("/opt/homebrew") else "/usr/local"
    for pattern in ("libpango*", "libcairo*", "libgobject*", "libglib*", "libgio*", "libgmodule*",
                    "libgdk_pixbuf*", "libffi*", "libharfbuzz*", "libfontconfig*", "libfreetype*",
                    "libpng*", "libintl*", "librsvg*", "libxml2*", "libfribidi*", "libpixman*",
                    "libgraphite2*", "libpcre2*", "libjpeg*", "libtiff*", "libzstd*", "liblzma*"):
        for lib in glob.glob(os.path.join(prefix, "lib", pattern + ".dylib")):
            if os.path.isfile(lib) and not os.path.islink(lib):
                binaries.append((lib, "."))
else:
    # Pango and friends are loaded by name with dlopen, so PyInstaller cannot see them. GL, NSS
    # and libstdc++ stay with the system.
    wanted = ("libpango-1.0.so.0", "libpangoft2-1.0.so.0", "libharfbuzz.so.0", "libharfbuzz-subset.so.0",
              "libfontconfig.so.1", "libfribidi.so.0", "libthai.so.0", "libdatrie.so.1", "libgraphite2.so.3",
              "libxcb-cursor.so.0")
    for directory in ("/usr/lib/x86_64-linux-gnu", "/usr/lib/aarch64-linux-gnu", "/usr/lib64", "/usr/lib"):
        for name in wanted:
            path = os.path.join(directory, name)
            if os.path.exists(path) and not any(os.path.basename(b[0]) == name for b in binaries):
                binaries.append((os.path.realpath(path), "."))

# -- Playwright driver and Chromium (Full) ------------------------------------------------------
browser_datas = []
if VARIANT.playwright:
    import playwright

    pw_dir = os.path.dirname(playwright.__file__)
    for root, _dirs, files in os.walk(os.path.join(pw_dir, "driver")):
        for name in files:
            browser_datas.append((os.path.join(root, name), os.path.join("playwright", os.path.relpath(root, pw_dir))))
    browsers = os.environ.get("NOVELDB_PW_BROWSERS", "")
    if not browsers or not os.path.isdir(browsers):
        raise SystemExit("Full builds need NOVELDB_PW_BROWSERS (run scripts/build_desktop.py).")
    revisions = [entry for entry in os.listdir(browsers) if entry.startswith(("chromium-", "chromium_headless_shell-"))]
    for prefix in ("chromium-", "chromium_headless_shell-"):
        if not any(entry.startswith(prefix) for entry in revisions):
            raise SystemExit(f"Missing Playwright browser {prefix}* in {browsers}")
    for entry in revisions:
        for root, _dirs, files in os.walk(os.path.join(browsers, entry)):
            for name in files:
                browser_datas.append((os.path.join(root, name), os.path.join("ms-playwright", os.path.relpath(root, browsers))))

a = Analysis(
    [here("noveldb_app.py")],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=list(VARIANT.all_hidden) + ["_build_info"],
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[here("desktop_build", "pyi_rth_noveldb.py")],
    excludes=list(VARIANT.all_excludes),
    noarchive=False,
)

# Drop QtWebEngine debugging resources and locales nobody reads.
def _keep(entry):
    dest = entry[0].replace("\\", "/")
    if any(token in dest for token in WEBENGINE_DROP):
        return False
    if "qtwebengine_locales/" in dest:
        return os.path.splitext(os.path.basename(dest))[0] in WEBENGINE_LOCALES
    if "/qml/" in dest.lower():  # QML modules: the widgets-based web view never loads them
        return False
    if "pyside6/translations/" in dest.lower():  # Qt's own strings: keep the app's languages
        stem = os.path.splitext(os.path.basename(dest))[0].lower()
        return stem.endswith(("_en", "_ko", "_ja", "_zh_cn", "_zh_tw"))
    return True


if not VARIANT.webengine:
    # Without QtWebEngine nothing needs Qt Quick/QML (pulled in by the virtual keyboard plugin) or
    # the software OpenGL fallback.
    _webengine_only = ("qt6quick", "qt6qml", "qt6virtualkeyboard", "qtvirtualkeyboard", "opengl32sw",
                       "qt6webengine", "qtwebengine", "qt6pdf", "qt6opengl", "qt6positioning", "qt6webchannel")
    _base_keep = _keep

    def _keep(entry):
        name = os.path.basename(entry[0]).lower()
        return _base_keep(entry) and not name.startswith(_webengine_only) and "/qml/" not in entry[0].replace("\\", "/").lower()


a.datas = [entry for entry in a.datas if _keep(entry)]
a.binaries = [entry for entry in a.binaries if _keep(entry)]
# The Playwright hook may list the driver's node binary as a binary; copy it untouched instead.
_driver = [entry for entry in a.binaries if "playwright/driver" in entry[0].replace("\\", "/")]
a.binaries = [entry for entry in a.binaries if entry not in _driver]
a.datas += [(dest, src, "DATA") for dest, src, _kind in _driver]
# Browser files are copied as they are: install_name_tool and codesign choke on Chromium.
a.datas += [(os.path.join(dest_dir, os.path.basename(src)), src, "DATA") for src, dest_dir in browser_datas]

pyz = PYZ(a.pure, a.zipped_data)
common = dict(
    name=NAME,
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=os.environ.get("NOVELDB_TARGET_ARCH") or None,
    codesign_identity=None,
    entitlements_file=None,
    icon=here("icon.ico") if IS_WIN else (icns if IS_MAC and os.path.exists(icns) else None),
)

if MODE == "onefile":
    exe = EXE(pyz, a.scripts, a.binaries, a.zipfiles, a.datas, [], runtime_tmpdir=None, **common)
else:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, **common)
    coll = COLLECT(exe, a.binaries, a.zipfiles, a.datas, strip=False, upx=False, name=NAME)
    if IS_MAC:
        app = BUNDLE(
            coll,
            name=f"{NAME}.app",
            icon=icns if os.path.exists(icns) else None,
            bundle_identifier=app_version.bundle_id(VARIANT.name),
            version=app_version.BUNDLE_VERSION,
            info_plist={
                "CFBundleName": app_version.display_name(VARIANT.name),
                "CFBundleDisplayName": app_version.display_name(VARIANT.name),
                "CFBundleShortVersionString": app_version.BUNDLE_VERSION,
                "CFBundleVersion": app_version.BUNDLE_VERSION,
                "NSHighResolutionCapable": True,
                "NSRequiresAquaSystemAppearance": False,
                "LSMinimumSystemVersion": "13.0",  # PySide6 6.11 wheels need macOS 13
            },
        )
