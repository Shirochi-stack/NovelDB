"""What each desktop build contains, and how its files are named.

Full: Playwright + bundled Chromium + QtWebEngine. Lite: QtWebEngine (embedded Catalog, Google
login), no Playwright. TurboLite: no Chromium at all - the Catalog opens in the system browser.
"""

from __future__ import annotations

import platform
import sys
from dataclasses import dataclass, field

# Never bundled: the old Tk window, other Qt bindings, the bot, test tools.
COMMON_EXCLUDES = (
    "tkinter", "_tkinter", "turtle", "idlelib",
    "PyQt5", "PyQt6", "PySide2",
    "discord", "pytest", "pytestqt", "_pytest", "IPython", "matplotlib",
    "gui", "external_dialog",
    "PySide6.Qt3DAnimation", "PySide6.Qt3DCore", "PySide6.Qt3DExtras", "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic", "PySide6.Qt3DRender", "PySide6.QtBluetooth", "PySide6.QtCharts",
    "PySide6.QtDataVisualization", "PySide6.QtGraphs", "PySide6.QtHelp", "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets", "PySide6.QtNfc", "PySide6.QtQuick3D", "PySide6.QtRemoteObjects",
    "PySide6.QtScxml", "PySide6.QtSensors", "PySide6.QtSerialBus", "PySide6.QtSerialPort",
    "PySide6.QtSpatialAudio", "PySide6.QtSql", "PySide6.QtStateMachine", "PySide6.QtTest",
    "PySide6.QtTextToSpeech", "PySide6.QtUiTools", "PySide6.QtDesigner", "PySide6.QtHttpServer",
    "PySide6.QtLocation", "PySide6.QtPdf", "PySide6.QtPdfWidgets", "PySide6.QtSvgWidgets",
    "PySide6.QtAxContainer", "PySide6.QtWebSockets",
)

PLAYWRIGHT_MODULES = ("playwright", "greenlet", "pyee")
WEBENGINE_MODULES = (
    "PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebEngineQuick",
    "PySide6.QtWebChannel", "PySide6.QtQml", "PySide6.QtQuick", "PySide6.QtQuickWidgets",
    "PySide6.QtPositioning",
)

COMMON_HIDDEN = (
    "dpi_setup", "requests", "requests.utils", "bs4", "bs4.element",
    "cryptography.hazmat.primitives.ciphers", "cryptography.hazmat.primitives.padding",
    "curl_cffi", "curl_cffi.requests", "rbooks_app_proxy", "kobo_web_proxy", "kobo_desktop_proxy",
    "psutil", "websocket", "noveldb.core.npia.job", "noveldb.core.external.job",
    "noveldb.login.webview_login", "PySide6.QtNetwork",
)

PLAYWRIGHT_HIDDEN = (
    "playwright", "playwright.sync_api", "playwright._impl", "playwright._impl._connection",
    "playwright._impl._driver", "playwright._impl._transport", "greenlet", "pyee",
)

WEBENGINE_HIDDEN = ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel")

# QtWebEngine resources that only matter for debugging (about 80 MB).
WEBENGINE_DROP = ("qtwebengine_devtools_resources", "qtwebengine_resources_debug", ".debug", ".pdb")
WEBENGINE_LOCALES = ("en-US", "en-GB", "ko", "ja", "zh-CN", "zh-TW")


@dataclass(frozen=True)
class Variant:
    name: str
    playwright: bool
    webengine: bool
    excludes: tuple = field(default=())
    hidden: tuple = field(default=())

    @property
    def all_excludes(self) -> tuple:
        out = list(COMMON_EXCLUDES) + list(self.excludes)
        if not self.playwright:
            out += PLAYWRIGHT_MODULES
        if not self.webengine:
            out += WEBENGINE_MODULES
        return tuple(dict.fromkeys(out))

    @property
    def all_hidden(self) -> tuple:
        out = list(COMMON_HIDDEN) + list(self.hidden)
        if self.playwright:
            out += PLAYWRIGHT_HIDDEN
        if self.webengine:
            out += WEBENGINE_HIDDEN
        return tuple(dict.fromkeys(out))

    def expected_features(self) -> dict:
        """What the smoke report must say for this build."""
        return {"playwright": self.playwright, "webengine": self.webengine}


VARIANTS = {
    "full": Variant("full", playwright=True, webengine=True),
    "lite": Variant("lite", playwright=False, webengine=True),
    "turbolite": Variant("turbolite", playwright=False, webengine=False),
}


def os_name() -> str:
    if sys.platform == "win32":
        return "windows"
    if sys.platform == "darwin":
        return "macos"
    return "linux"


def arch_name(machine: str = "") -> str:
    value = (machine or platform.machine()).lower()
    if value in ("amd64", "x86_64", "x64"):
        return "x86_64"
    if value in ("arm64", "aarch64"):
        return "arm64"
    return value or "unknown"


def package_ext(os_id: str, mode: str = "onefile") -> str:
    if os_id == "windows":
        return ".zip" if mode == "onedir" else ".exe"
    return ".dmg" if os_id == "macos" else ".AppImage"


def asset_name(variant: str, os_id: str, arch: str, ext: str, label: str = "") -> str:
    """e.g. NovelDB98_Lite-windows-x86_64_hotfix2.exe (label "" or "none" for plain names)."""
    import app_version

    suffix = f"_{label}" if label and label != "none" else ""
    return f"{app_version.app_name(variant)}-{os_id}-{arch}{suffix}{ext}"


def expected_assets(os_id: str, arches, variants, label: str = "") -> list:
    """Every file a release run for one OS must attach."""
    names = []
    for arch in arches:
        for variant in variants:
            names.append(asset_name(variant, os_id, arch, package_ext(os_id), label))
            if os_id == "windows" and variant == "full":
                names.append(asset_name(variant, os_id, arch, ".zip", label))
    return names
