/*
 * One book on several sources: NovelDB's search index (scripts/noveldb_index/build.cjs, mergeSame) finds
 * the entries of different sources with the same original title and author, and lists them in
 * same.json.gz on the `noveldb-index` branch: [[source, id, [[source, id, address], ...]], ...], the
 * fullest entry first. The catalog shows such a book once, as its first entry, with links to the others.
 *
 * The page and the catalog worker both read it. A million-book catalog must not grow the heap: each
 * entry is a 53-bit hash in a sorted Float64Array (with its book's number and whether it leads), and
 * the addresses stay one string read only for the cards on screen (`addresses: true`, the page).
 *
 * NovelSameBooks.load({addresses}) -> Promise of the table, or null when the index has none
 * table.find(source, id)            -> {book, lead} or null
 * table.also(source, id)            -> [[source, id, address], ...] for a leading entry, else []
 * table.version                     -> which build it is (for cached result pages)
 */
(function (root) {
    "use strict";
    if (root.NovelSameBooks) return;
    const BASE = "https://raw.githubusercontent.com/Shirochi-stack/NovelDB/noveldb-index/";
    let loading = null;

    /** "source:id" as a 53-bit number: two 32-bit FNV-1a hashes, one of them cut to 21 bits. */
    function hash(source, id) {
        const text = source + ":" + id;
        let a = 0x811c9dc5, b = 0x01000193 ^ 0x5bd1e995;
        for (let i = 0; i < text.length; i++) {
            const c = text.charCodeAt(i);
            a = Math.imul(a ^ c, 0x01000193) >>> 0;
            b = Math.imul(b ^ c, 0x5bd1e995) >>> 0;
        }
        return a * 0x200000 + (b & 0x1fffff);
    }

    /** The table of `groups` ([[source, id, [[source, id, address], ...]], ...]). */
    function build(groups, version, addresses) {
        let count = 0;
        for (const group of groups) count += 1 + group[2].length;
        const order = new Array(count);
        const keys = new Float64Array(count), books = new Int32Array(count), leads = new Uint8Array(count);
        let at = 0;
        groups.forEach((group, book) => {
            order[at++] = [hash(group[0], String(group[1])), book, 1];
            for (const other of group[2]) order[at++] = [hash(other[0], String(other[1])), book, 0];
        });
        order.sort((x, y) => x[0] - y[0]);
        order.forEach((entry, i) => { keys[i] = entry[0]; books[i] = entry[1]; leads[i] = entry[2]; });
        // The addresses: one string, each book's part found by its offset.
        let text = "", offsets = null;
        if (addresses) {
            const parts = groups.map((group) => JSON.stringify(group[2]));
            offsets = new Uint32Array(parts.length + 1);
            parts.forEach((part, i) => { offsets[i + 1] = offsets[i] + part.length; });
            text = parts.join("");
        }
        function find(source, id) {
            const key = hash(source, String(id));
            let low = 0, high = keys.length - 1;
            while (low <= high) {
                const mid = (low + high) >> 1;
                if (keys[mid] < key) low = mid + 1;
                else if (keys[mid] > key) high = mid - 1;
                else return {book: books[mid], lead: leads[mid] === 1};
            }
            return null;
        }
        function also(source, id) {
            const found = offsets && find(source, id);
            if (!found || !found.lead) return [];
            try { return JSON.parse(text.slice(offsets[found.book], offsets[found.book + 1])); } catch (_) { return []; }
        }
        return {find, also, version, size: groups.length};
    }

    async function read() {
        const response = await fetch(BASE + "manifest.json", {cache: "no-cache"});
        if (!response.ok) return null;
        const manifest = await response.json();
        if (!manifest.same) return null;
        const data = await fetch(BASE + "same.json.gz");
        if (!data.ok) return null;
        const stream = data.body.pipeThrough(new DecompressionStream("gzip"));
        return {groups: JSON.parse(await new Response(stream).text()), version: manifest.built};
    }

    function load(options = {}) {
        if (!loading) {
            loading = read().then((found) => (found ? build(found.groups, found.version, !!options.addresses) : null))
                .catch(() => null);
        }
        return loading;
    }

    root.NovelSameBooks = {load, build, hash};
})(typeof self !== "undefined" ? self : this);
