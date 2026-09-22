/* Shared browser/Node helpers for source-local metadata contracts. */
(function (root, factory) {
    const api = factory();
    if (typeof module === "object" && module.exports) module.exports = api;
    else root.NovelMetadata = api;
})(typeof globalThis !== "undefined" ? globalThis : this, function () {
    "use strict";

    function nullableNumber(value) {
        if (value === null || value === undefined || value === "" || typeof value === "boolean") return null;
        const number = Number(value);
        return Number.isFinite(number) && number >= 0 ? number : null;
    }

    function identity(novel) {
        return `${novel.source}:${String(novel.id)}`;
    }

    function mergeRecords(previous, incoming) {
        const records = new Map(previous.map((novel) => [identity(novel), novel]));
        for (const novel of incoming) {
            const key = identity(novel);
            const old = records.get(key);
            records.set(key, old ? { ...old, ...novel,
                titleEn: novel.titleEn || old.titleEn,
                synopsis: novel.synopsis || old.synopsis } : novel);
        }
        return [...records.values()];
    }

    // A plain absolute URL whose authority is a lowercase DNS name proves its
    // scheme, host and lack of credentials without running the URL parser,
    // which dominates catalog parsing for a million rows.
    const PLAIN_HTTP_URL = /^https?:\/\/([a-z0-9.-]+)(?=[/?#]|$)/;
    function safeHttpUrl(value, hosts) {
        if (typeof value !== "string" || !value) return "";
        const plain = PLAIN_HTTP_URL.exec(value);
        if (plain && (!hosts || hosts.includes(plain[1]))) return value;
        try {
            const url = new URL(value);
            if (url.protocol !== "https:" && url.protocol !== "http:") return "";
            if (url.username || url.password || (hosts && !hosts.includes(url.hostname))) return "";
            return url.href;
        } catch (_) { return ""; }
    }

    function parseRows(raw, source, cfg) {
        if (!Array.isArray(raw)) throw new Error(`Invalid ${source} catalog array`);
        return raw.map((row) => {
            if (!Array.isArray(row) || row.length < 2 || row[0] == null) throw new Error(`Invalid ${source} catalog row`);
            const fresh = cfg.format === "metadata-v1";
            if (fresh && row.length !== 16) throw new Error(`Invalid metadata-v1 row for ${source}`);
            let tags = row[4];
            if (!Array.isArray(tags)) tags = tags && typeof tags === "object" ? Object.values(tags) : [];
            const cover = row[3] ? String(row[3]) : "";
            const novel = {
                id: String(row[0]), source, title: String(row[1] || ""), author: String(row[2] || ""),
                cover: safeHttpUrl(cover && cfg.coverPrefix && !cover.startsWith("http") ? cfg.coverPrefix + cover : cover),
                tags: tags.filter((tag) => typeof tag === "string"),
                views: fresh ? nullableNumber(row[5]) : (row[5] || 0),
                likes: fresh ? nullableNumber(row[6]) : (row[6] || 0),
                chapters: fresh ? nullableNumber(row[7]) : (row[7] || 0),
                complete: fresh ? (row[8] === 0 || row[8] === 1 ? row[8] : null) : (row[8] || 0),
                updated: fresh ? (row[9] || null) : (row[9] || ""),
                age: fresh ? nullableNumber(row[10]) : ((cfg.sfacgRanks ? row[10] : row[11]) || 0),
                metadataV1: fresh,
            };
            if (fresh) {
                novel.canonicalUrl = safeHttpUrl(row[11], cfg.linkHosts);
                if (!novel.canonicalUrl) throw new Error(`Invalid ${source} official destination`);
                novel.tier = row[12] || null;
                novel.purchaseUrl = safeHttpUrl(row[13], cfg.purchaseHosts || cfg.linkHosts) || null;
                novel.metrics = row[14] && typeof row[14] === "object" && !Array.isArray(row[14]) ? row[14] : {};
                novel.rankings = {};
                for (const [board, rank] of Object.entries(row[15] || {})) {
                    if (Number.isInteger(rank) && rank > 0) novel.rankings[board] = rank;
                }
            } else if (cfg.sfacgRanks) {
                ["popularityRank", "bestSellerRank", "newBooksRank", "bookmarksRank", "jpRank", "ticketRank"]
                    .forEach((field, index) => { novel[field] = row[11 + index] || 0; });
                if (row[17]) novel.synopsis = String(row[17]).replace(/\\r\\n|\\n/g, "\n").trim();
            } else {
                novel.weeklyRank = row[10] || 0;
                if (source === "novelpia") {
                    ["monthlyRank", "dailyRank", "weeklyRankAdult", "monthlyRankAdult", "dailyRankAdult", "weeklyRankTeen", "monthlyRankTeen", "dailyRankTeen"]
                        .forEach((field, index) => { novel[field] = row[12 + index] || 0; });
                }
            }
            return novel;
        });
    }

    function matchesStatus(novel, status) {
        if (status !== "complete" && status !== "ongoing") return true;
        if (novel.metadataV1 && novel.complete === null) return false;
        return status === "complete" ? !!novel.complete : !novel.complete;
    }

    function matchesAudience(novel, audience) {
        if (!["adult", "r15", "general"].includes(audience)) return true;
        if (novel.metadataV1) {
            if (novel.age === null) return false;
            if (audience === "adult") return novel.age >= 19;
            if (audience === "r15") return novel.age >= 15 && novel.age < 19;
            return novel.age < 19;
        }
        if (audience === "adult") return ["novelpia", "kakao"].includes(novel.source) && novel.age === 19;
        if (audience === "r15") return (novel.source === "sfacg" && novel.age === 19) || (novel.source === "novelpia" && novel.age === 15);
        return novel.age !== 19;
    }

    function compareNullable(a, b, order = "desc", text = false) {
        // metadata-v1 decodes missing text as null. Legacy empty strings retain
        // their old lexical sort order, including ascending update-date sorts.
        const aMissing = a == null;
        const bMissing = b == null;
        if (aMissing || bMissing) return aMissing === bMissing ? 0 : aMissing ? 1 : -1;
        const result = text ? String(a).localeCompare(String(b)) : Number(a) - Number(b);
        return order === "asc" ? result : -result;
    }

    function nativeRank(novel, sort) {
        if (!sort.startsWith("rank:")) return null;
        const [, source, ...parts] = sort.split(":");
        if (novel.source !== source) return null;
        return novel.rankings?.[parts.join(":")] || null;
    }

    function metricEntries(novel) {
        const metrics = novel.metrics || {};
        const fields = [
            ["views", "Views", novel.views], ["likes", "Likes", novel.likes],
            ["episodes", metrics.episode_unit === "권" ? "Volumes" : "Episodes", novel.chapters],
            ["favorites", "Favorites", metrics.favorites],
            ["recommendations", "Recommendations", metrics.recommendations],
            ["rating", "Rating", metrics.rating], ["downloads", "Downloads", metrics.downloads],
        ];
        return fields.filter(([, , value]) => nullableNumber(value) !== null)
            .map(([key, label, value]) => ({ key, label, value: nullableNumber(value),
                scale: key === "rating" ? nullableNumber(metrics.rating_scale) : null }));
    }

    function rankValue(novel, type, audience) {
        if (type.startsWith("rank:")) return nativeRank(novel, type);
        if (type === "sfacg_popularity") return novel.popularityRank || 0;
        if (type === "sfacg_bestseller") return novel.bestSellerRank || 0;
        if (type === "sfacg_newbooks") return novel.newBooksRank || 0;
        if (type === "sfacg_bookmarks") return novel.bookmarksRank || 0;
        if (type === "sfacg_jp") return novel.jpRank || 0;
        if (type === "sfacg_ticket") return novel.ticketRank || 0;
        // all → all ranks; general → teen ranks; adult → adult ranks; r15 → all ranks.
        if (audience === "adult") {
            if (type === "weekly") return novel.weeklyRankAdult;
            if (type === "monthly") return novel.monthlyRankAdult;
            if (type === "daily") return novel.dailyRankAdult;
        } else if (audience === "general") {
            if (type === "weekly") return novel.weeklyRankTeen;
            if (type === "monthly") return novel.monthlyRankTeen;
            if (type === "daily") return novel.dailyRankTeen;
        }
        if (type === "weekly") return novel.weeklyRank;
        if (type === "monthly") return novel.monthlyRank;
        if (type === "daily") return novel.dailyRank;
        return 0;
    }

    const RANK_SORTS = new Set(["daily", "weekly", "monthly", "sfacg_popularity", "sfacg_bestseller",
        "sfacg_newbooks", "sfacg_bookmarks", "sfacg_jp", "sfacg_ticket"]);
    let collator = null;

    /**
     * Describe a sort as an ordered list of keys. Each key reads one value per
     * record; `dir` is +1 for ascending and -1 for descending; `nullsLast`
     * keeps unknown values at the end in either direction, and `text` selects
     * locale collation ("collate") or code point order ("plain") instead of
     * numeric comparison.
     */
    function sortPlan(options = {}) {
        const sortBy = String(options.sortBy || "daily");
        const dir = options.order === "asc" ? 1 : -1;
        const audience = options.audience || "all";
        const defaultSource = options.defaultSource || "";
        const viewsDesc = { get: (novel) => novel.views, dir: -1, nullsLast: true };
        if (sortBy.startsWith("rank:")) {
            const [, source, ...parts] = sortBy.split(":");
            const board = parts.join(":");
            return [{ get: (novel) => (novel.source === source ? novel.rankings?.[board] || null : null), dir: -dir, nullsLast: true }, viewsDesc];
        }
        if (sortBy.startsWith("metric:")) {
            const metric = sortBy.slice(7);
            return [{ get: (novel) => nullableNumber(novel.metrics?.[metric]), dir, nullsLast: true }];
        }
        if (RANK_SORTS.has(sortBy)) {
            // Unranked novels sort after ranked ones, then the board's own
            // platform comes first, then views break remaining ties.
            const preferred = sortBy.startsWith("sfacg_") ? "sfacg" : "novelpia";
            return [
                { get: (novel) => rankValue(novel, sortBy, audience) || 9999, dir: -dir, nullsLast: false },
                { get: (novel) => ((novel.source || defaultSource) === preferred ? 0 : 1), dir: 1, nullsLast: false },
                viewsDesc,
            ];
        }
        if (sortBy === "title") return [{ get: (novel) => novel.title, dir, nullsLast: true, text: "collate" }];
        // Update stamps are ISO-like strings; legacy empty strings keep their
        // lexical position. Code point order matches collation for them and
        // avoids millions of collator calls.
        if (sortBy === "updated") return [{ get: (novel) => novel.updated, dir, nullsLast: true, text: "plain" }];
        const field = sortBy === "likes" || sortBy === "chapters" ? sortBy : "views";
        return [{ get: (novel) => novel[field], dir, nullsLast: true }];
    }

    /** Comparator with the same order as sortRecords, for merging small batches. */
    function recordComparator(options) {
        const keys = sortPlan(options);
        return (a, b) => {
            for (const key of keys) {
                const va = key.get(a);
                const vb = key.get(b);
                if (key.nullsLast) {
                    const aMissing = va == null;
                    const bMissing = vb == null;
                    if (aMissing || bMissing) {
                        if (aMissing && bMissing) continue;
                        return aMissing ? 1 : -1;
                    }
                }
                let result;
                if (key.text === "collate") {
                    if (!collator) collator = new Intl.Collator();
                    result = collator.compare(String(va), String(vb));
                } else if (key.text === "plain") {
                    const sa = String(va);
                    const sb = String(vb);
                    result = sa < sb ? -1 : sa > sb ? 1 : 0;
                } else {
                    result = Number(va) - Number(vb);
                }
                if (result) return key.dir * result;
            }
            return 0;
        };
    }

    /**
     * Sort records in place. Numeric plans extract every key once into typed
     * arrays and sort an index, which is several times faster than calling a
     * comparator that reads object fields for every comparison. The order is
     * stable: ties keep their input order.
     */
    function sortRecords(records, options) {
        const keys = sortPlan(options);
        if (keys.some((key) => key.text)) return records.sort(recordComparator(options));
        const total = records.length;
        if (total < 2) return records;
        const columns = keys.map((key) => ({
            get: key.get, dir: key.dir, values: new Float64Array(total),
            missing: key.nullsLast ? new Uint8Array(total) : null,
        }));
        for (let index = 0; index < total; index++) {
            const record = records[index];
            for (const column of columns) {
                const value = column.get(record);
                if (column.missing && value == null) column.missing[index] = 1;
                else column.values[index] = Number(value);
            }
        }
        const order = new Uint32Array(total);
        for (let index = 0; index < total; index++) order[index] = index;
        const count = columns.length;
        order.sort((x, y) => {
            for (let position = 0; position < count; position++) {
                const column = columns[position];
                if (column.missing) {
                    const xMissing = column.missing[x];
                    if (xMissing !== column.missing[y]) return xMissing - column.missing[y];
                    if (xMissing) continue;
                }
                const delta = column.values[x] - column.values[y];
                if (delta) return column.dir * delta;
            }
            return x - y;
        });
        const copy = records.slice();
        for (let index = 0; index < total; index++) records[index] = copy[order[index]];
        return records;
    }

    /** Merge two arrays already sorted by `compare`; existing records win ties. */
    function mergeSortedRecords(sorted, batch, compare) {
        const merged = new Array(sorted.length + batch.length);
        let i = 0;
        let j = 0;
        let k = 0;
        while (i < sorted.length && j < batch.length) {
            merged[k++] = compare(batch[j], sorted[i]) < 0 ? batch[j++] : sorted[i++];
        }
        while (i < sorted.length) merged[k++] = sorted[i++];
        while (j < batch.length) merged[k++] = batch[j++];
        return merged;
    }

    function manifestConfig(manifest, source) {
        if (!manifest || manifest.format !== "metadata-v1" || !Array.isArray(manifest.files)
            || manifest.chunks !== manifest.files.length || !Number.isInteger(manifest.totalEntries)
            || manifest.totalEntries < 0 || !Number.isInteger(manifest.descriptionShardCount)
            || manifest.descriptionShardCount < 1) throw new Error(`Invalid ${source} manifest`);
        const localFile = (file) => {
            if (typeof file !== "string" || !/^[a-zA-Z0-9_.-]+$/.test(file) || file.includes("..")) throw new Error(`Invalid ${source} artifact path`);
            return `data/${file}`;
        };
        const prefix = manifest.descriptionShardPrefix;
        if (prefix !== `${source}_descriptions_shard_`) throw new Error(`Invalid ${source} synopsis prefix`);
        const boards = {};
        for (const [key, value] of Object.entries(manifest.boards || {})) {
            if (value && typeof value.label === "string") boards[key] = value;
        }
        return {
            format: "metadata-v1", chunked: true, chunkFiles: manifest.files.map(localFile),
            chunkCount: manifest.chunks, totalEntries: manifest.totalEntries,
            descriptionShardCount: manifest.descriptionShardCount, descriptionShardPrefix: localFile(prefix),
            topUrl: manifest.topUrl ? localFile(manifest.topUrl) : null,
            boards, coverage: manifest.coverage || {},
        };
    }

    return { nullableNumber, identity, mergeRecords, safeHttpUrl, parseRows, matchesStatus,
        matchesAudience, compareNullable, nativeRank, rankValue, sortPlan, recordComparator, sortRecords,
        mergeSortedRecords, metricEntries, manifestConfig };
});
