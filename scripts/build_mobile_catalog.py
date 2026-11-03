"""Builds the mobile apps' copy of the Catalog's generic search page.

The page is the desktop's (noveldb/qt/catalog/generic/index.html and generic.js); on a phone its app
side is scripts/generic/catalog-app.js, which answers the page's messages inside the page with the
generic engine (scripts/generic/generic-lib.js) and the LNReader and HaruNeko bundles. The folder this
writes is served to the page as it is: Android from its assets, iOS from NpiaWebEngine's resources.

    python scripts/build_mobile_catalog.py          # write both apps' copies
    python scripts/build_mobile_catalog.py --check  # fail when a copy is out of date
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PAGE = ROOT / "noveldb" / "qt" / "catalog" / "generic"
TARGETS = [
    ROOT / "android" / "app" / "src" / "main" / "assets" / "catalog",
    ROOT / "ios" / "NpiaKit" / "Sources" / "NpiaWebEngine" / "Resources" / "catalog",
]
# Scripts in the order the page runs them: the link to the app, the app's download artwork, the
# engine's helpers, the app side, the LNReader plugins (generic.js lists them when it starts), then
# the page's own script, which says it is ready.
SCRIPTS = ["catalog-host.js", "download-icon.js", "generic-lib.js", "catalog-app.js", "lnreader-lib.js", "generic.js"]
FILES = {
    "generic.js": PAGE / "generic.js",
    "halgakos-loading.webp": PAGE / "halgakos-loading.webp",
    "icon-cart.webp": PAGE / "icon-cart.webp",
    "icon-mining.webp": PAGE / "icon-mining.webp",
    "generic-lib.js": ROOT / "scripts" / "generic" / "generic-lib.js",
    "catalog-app.js": ROOT / "scripts" / "generic" / "catalog-app.js",
    "catalog-host.js": ROOT / "scripts" / "generic" / "catalog-host.js",
    "lnreader-lib.js": ROOT / "lnreader" / "lnreader-lib.js",
    "haruneko-lib.js": ROOT / "haruneko" / "haruneko-lib.js",
    "haruneko-manifest.json": ROOT / "haruneko" / "manifest.json",
    "generic_sites.json": ROOT / "noveldb" / "core" / "catalog" / "generic_sites.json",
    "id_templates.json": ROOT / "noveldb" / "core" / "catalog" / "id_templates.json",
}


# A phone's width: two cards to a row instead of one (the desktop grid wants 172 px per card).
PHONE_CSS = """<style>
    @media (max-width: 520px) {
        #grid { grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 10px; }
        header { padding: 10px 10px 8px; }
        /* The search box on its own row, then every control on one row below it: the type menu
           takes what the buttons leave. */
        #form { gap: 6px; }
        #searchbox { flex-basis: 100%; }
        #form > button { flex: 0 0 auto; padding: 7px 10px; }
        #kind { flex: 1 1 0; min-width: 0; padding: 7px 6px; }
    }
</style>
"""


def page_html() -> str:
    html = (PAGE / "index.html").read_text(encoding="utf-8")
    tags = "".join(f'<script src="{name}"></script>\n' for name in SCRIPTS)
    if "</body>" not in html or "</head>" not in html:
        raise SystemExit("index.html has no </head> or </body>")
    return html.replace("</head>", PHONE_CSS + "</head>", 1).replace("</body>", tags + "</body>", 1)


def download_icon() -> str:
    """The app's download artwork (the one catalog-bridge.js shows) for the page's Download buttons."""
    import json
    import re

    text = (ROOT / "noveldb" / "qt" / "catalog" / "catalog-bridge.js").read_text(encoding="utf-8")
    found = re.search(r"DOWNLOAD_ICON = '(data:image/[^']+)'", text)
    return "window.NovelDBDownloadIcon = " + json.dumps(found.group(1) if found else "") + ";\n"


def expected() -> dict:
    out = {"index.html": page_html().encode("utf-8"), "download-icon.js": download_icon().encode("utf-8")}
    for name, source in FILES.items():
        out[name] = source.read_bytes()
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="only report copies that are out of date")
    args = parser.parse_args()
    files = expected()
    stale = []
    for target in TARGETS:
        for name, data in files.items():
            path = target / name
            if path.exists() and path.read_bytes() == data:
                continue
            stale.append(path)
            if not args.check:
                target.mkdir(parents=True, exist_ok=True)
                path.write_bytes(data)
    if args.check and stale:
        print("Out of date (run python scripts/build_mobile_catalog.py):")
        for path in stale:
            print(f"  {path.relative_to(ROOT)}")
        return 1
    print(f"{'Checked' if args.check else 'Wrote'} {len(files)} files in {len(TARGETS)} folders"
          + (f"; {len(stale)} updated" if stale and not args.check else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
