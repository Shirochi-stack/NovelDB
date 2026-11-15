/*
 * A work's verified title and its page on NovelUpdates, where its title was confirmed there
 * (scripts/nu_titles.py). The catalog displays the machine-translated title; the verified one is only
 * searched (the catalog worker) and shown in Details (cover-save.js), with the link to its page.
 *
 * Every source's entries are one small file on the `data` branch:
 * {version: 1, sources: {source: {novel id: {title, url}}}}. The page reads it on the first Details
 * view and the worker on the first text search, never while the catalog loads; a failed read is tried
 * again the next time.
 *
 * NovelTitleAddresses.load()                -> Promise of the table, or null when it cannot be read
 * table.find(source, id)                    -> {title, url} or null (url only on the NovelUpdates site)
 * table.version                             -> which copy it is (for cached result pages)
 */
(function (root) {
    "use strict";
    if (root.NovelTitleAddresses) return;
    const FILE = "https://raw.githubusercontent.com/Shirochi-stack/NovelDB/data/metadata/state/nu_title_addresses.json.gz";
    const SITE = "https://www.novelupdates.com/";
    let loading = null;

    function build(sources, version) {
        function find(source, id) {
            const works = sources[source], key = String(id);
            const entry = works && Object.hasOwn(works, key) ? works[key] : null;
            if (!entry || typeof entry !== "object") return null;
            const title = typeof entry.title === "string" ? entry.title : "";
            const url = typeof entry.url === "string" && entry.url.startsWith(SITE) ? entry.url : "";
            return title || url ? {title, url} : null;
        }
        return {find, version};
    }

    async function read() {
        const controller = new AbortController(), timer = setTimeout(() => controller.abort(), 10000);
        try {
            const response = await fetch(FILE, {credentials: "omit", signal: controller.signal});
            if (!response.ok) { await response.body?.cancel(); throw new Error(`Titles HTTP ${response.status}`); }
            const version = response.headers.get("etag") || "";
            const data = JSON.parse(await new Response(response.body.pipeThrough(new DecompressionStream("gzip"))).text());
            if (data?.version !== 1 || !data.sources || typeof data.sources !== "object") throw new Error("Invalid titles");
            return build(data.sources, version || String(Object.keys(data.sources).reduce((n, source) => n + Object.keys(data.sources[source]).length, 0)));
        } finally { clearTimeout(timer); }
    }

    function load() {
        return loading ||= read().catch(() => { loading = null; return null; });
    }

    root.NovelTitleAddresses = {load, build};
})(typeof self !== "undefined" ? self : this);
