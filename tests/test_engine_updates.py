"""Opt-in rule-engine updates: picking the bundle, downloading, verifying and setting aside."""
import hashlib
import json
import time

import pytest

from noveldb.core import engine_updates as eu

ND, W2E = eu.NOVEL_DOWNLOADER, eu.WEBTOEPUB


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    """A fake app bundle folder: rules-lib.js 5.2.1285 at the top, WebToEpub of 2026-10-27."""
    bundle = tmp_path / "bundle"
    (bundle / "webtoepub").mkdir(parents=True)
    (bundle / "rules-lib.js").write_text(
        '// ==UserScript==\n// @version        5.2.1285\n// ==/UserScript==\n'
        'switch(e){case"www.site.test":{break}case"api.example.org":{break}default:'
        'if(/^book\\d+\\.com$/.test(e)){break}throw new Error("Not Found Rule!")}', encoding="utf-8")
    body = b'parserFactory.register("www.novel.test", () => 0);'
    (bundle / "webtoepub" / "webtoepub-lib.js").write_bytes(body)
    (bundle / "webtoepub" / "manifest.json").write_text(json.dumps({
        "api": 1, "commit": "a" * 40, "shim": "s1", "date": "2026-10-27T04:19:22Z",
        "built": "2026-10-28T00:00:00Z", "version": "1.0.14.61"}), encoding="utf-8")
    monkeypatch.setenv("NOVELDB_HOME", str(tmp_path / "home"))
    monkeypatch.setattr(eu.paths, "bundle_dir", lambda: bundle)
    return bundle


def _remote(engine, body, **extra):
    base = {"engine": engine.name, "api": 1, "commit": "b" * 40, "shim": "s2", "built": "2026-11-01T00:00:00Z",
            "file": f"{engine.lib}-new.js", "size": len(body), "sha256": hashlib.sha256(body).hexdigest()}
    if engine is ND:
        base.update(version="5.2.1289", date="2026-10-16T01:44:06Z")
    else:
        base.update(version="1.0.14.61", date="2026-11-01T00:00:00Z")
    return dict(base, **extra)


def _release(engine, manifest, body):
    files = {eu.RELEASE_URL + engine.release_manifest: json.dumps(manifest).encode(),
             eu.RELEASE_URL + manifest["file"]: body}
    calls = []

    def fetch(url, timeout):
        calls.append(url)
        if url not in files:
            raise OSError(f"404 {url}")
        return files[url]
    return fetch, calls


def test_shipped_bundles_are_used_until_updates_arrive(shipped):
    nd, w2e = eu.current(ND), eu.current(W2E)
    assert nd.folder == shipped and not nd.updated
    assert nd.manifest["version"] == "5.2.1285"  # read from the userscript header
    assert nd.label == "5.2.1285, built in"
    assert w2e.folder == shipped / "webtoepub" and w2e.label == "2026-10-27, aaaaaaa, built in"


def test_sites_each_engine_lists(shipped):
    nd, w2e = eu.current(ND), eu.current(W2E)
    assert nd.knows("https://www.site.test/book/1") and nd.knows("https://book42.com/novel/1")
    assert not nd.knows("https://site.test/book/1")  # novel-downloader matches location.host exactly
    assert w2e.knows("https://novel.test/b") and w2e.knows("https://www.novel.test/b")
    assert not w2e.knows("https://other.test/b")


@pytest.mark.parametrize("engine", [ND, W2E], ids=["novel_downloader", "webtoepub"])
def test_newer_release_is_downloaded_and_used(shipped, engine):
    body = b"/* newer */"
    fetch, calls = _release(engine, _remote(engine, body), body)
    result = eu.check_for_update(engine, fetch=fetch)
    assert result.status == "updated", result.message
    bundle = eu.current(engine)
    assert bundle.updated and bundle.read() == "/* newer */"
    assert bundle.manifest["file"] == engine.lib
    assert len(calls) == 2
    # Checking again finds nothing new and downloads nothing.
    fetch, calls = _release(engine, _remote(engine, body), body)
    assert eu.check_for_update(engine, fetch=fetch).status == "current"
    assert len(calls) == 1


def test_a_rebuild_of_the_bundle_in_use_is_not_downloaded(shipped):
    body = b"/* same parsers, built again */"
    rebuild = _remote(W2E, body, commit="a" * 40, shim="s1", date="2026-10-27T04:19:22Z")
    fetch, calls = _release(W2E, rebuild, body)
    assert eu.check_for_update(W2E, fetch=fetch).status == "current"
    assert len(calls) == 1


def test_older_or_incompatible_releases_are_not_downloaded(shipped):
    body = b"/* older */"
    fetch, calls = _release(ND, _remote(ND, body, version="5.2.1200", date="2027-01-01T00:00:00Z"), body)
    assert eu.check_for_update(ND, fetch=fetch).status == "current"  # the version decides, not the date
    fetch, calls = _release(W2E, _remote(W2E, body, api=2), body)
    result = eu.check_for_update(W2E, fetch=fetch)
    assert result.status == "current" and "newer NovelDB" in result.message
    fetch, calls = _release(W2E, dict(_remote(W2E, body), engine="novel_downloader"), body)
    assert eu.check_for_update(W2E, fetch=fetch).status == "failed"
    assert not eu.current(ND).updated and not eu.current(W2E).updated


def test_bad_checksum_or_file_name_is_refused(shipped):
    body = b"/* tampered */"
    fetch, _calls = _release(ND, _remote(ND, body, sha256="0" * 64), body)
    assert eu.check_for_update(ND, fetch=fetch).status == "failed"
    fetch, _calls = _release(ND, _remote(ND, body, file="../evil.js"), body)
    assert eu.check_for_update(ND, fetch=fetch).status == "failed"
    assert not eu.current(ND).updated


def test_network_failure_is_reported(shipped):
    def fetch(url, timeout):
        raise OSError("offline")
    result = eu.check_for_update(W2E, fetch=fetch)
    assert result.status == "failed" and "offline" in result.message
    assert eu.last_checked(W2E) > 0 and eu.last_checked(ND) == 0


def test_a_modified_download_is_ignored(shipped):
    body = b"/* good */"
    fetch, _calls = _release(ND, _remote(ND, body), body)
    eu.check_for_update(ND, fetch=fetch)
    assert eu.current(ND).updated
    (eu.updates_dir(ND) / "rules-lib.js").write_bytes(b"/* changed on disk */")
    assert not eu.current(ND).updated


def test_rejected_update_falls_back_and_is_not_fetched_again(shipped):
    body = b"/* broken */"
    fetch, _calls = _release(W2E, _remote(W2E, body), body)
    eu.check_for_update(W2E, fetch=fetch)
    eu.reject(eu.current(W2E), "SyntaxError")
    assert not eu.current(W2E).updated
    fetch, calls = _release(W2E, _remote(W2E, body), body)
    result = eu.check_for_update(W2E, fetch=fetch)
    assert result.status == "current" and "failed to load before" in result.message
    assert len(calls) == 1


def test_automatic_check_runs_once_a_day(shipped, monkeypatch):
    seen = []
    monkeypatch.setattr(eu, "check_for_update",
                        lambda engine: seen.append(engine) or eu.UpdateResult(engine, "current", "up to date"))
    logged = []
    eu.check_if_due(ND, logged.append)
    assert seen == [ND] and logged == []
    eu._save_state(ND, {"checked": time.time()})
    assert eu.check_if_due(ND, logged.append) is None
    assert seen == [ND]
