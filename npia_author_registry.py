"""Durable public author identities and search-coverage evidence for npia."""

import json
import os
from pathlib import Path
import tempfile
import time

from npia_author_catalog import author_id
from scripts import source_names

DELETED_IDENTITY_REASON = 'site reports this novel has been deleted'


def is_deleted(novel):
    tags = novel.get('tags', novel.get('novel_genre', [])) or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = [tags]
    return isinstance(tags, (list, tuple, set)) and any(str(tag).strip().casefold() == 'deleted' for tag in tags)


def record_live_novels(registry, novels, *, now=None):
    """Only fresh source listings can overturn a confirmed deletion."""
    now = time.time() if now is None else now
    previous = registry.get('catalog_deleted_novel_ids', [])
    deleted = set(previous)
    for novel in novels:
        nid = str(novel.get('id') or novel.get('novel_no') or '')
        unavailable = registry.get('r19_identity_unavailable', {}).get(nid, {})
        if nid.isdigit() and not is_deleted(novel) and unavailable.get('checked', 0) <= now:
            deleted.discard(nid)
            registry.get('r19_identity_unavailable', {}).pop(nid, None)
    if len(deleted) != len(previous) or 'catalog_deleted_novel_ids' not in registry:
        registry['catalog_deleted_novel_ids'] = sorted(deleted, key=int)


def is_r19(novel):
    """Use the age field or explicit age-rating tags, never a genre inference."""
    try:
        if int(novel.get('novel_age', novel.get('age', 0)) or 0) >= 19:
            return True
    except (ValueError, TypeError):
        pass
    tags = novel.get('tags', novel.get('novel_genre', [])) or []
    if isinstance(tags, str):
        try:
            tags = json.loads(tags)
        except ValueError:
            tags = [tags]
    if not isinstance(tags, list):
        return False
    markers = {'r19', 'r-19', '19+', '19금', '성인', 'adult'}
    return any(str(tag).strip().lstrip('#').casefold() in markers for tag in tags)


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
            entry["names"] = sorted(set(entry.get("names", [])) | {name})
        nid = str(novel.get("novel_no") or novel.get("id") or "")
        if nid.isdigit():
            entry["novel_ids"] = sorted(set(entry.get("novel_ids", [])) | {nid}, key=int)
            if is_r19(novel):
                entry['r19_novel_ids'] = sorted(set(entry.get('r19_novel_ids', [])) | {nid}, key=int)
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
    adults = {str(row[0]): row[2] for row in rows if len(row) > 2 and
              is_r19({'age': row[11] if len(row) > 11 else 0, 'tags': row[4] if len(row) > 4 else []})}
    confirmed = {}
    for novel in full:
        if is_r19(novel):
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


def refresh_r19(registry, rows=(), full=(), now=None, *, live=()):
    """Build the persisted R19 subset locally; name matches never prove identity."""
    now = time.time() if now is None else now
    full = list(full)
    rows = list(rows)
    deleted = set(registry.get('catalog_deleted_novel_ids', []))
    for novel in full:
        nid = str(novel.get('id') or novel.get('novel_no') or '')
        if is_deleted(novel) and nid.isdigit():
            deleted.add(nid)
    for row in rows:
        if len(row) > 4:
            if is_deleted({'tags': row[4]}):
                deleted.add(str(row[0]))
            else:
                deleted.discard(str(row[0]))
    registry['catalog_deleted_novel_ids'] = sorted(deleted, key=int)
    record_live_novels(registry, live, now=now)
    observe(registry, full, now)
    names = {name: set(ids) for name, ids in registry.get('r19', {}).get('known_names', {}).items()}
    unnamed = set(registry.get('r19', {}).get('unnamed_novel_ids', []))
    adults = {nid: name for name, ids in names.items() for nid in ids}
    adults.update({nid: '' for nid in unnamed if nid not in adults})
    for row in rows:
        if len(row) > 2 and is_r19({'age': row[11] if len(row) > 11 else 0,
                                   'tags': row[4] if len(row) > 4 else []}):
            adults[str(row[0])] = str(row[2] or '')
    for novel in full:
        if is_r19(novel):
            nid = str(novel.get('id') or novel.get('novel_no') or '')
            if nid.isdigit():
                adults[nid] = str(novel.get('author') or novel.get('writer_nick') or '')
    # Verified R19 evidence survives partial/no-auth results in later runs.
    for entry in registry['authors'].values():
        for nid in entry.get('r19_novel_ids', []):
            adults.setdefault(nid, entry.get('name', ''))
    names = {}
    for nid, name in adults.items():
        if name:
            names.setdefault(name, set()).add(nid)
    compact = [[int(nid), '', name] + [0]*8 + [19] for nid, name in adults.items()]
    plan = plan_r19_requests(registry, compact, now=now)
    selected, covered = {}, set()
    aliases = {}
    for owner, entry in registry['authors'].items():
        for name in set(entry.get('names', []) + [entry.get('name', '')]):
            if name in names:
                aliases.setdefault(name, set()).add(owner)
    for owner in plan['authors']:
        entry = registry['authors'][owner]
        verified = (set(entry.get('novel_ids', [])) | set(entry.get('r19_novel_ids', []))) & adults.keys()
        if verified:
            entry['r19_novel_ids'] = sorted(verified, key=int)
        matched_names = set(entry.get('names', []) + [entry.get('name', '')]) & names.keys()
        candidates = set(verified)
        for name in matched_names:
            matches = names[name]
            if entry.get('catalog_status') == 'complete':
                matches = matches & set(entry.get('catalog_novel_ids', []))
            candidates.update(matches)
        selected[owner] = dict(name=entry.get('name', ''), names=sorted(matched_names),
                               verified_novel_ids=sorted(verified, key=int),
                               candidate_novel_ids=sorted(candidates - verified, key=int),
                               evidence='verified' if verified else 'name_match')
        covered.update(candidates)
    verified_ids = {nid for entry in selected.values() for nid in entry['verified_novel_ids']}
    result = dict(updated=now, novel_count=len(adults), author_name_count=len(names), authors=selected,
                  verified_author_count=sum(bool(entry['verified_novel_ids']) for entry in selected.values()),
                  name_matched_author_count=sum(not entry['verified_novel_ids'] for entry in selected.values()),
                  known_names={name: sorted(ids, key=int) for name, ids in sorted(names.items())},
                  unresolved_names={name: sorted(ids - covered, key=int) for name, ids in sorted(names.items()) if ids - covered},
                  unverified_names={name: sorted(ids - verified_ids, key=int) for name, ids in sorted(names.items()) if ids - verified_ids},
                  ambiguous_names={name: sorted(owners, key=int) for name, owners in aliases.items() if len(owners) > 1},
                  unnamed_novel_ids=sorted((nid for nid, name in adults.items() if not name), key=int))
    registry['r19'] = result
    return result


def unresolved_r19_groups(registry, failures=None):
    """One identity lookup per known R19 name; nicknames alone cannot verify an owner."""
    failures = failures or {}
    subset = registry.get('r19', {})
    groups = dict(subset.get('unverified_names', {}))
    verified = {nid for entry in subset.get('authors', {}).values()
                for nid in entry.get('verified_novel_ids', [])}
    for nid in subset.get('unnamed_novel_ids', []):
        if nid not in verified:
            groups[f'id:{nid}'] = [nid]
    return [(name, sorted(ids, key=lambda nid: (failures.get(nid, 0), int(nid))))
            for name, ids in sorted(groups.items(), key=lambda item: (-len(item[1]), item[0]))]


def identity_candidates(registry, ids, failures=None, now=None):
    """Exclude known deletions; only other access failures get a weekly retry."""
    now = time.time() if now is None else now
    failures = failures or {}
    unavailable = registry.get('r19_identity_unavailable', {})
    deleted = set(registry.get('catalog_deleted_novel_ids', []))
    return [nid for nid in ids if nid not in deleted and
            unavailable.get(nid, {}).get('reason') != DELETED_IDENTITY_REASON and failures.get(nid, 0) < 3 and
            now - unavailable.get(nid, {}).get('checked', -float('inf')) >= 7 * 86400]


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
    record_live_novels(registry, novels, now=now)
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
