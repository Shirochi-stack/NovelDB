const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../docs/metadata-core.js');

const cfg = { format: 'metadata-v1', linkHosts: ['novel.naver.com'], purchaseHosts: ['series.naver.com'] };
function row(id = 7, values = {}) {
    const result = [id, '제목', '작가', '', ['판타지'], null, null, null, null, null, null,
        `https://novel.naver.com/best/list?novelId=${id}`, 'best', null, {}, {}];
    for (const [index, value] of Object.entries(values)) result[Number(index)] = value;
    return result;
}

test('metadata-v1 keeps null distinct from measured zero and explicit status', () => {
    const [unknown, known] = core.parseRows([row(), row(8, {5: 0, 6: 0, 7: 0, 8: 0, 10: 0})], 'naver', cfg);
    assert.equal(unknown.id, '7');
    for (const field of ['views', 'likes', 'chapters', 'complete', 'age', 'updated']) assert.equal(unknown[field], null);
    assert.equal(known.views, 0);
    assert.equal(core.matchesStatus(unknown, 'ongoing'), false);
    assert.equal(core.matchesStatus(unknown, 'complete'), false);
    assert.equal(core.matchesAudience(unknown, 'general'), false);
    assert.equal(core.matchesAudience(unknown, 'all'), true);
    assert.equal(core.matchesStatus(known, 'ongoing'), true);
    assert.equal(core.matchesAudience(known, 'general'), true);
});

test('legacy positions and SFACG audience behavior remain unchanged', () => {
    const npia = [7, 'N', 'A', '/cover.jpg', [], 100, 8, 3, 1, '2026-01-01', 4, 19, 5, 6, 7, 8, 9, 10, 11, 12];
    const [n] = core.parseRows([npia], 'novelpia', {coverPrefix: 'https://images.novelpia.com'});
    assert.equal(n.id, '7'); assert.equal(n.dailyRankTeen, 12); assert.equal(n.monthlyRankAdult, 8);
    assert.equal(n.cover, 'https://images.novelpia.com/cover.jpg');
    assert.equal(core.matchesAudience(n, 'adult'), true);
    const sfacg = [7, 'S', 'A', 'cover.jpg', [], 100, 8, 9000, 0, '', 19, 1, 2, 3, 4, 5, 6, '第一行\\n第二行', '最新話', 9, '2026-01-01'];
    const [s] = core.parseRows([sfacg], 'sfacg', {sfacgRanks: true, coverPrefix: 'https://example.test/'});
    assert.equal(s.chapters, 9000); assert.equal(s.ticketRank, 6); assert.equal(s.synopsis, '第一行\n第二行');
    assert.equal(core.matchesAudience(s, 'r15'), true); assert.equal(core.matchesAudience(s, 'adult'), false);
    assert.ok(core.compareNullable('', '2026-01-01', 'asc', true) < 0);
    assert.equal(core.matchesStatus(n, ''), true);
    assert.equal(core.matchesAudience(n, ''), true);
});

test('source-qualified identity deduplicates top/chunk numeric IDs without crossing providers', () => {
    const old = [{id: 7, source: 'naver', title: 'old', synopsis: 'cached'}, {id: 7, source: 'joara', title: 'J'}];
    const merged = core.mergeRecords(old, [{id: '7', source: 'naver', title: 'new'}]);
    assert.equal(merged.length, 2);
    assert.equal(merged[0].title, 'new'); assert.equal(merged[0].synopsis, 'cached');
    assert.equal(core.identity(merged[1]), 'joara:7');
});

test('unknown values sort last in either direction, and native ranks stay source-specific', () => {
    for (const order of ['asc', 'desc']) {
        assert.ok(core.compareNullable(null, 0, order) > 0);
        assert.ok(core.compareNullable(0, null, order) < 0);
        assert.ok(core.compareNullable(null, '2026-01-01', order, true) > 0);
    }
    const novel = {source: 'naver', rankings: {best_fantasy: 8}};
    assert.equal(core.nativeRank(novel, 'rank:naver:best_fantasy'), 8);
    assert.equal(core.nativeRank(novel, 'rank:joara:best_fantasy'), null);
});

test('native counters are labelled separately without synthetic likes', () => {
    const [novel] = core.parseRows([row(7, {14: {favorites: 7, rating: 9.4, rating_scale: 10, recommendations: 4}})], 'naver', cfg);
    assert.deepEqual(core.metricEntries(novel).map(m => m.label), ['Favorites', 'Recommendations', 'Rating']);
    assert.equal(core.metricEntries(novel).find(m => m.key === 'rating').scale, 10);
    assert.equal(novel.likes, null);
});

test('canonical destinations and manifests reject unrelated or executable URLs', () => {
    assert.throws(() => core.parseRows([row(7, {11: 'javascript:alert(1)'})], 'naver', cfg));
    assert.throws(() => core.parseRows([row(7, {11: 'https://attacker.test/book/7'})], 'naver', cfg));
    assert.throws(() => core.parseRows([row().slice(0, 12)], 'naver', cfg));
    const manifest = {format: 'metadata-v1', files: ['naver_chunk_0.json.gz'], chunks: 1, totalEntries: 1,
        descriptionShardCount: 128, descriptionShardPrefix: 'naver_descriptions_shard_', topUrl: 'naver_top.json.gz',
        boards: {best: {label: 'Best', observed_at: '2026-09-13T00:00:00Z', stale: false}}};
    assert.equal(core.manifestConfig(manifest, 'naver').chunkFiles[0], 'data/naver_chunk_0.json.gz');
    assert.throws(() => core.manifestConfig({...manifest, files: ['../novelpia_chunk_0.json.gz']}, 'naver'));
    assert.throws(() => core.manifestConfig({...manifest, descriptionShardPrefix: 'joara_descriptions_shard_'}, 'naver'));
});
