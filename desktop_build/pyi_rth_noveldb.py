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
    # A separate taskbar identity per variant, so pinned Full and Lite builds stay apart.
    try:
        import ctypes

        from _build_info import VARIANT

        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(f"NovelDB.Desktop.{VARIANT}")
    except Exception:
        pass
