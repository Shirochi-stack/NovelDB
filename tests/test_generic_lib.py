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


def test_the_apps_ship_the_current_generic_search_page():
    # The page and its scripts; the bundles and site lists the daily jobs rebuild are left to them.
    import importlib.util

    spec = importlib.util.spec_from_file_location("build_mobile_catalog", ROOT / "scripts" / "build_mobile_catalog.py")
    build = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(build)
    files = build.expected()
    for name in ("index.html", "generic.js", "generic-lib.js", "catalog-app.js", "catalog-host.js", "download-icon.js"):
        for target in build.TARGETS:
            assert (target / name).read_bytes() == files[name],                 f"{target / name} is out of date; run python scripts/build_mobile_catalog.py"
