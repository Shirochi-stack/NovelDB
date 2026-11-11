"""Sorted mobile results keep publication order when older blocks answer first."""
import base64
import gzip
import json
from pathlib import Path

import pytest

from tests.test_generic_page_merge_browser import browser
from tests.test_noveldb_index import BOOKS, build, fetcher

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("platform", ["android/app/src/main/assets/catalog", "ios/NpiaKit/Sources/NpiaWebEngine/Resources/catalog"])
def test_r19_new_stream_preserves_index_order_with_reversed_network_replies(browser, platform):
    newer = dict(BOOKS[0], titleEn="New novel", tags=["R19", "R19|new"], rating=19, published="2026-11-11")
    older = dict(BOOKS[1], titleEn="Old novel", tags=["R19", "R19|new"], rating=19, published="2020-01-01")
    manifest, payload = build([newer, older])
    start, length = manifest['blockAt'][0]
    rows = json.loads(gzip.decompress(payload[start:start + length]))
    manifest.update(block=1, ratingSorts=True)
    manifest['blockAt'] = []
    for row in rows:
        block = gzip.compress(json.dumps([row]).encode())
        manifest['blockAt'].append([len(payload), len(block)])
        payload += block
    fetch = fetcher(manifest, payload, [])
    context = browser.new_context()
    page = context.new_page()
    page.route('https://catalog.test/**', lambda route: route.fulfill(json=[]))
    page.goto('https://catalog.test/')

    def transport(request):
        result = fetch(request['url'], headers=request.get('headers'))
        return {'status': result['status'], 'responseBase64': base64.b64encode(result['content']).decode()}

    page.expose_function('indexTransport', transport)
    delayed_range = 'bytes=' + str(manifest['blockAt'][0][0]) + '-'
    page.evaluate("""delay=>{
        window.NovelDBTransport=async request=>{
            if((request.headers?.Range||'').startsWith(delay))await new Promise(resolve=>setTimeout(resolve,180));
            return indexTransport(request);
        };
        window.seen=[];
    }""", delayed_range)
    page.add_script_tag(path=ROOT / platform / 'generic-lib.js')
    page.add_script_tag(path=ROOT / platform / 'catalog-app.js')
    result = page.evaluate("""async()=>{
        const result=await NovelDBCatalogApp.ndbSearch('R19','tag',2,0,card=>seen.push(card.title),null,'new');
        return {seen,titles:result.cards.map(card=>card.title)};
    }""")
    assert result['seen'] == result['titles'] == ['New novel', 'Old novel']
    context.close()
