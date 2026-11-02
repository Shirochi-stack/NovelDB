"""Import every lightnovel-crawler source in an assembled bundle folder and print a JSON index.

Run by scripts/update_lncrawl.py in a fresh interpreter: `python index_sources.py <folder>`. Prints
{"crawlers": [{file, name, base_urls, search, manga, mtl, language, disabled}], "failed": {file: error}}.
"""

import importlib
import importlib.util
import inspect
import json
import logging
import sys
from pathlib import Path


def module_for(relative: str) -> str:
    """The module name a source file loads as (file names like `novelbin.net.py` have dots)."""
    parts = relative[:-3].split("/")
    return ".".join(parts[:-1] + [parts[-1].replace(".", "_").replace("-", "_")])


def load(folder: Path, relative: str):
    """Import the source file at `relative` (the app's lncrawl_engine loads them the same way)."""
    name = module_for(relative)
    if name in sys.modules:
        return sys.modules[name]
    spec = importlib.util.spec_from_file_location(name, folder / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(name, None)
        raise
    return module


def can_search(cls, crawler, legacy) -> bool:
    """The crawler answers search queries: it says so, or it implements a search of its own."""
    if getattr(cls, "can_search", False):
        return True
    if issubclass(cls, legacy):
        return cls.search_novel is not legacy.search_novel
    return cls.search is not crawler.search and getattr(cls, "build_search_url", None) is not None


def main() -> int:
    folder = Path(sys.argv[1]).resolve()
    sys.path.insert(0, str(folder))
    logging.disable(logging.CRITICAL)
    from lncrawl.core.crawler import Crawler
    from lncrawl.core.legacy import LegacyCrawler

    crawlers, failed = [], {}
    for path in sorted((folder / "sources").rglob("*.py")):
        if path.name.startswith("_"):
            continue
        relative = path.relative_to(folder).as_posix()
        module_name = module_for(relative)
        try:
            module = load(folder, relative)
        except BaseException as e:  # a source that cannot load is left out, whatever it raised
            failed[relative] = f"{type(e).__name__}: {e}"[:300]
            continue
        for name, cls in inspect.getmembers(module, inspect.isclass):
            if cls.__module__ != module_name or not issubclass(cls, Crawler) or cls.__dict__.get("is_template"):
                continue
            if inspect.isabstract(cls):
                continue
            base = cls.base_url
            urls = [base] if isinstance(base, str) else list(base or [])
            urls = [str(url) for url in urls if url]
            if not urls:
                continue
            crawlers.append({
                "file": relative,
                "name": name,
                "base_urls": urls,
                "search": can_search(cls, Crawler, LegacyCrawler),
                "manga": bool(getattr(cls, "has_manga", False)),
                "mtl": bool(getattr(cls, "has_mtl", False)),
                "language": str(getattr(cls, "language", "") or ""),
                "disabled": bool(getattr(cls, "is_disabled", False)),
            })
    json.dump({"crawlers": crawlers, "failed": failed}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
