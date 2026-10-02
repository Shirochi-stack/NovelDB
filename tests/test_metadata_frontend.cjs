const test = require('node:test');
const assert = require('node:assert/strict');
const core = require('../docs/metadata-core.js');

const cfg = { format: 'metadata-v1', linkHosts: ['novel.\u006e\u0061\u0076\u0065\u0072.com'], purchaseHosts: ['series.\u006e\u0061\u0076\u0065\u0072.com'] };
function row(id = 7, values = {}) {
    const result = [id, '제목', '작가', '', ['판타지'], null, null, null, null, null, null,
        `https://novel.\u006e\u0061\u0076\u0065\u0072.com/best/list?novelId=${id}`, 'best', null, {}, {}];
    for (const [index, value] of Object.entries(values)) result[Number(index)] = value;
    return result;
}

test('metadata-v1 keeps null distinct from measured zero and explicit status', () => {
    const [unknown, known] = core.parseRows([row(), row(8, {5: 0, 6: 0, 7: 0, 8: 0, 10: 0})], 'nweb', cfg);
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

test('legacy positions and SFC audience behavior remain unchanged', () => {
    const npia = [7, 'N', 'A', '/cover.jpg', [], 100, 8, 3, 1, '2026-01-01', 4, 19, 5, 6, 7, 8, 9, 10, 11, 12];
    const [n] = core.parseRows([npia], 'npia', {coverPrefix: 'https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com'});
    assert.equal(n.id, '7'); assert.equal(n.dailyRankTeen, 12); assert.equal(n.monthlyRankAdult, 8);
    assert.equal(n.cover, 'https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/cover.jpg');
    assert.equal(core.matchesAudience(n, 'adult'), true);
    const sfc = [7, 'S', 'A', 'cover.jpg', [], 100, 8, 9000, 0, '', 19, 1, 2, 3, 4, 5, 6, '第一行\\n第二行', '最新話', 9, '2026-01-01'];
    const [s] = core.parseRows([sfc], 'sfc', {sfcRanks: true, coverPrefix: 'https://example.test/'});
    assert.equal(s.chapters, 9000); assert.equal(s.ticketRank, 6); assert.equal(s.synopsis, '第一行\n第二行');
    assert.equal(core.matchesAudience(s, 'r15'), true); assert.equal(core.matchesAudience(s, 'adult'), false);
    assert.ok(core.compareNullable('', '2026-01-01', 'asc', true) < 0);
    assert.equal(core.matchesStatus(n, ''), true);
    assert.equal(core.matchesAudience(n, ''), true);
});

test('source-qualified identity deduplicates top/chunk numeric IDs without crossing providers', () => {
    const old = [{id: 7, source: 'nweb', title: 'old', synopsis: 'cached'}, {id: 7, source: 'jara', title: 'J'}];
    const merged = core.mergeRecords(old, [{id: '7', source: 'nweb', title: 'new'}]);
    assert.equal(merged.length, 2);
    assert.equal(merged[0].title, 'new'); assert.equal(merged[0].synopsis, 'cached');
    assert.equal(core.identity(merged[1]), 'jara:7');
});

test('unknown values sort last in either direction, and native ranks stay source-specific', () => {
    for (const order of ['asc', 'desc']) {
        assert.ok(core.compareNullable(null, 0, order) > 0);
        assert.ok(core.compareNullable(0, null, order) < 0);
        assert.ok(core.compareNullable(null, '2026-01-01', order, true) > 0);
    }
    const novel = {source: 'nweb', rankings: {best_fantasy: 8}};
    assert.equal(core.nativeRank(novel, 'rank:nweb:best_fantasy'), 8);
    assert.equal(core.nativeRank(novel, 'rank:jara:best_fantasy'), null);
});

test('native counters are labelled separately without synthetic likes', () => {
    const [novel] = core.parseRows([row(7, {14: {favorites: 7, rating: 9.4, rating_scale: 10, recommendations: 4}})], 'nweb', cfg);
    assert.deepEqual(core.metricEntries(novel).map(m => m.label), ['Favorites', 'Recommendations', 'Rating']);
    assert.equal(core.metricEntries(novel).find(m => m.key === 'rating').scale, 10);
    assert.equal(novel.likes, null);
});

test('canonical destinations and manifests reject unrelated or executable URLs', () => {
    assert.throws(() => core.parseRows([row(7, {11: 'javascript:alert(1)'})], 'nweb', cfg));
    assert.throws(() => core.parseRows([row(7, {11: 'https://attacker.test/book/7'})], 'nweb', cfg));
    assert.throws(() => core.parseRows([row().slice(0, 12)], 'nweb', cfg));
    const manifest = {format: 'metadata-v1', files: ['nweb_chunk_0.json.gz'], chunks: 1, totalEntries: 1,
        descriptionShardCount: 128, descriptionShardPrefix: 'nweb_descriptions_shard_', topUrl: 'nweb_top.json.gz',
        boards: {best: {label: 'Best', observed_at: '2026-09-13T00:00:00Z', stale: false}}};
    assert.equal(core.manifestConfig(manifest, 'nweb').chunkFiles[0], 'data/nweb_chunk_0.json.gz');
    assert.throws(() => core.manifestConfig({...manifest, files: ['../npia_chunk_0.json.gz']}, 'nweb'));
    assert.throws(() => core.manifestConfig({...manifest, descriptionShardPrefix: 'jara_descriptions_shard_'}, 'nweb'));
});

function legacyCompare(sortBy, order, audience) {
    // The comparator the site used before sortRecords existed.
    const rank = (novel) => core.rankValue(novel, sortBy, audience);
    return (a, b) => {
        if (sortBy.startsWith('rank:')) {
            return core.compareNullable(core.nativeRank(a, sortBy), core.nativeRank(b, sortBy), order === 'asc' ? 'desc' : 'asc')
                || core.compareNullable(a.views, b.views, 'desc');
        }
        if (sortBy.startsWith('metric:')) {
            const metric = sortBy.slice(7);
            return core.compareNullable(core.nullableNumber(a.metrics?.[metric]), core.nullableNumber(b.metrics?.[metric]), order);
        }
        if (['daily', 'weekly', 'monthly', 'sfc_popularity', 'sfc_jp'].includes(sortBy)) {
            const ra = rank(a) || 9999;
            const rb = rank(b) || 9999;
            if (ra !== rb) return order === 'asc' ? rb - ra : ra - rb;
            const preferred = sortBy.startsWith('sfc_') ? 'sfc' : 'npia';
            if (a.source === preferred && b.source !== preferred) return -1;
            if (b.source === preferred && a.source !== preferred) return 1;
            return core.compareNullable(a.views, b.views, 'desc');
        }
        const field = sortBy === 'title' || sortBy === 'updated' ? sortBy : ['likes', 'chapters'].includes(sortBy) ? sortBy : 'views';
        return core.compareNullable(a[field], b[field], order, field === 'title' || field === 'updated');
    };
}

function syntheticRecords() {
    const records = [];
    let seed = 7;
    const rand = () => { seed = (seed * 48271) % 2147483647; return seed / 2147483647; };
    const pick = (values) => values[Math.floor(rand() * values.length)];
    const sources = ['npia', 'sfc', 'nweb', 'jara', 'kpage'];
    for (let index = 0; index < 2000; index++) {
        const source = pick(sources);
        records.push({
            id: String(index), source, title: pick(['Alpha', 'beta', '가나다', '魔王', 'Zeta', 'alpha']) + (index % 13),
            views: pick([null, 0, 5, 5, 100, 250000]), likes: pick([null, 0, 3, 9]), chapters: pick([null, 1, 40]),
            updated: pick([null, '', '2001-03-23T23:27:36', '2026-09-20 10:00:00', '2026-09-20T16:19:06', '2026-05-20T00:01:05+09:00']),
            dailyRank: pick([0, 0, 1, 2, 3, 50]), weeklyRank: pick([0, 4, 7]), monthlyRank: pick([0, 9]),
            dailyRankAdult: pick([0, 1, 8]), dailyRankTeen: pick([0, 2, 6]), weeklyRankTeen: pick([0, 3]),
            popularityRank: pick([0, 1, 12]), jpRank: pick([0, 5]),
            metrics: pick([{}, { favorites: 3 }, { favorites: 3 }, { favorites: null }, { rating: 9.5 }]),
            rankings: pick([{}, { best_fantasy: 1 }, { best_fantasy: 4 }, { other: 2 }]),
        });
    }
    return records;
}

test('sortRecords matches the legacy comparator order for every sort, including ties and unknown values', () => {
    const records = syntheticRecords();
    const configs = [];
    for (const sortBy of ['daily', 'weekly', 'monthly', 'sfc_popularity', 'sfc_jp', 'views', 'likes', 'chapters', 'title', 'updated',
        'metric:favorites', 'metric:rating', 'rank:nweb:best_fantasy', 'rank:jara:other', 'unknown']) {
        for (const order of ['desc', 'asc']) configs.push({ sortBy, order, audience: 'all' });
    }
    configs.push({ sortBy: 'daily', order: 'desc', audience: 'adult' }, { sortBy: 'weekly', order: 'asc', audience: 'general' });
    for (const options of configs) {
        const expected = records.slice().sort(legacyCompare(options.sortBy, options.order, options.audience));
        const actual = core.sortRecords(records.slice(), options);
        assert.deepEqual(actual.map((novel) => novel.id), expected.map((novel) => novel.id), JSON.stringify(options));
        const compare = core.recordComparator(options);
        for (let index = 1; index < actual.length; index++) {
            assert.ok(compare(actual[index - 1], actual[index]) <= 0, `comparator disagrees for ${JSON.stringify(options)}`);
        }
        const existing = actual.filter((_, index) => index % 3);
        const batch = core.sortRecords(actual.filter((_, index) => index % 3 === 0), options);
        const merged = core.mergeSortedRecords(existing, batch, compare);
        assert.equal(merged.length, actual.length);
        for (let index = 1; index < merged.length; index++) {
            assert.ok(compare(merged[index - 1], merged[index]) <= 0, `merge breaks order for ${JSON.stringify(options)}`);
        }
    }
});

test('rankValue follows the audience selection for Npia boards and stays board-specific elsewhere', () => {
    const novel = { source: 'npia', dailyRank: 3, dailyRankAdult: 8, dailyRankTeen: 2, weeklyRank: 4, popularityRank: 9, rankings: { best: 1 } };
    assert.equal(core.rankValue(novel, 'daily', 'all'), 3);
    assert.equal(core.rankValue(novel, 'daily', 'r15'), 3);
    assert.equal(core.rankValue(novel, 'daily', 'adult'), 8);
    assert.equal(core.rankValue(novel, 'daily', 'general'), 2);
    assert.equal(core.rankValue(novel, 'weekly', 'general'), undefined);
    assert.equal(core.rankValue(novel, 'sfc_popularity', 'all'), 9);
    assert.equal(core.rankValue(novel, 'rank:npia:best', 'all'), 1);
    assert.equal(core.rankValue(novel, 'rank:nweb:best', 'all'), null);
    assert.equal(core.rankValue(novel, 'nonsense', 'all'), 0);
});

test('safeHttpUrl accepts plain URLs without the parser and still rejects unsafe destinations', () => {
    assert.equal(core.safeHttpUrl('https://novel.\u006e\u0061\u0076\u0065\u0072.com/best/list?novelId=7', ['novel.\u006e\u0061\u0076\u0065\u0072.com']), 'https://novel.\u006e\u0061\u0076\u0065\u0072.com/best/list?novelId=7');
    assert.equal(core.safeHttpUrl('https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/imagebox/cover/a.jpg'), 'https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/imagebox/cover/a.jpg');
    assert.equal(core.safeHttpUrl('https://Novel.\u006e\u0061\u0076\u0065\u0072.com:443/x', ['novel.\u006e\u0061\u0076\u0065\u0072.com']), 'https://novel.\u006e\u0061\u0076\u0065\u0072.com/x');
    for (const unsafe of ['javascript:alert(1)', 'https://novel.\u006e\u0061\u0076\u0065\u0072.com@evil.test/x', 'https://novel.\u006e\u0061\u0076\u0065\u0072.com.evil.test/x',
        'https://user:pw@novel.\u006e\u0061\u0076\u0065\u0072.com/x', 'https://evil.test/novel.\u006e\u0061\u0076\u0065\u0072.com/', 'data:text/html,x']) {
        assert.equal(core.safeHttpUrl(unsafe, ['novel.\u006e\u0061\u0076\u0065\u0072.com']), '', unsafe);
    }
    assert.equal(core.safeHttpUrl(' https://novel.\u006e\u0061\u0076\u0065\u0072.com/x', ['novel.\u006e\u0061\u0076\u0065\u0072.com']), 'https://novel.\u006e\u0061\u0076\u0065\u0072.com/x');
    assert.equal(core.safeHttpUrl('https://user:pw@images.test/x'), '');
    assert.equal(core.safeHttpUrl('ftp://images.test/x'), '');
});

test('covers normalize legacy protocols without corrupting absolute CDN URLs', () => {
    const prefix = 'https://images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com';
    for (const input of ['http://cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/cover.jpg', '//cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/cover.jpg',
        '  https://cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/cover.jpg  ', 'HTTPS://cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/cover.jpg']) {
        assert.equal(core.safeCoverUrl(input, prefix), 'https://cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/cover.jpg');
    }
    assert.equal(core.safeCoverUrl('/cover.jpg', prefix), prefix + '/cover.jpg');
    assert.equal(core.safeCoverUrl('2026/cover.jpg', 'https://rss.\u0073\u0066\u0061\u0063\u0067.com/covers/'), 'https://rss.\u0073\u0066\u0061\u0063\u0067.com/covers/2026/cover.jpg');
    assert.equal(core.safeCoverUrl('/2026/cover.jpg', 'https://rss.\u0073\u0066\u0061\u0063\u0067.com/covers/'), 'https://rss.\u0073\u0066\u0061\u0063\u0067.com/covers/2026/cover.jpg');
    for (const unsafe of ['javascript:alert(1)', 'data:image/svg+xml,x', 'https://user:pw@images.test/cover.jpg']) {
        assert.equal(core.safeCoverUrl(unsafe, prefix), '', unsafe);
    }
    const [record] = core.parseRows([row(7, {3: '//cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/cover.jpg'})], 'nweb', cfg);
    assert.equal(record.cover, 'https://cdn1.\u006d\u0075\u006e\u0070\u0069\u0061.com/cover.jpg');
});
