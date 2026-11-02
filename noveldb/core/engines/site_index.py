"""Which sites each site engine lists, kept in one cache file for dynamic priority.

With dynamic priority on, a link only goes to the engines that list its site (the generic fallback
still comes last), so an unsupported engine is skipped instead of tried. The lists come from each
engine's own bundle (its manifest's sites, or the hosts and patterns its rules match) and from
FanFicFare's adapters; they're written to `engine-sites.json` in the cache folder and rebuilt when a
bundle changes (a daily update) or the FanFicFare version does.
"""

from __future__ import annotations

import json
import re
import threading
from typing import Dict, Optional
from urllib.parse import urlsplit

from noveldb.core import paths

CACHE_NAME = "engine-sites.json"
FORMAT = 1
_lock = threading.Lock()
_loaded: Optional[dict] = None


def _strip_www(host: str) -> str:
    return host[4:] if host.startswith("www.") else host


def _versions() -> Dict[str, str]:
    """A label per engine that changes when its site list may have changed."""
    from noveldb.core import engine_updates

    out = {}
    for engine in engine_updates.ENGINES:
        bundle = engine_updates.current(engine)
        if bundle is not None:
            try:
                out[engine.name] = f"{bundle.label}|{bundle.path.stat().st_mtime:.0f}"
            except OSError:
                out[engine.name] = bundle.label
    if "fanficfare" not in out:
        try:
            from importlib.metadata import version  # the installed copy, when no bundle is shipped

            out["fanficfare"] = version("FanFicFare")
        except Exception:
            pass
    return out


def _engine_sites(engine) -> dict:
    from noveldb.core import engine_updates

    bundle = engine_updates.current(engine)
    if bundle is None:
        return {"hosts": [], "patterns": []}
    if engine.sites_in_manifest:
        return {"hosts": sorted(engine_updates._manifest_hosts(tuple(bundle.manifest.get("sites") or ()))), "patterns": []}
    hosts, patterns = engine_updates._site_index(engine.name, str(bundle.path), bundle.path.stat().st_mtime)
    return {"hosts": sorted(_strip_www(h.lower()) for h in hosts), "patterns": [p.pattern for p in patterns]}


def _fanficfare_sites() -> dict:
    from noveldb.core.engines.fanficfare_engine import _prepare

    _prepare()
    from fanficfare import adapters

    hosts = set()
    for cls in getattr(adapters, "__class_list", []):
        try:
            domains = cls.getAcceptDomains() or [cls.getSiteDomain()]
        except Exception:
            continue
        hosts.update(_strip_www(str(d).lower()) for d in domains if d)
    return {"hosts": sorted(hosts), "patterns": []}


def build() -> dict:
    """Every engine's site list, read from the engines themselves now."""
    from noveldb.core import engine_updates

    engines = {}
    for engine in engine_updates.ENGINES:
        try:
            engines[engine.name] = _engine_sites(engine)
        except Exception:
            engines[engine.name] = {"hosts": [], "patterns": []}
    if not engines.get("gallerydl", {}).get("hosts"):
        try:
            from noveldb.core.engines.gallerydl_engine import manga_hosts
            engines["gallerydl"] = {"hosts": manga_hosts(), "patterns": []}
        except Exception:
            pass
    if not engines.get("fanficfare", {}).get("hosts"):
        try:
            engines["fanficfare"] = _fanficfare_sites()  # no bundle: the installed copy's adapters
        except Exception:
            pass
    return {"format": FORMAT, "versions": _versions(), "engines": engines}


def index(refresh: bool = False) -> dict:
    """The site lists: from the cache file when it matches the engines installed now, else rebuilt."""
    global _loaded
    with _lock:
        versions = _versions()
        if not refresh and _loaded is not None and _loaded.get("versions") == versions:
            return _loaded
        path = paths.cache_dir() / CACHE_NAME
        data = None
        if not refresh:
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                data = None
        if not data or data.get("format") != FORMAT or data.get("versions") != versions:
            data = build()
            try:
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            except OSError:
                pass
        for entry in data["engines"].values():
            entry["_hosts"] = frozenset(entry.get("hosts") or ())
            entry["_patterns"] = [re.compile(p) for p in entry.get("patterns") or () if _compiles(p)]
        _loaded = data
        return data


def _compiles(pattern: str) -> bool:
    try:
        re.compile(pattern)
        return True
    except re.error:
        return False


def supports(engine: str, url: str) -> bool:
    """The engine lists the URL's site (its host, a parent domain of it, or one of its patterns)."""
    entry = index()["engines"].get(engine)
    if not entry:
        return False
    netloc = (urlsplit(url).netloc or "").lower()
    host = _strip_www((urlsplit(url).hostname or "").lower())
    hosts = entry["_hosts"]
    labels = host.split(".")
    if any(".".join(labels[i:]) in hosts for i in range(max(1, len(labels) - 1))):
        return True
    return any(p.search(netloc) for p in entry["_patterns"])


def counts() -> Dict[str, int]:
    """How many sites each engine lists (for Settings)."""
    return {name: len(entry.get("hosts") or ()) + len(entry.get("patterns") or ())
            for name, entry in index()["engines"].items()}
