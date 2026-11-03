"""The mobile apps' generic fallback engine: one script, shipped unchanged by Android and iOS."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "scripts" / "generic" / "generic-lib.js"
COPIES = [
    ROOT / "android" / "sources" / "src" / "main" / "resources" / "com" / "shirochi" / "npia" / "sources"
    / "reader" / "generic" / "generic-lib.js",
    ROOT / "ios" / "NpiaKit" / "Sources" / "NpiaSources" / "Reader" / "Resources" / "generic" / "generic-lib.js",
]


def test_the_apps_ship_the_same_script():
    source = SOURCE.read_bytes()
    for copy in COPIES:
        assert copy.read_bytes() == source, f"{copy} differs from {SOURCE}; copy it again"


def test_the_script_has_the_entry_points_the_apps_call():
    text = SOURCE.read_text(encoding="utf-8")
    for name in ("window.__GEN_parseBook", "window.__GEN_parseChapter", "window.__GEN_READY",
                 "window.__GEN_API", "window.__GEN_LOAD_ERROR", "window.__GEN_transport"):
        assert name in text, name
    # A page with nothing to read says so as a miss, so the apps call the link unsupported.
    assert "noParser: true" in text
