
// -- NovelDB bridge ---------------------------------------------------------
// window.__LNR_parseBook(url)                -> JSON book info and chapter list
// window.__LNR_parseChapter(json)            -> JSON chapter {chapterName, contentHtml, contentText}
// window.__LNR_parseChapterBatch(json)       -> JSON array of chapter JSON strings
// window.__LNR_search(id, term, page)        -> JSON [{name, url, cover, plugin}] from the site's own search
// window.__LNR_popular(id, page, latest)     -> JSON [{name, url, cover, plugin}] of the site's popular list
// window.__LNR_sites()                       -> JSON [{id, name, site, lang}]
// Results use the same shape as the other engines' bridges, so the app's
// pipeline treats every engine alike. Images stay absolute <img src> URLs.
__lnr_file = "bridge";

const LNR_API = 1;

function __lnr_error(error, extra) {
    return Object.assign({
        error: String((error && error.message) || error),
        stack: String((error && error.stack) || "").substring(0, 2000),
        log: __lnr_errors.slice(-5)
    }, extra || {});
}

function __lnr_host(url) {
    try {
        return new URL(url).hostname.toLowerCase().replace(/^www\./, "");
    } catch (e) {
        return "";
    }
}

function __lnr_site(plugin) {
    let site = String(plugin.site || "");
    return site.endsWith("/") ? site : site + "/";
}

// The plugins listed for a page's site, most specific first.
function __lnr_candidates(url) {
    let host = __lnr_host(url);
    let out = [];
    for (let plugin of __lnr_plugins.values()) {
        if (__lnr_host(plugin.site) === host) {
            out.push(plugin.id);
        }
    }
    return out;
}

// Plugins name a novel or chapter by its path on the site. Most take it
// without the leading slash ("novel/x/"), some with it, a few take the whole
// address; try them in that order.
function __lnr_paths(plugin, url) {
    let address = new URL(url);
    let base = new URL(__lnr_site(plugin));
    let path = address.pathname;
    if (path.startsWith(base.pathname)) {
        path = path.substring(base.pathname.length);
    } else {
        path = path.replace(/^\/+/, "");
    }
    path += address.search;
    return [...new Set([path, "/" + path, address.href])];
}

function __lnr_resolve(plugin, instance, path, isNovel) {
    if (path == null || path === "") {
        return null;
    }
    try {
        if (typeof instance.resolveUrl === "function") {
            let resolved = instance.resolveUrl(path, isNovel);
            if (resolved) {
                return new URL(resolved, __lnr_site(plugin)).href;
            }
        }
    } catch (e) {
        // fall back to the site address
    }
    try {
        return new URL(path, __lnr_site(plugin)).href;
    } catch (e) {
        return null;
    }
}

function __lnr_cover(plugin, url) {
    if (!url || url === __lnr_defaultCover || /coverNotAvailable/.test(url)) {
        return null;
    }
    try {
        return new URL(url, __lnr_site(plugin)).href;
    } catch (e) {
        return null;
    }
}

function __lnr_text(value) {
    return (value == null) ? "" : String(value).replace(/\s+/g, " ").trim();
}

function __lnr_plainSummary(summary) {
    let text = String(summary || "");
    if (/<[a-z][\s\S]*>/i.test(text)) {
        let dom = new DOMParser().parseFromString(text, "text/html");
        dom.querySelectorAll("br").forEach(br => br.replaceWith("\n"));
        dom.querySelectorAll("p, div, li").forEach(el => el.insertAdjacentText("afterend", "\n"));
        text = dom.body.textContent || "";
    }
    return text.split("\n").map(line => line.trim()).filter(line => line).join("\n");
}

async function __lnr_chapterPages(instance, path, novel, append, active, streaming) {
    let total = Math.min(Number(novel.totalPages) || 0, 1000);
    if (total <= 0 || typeof instance.parsePage !== "function") {
        return;
    }
    let pages = [];
    for (let page = 1; page <= total; page++) {
        pages.push(String(page));
    }
    for (let i = 0; i < pages.length && active();) {
        // Get the first page out before waiting for any other page in the batch.
        let size = streaming && i === 0 ? 1 : 5;
        let batch = pages.slice(i, i + size).map(async page => {
            try {
                let result = await instance.parsePage(path, page);
                return (result && result.chapters) || [];
            } catch (error) {
                __lnr_errors.push("page " + page + ": " + ((error && error.message) || error));
                return [];
            }
        });
        for (const pending of batch) {
            let chapters = await pending;
            if (active()) append(chapters);
        }
        i += size;
    }
}

async function __lnr_scribbleNovel(instance, path) {
    let complete = null;
    let reader = Object.create(instance);
    reader.fetchAllChapters = async (...args) => {
        complete = await instance.fetchAllChapters(...args);
        return complete;
    };
    let novel = await reader.parseNovel(path);
    if (!(complete && complete.length)) {
        throw new Error("ScribbleHub returned no complete chapter list");
    }
    return novel;
}

async function __lnr_book(url, onChapters, active = () => true) {
    let candidates = __lnr_candidates(url);
    if (!candidates.length) {
        return { error: "LNReader has no plugin for this site", noParser: true };
    }
    let lastError = null;
    for (let id of candidates) {
        let plugin = __lnr_plugins.get(id);
        let instance;
        try {
            instance = __lnr_instance(id);
        } catch (error) {
            lastError = error;
            continue;
        }
        for (let path of __lnr_paths(plugin, url)) {
            if (!active()) return __lnr_error("the reader was closed");
            let novel;
            try {
                novel = id === "scribblehub" ? await __lnr_scribbleNovel(instance, path) : await instance.parseNovel(path);
            } catch (error) {
                lastError = error;
                continue;
            }
            if (!novel || (!novel.name && !(novel.chapters || []).length && !novel.totalPages)) {
                continue;
            }
            let seen = new Set();
            let list = [];
            function append(chapters) {
                let start = list.length;
                for (const chapter of chapters) {
                    if (!chapter || chapter.path == null || seen.has(chapter.path)) continue;
                    seen.add(chapter.path);
                    list.push({
                        url: __lnr_resolve(plugin, instance, chapter.path, false) || chapter.path,
                        name: __lnr_text(chapter.name) || ("Chapter " + (list.length + 1)),
                        number: list.length + 1,
                        path: chapter.path,
                        plugin: id,
                        releaseTime: chapter.releaseTime || null,
                        isVIP: false,
                        isPaid: false
                    });
                }
                if (onChapters && list.length > start && active()) onChapters({chapters: list.slice()});
            }
            append(novel.chapters || []);
            await __lnr_chapterPages(instance, novel.path || path, novel, append, active, !!onChapters);
            if (!list.length && !novel.name) {
                continue;
            }
            let tags = String(novel.genres || "").split(/[,\n]/).map(tag => tag.trim()).filter(tag => tag);
            return {
                api: LNR_API,
                engine: "lnreader",
                plugin: id,
                pluginName: plugin.name,
                novelPath: novel.path || path,
                ruleName: "LNReader " + plugin.name,
                bookUrl: url,
                bookname: __lnr_text(novel.name),
                author: __lnr_text(novel.author),
                introduction: __lnr_plainSummary(novel.summary),
                introductionHTML: "",
                coverUrl: __lnr_cover(plugin, novel.cover),
                tags: tags,
                status: novel.status || "",
                language: plugin.lang || "",
                chapterCount: list.length,
                chapters: list,
                warnings: __lnr_errors.slice(-5)
            };
        }
    }
    return __lnr_error(lastError || "The LNReader plugin found no novel on this page");
}

window.__LNR_parseBook = async function (url, onChapters, active) {
    try {
        __lnr_errors.length = 0;
        return JSON.stringify(await __lnr_book(url || location.href, onChapters, active));
    } catch (error) {
        return JSON.stringify(__lnr_error(error));
    }
};

// One paragraph per line, as the other engines give the TXT output.
function __lnr_plainText(container) {
    let clone = container.cloneNode(true);
    clone.querySelectorAll("br").forEach(br => br.replaceWith("\n"));
    let blocks = "p, div, h1, h2, h3, h4, h5, h6, li, blockquote, pre, tr, hr, section, article, figure";
    clone.querySelectorAll(blocks).forEach(element => {
        element.insertAdjacentText("beforebegin", "\n");
        element.insertAdjacentText("afterend", "\n");
    });
    return clone.textContent.split("\n")
        .map(line => line.replace(/\s+/g, " ").trim())
        .filter(line => line)
        .join("\n");
}

const __lnr_lazyAttributes = ["data-src", "data-lazy-src", "data-original", "data-lazy"];

function __lnr_chapterHtml(html, base) {
    let dom = new DOMParser().parseFromString("<div id=\"lnr-root\">" + String(html || "") + "</div>", "text/html");
    let root = dom.getElementById("lnr-root");
    root.querySelectorAll("script, style, iframe, noscript, form, button, ins").forEach(el => el.remove());
    for (let img of root.querySelectorAll("img")) {
        let src = null;
        for (let name of __lnr_lazyAttributes) {
            if (img.getAttribute(name)) {
                src = img.getAttribute(name);
                break;
            }
        }
        src = src || img.getAttribute("src");
        let absolute = null;
        try {
            absolute = src ? new URL(src, base).href : null;
        } catch (e) {
            absolute = null;
        }
        ["srcset", "sizes", "loading"].concat(__lnr_lazyAttributes).forEach(name => img.removeAttribute(name));
        if (absolute == null || (absolute.startsWith("data:") && absolute.length < 200)) {
            img.remove();
        } else {
            img.setAttribute("src", absolute);
        }
    }
    for (let link of root.querySelectorAll("a[href]")) {
        try {
            link.setAttribute("href", new URL(link.getAttribute("href"), base).href);
        } catch (e) {
            link.removeAttribute("href");
        }
    }
    return root;
}

function __lnr_chapterTarget(item) {
    let id = item.plugin;
    let path = item.path;
    if (!id || !__lnr_plugins.has(id)) {
        id = __lnr_candidates(item.url)[0];
    }
    if (!id) {
        throw new Error("LNReader has no plugin for " + item.url);
    }
    if (path == null || path === "") {
        path = __lnr_paths(__lnr_plugins.get(id), item.url)[0];
    }
    return { id: id, path: path };
}

async function __lnr_chapter(item) {
    let target = __lnr_chapterTarget(item);
    let instance = __lnr_instance(target.id);
    let html = await instance.parseChapter(target.path);
    let root = __lnr_chapterHtml(html, item.url || __lnr_site(__lnr_plugins.get(target.id)));
    if (!root.textContent.trim() && !root.querySelector("img")) {
        throw new Error("The chapter has no visible text: " + item.url);
    }
    return {
        chapterName: item.name || "",
        contentHtml: root.innerHTML,
        contentText: __lnr_plainText(root),
        imageCount: 0,
        images: []
    };
}

window.__LNR_parseChapter = async function (itemJson) {
    try {
        let item = (typeof itemJson === "string") ? JSON.parse(itemJson) : itemJson;
        return JSON.stringify(await __lnr_chapter(item));
    } catch (error) {
        return JSON.stringify(__lnr_error(error));
    }
};

window.__LNR_parseChapterBatch = async function (itemsJson) {
    try {
        let items = (typeof itemsJson === "string") ? JSON.parse(itemsJson) : itemsJson;
        let results = await Promise.all(items.map(item => window.__LNR_parseChapter(item)));
        return JSON.stringify(results);
    } catch (error) {
        return JSON.stringify(__lnr_error(error));
    }
};

function __lnr_items(id, novels) {
    let plugin = __lnr_plugins.get(id);
    let instance = __lnr_instance(id);
    return (novels || []).filter(novel => novel && novel.path != null).map(novel => ({
        name: __lnr_text(novel.name),
        url: __lnr_resolve(plugin, instance, novel.path, true),
        cover: __lnr_cover(plugin, novel.cover),
        path: novel.path,
        plugin: id,
        site: plugin.site
    })).filter(novel => novel.url && novel.name);
}

window.__LNR_search = async function (id, term, page) {
    try {
        let found = await __lnr_instance(id).searchNovels(String(term || ""), Number(page) || 1);
        return JSON.stringify(__lnr_items(id, found));
    } catch (error) {
        return JSON.stringify(__lnr_error(error));
    }
};

window.__LNR_popular = async function (id, page, latest) {
    try {
        let instance = __lnr_instance(id);
        let filters = {};
        for (let [key, filter] of Object.entries(instance.filters || {})) {
            filters[key] = { type: filter.type, value: filter.value };
        }
        let found = await instance.popularNovels(Number(page) || 1, { showLatestNovels: !!latest, filters: filters });
        return JSON.stringify(__lnr_items(id, found));
    } catch (error) {
        return JSON.stringify(__lnr_error(error));
    }
};

// Books of a genre or tag: the plugin's own filter when one of its options has that name, else a search.
window.__LNR_genre = async function (id, tag) {
    try {
        let instance = __lnr_instance(id);
        let words = text => String(text || "").trim().toLowerCase().replace(/[\s_\-]+/g, " ");
        let want = words(tag);
        let filters = {}, matched = false;
        for (let [key, filter] of Object.entries(instance.filters || {})) {
            let value = filter.value;
            let option = (filter.options || []).find(o => words(o.label) === want);
            if (option && !matched) {
                matched = true;
                if (filter.type === __lnr_FilterTypes.Picker) {
                    value = option.value;
                } else if (filter.type === __lnr_FilterTypes.CheckboxGroup) {
                    value = [option.value];
                } else if (filter.type === __lnr_FilterTypes.ExcludableCheckboxGroup) {
                    value = { include: [option.value], exclude: [] };
                } else {
                    matched = false;
                }
            }
            filters[key] = { type: filter.type, value: value };
        }
        let found = matched
            ? await instance.popularNovels(1, { showLatestNovels: false, filters: filters })
            : await instance.searchNovels(String(tag || ""), 1);
        // "verified": the plugin's own filter for the tag (a search for its words may find anything).
        return JSON.stringify(__lnr_items(id, found).map(item => Object.assign(item, { verified: matched })));
    } catch (error) {
        return JSON.stringify(__lnr_error(error));
    }
};

window.__LNR_sites = function () {
    return JSON.stringify(Array.from(__lnr_plugins.values(), plugin => ({
        id: plugin.id, name: plugin.name, site: plugin.site, lang: plugin.lang
    })));
};

window.__LNR_info = function () {
    return JSON.stringify(Object.assign({ api: LNR_API, sites: __lnr_plugins.size }, __LNR_META));
};

// The network shim itself, for checks and diagnostics.
window.__LNR_fetch = fetch;
window.__LNR_API = LNR_API;
window.__LNR_READY = true;
__lnr_log("LNReader " + String(__LNR_META.commit).substring(0, 12) + " ready, " + __lnr_plugins.size + " plugins");

} catch (error) {
    window.__LNR_LOAD_ERROR = { file: __lnr_file, message: String((error && error.message) || error) };
    console.error("[LNR] failed to load " + __lnr_file + ": " + error);
}
})();
