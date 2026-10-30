"""Single source of truth for NovelDB build/version names."""

VERSION_NUMBER = 99
VERSION = str(VERSION_NUMBER)

APP_NAME = f"NovelDB{VERSION}"
APP_NAME_LITE = f"{APP_NAME}_Lite"

RELEASE_TAG = f"v{VERSION}"
BUNDLE_ID = f"com.npiadownloader.nd{VERSION}"
BUNDLE_ID_LITE = f"{BUNDLE_ID}lite"

# macOS CFBundle* fields accept dotted strings, but keeping them tied to the
# release number avoids another value to remember during version bumps.
BUNDLE_VERSION = VERSION

APP_NAME_TURBOLITE = f"{APP_NAME}_TurboLite"
BUNDLE_ID_TURBOLITE = f"{BUNDLE_ID}turbolite"
APP_DISPLAY_NAME = "NovelDB"

# Desktop build variants: Full (Playwright + Chromium + QtWebEngine), Lite (QtWebEngine, no
# Playwright) and TurboLite (no Chromium at all).
VARIANTS = ("full", "lite", "turbolite")
_SUFFIX = {"full": "", "lite": "_Lite", "turbolite": "_TurboLite"}
_DISPLAY = {"full": "", "lite": " Lite", "turbolite": " TurboLite"}


def app_name(variant: str = "full") -> str:
    """File name stem of a build, e.g. NovelDB98_Lite."""
    return APP_NAME + _SUFFIX[variant]


def bundle_id(variant: str = "full") -> str:
    return BUNDLE_ID + _SUFFIX[variant].lstrip("_").lower()


def display_name(variant: str = "full") -> str:
    """Name shown in window titles, the macOS menu bar and the Linux launcher."""
    return APP_DISPLAY_NAME + _DISPLAY[variant]
