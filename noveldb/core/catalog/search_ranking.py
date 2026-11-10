"""Measured source responsiveness and a bounded automatic search plan."""

import json
import threading
import time
from pathlib import Path

from noveldb.core.engines.generic import site_of

PROFILE = Path(__file__).with_name("search_profile.json")
MAX_AUTOMATIC = 12
MAX_AGE = 30 * 86400
_lock = threading.RLock()
_observed = {}


def profile():
    try:
        data = json.loads(PROFILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    with _lock:
        return {"preferred": data.get("preferred", []), "measurements": {**data.get("measurements", {}), **_observed}}


def score(host, data=None):
    data = data or profile()
    host = site_of(host)
    measured = data.get("measurements", {}).get(host)
    if measured and time.time() - measured.get("at", 0) < (MAX_AGE if measured.get("ok") else 86400):
        return measured.get("ms", 12000) if measured.get("ok") else 1000000
    preferred = data.get("preferred", [])
    return 6000 + preferred.index(host) if host in preferred else 100000


def ranked(sites, automatic=False, limit=MAX_AUTOMATIC):
    data = profile()
    ordered = sorted(sites, key=lambda site: (score(site["host"], data), site.get("rank", 1000000), site["host"]))
    if automatic:
        ordered = [site for site in ordered if score(site["host"], data) < 12000][:limit]
    return ordered


def record(host, ms, ok):
    with _lock:
        _observed[site_of(host)] = {"ms": int(ms), "ok": bool(ok and ms < 12000), "at": time.time()}
