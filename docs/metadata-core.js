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

    function safeHttpUrl(value, hosts) {
        if (typeof value !== "string" || !value) return "";
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
        matchesAudience, compareNullable, nativeRank, metricEntries, manifestConfig };
});
