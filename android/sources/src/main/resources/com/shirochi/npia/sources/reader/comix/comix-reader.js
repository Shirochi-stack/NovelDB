/*
 * NovelDB's Comix adapter. Like Comix Downloader
 * (https://github.com/N3uralCreativity/comix-downloader), it reads data from the
 * site's running browser app so API changes do not require shipping its cipher.
 */
(() => {
    "use strict";
    const hosts = ["comix.to", "comix.ws"];
    const titlePath = /^\/title\/([a-z0-9]+)(?:-[^/]+)?\/?$/i;
    const chapterPath = /^\/title\/([a-z0-9]+)(?:-[^/]+)?\/(\d+)(?:-chapter-([\d.]+))?(?:-[^/]+)?\/?$/i;
    const isSite = url => url.protocol === "https:" && hosts.includes(url.host.replace(/^www\./, ""));
    const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
    const escaped = value => value.replaceAll("&", "&amp;").replaceAll('"', "&quot;").replaceAll("<", "&lt;");

    async function waitFor(read, label, milliseconds = 12000) {
        const until = Date.now() + milliseconds;
        do {
            const value = read();
            if (value) return value;
            await pause(120);
        } while (Date.now() < until);
        throw new Error(typeof label === "function" ? label() : label);
    }

    function accessError(path, otherwise) {
        if (location.pathname.startsWith("/@waf/")) return "Comix requires human verification in the browser.";
        const response = performance.getEntriesByType("resource").reverse()
            .find(entry => new URL(entry.name).pathname === path);
        if (response?.responseStatus === 403) return "Comix refused the request (HTTP 403). Check the site in the browser.";
        if (response?.responseStatus === 429) return "Comix asked the reader to wait (HTTP 429).";
        return otherwise;
    }

    function chapterRows(hid) {
        const rows = [];
        for (const link of document.querySelectorAll("a.mchap-row__primary[href]")) {
            const url = new URL(link.href);
            const route = chapterPath.exec(url.pathname);
            if (!isSite(url) || !route || route[1] !== hid) continue;
            const name = link.textContent.trim().replace(/\s+/g, " ");
            const number = Number(/^(?:Ch\.|Chapter)\s*(\d+(?:\.\d+)?)/i.exec(name)?.[1] ?? route[3] ?? NaN);
            rows.push({url: url.href, id: route[2], name, number});
        }
        return rows;
    }

    async function readBook(requested) {
        const url = new URL(location.href);
        const hid = titlePath.exec(url.pathname)?.[1];
        const requestedUrl = new URL(requested);
        const requestedHid = (titlePath.exec(requestedUrl.pathname) || chapterPath.exec(requestedUrl.pathname))?.[1];
        const chapterList = "/api/v1/manga/" + requestedHid + "/chapters";
        if (!isSite(url) || !hid || hid !== requestedHid) {
            throw new Error(accessError(chapterList, "The Comix title page did not open."));
        }
        const state = JSON.parse(document.getElementById("initial-data")?.textContent || "{}");
        const meta = state.queries?.[JSON.stringify(["manga", "detail", hid])];
        if (!meta?.title) throw new Error("Comix did not return the title metadata.");
        let rows = await waitFor(() => {
            const found = chapterRows(hid);
            return found.length && found;
        }, () => accessError(chapterList, "Comix did not display its chapter list."));
        const chapters = new Map();
        const requestedChapter = chapterPath.exec(requestedUrl.pathname)?.[2];
        for (let page = 1; ; page++) {
            for (const row of rows) {
                const key = Number.isFinite(row.number) ? row.number : row.id;
                if (!chapters.has(key) || row.id === requestedChapter) chapters.set(key, row);
            }
            const pager = document.querySelector(".npager__num.is-active")?.closest(".npager");
            const next = [...(pager?.querySelectorAll("button.npager__num") || [])]
                .find(button => Number(button.textContent.trim()) === page + 1 && !button.disabled);
            if (!next) break;
            if (page >= 250) throw new Error("The Comix chapter list exceeded its page limit.");
            const previous = rows.map(row => row.id).join(",");
            next.click();
            rows = await waitFor(() => {
                const found = chapterRows(hid);
                const current = Number(pager?.querySelector(".npager__num.is-active")?.textContent.trim());
                return current === page + 1 && found.length && found.map(row => row.id).join(",") !== previous && found;
            }, () => accessError(chapterList, "Comix did not load the next chapter list page."));
        }
        const list = [...chapters.values()].sort((a, b) => (a.number || 0) - (b.number || 0));
        const bookUrl = url.origin + url.pathname;
        const cover = meta.poster?.large || meta.poster?.medium || meta.poster?.small;
        return {
            bookUrl, pageUrl: bookUrl, bookname: meta.title, ruleName: "Comix reader", engine: "comix",
            author: (meta.authors || []).map(person => person.title || person.name).filter(Boolean).join(", "),
            introduction: meta.synopsis || "",
            coverUrl: cover ? new URL(cover, bookUrl).href : "",
            chapterCount: list.length,
            chapters: list.map((row, i) => ({...row, number: i + 1, bookUrl})),
        };
    }

    // Runs in the chapter page. Use the loaded reader's query data to obtain even the
    // offscreen images without scrolling through or transferring all their bytes.
    async function readPages(id, hid, hosts, accessError, maximumWait) {
        const current = new URL(location.href);
        const endpoint = "/api/v1/chapters/" + id;
        const chapter = /^\/title\/([a-z0-9]+)(?:-[^/]+)?\/(\d+)(?:-[^/]+)?\/?$/i.exec(current.pathname);
        if (!hosts.includes(current.host.replace(/^www\./, "")) || chapter?.[1] !== hid || chapter?.[2] !== id) {
            return {error: accessError(endpoint, "The Comix chapter page did not open.")};
        }
        function readerQuery() {
            const app = document.getElementById("app-root");
            const key = app && Object.keys(app).find(name => name.startsWith("__reactContainer$"));
            if (!key) return null;
            const fiber = app[key];
            const queue = [fiber.stateNode?.current || fiber];
            for (let i = 0; i < queue.length && i < 400; i++) {
                const node = queue[i];
                if (!node) continue;
                const client = node.memoizedProps?.client;
                if (typeof client?.getQueryCache === "function") {
                    const query = client.getQueryCache().find({queryKey: ["chapters", "detail", Number(id)], exact: true});
                    return query?.state?.data;
                }
                queue.push(node.child, node.sibling);
            }
            return null;
        }
        function imageUrl(row) {
            const candidate = typeof row === "string" ? row : Object.values(row || {}).find(v => typeof v === "string" && v.startsWith("https://"));
            if (!candidate) return "";
            const url = new URL(candidate);
            return url.protocol === "https:" && !url.username && !url.password && !url.port ? url.href : "";
        }
        const until = Date.now() + maximumWait;
        do {
            const data = readerQuery();
            const count = document.querySelectorAll(".rpage-progress__seg").length;
            const sample = document.querySelector(".rpage-page[data-page='1'] img[src]")?.src;
            if (data && count && count <= 2000) {
                // The keys inside this object change with the site's build. Match the
                // ordered image array against the page count and the rendered first page.
                for (const list of Object.values(data).filter(Array.isArray)) {
                    if (list.length !== count) continue;
                    const images = list.map(imageUrl);
                    if (images.every(Boolean) && (!sample || sample === images[0])) return {images};
                }
            }
            const pages = [...document.querySelectorAll(".rpage-page[data-page]")];
            if (count && pages.length === count) {
                const images = pages.map(page => imageUrl(page.querySelector("img[src]")?.src));
                if (images.every(Boolean)) return {images};
            }
            await new Promise(resolve => setTimeout(resolve, 120));
        } while (Date.now() < until);
        return {error: accessError(endpoint, "Comix did not provide a complete chapter image list.")};
    }

    async function readChapter(item) {
        const url = new URL(item.url, location.href);
        const route = chapterPath.exec(url.pathname);
        if (!isSite(url) || !route) throw new Error("Invalid Comix chapter address.");
        const run = window.__COMIX_transport;
        if (typeof run !== "function") throw new Error("The Comix browser transport is unavailable.");
        const script = `(${readPages})(${JSON.stringify(route[2])},${JSON.stringify(route[1])},${JSON.stringify(hosts)},${accessError},12000)`;
        let failure = "";
        const other = hosts.find(host => host !== url.host.replace(/^www\./, ""));
        for (const address of [url.href, "https://" + other + url.pathname + url.search]) {
            const reply = await run({op: "window", url: address, script});
            if (reply?.value?.images?.length) {
                const images = reply.value.images;
                return {chapterName: item.name || "", contentText: "", comic: true, imageCount: images.length,
                    contentHtml: images.map(url => `<p><img src="${escaped(url)}" alt=""/></p>`).join("")};
            }
            failure = reply?.value?.error || reply?.error || "Comix returned no images.";
        }
        throw new Error(failure);
    }

    const json = async work => {
        try { return JSON.stringify(await work()); }
        catch (error) { return JSON.stringify({error: error.message}); }
    };
    window.__COMIX_parseBook = url => json(() => readBook(url));
    window.__COMIX_parseChapter = item => json(() => readChapter(JSON.parse(item)));
    window.__COMIX_parseChapterBatch = async items => {
        const result = [];
        for (const item of JSON.parse(items)) result.push(JSON.parse(await window.__COMIX_parseChapter(JSON.stringify(item))));
        return JSON.stringify(result);
    };
    window.__COMIX_API = 1;
    window.__COMIX_READY = true;
})();
