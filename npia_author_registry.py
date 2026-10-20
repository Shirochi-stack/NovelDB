"""Durable public author identities and search-coverage evidence for npia."""

import json
import os
from pathlib import Path
import tempfile
import time

from npia_author_catalog import author_id
from scripts import source_names


def load(path):
    if not Path(path).exists():
        return {"version": 1, "authors": {}}
    with open(path, encoding="utf-8") as stream:
        result = json.load(stream)
    if result.get("version") != 1 or not isinstance(result.get("authors"), dict):
        raise ValueError("invalid npia author registry; refusing to replace it")
    return result


def observe(registry, novels, now=None):
    now = time.time() if now is None else now
    observed = set()
    for novel in novels:
        owner = author_id(novel)
        if not owner:
            continue
        observed.add(owner)
        entry = registry["authors"].setdefault(owner, {
            "name": "", "names": [], "novel_ids": [], "first_seen": now,
        })
        name = novel.get("writer_nick") or novel.get("author") or ""
        if name:
            entry["name"] = name
            entry["names"] = sorted(set(entry["names"]) | {name})
        nid = str(novel.get("novel_no") or novel.get("id") or "")
        if nid.isdigit():
            entry["novel_ids"] = sorted(set(entry["novel_ids"]) | {nid}, key=int)
            unresolved = registry.get("unresolved_names", {})
            if name in unresolved and nid in unresolved[name]:
                unresolved[name].remove(nid)
                if not unresolved[name]:
                    del unresolved[name]
        entry["last_seen"] = now
    return observed


def plan_r19_requests(registry, rows, full=(), *, checked=None, now=None):
    """Prioritize public catalogs using evidence; names are candidates, not identities."""
    now = time.time() if now is None else now
    checked = checked or {}
    adults = {str(row[0]): row[2] for row in rows if len(row) > 11 and str(row[11]) == '19'}
    confirmed = {}
    for novel in full:
        if str(novel.get('age')) == '19':
            nid = str(novel.get('id', ''))
            if nid.isdigit():
                adults[nid] = novel.get('author', '')
                owner = author_id(novel)
                if owner:
                    confirmed[nid] = owner
    names = {}
    for nid, name in adults.items():
        if name:
            names.setdefault(name, set()).add(nid)
    targets = {}
    for owner, entry in registry.get('authors', {}).items():
        known = set(entry.get('novel_ids', [])) & adults.keys()
        for nid in known:
            confirmed[nid] = owner
        candidates = set(known)
        for name in set(entry.get('names', []) + [entry.get('name', '')]):
            named = names.get(name, set())
            if entry.get('catalog_status') == 'complete':
                # A completed catalog can disprove a name match. Unmatched
                # novels must fall back to a detail lookup, not get stranded.
                named = named & set(entry.get('catalog_novel_ids', []))
            candidates.update(named)
        if candidates:
            targets[owner] = candidates
    for nid, owner in confirmed.items():
        targets.setdefault(owner, set()).add(nid)
    candidate_ids = set().union(*targets.values()) if targets else set()
    groups = {}
    for nid, name in adults.items():
        if nid not in candidate_ids and now - checked.get(nid, 0) >= 7 * 86400:
            groups.setdefault(name or f'id:{nid}', []).append(nid)
    # One detail resolves an author candidate for the whole name group. The
    # subsequent public catalog, never the name match, verifies membership.
    details = []
    for ids in sorted(groups.values(), key=lambda ids: (-len(ids), min(checked.get(n, 0) for n in ids), min(map(int, ids)))):
        details.append(min(ids, key=lambda n: (checked.get(n, 0), int(n))))
    def author_priority(owner):
        entry = registry.get('authors', {}).get(owner, {})
        last = entry.get('last_catalog_checked', 0)
        # Backfill unchecked authors before refreshing already covered ones.
        # A prolific author must not consume the same budget every week.
        return (bool(entry.get('catalog_error')), bool(last), last, -len(targets[owner]), int(owner))
    authors = sorted(targets, key=author_priority)
    return dict(detail_ids=details, authors=authors, r19_total=len(adults),
                confirmed_ids=len(confirmed), candidate_ids=len(candidate_ids),
                unresolved_groups=len(groups))


def seed_catalog_names(registry, rows):
    """Keep legacy names without inventing IDs or treating names as unique identities."""
    assigned = {nid for entry in registry["authors"].values() for nid in entry["novel_ids"]}
    unresolved = registry.setdefault("unresolved_names", {})
    for name in list(unresolved):
        unresolved[name] = [nid for nid in unresolved[name] if nid not in assigned]
        if not unresolved[name]:
            del unresolved[name]
    for row in rows:
        if len(row) > 2 and row[2] and str(row[0]) not in assigned:
            ids = unresolved.setdefault(row[2], [])
            nid = str(row[0])
            if nid not in ids:
                ids.append(nid)


def record_search(registry, novels, now=None):
    novels = list(novels)
    owners = observe(registry, novels, now)
    registry["last_search"] = {
        "at": time.time() if now is None else now,
        "author_ids": sorted(owners, key=int),
        "names": sorted({n.get("writer_nick") or n.get("author") for n in novels
                         if n.get("writer_nick") or n.get("author")}),
        "novels_without_author_id": sum(author_id(n) is None for n in novels),
    }


def report_coverage(registry, search_ids):
    searched = set(registry.get("last_search", {}).get("author_ids", []))
    missing = sorted(set(registry["authors"]) - searched, key=int)
    checked = [entry for entry in registry["authors"].values() if entry.get("catalog_status") == "complete"]
    unresolved = registry.get("unresolved_names", {})
    missing_names = sorted(set(unresolved) - set(registry.get("last_search", {}).get("names", [])))
    catalog_ids = {nid for entry in checked for nid in entry.get("catalog_novel_ids", [])}
    report = dict(known_authors=len(registry["authors"]), search_authors=len(searched),
                  known_authors_missing_from_search=missing,
                  checked_catalog_novels_missing_from_search=sorted(catalog_ids - set(search_ids), key=int),
                  complete_author_catalogs=len(checked), unresolved_name_groups=len(unresolved),
                  unresolved_names_missing_from_search=missing_names)
    registry["coverage"] = report
    print(f"Author coverage: {len(searched)}/{len(registry['authors'])} known authors found by search; "
          f"{len(missing)} known authors absent; {len(catalog_ids - set(search_ids))} catalog novels absent. "
          f"{len(unresolved)} legacy name groups still need author IDs ({len(missing_names)} absent from search). "
          "Absence is a coverage gap, not proof of deletion.", flush=True)
    return report


def save(path, registry):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            temporary = Path(stream.name)
            source_names.dump(registry, stream, ensure_ascii=False, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None and temporary.exists():
            temporary.unlink()
