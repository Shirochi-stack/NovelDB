
// -- NovelDB bridge ---------------------------------------------------------
// window.__W2E_parseBook()            -> JSON book info and chapter list
// window.__W2E_parseChapter(json)     -> JSON chapter {chapterName, contentHtml}
// window.__W2E_parseChapterBatch(json)-> JSON array of chapter JSON strings
// Results use the same shape as the novel-downloader bridge, so the app's
// pipeline treats both engines alike. Images stay as absolute <img src> URLs
// for the app's own image downloader.
__w2e_file = "bridge";

const W2E_API = 1;

ErrorLog.showErrorMessage = function (message) {
    let text = (message && message.message) ? message.message : String(message);
    __w2e_errors.push(text);
    __w2e_log(text);
    // A retry prompt waits for a click that never comes; give up instead.
    if (message && typeof message.cancelAction === "function") {
        try {
            message.cancelAction();
        } catch (e) {
            // the request already failed
        }
    }
};
ErrorLog.log = function (error) {
    __w2e_errors.push(String((error && error.message) || error));
};
ErrorLog.showLogToUser = () => undefined;
ErrorLog.clearHistory = () => undefined;

// The extension waits up to minutes between retries and then asks the user.
// The app has its own retries, so keep these short and never prompt.
FetchErrorHandler.show429Error = () => undefined;
FetchErrorHandler.getAutomaticRetryBehaviourForStatus = function (response) {
    switch (response.status) {
        case 999:
            return { retryDelay: (response.retryDelay || []).slice(), promptUser: false };
        case 429:
        case 500:
        case 502:
        case 503:
        case 504:
        case 509:
        case 520:
        case 522:
        case 524:
        case 445:
            return { retryDelay: [6, 3], promptUser: false };
        default:
            return { retryDelay: [], promptUser: false };
    }
};

function __w2e_preferences() {
    if (__w2e_prefs == null) {
        // Defaults only: the extension's saved settings live in the site's storage here.
        __w2e_prefs = new UserPreferences();
    }
    return __w2e_prefs;
}

function __w2e_error(error, extra) {
    return Object.assign({
        error: String((error && error.message) || error),
        stack: String((error && error.stack) || "").substring(0, 2000),
        log: __w2e_errors.slice(-5)
    }, extra || {});
}

function __w2e_firstPage(url) {
    let html = __w2e_realDocument.documentElement.outerHTML;
    let dom = new DOMParser().parseFromString(html, "text/html");
    util.setBaseTag(url, dom);
    return dom;
}

// A parser written for this site (registered host or URL rule), or one that
// recognises the page (for example a site theme). The generic fallback parser
// treats every link as a chapter, so it is never used.
function __w2e_findParser(url, dom) {
    let parser = parserFactory.fetchByUrl(url);
    if (parser != null) {
        return { parser: parser, match: "site" };
    }
    parser = parserFactory.fetch(url, dom);
    if (parser != null && !(parser instanceof DefaultParser)) {
        return { parser: parser, match: "page" };
    }
    return { parser: null, match: "" };
}

function __w2e_text(value) {
    if (value == null) {
        return "";
    }
    if (value.textContent !== undefined) {
        value = value.textContent;
    }
    return String(value).trim();
}

function __w2e_absolute(url, base) {
    if (!url) {
        return null;
    }
    try {
        return new URL(url, base).href;
    } catch (e) {
        return null;
    }
}

function __w2e_meta(parser, dom) {
    try {
        return parser.getEpubMetaInfo(dom, true);
    } catch (e) {
        return {};
    }
}

window.__W2E_parseBook = async function () {
    try {
        __w2e_errors.length = 0;
        let url = location.href;
        let dom = __w2e_firstPage(url);
        let found = __w2e_findParser(url, dom);
        if (found.parser == null) {
            return JSON.stringify({ error: "WebToEpub has no parser for this site", noParser: true });
        }
        let parser = found.parser;
        let disabled = (typeof parser.disabled === "function") ? parser.disabled() : null;
        if (disabled != null) {
            return JSON.stringify({ error: String(disabled), noParser: true });
        }
        parser.onUserPreferencesUpdate(__w2e_preferences());
        __w2e_state = { url: url, parser: parser, dom: dom };

        // Same order as the extension: metadata first, then the chapter list.
        await parser.loadEpubMetaInfo(dom);
        let meta = __w2e_meta(parser, dom);
        parser.state.firstPageDom = dom;
        parser.state.chapterListUrl = url;
        let chapters = await parser.getChapterUrls(dom, new ChapterUrlsUI(parser)) || [];
        chapters = parser.cleanWebPageUrls(chapters);
        chapters.forEach(chapter => { chapter.title = __w2e_text(chapter.title); });
        parser.state.setPagesToFetch(chapters);
        // Some parsers only learn the description while reading the chapter list.
        let later = __w2e_meta(parser, dom);
        for (let key of ["title", "author", "description", "subject", "language"]) {
            if (!meta[key] && later[key]) {
                meta[key] = later[key];
            }
        }

        let cover = null;
        try {
            cover = __w2e_absolute(parser.findCoverImageUrl(dom), url);
        } catch (e) {
            cover = null;
        }
        let section = null;
        let list = chapters.map((chapter, index) => {
            if (chapter.newArc) {
                section = __w2e_text(chapter.newArc);
            }
            return {
                url: chapter.sourceUrl,
                name: chapter.title || ("Chapter " + (index + 1)),
                number: index + 1,
                sectionName: section,
                isVIP: false,
                isPaid: chapter.isIncludeable === false
            };
        });
        let tags = String(meta.subject || "").split(",").map(tag => tag.trim()).filter(tag => tag);
        return JSON.stringify({
            api: W2E_API,
            engine: "webtoepub",
            parser: parser.constructor.name,
            match: found.match,
            ruleName: "WebToEpub " + parser.constructor.name,
            bookUrl: url,
            bookname: __w2e_text(meta.title),
            author: (meta.author === "<unknown>") ? "" : __w2e_text(meta.author),
            introduction: __w2e_text(meta.description),
            introductionHTML: "",
            coverUrl: cover,
            tags: tags,
            language: meta.language || "en",
            chapterCount: list.length,
            chapters: list,
            warnings: __w2e_errors.slice(-5)
        });
    } catch (error) {
        return JSON.stringify(__w2e_error(error));
    }
};

const __w2e_lazyAttributes = [
    "srcset", "sizes", "loading", "data-src", "data-srcset", "data-lazy-src",
    "data-lazy-srcset", "data-orig-file", "data-original", "data-lazy"
];

// Keep each image as an absolute, highest-resolution URL instead of the
// extension's EPUB-internal reference.
function __w2e_fixImages(collector, element) {
    for (let img of element.querySelectorAll("img")) {
        let src = null;
        try {
            collector.fixLazyLoadImageSource(img);
            src = collector.findHighestResImage(img);
        } catch (e) {
            src = img.getAttribute("src");
        }
        src = __w2e_absolute(src || img.getAttribute("src"), img.ownerDocument.baseURI);
        __w2e_lazyAttributes.forEach(name => img.removeAttribute(name));
        if (src == null || (src.startsWith("data:") && src.length < 200)) {
            img.remove();
        } else {
            img.setAttribute("src", src);
        }
    }
}

function __w2e_convert(parser, webPage) {
    let collector = parser.imageCollector;
    let replaceImageTags = collector.replaceImageTags;
    let addTitleToContent = parser.addTitleToContent;
    // Conversion is synchronous, so swapping these two hooks cannot leak into
    // another chapter that is converting at the same time.
    collector.replaceImageTags = (element) => __w2e_fixImages(collector, element);
    parser.addTitleToContent = function (page) {
        let title = __w2e_text(this.findChapterTitle(page.rawDom, page));
        if (title && (!page.title || page.title === "[placeholder]")) {
            page.title = title;
        }
    };
    try {
        return parser.webPageToEpubItems(webPage, 0) || [];
    } finally {
        collector.replaceImageTags = replaceImageTags;
        parser.addTitleToContent = addTitleToContent;
    }
}

function __w2e_itemsToHtml(items, chapterTitle) {
    let container = __w2e_realDocument.createElement("div");
    items.forEach((item, index) => {
        if (index > 0 && item.chapterTitle) {
            let heading = __w2e_realDocument.createElement("h2");
            heading.textContent = item.chapterTitle;
            container.appendChild(heading);
        }
        for (let node of (item.nodes || [])) {
            container.appendChild(__w2e_realDocument.importNode(node, true));
        }
    });
    // The app writes the chapter title itself, so drop a heading that opens
    // the text with the same words ("Chapter 1: X" and "Chapter 1 - X" match).
    let heading = container.querySelector("h1, h2, h3, h4");
    let words = (text) => __w2e_text(text).toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
    if (heading && words(chapterTitle) && words(heading) === words(chapterTitle)
        && words(container).startsWith(words(heading))) {
        heading.remove();
    }
    return container;
}

// One paragraph per line, as the other engines give the TXT output.
function __w2e_plainText(container) {
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

async function __w2e_chapter(item) {
    let state = __w2e_state;
    if (state == null) {
        throw new Error("No WebToEpub parser; parse the book first");
    }
    let url = item.url;
    let known = state.parser.state.webPages.get(url);
    let webPage = Object.assign({ isIncludeable: true, nextPrevChapters: new Set() }, known || {}, {
        sourceUrl: url,
        title: (known && known.title) || item.name || "[placeholder]"
    });
    delete webPage.rawDom;
    if (webPage.parser == null) {
        await parserFactory.addParsersToPages(state.parser, [webPage]);
    }
    let parser = webPage.parser || state.parser;
    let raw = await parser.fetchChapter(url);
    if (raw == null) {
        throw new Error("The chapter page was empty");
    }
    webPage.rawDom = raw;
    parser.preprocessRawDom(raw);
    parser.removeUnusedElementsToReduceMemoryConsumption(raw);
    let content = parser.findContent(raw);
    if (content == null) {
        throw new Error("Could not find the chapter text on " + url);
    }
    await parser.imageCollector.preprocessImageTags(content, url);
    let items = __w2e_convert(parser, webPage);
    let container = __w2e_itemsToHtml(items, webPage.title);
    __w2e_fixImages(parser.imageCollector, container);
    if (!container.textContent.trim() && !container.querySelector("img")) {
        throw new Error("The chapter has no visible text: " + url);
    }
    let title = (webPage.title && webPage.title !== "[placeholder]") ? webPage.title : (item.name || "");
    return {
        chapterName: title,
        contentHtml: container.innerHTML,
        contentText: __w2e_plainText(container),
        imageCount: 0,
        images: []
    };
}

window.__W2E_parseChapter = async function (itemJson) {
    try {
        let item = (typeof itemJson === "string") ? JSON.parse(itemJson) : itemJson;
        return JSON.stringify(await __w2e_chapter(item));
    } catch (error) {
        return JSON.stringify(__w2e_error(error));
    }
};

window.__W2E_parseChapterBatch = async function (itemsJson) {
    try {
        let items = (typeof itemsJson === "string") ? JSON.parse(itemsJson) : itemsJson;
        let results = await Promise.all(items.map(item => window.__W2E_parseChapter(item)));
        return JSON.stringify(results);
    } catch (error) {
        return JSON.stringify(__w2e_error(error));
    }
};

window.__W2E_info = function () {
    return JSON.stringify(Object.assign({ api: W2E_API, sites: parserFactory.parsers.size }, __W2E_META));
};

// The network shim itself, for checks and diagnostics.
window.__W2E_fetch = fetch;
window.__W2E_API = W2E_API;
window.__W2E_READY = true;
__w2e_log("WebToEpub " + __W2E_META.commit.substring(0, 12) + " ready, " + parserFactory.parsers.size + " sites");

} catch (error) {
    window.__W2E_LOAD_ERROR = { file: __w2e_file, message: String((error && error.message) || error) };
    console.error("[W2E] failed to load " + __w2e_file + ": " + error);
}
})();
