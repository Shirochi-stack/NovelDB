/*
 * The generic fallback engine for the mobile apps: any site's metadata, chapter list and chapter
 * text, without site rules. A port of the desktop's noveldb/core/engines/generic.py; keep the two
 * in step. The Android and iOS apps ship copies of this file (tests/test_generic_lib.py checks them).
 *
 * It runs in the book's own web view page, so the page the site's scripts built is the first one read
 * (the desktop's "render"); a list that loads as you scroll is scrolled here. Further pages come
 * through the page's fetch (same site) or the app's transport (other sites, refused requests).
 *
 * window.__GEN_parseBook(url)        -> JSON book {bookname, author, ..., chapters: [{url, name}]}, or {error}
 * window.__GEN_parseChapter(json)    -> JSON chapter {chapterName, contentHtml, contentText}, or {error}
 */
(function () {
if (window.__GEN_READY) { return; }
try {

const GEN_API = 1;
const nativeFetch = window.fetch.bind(window);

const CHAPTER_WORDS = new RegExp(
    "(chapter|chap\\b|ch\\.?\\s*\\d|episode|ep\\.?\\s*\\d|\\bpart\\s*\\d|prologue|epilogue|side ?story|extra|" +
    "глава|capítulo|capitulo|chapitre|kapitel|bölüm|bab\\s*\\d|chương|ตอน|" +
    "第\\s*[\\d零一二三四五六七八九十百千两]+\\s*[章节話话回卷集]|\\d+\\s*[화話话章回]|[화話]\\s*$)", "i");
const NEXT_WORDS = new RegExp(
    "^\\s*(next(\\s+chapter)?|next\\s*»|»|›|→|下一章|下一页|下章|下一話|下一话|다음\\s*화?|다음 ?페이지|次へ|次の話|" +
    "siguiente|suivant|próximo|weiter|далее|следующая|sonraki)\\s*[»›>→]*\\s*$", "i");
const NEXT_PAGE_WORDS = /(下一页|下页|next\s*page|다음\s*페이지|次のページ|página siguiente|page suivante)/i;
const BAD_BLOCKS = new RegExp(
    "(comment|share|social|sidebar|footer|header|nav|menu|breadcrumb|related|recommend|" +
    "advert|\\bads?\\b|sponsor|banner|popup|modal|cookie|subscribe|newsletter|login|rating|" +
    "widget|pagination|pager|toolbar|copyright)", "i");
const GOOD_BLOCKS = new RegExp(
    "(chapter|content|text|reader|reading|entry|article|post-body|novel|story|txt|" +
    "main|nr_?content|booktxt|bookcontent)", "i");
const SECOND_LEVEL = new Set(["co", "com", "ne", "net", "or", "org", "go", "ac", "ed", "gov", "edu"]);

const MAX_LIST_PAGES = 150;
const MAX_CHAIN = 400;
const MAX_CHAPTER_PAGES = 15;
const MAX_MANGA_PAGES = 250;

// -- pages ------------------------------------------------------------------------------------------

const MOJIBAKE = /[ÂÃâ][\u0080-¿€‚-„‘-”™œš]/;
const WESTERN = ["iso-8859-1", "latin-1", "latin1", "windows-1252", "cp1252", "us-ascii", "ascii"];
// The characters cp1252 puts at 0x80-0x9f.
const CP1252 = {
    0x20ac: 0x80, 0x201a: 0x82, 0x0192: 0x83, 0x201e: 0x84, 0x2026: 0x85, 0x2020: 0x86, 0x2021: 0x87,
    0x02c6: 0x88, 0x2030: 0x89, 0x0160: 0x8a, 0x2039: 0x8b, 0x0152: 0x8c, 0x017d: 0x8e, 0x2018: 0x91,
    0x2019: 0x92, 0x201c: 0x93, 0x201d: 0x94, 0x2022: 0x95, 0x2013: 0x96, 0x2014: 0x97, 0x02dc: 0x98,
    0x2122: 0x99, 0x0161: 0x9a, 0x203a: 0x9b, 0x0153: 0x9c, 0x017e: 0x9e, 0x0178: 0x9f
};

/** UTF-8 text that was read as Latin-1 ("â€™" for "’"), put right. */
function repair(text) {
    if (!text || !MOJIBAKE.test(text)) {
        return text;
    }
    let bytes = new Uint8Array(text.length);
    for (let i = 0; i < text.length; i++) {
        let code = text.charCodeAt(i);
        if (code < 0x100) {
            bytes[i] = code;
        } else if (CP1252[code] !== undefined) {
            bytes[i] = CP1252[code];
        } else {
            return text;
        }
    }
    try {
        return new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    } catch (e) {
        return text;
    }
}

/** The page's text in the charset its meta tag or headers declare; a Western or missing one that is valid UTF-8 is UTF-8. */
function decode(bytes, headers) {
    let head = "";
    for (let i = 0; i < Math.min(bytes.length, 4096); i++) {
        head += String.fromCharCode(bytes[i]);
    }
    let match = /<meta[^>]+charset\s*=\s*["']?\s*([\w-]+)/i.exec(head) || /charset=([\w-]+)/i.exec(String(headers || ""));
    let charset = (match ? match[1] : "utf-8").toLowerCase();
    charset = { gb2312: "gb18030", gbk: "gb18030", "x-gbk": "gb18030" }[charset] || charset;
    if (WESTERN.includes(charset) || charset === "utf-8" || charset === "utf8") {
        try {
            return repair(new TextDecoder("utf-8", { fatal: true }).decode(bytes));
        } catch (e) {
            // not UTF-8 after all
        }
    }
    try {
        return repair(new TextDecoder(charset).decode(bytes));
    } catch (e) {
        return repair(new TextDecoder("utf-8").decode(bytes));
    }
}

function transport() {
    return window.__GEN_transport || window.__noveldb_engine_transport || window.__npia_gm_xmlhttp_request || null;
}

function base64ToBytes(text) {
    let binary = atob(text || "");
    let bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) {
        bytes[i] = binary.charCodeAt(i);
    }
    return bytes;
}

/** {html, url} of a page: the browser for the page's own site, the app's transport otherwise (or when refused). */
async function load(url, referer) {
    let bytes = null;
    let headers = "";
    let finalUrl = url;
    let status = 0;
    let carrier = transport();
    let sameSite = new URL(url).origin === location.origin;
    if (sameSite || carrier == null) {
        try {
            let response = await nativeFetch(url, { credentials: "include" });
            status = response.status;
            finalUrl = response.url || url;
            headers = "content-type: " + (response.headers.get("content-type") || "");
            bytes = new Uint8Array(await response.arrayBuffer());
        } catch (error) {
            if (carrier == null) {
                throw error;
            }
        }
    }
    if (bytes == null) {
        let request = {
            url: url, method: "GET", headers: referer ? { Referer: referer } : {},
            responseType: "arraybuffer", timeout: 60000
        };
        let result = await carrier(request);
        if (result && result.pending && window.__noveldb_async) {
            result = await window.__noveldb_async.wait(result.pending);
        }
        if (result == null || result.error) {
            throw new Error("could not load " + url + (result && result.error ? " (" + result.error + ")" : ""));
        }
        status = Number(result.status) || 0;
        finalUrl = result.finalUrl || url;
        headers = result.responseHeaders || "";
        bytes = (result.responseBase64 != null && result.responseBase64 !== "")
            ? base64ToBytes(result.responseBase64)
            : new TextEncoder().encode(result.responseText || "");
    }
    let html = decode(bytes, headers);
    if ((REFUSED.includes(status) || (status < 400 && CHALLENGE.test(html.slice(0, 3000)))) && carrier != null) {
        // A bot check or a refusal: the page as a browser shows it, from a hidden page of the app.
        let shown = await carrier({ op: "window", url: url, script: "document.documentElement.outerHTML", delay: 1200 });
        if (shown && typeof shown.value === "string" && shown.value) {
            return { html: shown.value, url: url };
        }
    }
    if (status >= 400) {
        throw new Error("could not load " + url + " (" + status + ")");
    }
    return { html: html, url: finalUrl };
}

const REFUSED = [401, 403, 418, 429, 503];
const CHALLENGE = /just a moment|attention required|security check|verify you are human/i;

function parse(html) {
    return new DOMParser().parseFromString(html, "text/html");
}

/** A page read from the network: {doc, url}. */
async function page(url, referer) {
    let found = await load(url, referer);
    return { doc: parse(found.html), url: found.url };
}

/** `fn` over `items`, `limit` at a time; results in item order, a failure gives null. */
async function pool(items, limit, fn) {
    let results = new Array(items.length).fill(null);
    let next = 0;
    async function worker() {
        while (next < items.length) {
            let index = next++;
            try {
                results[index] = await fn(items[index], index);
            } catch (e) {
                results[index] = null;
            }
        }
    }
    await Promise.all(Array.from({ length: Math.min(limit, items.length) }, worker));
    return results;
}

function hostOf(url) {
    try {
        return new URL(url).hostname.toLowerCase();
    } catch (e) {
        return "";
    }
}

/** `m.example.co.uk` gives `example.co.uk`: links on any of the site's hosts count as its own. */
function siteOf(host) {
    let labels = String(host || "").toLowerCase().split(".").filter(label => label);
    if (labels.length <= 2) {
        return labels.join(".");
    }
    let keep = labels[labels.length - 1].length === 2 && SECOND_LEVEL.has(labels[labels.length - 2]) ? 3 : 2;
    return labels.slice(-keep).join(".");
}

/** An absolute http(s) address without its fragment, or "". */
function cleanUrl(href, base) {
    href = String(href || "").trim();
    if (!href || /^(javascript:|mailto:|tel:|#|data:)/i.test(href)) {
        return "";
    }
    let parts;
    try {
        parts = new URL(href, base);
    } catch (e) {
        return "";
    }
    if ((parts.protocol !== "http:" && parts.protocol !== "https:") || !parts.host) {
        return "";
    }
    parts.hash = "";
    return parts.href;
}

function pathOf(url) {
    try {
        return new URL(url).pathname;
    } catch (e) {
        return "";
    }
}

function trimSlash(url) {
    return String(url || "").replace(/\/+$/, "");
}

/** The node's text with its parts spaced apart, as one line. */
function textOf(node) {
    if (!node) {
        return "";
    }
    if (node.nodeType === 3) {
        return node.nodeValue.replace(/\s+/g, " ").trim();
    }
    let parts = [];
    let walker = (node.ownerDocument || node).createTreeWalker(node, NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
        let parent = walker.currentNode.parentNode;
        if (parent && /^(SCRIPT|STYLE|NOSCRIPT)$/.test(parent.nodeName)) {
            continue;
        }
        let text = walker.currentNode.nodeValue.trim();
        if (text) {
            parts.push(text);
        }
    }
    return parts.join(" ").replace(/\s+/g, " ").trim();
}

/** A block's text length, quickly (for scoring). */
function flatLength(node) {
    return (node.textContent || "").replace(/\s+/g, " ").trim().length;
}

function hintOf(node) {
    if (!node || !node.getAttribute) {
        return "";
    }
    return (node.getAttribute("class") || "") + " " + (node.getAttribute("id") || "");
}

/** Text nodes whose text matches `pattern`, at most `limit`. */
function textNodes(doc, pattern, limit) {
    let out = [];
    let root = doc.body || doc.documentElement;
    if (!root) {
        return out;
    }
    let walker = doc.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    while (walker.nextNode() && out.length < (limit || 8)) {
        if (pattern.test(walker.currentNode.nodeValue)) {
            out.push(walker.currentNode);
        }
    }
    return out;
}

function nextElement(node) {
    return node ? node.nextElementSibling : null;
}

// -- metadata ---------------------------------------------------------------------------------------

function jsonLd(doc) {
    let out = [];
    for (let script of doc.querySelectorAll("script[type*='ld+json' i]")) {
        let data;
        try {
            data = JSON.parse(script.textContent || "");
        } catch (e) {
            continue;
        }
        let stack = Array.isArray(data) ? data.slice() : [data];
        while (stack.length) {
            let item = stack.shift();
            if (item && typeof item === "object" && !Array.isArray(item)) {
                out.push(item);
                for (let key of ["@graph", "mainEntity", "itemListElement"]) {
                    let value = item[key];
                    if (Array.isArray(value)) {
                        stack.push(...value);
                    } else if (value && typeof value === "object") {
                        stack.push(value);
                    }
                }
            }
        }
    }
    return out;
}

function ldText(value) {
    if (Array.isArray(value)) {
        return value.map(ldText).filter(v => v).join(", ");
    }
    if (value && typeof value === "object") {
        return String(value.name || value.url || value["@id"] || "");
    }
    return value == null ? "" : String(value);
}

// The last tag of a name wins: a page whose script adds its own (Inertia, React Helmet) keeps the
// site's default first ("Bookkor - Web novels", then the book's title).
function meta(doc, ...names) {
    let tags = Array.from(doc.querySelectorAll("meta[property], meta[name]"));
    for (let name of names) {
        let found = tags.filter(tag => (tag.getAttribute("property") === name || tag.getAttribute("name") === name)
            && (tag.getAttribute("content") || "").trim());
        if (found.length) {
            return found[found.length - 1].getAttribute("content").trim();
        }
    }
    return "";
}

/** `global.example.co.uk` gives `example`: the name people use for the site. */
function siteLabel(url) {
    let labels = hostOf(url).replace(/^www\./, "").split(".").filter(part => part);
    if (labels.length < 2) {
        return "";
    }
    let site = siteOf(labels.join(".")).split(".");
    return site[0] || "";
}

function cleanPageTitle(title, url) {
    let text = String(title || "").replace(/\s+/g, " ").trim();
    if (!text) {
        return "";
    }
    let label = siteLabel(url).toLowerCase();
    let parts = text.split(/\s+[-–—|·•]\s+|\s*\|\s*/).map(part => part.trim()).filter(part => part);
    let kept = parts.filter(part => !((label.length >= 3 && part.toLowerCase().includes(label))
        || ["home", "official site"].includes(part.toLowerCase())));
    return (kept.length ? kept.join(" - ") : text).slice(0, 200);
}

function stripSiteName(title, siteName, url) {
    title = title ? cleanPageTitle(title, url) : "";
    if (siteName && title.toLowerCase().endsWith(siteName.toLowerCase()) && title.length > siteName.length + 3) {
        title = title.slice(0, -siteName.length).replace(/[ \-|–—·:]+$/, "");
    }
    title = title.replace(/^(read\s+)/i, "");
    title = title.replace(/\s+(novel|light novel|manga|manhwa|webtoon)?\s*(online)?\s*(for\s+)?free.*$/i, "");
    return title.trim();
}

const DATE = /(\d{4})[-./年]\s*(\d{1,2})[-./月]\s*(\d{1,2})/;
const COUNT = /(\d[\d,.\s]*\d|\d)\s*([kmb万萬억천千])?/i;
const LABELS = {
    published: "(published|release[sd]?|first published|created|start(ed)?|连载时间|發布|发布|出版|연재\\s*시작|公開日|配信開始)",
    updated: "(updated?|last updated?|last update|latest update|更新(时间|時間|日)?|최근\\s*업데이트|업데이트|最終更新)",
    rating: "(rating|rated|score|评分|評分|평점|評価)",
    likes: "(likes?|favou?rites?|bookmarks?|followers?|收藏|推荐|좋아요|선호작|お気に入り|ブックマーク)",
    views: "(views?|reads?|点击|點擊|阅读|조회수?|閲覧数|PV)",
    chapters: "(chapters?|episodes?|章节数|章節|회차|話数)"
};

function isoDate(value) {
    value = String(value || "").replace(/\s+/g, " ").trim();
    let match = DATE.exec(value);
    if (match) {
        let year = +match[1], month = +match[2], day = +match[3];
        if (year >= 1990 && year <= 2100 && month >= 1 && month <= 12 && day >= 1 && day <= 31) {
            return year + "-" + String(month).padStart(2, "0") + "-" + String(day).padStart(2, "0");
        }
    }
    return /\d/.test(value) || /\bago\b/i.test(value) ? value.slice(0, 40) : "";
}

function count(value) {
    let match = COUNT.exec(String(value || ""));
    if (!match || !match[1].replace(/[0.,\s]/g, "")) {
        return "";
    }
    return match[1].replace(/ /g, "") + (match[2] || "").toUpperCase();
}

function labelled(doc, label) {
    let pattern = new RegExp("^\\s*" + label + "\\s*[:：]?", "i");
    for (let found of textNodes(doc, pattern, 8)) {
        let node = found.parentNode;
        if (!node || /^(SCRIPT|STYLE|A|OPTION|TITLE)$/.test(node.nodeName)) {
            continue;
        }
        let rest = found.nodeValue.replace(pattern, "").replace(/^[ :：|·]+|[ :：|·]+$/g, "");
        if (!/\d/.test(rest)) {
            let whole = textOf(node);
            rest = whole.length < 80 ? whole.replace(pattern, "").replace(/^[ :：|·]+|[ :：|·]+$/g, "") : "";
        }
        if (!/\d/.test(rest)) {
            let sibling = nextElement(node);
            rest = sibling ? textOf(sibling).slice(0, 60) : "";
        }
        if (/\d/.test(rest)) {
            return rest.slice(0, 60);
        }
    }
    return "";
}

function interaction(book, ...kinds) {
    let stats = book.interactionStatistic;
    for (let stat of Array.isArray(stats) ? stats : [stats]) {
        if (stat && typeof stat === "object" && kinds.some(kind => ldText(stat.interactionType).includes(kind))) {
            return String(stat.userInteractionCount || "");
        }
    }
    return "";
}

function figures(doc, book) {
    let rating = book.aggregateRating && typeof book.aggregateRating === "object" ? book.aggregateRating : {};
    let value = String(rating.ratingValue || "") || meta(doc, "og:rating", "rating");
    if (!value) {
        let node = doc.querySelector("[itemprop=ratingValue]");
        value = node ? (node.getAttribute("content") || textOf(node)) : "";
    }
    if (!value) {
        value = labelled(doc, LABELS.rating);
    }
    value = ((/\d+(?:[.,]\d+)?/.exec(value) || [""])[0]).replace(",", ".");
    if (value && !parseFloat(value)) {
        value = "";
    }
    let best = String(rating.bestRating || "");
    if (value && best && best !== "5" && best !== "5.0") {
        value = value + "/" + best;
    }
    let published = ldText(book.datePublished || book.dateCreated)
        || meta(doc, "article:published_time", "book:release_date", "og:novel:release_time", "datePublished")
        || labelled(doc, LABELS.published);
    let updated = ldText(book.dateModified)
        || meta(doc, "article:modified_time", "og:updated_time", "og:novel:update_time", "dateModified")
        || labelled(doc, LABELS.updated);
    return {
        published: isoDate(published),
        updated: isoDate(updated),
        rating: value,
        ratings: count(String(rating.ratingCount || rating.reviewCount || "")),
        likes: count(interaction(book, "Like", "Bookmark", "Follow") || labelled(doc, LABELS.likes)),
        views: count(interaction(book, "View", "Read") || labelled(doc, LABELS.views)),
        chapters: count(String(book.numberOfEpisodes || book.numberOfPages || "") || labelled(doc, LABELS.chapters))
    };
}

const BOOK_KINDS = ["Book", "CreativeWork", "CreativeWorkSeries", "BookSeries", "ComicSeries", "Article",
    "Product", "Novel", "WebPage"];

// Where a book page keeps its synopsis, the most specific first: Madara's ".summary__content" before its
// ".summary_content" stats box, which "[class*=summary]" matches too.
// A synopsis shown short with "more", its full text kept in the page: read before anything else.
const FULL_SYNOPSIS = ".fullcontent, [class*=fullcontent], [id*=fullcontent], [class*=full-summary], [class*=full-desc]";
const SYNOPSIS = "[class*=detail-info-right-content], " +
    ".summary__content, .description-summary, .manga-excerpt, [itemprop=description], .synopsis, " +
    "[class*=synopsis], #synopsis, [class*=description], [id*=description], [class*=desc], " +
    "[class*=summary], [class*=intro], #intro";
// A book page's own list of tags or genres (not the site's menu of them).
const TAG_LISTS = "[class*=tag-list], [class*=tags-list], [class*=genre-list], [class*=genres], .tags, .genre, [class*=manga-tags]";
const MENU = /(^|[-_ ])(menu|nav|navbar|dropdown|header|footer|sidebar)([-_ ]|$)/i;

function inMenu(node) {
    for (let at = node.parentElement; at; at = at.parentElement) {
        if (MENU.test(at.getAttribute("class") || "")) {
            return true;
        }
    }
    return false;
}

/** A description that never names the book: the site's blurb, the same on every page. */
function siteWide(description, title) {
    let words = (String(title || "").toLowerCase().match(/[\p{L}\p{N}]{4,}/gu) || [])
        .filter(w => !["manga", "novel", "read", "online"].includes(w));
    if (!description || !words.length) {
        return false;
    }
    let text = description.toLowerCase();
    return !words.some(w => text.includes(w)) && description.length < 600;
}

const STATS_WORDS = /(\brating\b|\baverage\b|\bout of\b|alternative|author\(s\)|artist\(s\)|genre\(s\)|\brelease\b|\bstatus\b|\bcomments?\b|bookmarked|read first|read last|التقييم|التصنيف|الحالة|평점|评分|評価)/gi;

/** A block of a book page's figures (rating, alternative names, genres, status, bookmarks), not a synopsis. */
function statsBox(text) {
    let head = text.slice(0, 600);
    let found = new Set((head.match(STATS_WORDS) || []).map(word => word.toLowerCase()));
    return found.size >= 3 || /\d(?:\.\d)?\s*\/\s*(5|10)\b.{0,20}\bout of\b/i.test(head);
}

/** A tag as words: a link to a genre page becomes the genre it names ("...?genre=summoned_hero" gives "Summoned Hero"). */
function tagText(tag) {
    tag = String(tag || "").trim();
    if (/^https?:\/\//.test(tag)) {
        let parts;
        try { parts = new URL(tag); } catch (e) { return ""; }
        let named = /(?:^|&)(?:genre|tag|tags|category)=([^&]+)/.exec(parts.search.replace(/^\?/, ""));
        tag = named ? decodeURIComponent(named[1]) : (parts.pathname.replace(/\/+$/, "").split("/").pop() || "");
        tag = tag.replace(/[_\-+]+/g, " ").trim().replace(/\b\w/g, c => c.toUpperCase());
    }
    return tag.length > 1 && tag.length <= 40 ? tag : "";
}

/** A synopsis that came as HTML (in a page's structured data) as plain text, its paragraphs kept. */
function htmlText(text) {
    if (!/<(p|br|div|span|em|strong|i|b)\b[^>]*>/i.test(text)) return text;
    let root = parse("<div>" + text + "</div>").body.firstElementChild;
    return root ? plain(root) : text;
}

/** {title, author, cover, description, tags, status, figures...} of any book page. */
function metadata(doc, url) {
    let book = {};
    let bestScore = -1;
    for (let item of jsonLd(doc)) {
        let kinds = Array.isArray(item["@type"]) ? item["@type"] : [item["@type"]];
        if (kinds.some(kind => BOOK_KINDS.includes(String(kind)))) {
            let score = ["author", "image", "genre", "description", "numberOfPages"].filter(key => item[key]).length;
            if (score >= bestScore) {
                book = item;
                bestScore = score;
            }
        }
    }
    let siteName = meta(doc, "og:site_name", "application-name");
    let title = ldText(book.name || book.headline) || meta(doc, "og:title", "twitter:title");
    if (!title) {
        let h1 = doc.querySelector("h1");
        title = h1 ? textOf(h1) : textOf(doc.querySelector("title"));
    }
    let author = ldText(book.author || book.creator) || meta(doc, "book:author", "author", "og:novel:author");
    if (!author) {
        let label = textNodes(doc, /^\s*(author|writer|作者|저자|작가|著者|autor|auteur|автор)\s*[:：]?\s*$/i, 1)[0];
        let node = label ? label.parentNode : null;
        if (node) {
            let inside = textOf(node).replace(label.nodeValue.trim(), "").replace(/^[ :：]+|[ :：]+$/g, "");
            let sibling = nextElement(node);
            author = inside || (sibling ? textOf(sibling) : "");
        }
        if (!author) {
            let link = Array.from(doc.querySelectorAll("a[href]"))
                .find(a => /\/(author|authors|writer|artist)\//i.test(a.getAttribute("href")));
            author = link ? textOf(link) : "";
        }
    }
    if (!author) {
        let found = textNodes(doc, /(author|作者|저자|著者)\s*[:：]/i, 1)[0];
        if (found) {
            author = found.nodeValue.split(/[:：]/).slice(1).join(":").trim().slice(0, 80);
        }
    }
    let cover = ldText(book.image) || meta(doc, "og:image", "twitter:image", "og:novel:image");
    if (!cover) {
        let image = doc.querySelector("[class*=cover] img, [class*=thumb] img, [class*=poster] img, img[class*=cover], " +
            "img[itemprop=image], [class*=book] img");
        if (image) {
            cover = image.getAttribute("data-src") || image.getAttribute("data-original") || image.getAttribute("src") || "";
        }
    }
    cover = cover ? cleanUrl(cover, url) : "";
    let description = ldText(book.description) || meta(doc, "og:description", "description", "twitter:description");
    if (siteWide(description, title)) {
        description = "";  // the site's own blurb on every page ("Read your favorite manga online!...")
    }
    for (let block of [...doc.querySelectorAll(FULL_SYNOPSIS), ...doc.querySelectorAll(SYNOPSIS)]) {
        let longer = textOf(block);
        if (longer.length > description.length && !statsBox(longer)) {
            description = longer;
            break;
        }
    }
    let tags = [];
    let genre = book.genre || book.keywords;
    if (genre) {
        tags.push(...ldText(genre).split(/[,;|]/).map(tagText).filter(t => t));
    }
    // Genre links by their address, and the links of the page's tag or genre list wherever they point
    // (fanfox's /directory/action/); none from the site's own menus.
    let tagLinks = Array.from(doc.querySelectorAll("a[href]"))
        .filter(a => /\/(genres?|tags?|categor(y|ies)|keywords?)\//i.test(a.getAttribute("href"))).slice(0, 40);
    for (let box of doc.querySelectorAll(TAG_LISTS)) {
        if (!box.closest("header, nav, footer") && !inMenu(box)) {
            tagLinks.push(...Array.from(box.querySelectorAll("a[href]")).slice(0, 40));
        }
    }
    for (let link of tagLinks) {
        let word = textOf(link);
        if (word.length > 1 && word.length <= 40) {
            tags.push(word);
        }
    }
    let status = meta(doc, "og:novel:status");
    if (!status) {
        let found = textNodes(doc, /\b(ongoing|completed|complete|hiatus)\b/i, 1)[0];
        let match = found ? /(ongoing|completed|complete|hiatus)/i.exec(found.nodeValue) : null;
        status = match ? match[1][0].toUpperCase() + match[1].slice(1).toLowerCase() : "";
    }
    return Object.assign(figures(doc, book), {
        title: stripSiteName(String(title || "").replace(/\s+/g, " ").trim(), siteName, url).slice(0, 300),
        author: String(author || "").replace(/\s+/g, " ").trim().slice(0, 200),
        cover: cover,
        description: htmlText(String(description || "")).replace(/[ \t]+/g, " ").trim().slice(0, 4000),
        tags: Array.from(new Set(tags)).slice(0, 30),
        status: status,
        site: siteName
    });
}

// -- the chapter list -------------------------------------------------------------------------------

/** The address with its numbers as placeholders: chapters of one book share a shape. */
function shape(url) {
    let parts;
    try {
        parts = new URL(url);
    } catch (e) {
        return url;
    }
    let path = parts.pathname.replace(/\/+$/, "").replace(/\d+/g, "{n}");
    let query = parts.search.replace(/^\?/, "").split("&").filter(q => q)
        .map(q => q.split("=")[0].replace(/\d+/g, "{n}") + "=").sort().join("&");
    let segments = path.split("/");
    let last = segments[segments.length - 1];
    if (last && last.includes("{n}")) {
        segments[segments.length - 1] = last.split("{n}")[0] + "{n}*";
    }
    return parts.host.replace(/^www\./, "") + segments.join("/") + (query ? "?" + query : "");
}

const AGE = new RegExp(
    "\\s*(updated\\s*)?(\\d+|an?|one)\\s*(seconds?|secs?|minutes?|mins?|hours?|hrs?|days?|weeks?|months?|years?|" +
    "mo|mos|yrs?|[smhdwy]|" +
    "초|분|시간|일|주|개월|년|秒|分钟?|小时|天|周|个月|年|日|週間|か月|ヶ月)\\s*(ago|전|前)\\s*$", "i");
const DATE_TAIL = /\s*(updated\s*)?\d{4}[-./]\d{1,2}[-./]\d{1,2}(\s+\d{1,2}:\d{2}(:\d{2})?)?\s*$/i;

/** A chapter name without its age or date, a "Read" prefix or a repeated number. */
function cleanName(text) {
    let name = String(text || "").replace(/\s+/g, " ").trim();
    for (let i = 0; i < 2; i++) {
        name = name.replace(AGE, "");
        name = name.replace(DATE_TAIL, "").trim();
    }
    name = name.replace(/^(read|start reading|continue reading|read now)\s+/i, "");
    name = name.replace(/^(\d+(?:\.\d+)?)\s+\1\b\s*/, "$1 ");
    name = name.replace(/ /g, " ").replace(/\s{2,}/g, " ");
    name = name.replace(/^(novel chapters|chapters?\s+list|latest chapters?)\s+/i, "");
    return name.replace(/^[ \-|:·]+|[ \-|:·]+$/g, "");
}

const NUMBERED = /(?:(?:^|[^a-z])(?:chapter|chap|ch|episode|ep)(?![a-z])|第|(?:^|[^a-z])no\.?|#)\s*[-_.]?\s*(\d+(?:\.\d+)?)/i;

function chapterNumber(text, url) {
    text = cleanName(text);
    let path = pathOf(url);
    for (let source of [text, path]) {
        let found = NUMBERED.exec(source);
        if (found) {
            return parseFloat(found[1]);
        }
    }
    let numbers = text.match(/\d+(?:\.\d+)?/g) || (path.split("/").pop() || "").match(/\d+/g);
    return numbers ? parseFloat(numbers[0]) : null;
}

function bare(name) {
    return /^\s*((chapter|chap|ch|episode|ep)\.?\s*)?\d+(\.\d+)?\s*$/i.test(name || "");
}

function urlNumbers(chapters) {
    let found = chapters.map(chapter => {
        let parts;
        try {
            parts = new URL(chapter.url);
        } catch (e) {
            return [];
        }
        return ((parts.pathname.split("/").pop() || "") + "?" + parts.search.replace(/^\?/, "")).match(/\d+/g) || [];
    });
    if (found.length < 2 || new Set(found.map(n => n.length)).size !== 1 || !found[0].length) {
        return null;
    }
    let varying = [];
    for (let i = 0; i < found[0].length; i++) {
        if (new Set(found.map(n => n[i])).size > 1) {
            varying.push(i);
        }
    }
    if (varying.length !== 1) {
        return null;
    }
    let values = found.map(n => parseFloat(n[varying[0]]));
    return new Set(values).size === values.length ? values : null;
}

/** Chapters by their number when every one differs; a newest-first list turned round; else as given. */
function readingOrder(chapters) {
    let numbers = urlNumbers(chapters);
    if (numbers == null) {
        numbers = chapters.map(c => chapterNumber(c.name, c.url));
        let known = numbers.filter(n => n != null);
        if (!(known.length === chapters.length && new Set(known).size === known.length)) {
            if (known.length >= Math.max(2, chapters.length * 0.6) && known[0] > known[known.length - 1]) {
                return chapters.slice().reverse();
            }
            return chapters;
        }
    }
    return chapters.map((chapter, index) => [numbers[index], index, chapter])
        .sort((a, b) => a[0] - b[0] || a[1] - b[1]).map(entry => entry[2]);
}

function links(doc, base) {
    let site = siteOf(hostOf(base));
    let out = [];
    for (let anchor of doc.querySelectorAll("a[href]")) {
        let href = cleanUrl(anchor.getAttribute("href"), base);
        if (!href || siteOf(hostOf(href)) !== site) {
            continue;
        }
        if (/\.(jpe?g|png|gif|webp|zip|pdf|epub|css|js)(\?|$)/i.test(href)) {
            continue;
        }
        out.push([href, textOf(anchor) || anchor.getAttribute("title") || "", anchor]);
    }
    return out;
}

function inBadBlock(anchor) {
    let hints = [];
    for (let node = anchor.parentElement; node; node = node.parentElement) {
        hints.push(node.getAttribute("class") || node.getAttribute("id") || "");
    }
    return BAD_BLOCKS.test(hints.join(" "));
}

/** The page's chapter links in reading order: [{url, name}]. */
function findChapters(doc, base) {
    let found = links(doc, base);
    if (!found.length) {
        return [];
    }
    let bookPath = pathOf(base).replace(/\/+$/, "");
    let groups = new Map();
    for (let link of found) {
        if (trimSlash(link[0]) === trimSlash(base)) {
            continue;
        }
        let key = shape(link[0]);
        if (!groups.has(key)) {
            groups.set(key, []);
        }
        groups.get(key).push(link);
    }
    let best = null;
    let bestScore = 0;
    for (let [key, members] of groups) {
        let unique = new Set(members.map(m => m[0]));
        if (unique.size < 2 && !(unique.size === 1 && CHAPTER_WORDS.test(members[0][1] || ""))) {
            continue;
        }
        let worded = members.filter(m => CHAPTER_WORDS.test(m[1] || "")).length / members.length;
        let numbered = members.filter(m => chapterNumber(m[1], m[0]) != null).length / members.length;
        let underBook = bookPath ? members.filter(m => pathOf(m[0]).startsWith(bookPath + "/")).length / members.length : 0;
        let sample = members.slice(0, 20);
        let inBad = sample.filter(m => inBadBlock(m[2])).length / sample.length;
        let score = Math.sqrt(unique.size) * (1 + 3 * worded + numbered + 2 * underBook) * (1 - 0.6 * inBad);
        if (!key.includes("{n}")) {
            score *= 0.3;
        }
        if (score > bestScore) {
            best = members;
            bestScore = score;
        }
    }
    if (!best) {
        return [];
    }
    let seen = new Map();
    let chapters = [];
    for (let [href, rawText] of best) {
        let text = cleanName(rawText);
        if (seen.has(href)) {
            let chapter = seen.get(href);
            if (text && CHAPTER_WORDS.test(text) && !CHAPTER_WORDS.test(chapter.name)) {
                chapter.name = text;
            }
            continue;
        }
        let chapter = { url: href, name: text || "Chapter " + (chapters.length + 1) };
        seen.set(href, chapter);
        chapters.push(chapter);
    }
    return readingOrder(chapters);
}

/** Further pages of a chapter list (?page=2, /page/2, rel=next), not yet read. */
function listPages(doc, base, seen) {
    let out = [];
    let baseParts = new URL(base);
    let basePath = baseParts.pathname.replace(/\/+$/, "");
    let slug = (basePath.split("/").pop() || "").replace(/\.\w+$/, "");
    let prefix = basePath.split("/page/")[0].replace(/\.\w+$/, "");
    for (let anchor of doc.querySelectorAll("a[href], link[href]")) {
        let href = cleanUrl(anchor.getAttribute("href"), base);
        if (!href || seen.has(href) || out.includes(href)) {
            continue;
        }
        let parts = new URL(href);
        if (parts.host !== baseParts.host) {
            continue;
        }
        let rel = String(anchor.getAttribute("rel") || "");
        let sameBook = parts.pathname.replace(/\/+$/, "").startsWith(prefix) || (slug.length > 3 && parts.search.includes(slug));
        let paged = /([?&](page|p|pg|pageNo|epage)=\d+)|(\/page\/\d+\/?$)|(_\d+\/?$)/i.test(href);
        if ((sameBook && paged) || (/next/i.test(rel) && sameBook)) {
            out.push(href);
        }
    }
    // Pages a script draws as buttons (no links): the page's own data names them, as a Laravel
    // paginator does ("next_page_url", "last_page_url": "...?page=2") in Inertia's data-page JSON.
    for (let href of dataPageUrls(doc, base)) {
        let parts = new URL(href);
        if (parts.host === baseParts.host && !seen.has(href) && !out.includes(href)
            && parts.pathname.replace(/\/+$/, "").startsWith(prefix) && /[?&](page|p|pg|pageNo)=\d+/i.test(href)) {
            out.push(href);
        }
    }
    return out;
}

/** The list page addresses in a page's embedded data (scripts and data-page attributes). */
function dataPageUrls(doc, base) {
    let out = [];
    let texts = Array.from(doc.querySelectorAll("script")).map(s => s.textContent || "")
        .concat(Array.from(doc.querySelectorAll("[data-page]")).map(el => el.getAttribute("data-page") || ""));
    for (let text of texts) {
        if (text.length > 3000000 || !/_page_url/.test(text)) {
            continue;
        }
        for (let match of text.matchAll(/"(?:first|last|next|prev)_page_url"\s*:\s*"((?:[^"\\]|\\.)*)"/g)) {
            let url;
            try {
                url = cleanUrl(JSON.parse('"' + match[1] + '"'), base);
            } catch (e) {
                continue;
            }
            if (url && !out.includes(url)) {
                out.push(url);
            }
        }
    }
    return out;
}

/** List pages that differ only in a page number: every page up to the highest one shown. */
function numberedPages(pages) {
    if (!pages.length) {
        return pages;
    }
    let pattern = null;
    let numbers = [];
    for (let url of pages) {
        let found = Array.from(url.matchAll(/([?&/_=-])(\d{1,4})(?=(\.html?)?(\/|&|$))/g));
        if (!found.length) {
            return pages;
        }
        let last = found[found.length - 1];
        let start = last.index + last[1].length;
        let shapeNow = url.slice(0, start) + "{n}" + url.slice(start + last[2].length);
        if (pattern == null) {
            pattern = shapeNow;
        } else if (shapeNow !== pattern) {
            return pages;
        }
        numbers.push(parseInt(last[2], 10));
    }
    let top = Math.max(...numbers);
    if (top > 400 || top <= new Set(numbers).size) {
        return pages;
    }
    let low = Math.min(...numbers);
    let out = [];
    for (let n = low; n <= top; n++) {
        out.push(pattern.replace("{n}", String(n)));
    }
    return out;
}

const ALL_CHAPTERS = new RegExp(
    "^\\s*(view|see|show|read)?\\s*(all|full|complete)\\s*(chapters?|episodes?|list|chapter list|toc)\\b|" +
    "^\\s*(chapter list|table of contents|contents|toc|index)\\s*$|全部章节|章节目录|目录|전체\\s*(목록|회차)|" +
    "目次|すべての話|^\\s*(oldest|ascending|sort by oldest|from the beginning)\\s*$", "i");

function fullListLinks(doc, base) {
    let out = [];
    for (let anchor of doc.querySelectorAll("a[href]")) {
        if (ALL_CHAPTERS.test(textOf(anchor) || anchor.getAttribute("title") || "")) {
            let url = cleanUrl(anchor.getAttribute("href"), base);
            if (url && trimSlash(url) !== trimSlash(base) && siteOf(hostOf(url)) === siteOf(hostOf(base)) && !out.includes(url)) {
                out.push(url);
            }
        }
    }
    return out.slice(0, 3);
}

function nextLink(doc, base, words) {
    words = words || NEXT_WORDS;
    for (let link of doc.querySelectorAll("a[rel][href], link[rel][href]")) {
        if (/(^|\s)next(\s|$)/i.test(link.getAttribute("rel"))) {
            let href = cleanUrl(link.getAttribute("href"), base);
            if (href) {
                return href;
            }
        }
    }
    for (let anchor of doc.querySelectorAll("a[href]")) {
        if (words.test(textOf(anchor) || anchor.getAttribute("title") || anchor.getAttribute("aria-label") || "")) {
            let href = cleanUrl(anchor.getAttribute("href"), base);
            if (href && trimSlash(href) !== trimSlash(base)) {
                return href;
            }
        }
    }
    return "";
}

const CHAPTER_PATH = /\/(c|ch|chap|chapter|episode|ep|vol|v)[-_.]?\d+(\.\d+)?(\/|\.html?|$)/i;

function isChapterAddress(url) {
    return CHAPTER_PATH.test(pathOf(url));
}

/** The addresses above a chapter's, nearest first: /manga/x/c147/2.html -> /manga/x/c147/, /manga/x/. */
function parentAddresses(url) {
    let parts = new URL(url);
    let segments = parts.pathname.split("/").filter(s => s);
    let out = [];
    while (segments.length) {
        segments = segments.slice(0, -1);
        if (!segments.length) {
            break;
        }
        let path = "/" + segments.join("/") + "/";
        if (!CHAPTER_PATH.test(path)) {
            out.push(parts.protocol + "//" + parts.host + path);
        }
    }
    return out.slice(0, 2);
}

/** Scrolls the live page to its end and clicks "Load more" until it stops growing (lists that load as you scroll). */
function scrollToEnd() {
    return new Promise(resolve => {
        let words = /^(load|show|view|see)\s+(more|all)|more chapters|all chapters|show all|view all|더보기|더 보기|加载更多|查看更多|全部章节|もっと見る/i;
        let rounds = 0, still = 0, last = 0;
        function step() {
            rounds++;
            Array.from(document.querySelectorAll("button, a, [role=button], div[class*=more], span[class*=more]"))
                .filter(el => { let t = (el.innerText || "").trim(); return t.length < 40 && words.test(t) && el.offsetParent; })
                .slice(0, 2).forEach(el => {
                    try {
                        let href = el.getAttribute("href");
                        if (!href || /^#|javascript/i.test(href)) {
                            el.click();
                        }
                    } catch (e) {
                        // a control that refuses the click
                    }
                });
            window.scrollTo(0, document.documentElement.scrollHeight);
            let size = document.documentElement.scrollHeight + document.querySelectorAll("[class*=chapter] a, [id*=chapter] a, li a").length;
            still = size === last ? still + 1 : 0;
            last = size;
            if (still >= 3 || rounds >= 60) {
                window.scrollTo(0, 0);
                resolve();
                return;
            }
            setTimeout(step, 700);
        }
        step();
    });
}

function sameAddress(a, b) {
    return trimSlash(String(a || "").split("#")[0]) === trimSlash(String(b || "").split("#")[0]);
}

function mergeNames(chapters, found) {
    let byUrl = new Map(chapters.map(c => [c.url, c]));
    for (let chapter of found) {
        let earlier = byUrl.get(chapter.url);
        if (earlier && bare(earlier.name) && !bare(chapter.name)) {
            earlier.name = chapter.name;
        }
    }
}

async function moreListPages(doc, base, chapters, warnings) {
    let seenPages = new Set([base]);
    let known = new Set(chapters.map(c => c.url));
    let queue = numberedPages(listPages(doc, base, seenPages));
    if (queue.length > 2) {
        // Every numbered list page at once (8 at a time), kept in page order.
        let pages = await pool(queue.slice(0, MAX_LIST_PAGES), 8, async url => {
            let read = await page(url, base);
            return findChapters(read.doc, read.url);
        });
        for (let found of pages) {
            if (!found) {
                continue;
            }
            mergeNames(chapters, found);
            let more = found.filter(c => !known.has(c.url));
            more.forEach(c => known.add(c.url));
            chapters.push(...more);
        }
        if (queue.length > MAX_LIST_PAGES) {
            warnings.push("The chapter list goes on past " + MAX_LIST_PAGES + " pages; read the first ones.");
        }
        return chapters.length > 2 ? readingOrder(chapters) : chapters;
    }
    let pages = 0;
    while (queue.length && pages < MAX_LIST_PAGES) {
        let url = queue.shift();
        if (seenPages.has(url)) {
            continue;
        }
        seenPages.add(url);
        pages++;
        let read;
        try {
            read = await page(url, base);
        } catch (e) {
            continue;
        }
        let found = findChapters(read.doc, read.url);
        mergeNames(chapters, found);
        let more = found.filter(c => !known.has(c.url));
        if (!more.length) {
            continue;
        }
        more.forEach(c => known.add(c.url));
        chapters.push(...more);
        for (let next of listPages(read.doc, read.url, seenPages)) {
            if (!queue.includes(next)) {
                queue.push(next);
            }
        }
    }
    if (pages >= MAX_LIST_PAGES) {
        warnings.push("The chapter list goes on past " + MAX_LIST_PAGES + " pages; read the first ones.");
    }
    return chapters.length > 2 ? readingOrder(chapters) : chapters;
}

async function chain(start, doc, warnings) {
    let chapters = [];
    let seen = new Set();
    let url = start;
    let current = doc;
    while (url && !seen.has(url) && chapters.length < MAX_CHAIN) {
        seen.add(url);
        let heading = current.querySelector("h1, h2");
        chapters.push({ url: url, name: textOf(heading) || "Chapter " + (chapters.length + 1) });
        let following = nextLink(current, url);
        if (!following || seen.has(following)) {
            break;
        }
        let read;
        try {
            read = await page(following, url);
        } catch (e) {
            break;
        }
        url = read.url;
        current = read.doc;
    }
    if (chapters.length >= MAX_CHAIN) {
        warnings.push("Followed " + MAX_CHAIN + " chapters through their 'next' links; stopped there.");
    }
    return chapters;
}

async function book(url) {
    let doc, final;
    if (sameAddress(url, location.href) || !url) {
        // The page the site's scripts built: the desktop reads this one only after the plain page fails.
        doc = document;
        final = location.href;
    } else {
        let read = await page(url);
        doc = read.doc;
        final = read.url;
    }
    let chapters = findChapters(doc, final);
    if (isChapterAddress(final)) {
        // A chapter's link (a manga chapter's pages look like a chapter list): the series page above it.
        for (let parent of parentAddresses(final)) {
            let read;
            try {
                read = await page(parent, final);
            } catch (e) {
                continue;
            }
            let found = findChapters(read.doc, read.url);
            if (found.length >= 2 && found.filter(c => isChapterAddress(c.url)).length >= found.length / 2) {
                doc = read.doc;
                final = read.url;
                chapters = found;
                break;
            }
        }
    }
    let info = metadata(doc, final);
    let warnings = [];
    let expected = parseInt(String(info.chapters || "").replace(/\D/g, ""), 10) || 0;
    if (chapters.length && expected && chapters.length < expected * 0.8) {
        for (let link of fullListLinks(doc, final)) {
            let read;
            try {
                read = await page(link, final);
            } catch (e) {
                continue;
            }
            let found = findChapters(read.doc, read.url);
            if (found.length > chapters.length) {
                doc = read.doc;
                final = read.url;
                chapters = found;
                break;
            }
        }
    }
    if (chapters.length) {
        chapters = await moreListPages(doc, final, chapters, warnings);
    }
    if (doc === document && (chapters.length < 2 || (expected && chapters.length < expected * 0.8))) {
        // A list that loads as you scroll, or behind "Load more".
        await scrollToEnd();
        let found = findChapters(document, location.href);
        if (found.length > chapters.length) {
            chapters = found;
        }
    }
    if (!chapters.length && nextLink(doc, final)) {
        // No list: the page may be a chapter itself; follow its "next" links.
        chapters = await chain(final, doc, warnings);
    }
    if (!chapters.length) {
        return { error: "The generic engine found no chapter list on this page.", noParser: true };
    }
    let list = chapters.map((chapter, index) => ({
        url: chapter.url, name: chapter.name, number: index + 1, bookUrl: final, isVIP: false, isPaid: false
    }));
    return {
        api: GEN_API,
        engine: "generic",
        ruleName: "Generic (no site rules)",
        bookUrl: final,
        pageUrl: final,
        bookname: info.title || hostOf(final),
        author: info.author,
        introduction: info.description,
        introductionHTML: "",
        coverUrl: info.cover || null,
        tags: info.tags,
        status: info.status,
        published: info.published,
        updated: info.updated,
        rating: info.rating,
        likes: info.likes,
        views: info.views,
        chapterCount: list.length,
        chapters: list,
        warnings: warnings
    };
}

// -- chapter text -----------------------------------------------------------------------------------

function score(node) {
    let length = flatLength(node);
    if (length < 80) {
        return 0;
    }
    let linkText = 0;
    for (let a of node.querySelectorAll("a")) {
        linkText += flatLength(a);
    }
    let density = linkText / Math.max(1, length);
    let paragraphs = 0;
    let direct = 0;
    for (let child of node.childNodes) {
        if (child.nodeName === "P") {
            paragraphs += 1;
        } else if (child.nodeName === "BR") {
            paragraphs += 0.5;
        } else if (child.nodeType === 3) {
            direct += child.nodeValue.trim().length;
        }
    }
    let hint = hintOf(node);
    let value = length * Math.pow(1 - density, 2) + paragraphs * 40;
    if (GOOD_BLOCKS.test(hint)) {
        value *= 1.5;
    }
    if (BAD_BLOCKS.test(hint)) {
        value *= 0.2;
    }
    return value + direct * 0.5;
}

function words(text) {
    return String(text || "").toLowerCase().replace(/[^\p{L}\p{N}]+/gu, "");
}

/** One paragraph per line. */
function plain(node) {
    let clone = node.cloneNode(true);
    clone.querySelectorAll("br").forEach(br => br.replaceWith("\n"));
    clone.querySelectorAll("p, div, li, h1, h2, h3, h4, blockquote, pre, tr, hr").forEach(block => {
        block.insertAdjacentText("beforebegin", "\n");
        block.insertAdjacentText("afterend", "\n");
    });
    return clone.textContent.split("\n").map(line => line.replace(/\s+/g, " ").trim()).filter(line => line).join("\n");
}

/** [content HTML, plain text] of the chapter's main text block. */
function extractContent(doc, url, title) {
    doc.querySelectorAll("script, style, noscript, iframe, form, button, nav, footer, header, aside, svg, select, " +
        "input, textarea, ins").forEach(node => node.remove());
    for (let node of Array.from(doc.querySelectorAll("*"))) {
        if (!node.isConnected) {
            continue;
        }
        let hint = hintOf(node);
        let style = (node.getAttribute("style") || "").replace(/ /g, "").toLowerCase();
        if ((BAD_BLOCKS.test(hint) && !GOOD_BLOCKS.test(hint)) || style.includes("display:none")) {
            if (node !== doc.body && node !== doc.documentElement) {
                node.remove();
            }
        }
    }
    let best = null;
    let bestScore = 0;
    for (let node of doc.querySelectorAll("div, article, section, td, main, pre")) {
        let value = score(node);
        if (value > bestScore) {
            best = node;
            bestScore = value;
        }
    }
    best = best || doc.body || doc.documentElement;
    for (let anchor of Array.from(best.querySelectorAll("a"))) {
        let text = textOf(anchor);
        if (NEXT_WORDS.test(text) || /(previous|prev|上一章|이전|前へ|index|table of contents|目录|목록)/i.test(text)) {
            anchor.remove();
        }
    }
    for (let image of Array.from(best.querySelectorAll("img"))) {
        let source = image.getAttribute("data-src") || image.getAttribute("data-original")
            || image.getAttribute("data-lazy-src") || image.getAttribute("src");
        source = source ? cleanUrl(source, url) : "";
        for (let attribute of Array.from(image.attributes)) {
            image.removeAttribute(attribute.name);
        }
        if (source) {
            image.setAttribute("src", source);
        } else {
            image.remove();
        }
    }
    for (let node of best.querySelectorAll("*")) {
        for (let attribute of Array.from(node.attributes)) {
            if (!["src", "href", "alt"].includes(attribute.name)) {
                node.removeAttribute(attribute.name);
            }
        }
    }
    let heading = best.querySelector("h1, h2, h3");
    if (heading && title && words(textOf(heading)) === words(title)) {
        heading.remove();
    }
    return [best.innerHTML.trim(), plain(best)];
}

const SKIP_IMAGES = new RegExp(
    "(logo|icon|avatar|banner|sprite|loading|spinner|blank|pixel|emoji|button|ads?[-_/]|" +
    "advert|placeholder|thumb[-_]?nav|\\.svg|\\.gif$)", "i");

/** A manga page's own images (not logos, icons or ads), in page order. */
function pageImages(doc, base) {
    let out = [];
    for (let image of doc.querySelectorAll("img")) {
        let source = (image.getAttribute("data-src") || image.getAttribute("data-original") || image.getAttribute("data-lazy-src")
            || image.getAttribute("data-url") || image.getAttribute("src") || "").trim();
        if (!source || source.startsWith("data:") || SKIP_IMAGES.test(source)) {
            continue;
        }
        let hint = hintOf(image) + " " + (image.getAttribute("alt") || "");
        if (/(logo|icon|avatar|ads?\b|banner)/i.test(hint)) {
            continue;
        }
        let width = /^\d+/.exec(String(image.getAttribute("width") || ""));
        if (width && parseInt(width[0], 10) < 150) {
            continue;
        }
        let blocked = false;
        for (let parent = image.parentElement; parent; parent = parent.parentElement) {
            let parentHint = hintOf(parent);
            if (BAD_BLOCKS.test(parentHint) && !GOOD_BLOCKS.test(parentHint)) {
                blocked = true;
                break;
            }
        }
        if (blocked) {
            continue;
        }
        let url = cleanUrl(source, base);
        if (url && !out.includes(url)) {
            out.push(url);
        }
    }
    return out;
}

/** The other pages of a chapter shown one image per page: its page picker, or ".../c147/2.html" links. */
function chapterPages(doc, base) {
    let path = pathOf(base);
    let folder = path.endsWith("/") ? path : path.slice(0, path.lastIndexOf("/") + 1);
    let found = [];
    for (let option of doc.querySelectorAll("select option[value]")) {
        let url = cleanUrl(option.getAttribute("value") || "", base);
        if (url && pathOf(url).startsWith(folder) && !found.includes(url)) {
            found.push(url);
        }
    }
    if (found.length < 2) {
        found = [];
        for (let anchor of doc.querySelectorAll("a[href]")) {
            let url = cleanUrl(anchor.getAttribute("href"), base);
            let urlPath = url ? pathOf(url) : "";
            if (urlPath.startsWith(folder) && /^\d{1,3}(\.html?)?$/.test(urlPath.slice(folder.length)) && !found.includes(url)) {
                found.push(url);
            }
        }
        found.sort((a, b) => parseInt(/(\d+)(\.html?)?$/.exec(pathOf(a))[1], 10) - parseInt(/(\d+)(\.html?)?$/.exec(pathOf(b))[1], 10));
    }
    return found.filter(u => trimSlash(u) !== trimSlash(base));
}

/** A chapter of images as HTML, or null for a chapter of text. */
async function mangaChapter(doc, final) {
    let text = plain(doc.body || doc.documentElement).replace(/\s+/g, "").length;
    let images = pageImages(doc, final);
    let pages = chapterPages(doc, final);
    if (!images.length || (!pages.length && text > 3000 && images.length < 3)) {
        return null;
    }
    if (pages.length && images.length <= 3) {
        let found = await pool(pages.slice(0, MAX_MANGA_PAGES), 8, async url => {
            let read = await page(url, final);
            return pageImages(read.doc, read.url).slice(0, 3);
        });
        for (let more of found) {
            for (let image of more || []) {
                if (!images.includes(image)) {
                    images.push(image);
                }
            }
        }
    } else if (images.length < 3) {
        return null;
    }
    return images.map(image => "<p><img src=\"" + image.replace(/"/g, "&quot;") + "\" alt=\"\"/></p>").join("");
}

async function chapter(item) {
    let url = item.url || "";
    let name = item.fullName || item.name || "";
    let read = await page(url, item.bookUrl || location.href);
    let manga = await mangaChapter(read.doc, read.url);
    if (manga != null) {
        return { chapterName: name, contentHtml: manga, contentText: "", imageCount: 0, images: [] };
    }
    let firstDoc = parse(read.doc.documentElement.outerHTML);
    let [content, text] = extractContent(read.doc, read.url, name);
    // A chapter split over several pages ("下一页", "Next page"): join them.
    let final = read.url;
    let seen = new Set([final]);
    let doc = firstDoc;
    for (let i = 0; i < MAX_CHAPTER_PAGES; i++) {
        let following = nextLink(doc, final, NEXT_PAGE_WORDS);
        if (!following || seen.has(following) || shape(following) !== shape(final)) {
            break;
        }
        seen.add(following);
        let more;
        try {
            more = await page(following, url);
        } catch (e) {
            break;
        }
        final = more.url;
        doc = parse(more.doc.documentElement.outerHTML);
        let [moreContent, moreText] = extractContent(more.doc, final, name);
        content += "\n" + moreContent;
        text += "\n" + moreText;
    }
    if (text.length < 20 && !content.includes("<img")) {
        throw new Error("no chapter text found on " + url);
    }
    return { chapterName: name, contentHtml: content, contentText: text, imageCount: 0, images: [] };
}

function failure(error) {
    return { error: String((error && error.message) || error || "unknown error") };
}

window.__GEN_parseBook = async function (url) {
    try {
        return JSON.stringify(await book(url || location.href));
    } catch (error) {
        return JSON.stringify(failure(error));
    }
};

window.__GEN_parseChapter = async function (itemJson) {
    try {
        let item = typeof itemJson === "string" ? JSON.parse(itemJson) : itemJson;
        return JSON.stringify(await chapter(item || {}));
    } catch (error) {
        return JSON.stringify(failure(error));
    }
};

// The helpers the mobile Catalog's search (scripts/generic/catalog-app.js) shares with this engine.
window.__GEN_lib = {
    shape, cleanName, chapterNumber, readingOrder, numberedPages, findChapters, metadata, extractContent,
    repair, siteOf, hostOf, cleanUrl, textOf, parse, cleanPageTitle, parentAddresses, isChapterAddress, decode,
    CHAPTER_WORDS
};
window.__GEN_test = window.__GEN_lib;
window.__GEN_API = GEN_API;
window.__GEN_READY = true;
} catch (error) {
    window.__GEN_LOAD_ERROR = { message: String((error && error.message) || error) };
}
})();
