"""Single source of truth for Novelpia Downloader build/version names."""

VERSION_NUMBER = 47
VERSION = str(VERSION_NUMBER)

APP_NAME = f"ND{VERSION}"
APP_NAME_LITE = f"{APP_NAME}_Lite"

RELEASE_TAG = f"v{VERSION}"
BUNDLE_ID = f"com.novelpiadownloader.nd{VERSION}"
BUNDLE_ID_LITE = f"{BUNDLE_ID}lite"

# macOS CFBundle* fields accept dotted strings, but keeping them tied to the
# release number avoids another value to remember during version bumps.
BUNDLE_VERSION = VERSION
