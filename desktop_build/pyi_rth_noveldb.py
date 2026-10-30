"""PyInstaller runtime hook: environment the frozen app needs before anything imports Qt."""

import os
import sys

_base = getattr(sys, "_MEIPASS", "")
_browsers = os.path.join(_base, "ms-playwright")
if _base and os.path.isdir(_browsers):
    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", _browsers)

if sys.platform.startswith("linux"):
    # AppImages mount without the setuid sandbox helper, and Ubuntu 24.04 restricts the user
    # namespaces Chromium's sandbox needs; the Catalog is a plain website, so run unsandboxed.
    if os.environ.get("APPIMAGE") or not os.path.exists("/proc/self/ns/user"):
        os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
    else:
        try:
            with open("/proc/sys/kernel/apparmor_restrict_unprivileged_userns", "r") as handle:
                if handle.read().strip() == "1":
                    os.environ.setdefault("QTWEBENGINE_DISABLE_SANDBOX", "1")
        except OSError:
            pass

if sys.platform == "win32":
    import ctypes

    # Stop a stray DLL sitting next to the exe (a DWrite.dll or d3d11.dll left in the Downloads
    # folder, say) from being loaded instead of the real system one — that makes Qt fail to start
    # ("DLL load failed while importing QtWidgets"). Windows searches the exe's own directory and the
    # working directory before System32, so restrict the search to System32 plus our bundled folder,
    # and pre-bind by full path the graphics/text DLLs Qt and Chromium pull in by name.
    try:
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        if _base and hasattr(os, "add_dll_directory"):
            os.add_dll_directory(_base)
        LOAD_LIBRARY_SEARCH_SYSTEM32 = 0x00000800
        LOAD_LIBRARY_SEARCH_USER_DIRS = 0x00000400
        kernel32.SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32 | LOAD_LIBRARY_SEARCH_USER_DIRS)
    except Exception:
        pass
    try:
        _system32 = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
        for _name in (
            "DWrite.dll", "d3d11.dll", "dxgi.dll", "d3d12.dll", "d3d9.dll",
            "opengl32.dll", "glu32.dll", "vulkan-1.dll", "dcomp.dll", "dxva2.dll",
            "mf.dll", "mfplat.dll", "mfreadwrite.dll", "mfcore.dll",
            "uxtheme.dll", "dwmapi.dll", "version.dll", "winmm.dll",
            "bcrypt.dll", "bcryptprimitives.dll", "crypt32.dll", "secur32.dll",
            "userenv.dll", "propsys.dll", "profapi.dll", "netapi32.dll",
        ):
            _path = os.path.join(_system32, _name)
            if os.path.exists(_path):
                try:
                    ctypes.WinDLL(_path)
                except OSError:
                    pass
    except Exception:
        pass

    # A separate taskbar identity per variant, so pinned Full and Lite builds stay apart.
    try:
        from _build_info import VARIANT

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"NovelDB.Desktop.{VARIANT}")
    except Exception:
        pass
