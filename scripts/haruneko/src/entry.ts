// NovelDB bridge for HaruNeko's website plugins (bundled by build.mjs, wrapped by update_haruneko.py).
// window.__HN_parseBook(url)            -> JSON book info and chapter list
// window.__HN_parseChapter(json)        -> JSON chapter {chapterName, contentHtml, images: [{url, name, data}]}
// window.__HN_parseChapterBatch(json)   -> JSON array of chapter JSON strings
// window.__HN_sites()                   -> JSON [{id, title, url}]
// Chapters are image pages: each page is fetched by the website's own code (some sites need their
// own requests or post-processing) and handed to the app as a data URL.
import '@hn/engine/RegExpSafe';  // a global the websites use (HaruNeko's index.ts imports it first)
import '@hn/engine/ArrayExtensions';
import * as websites from '@hn/engine/websites/_index';
import { SettingsManager } from '@hn/engine/SettingsManager';
import { FeatureFlags } from '@hn/engine/FeatureFlags';
import { Priority } from '@hn/engine/taskpool/DeferredTask';
import type { MangaScraper, MangaPlugin, Manga, Chapter } from '@hn/engine/providers/MangaPlugin';
import { SetupFetchProvider } from './platform';
import { MemoryStorage } from './storage';

const HN_API = 1;
const storage = new MemoryStorage();
const settings = new SettingsManager(storage);
SetupFetchProvider(new FeatureFlags(settings));

type Entry = { name: string; scraper: MangaScraper; plugin?: MangaPlugin };
const entries: Entry[] = [];
for (const [name, Website] of Object.entries(websites)) {
    try {
        entries.push({ name, scraper: new (Website as unknown as new () => MangaScraper)() });
    } catch (error) {
        console.warn('[HN] website failed to load', name, error);
    }
}

const errors: string[] = [];
const books = new Map<string, { manga: Manga; chapters: Map<string, Chapter> }>();

function message(error: unknown): string {
    return String((error as Error)?.message ?? error);
}

function host(url: string): string {
    try {
        return new URL(url).hostname.toLowerCase().replace(/^www\./, '');
    } catch {
        return '';
    }
}

function plugin(entry: Entry): MangaPlugin {
    entry.plugin ??= entry.scraper.CreatePlugin(storage, settings) as MangaPlugin;
    return entry.plugin;
}

// Websites whose own check accepts the address, those on the same host first.
function candidates(url: string): Entry[] {
    const found = entries.filter(entry => {
        try {
            return entry.scraper.ValidateMangaURL(url);
        } catch {
            return false;
        }
    });
    const own = host(url);
    return found.sort((a, b) => Number(host(b.scraper.URI.href) === own) - Number(host(a.scraper.URI.href) === own));
}

function chapterNumber(title: string): number {
    const match = /(\d+(?:\.\d+)?)/.exec(title || '');
    return match ? parseFloat(match[1]) : NaN;
}

function absolute(identifier: string, base: URL): string {
    try {
        return new URL(identifier, base).href;
    } catch {
        return base.href;
    }
}

async function parseBook(url: string) {
    let lastError: unknown = null;
    const list = candidates(url);
    if (!list.length) {
        return { error: 'HaruNeko has no website for this address', noParser: true };
    }
    for (const entry of list) {
        try {
            const manga = await plugin(entry).TryGetEntry(url);
            if (!manga) continue;
            await manga.Update();
            let chapters = [...manga.Entries.Value];
            // Websites list the newest chapter first; the app wants reading order.
            if (chapters.length > 1) {
                const first = chapterNumber(chapters[0].Title), last = chapterNumber(chapters[chapters.length - 1].Title);
                if (!isNaN(first) && !isNaN(last) ? first > last : true) chapters = chapters.reverse();
            }
            const key = `${entry.name}|${manga.Identifier}`;
            books.set(key, { manga, chapters: new Map(chapters.map(chapter => [chapter.Identifier, chapter])) });
            const cover = document.querySelector<HTMLMetaElement>('meta[property="og:image"]')?.content || null;
            return {
                api: HN_API,
                engine: 'haruneko',
                ruleName: `HaruNeko ${entry.scraper.Title}`,
                website: entry.name,
                mangaId: manga.Identifier,
                bookUrl: url,
                bookname: manga.Title,
                author: '',
                introduction: document.querySelector<HTMLMetaElement>('meta[property="og:description"], meta[name="description"]')?.content || '',
                introductionHTML: '',
                coverUrl: host(location.href) === host(url) ? cover : null,
                tags: [],
                isComic: true,
                chapterCount: chapters.length,
                chapters: chapters.map((chapter, index) => ({
                    url: absolute(chapter.Identifier, entry.scraper.URI),
                    name: chapter.Title || `Chapter ${index + 1}`,
                    number: index + 1,
                    id: chapter.Identifier,
                    plugin: entry.name,
                    mangaId: manga.Identifier,
                    bookUrl: url,
                    isVIP: false,
                    isPaid: false,
                })),
                warnings: errors.slice(-5),
            };
        } catch (error) {
            lastError = error;
            errors.push(`${entry.name}: ${message(error)}`);
        }
    }
    return { error: message(lastError ?? 'HaruNeko found no title on this page') };
}

async function findChapter(item: Record<string, string>): Promise<Chapter> {
    const key = `${item.plugin}|${item.mangaId}`;
    if (!books.has(key)) {
        await parseBook(item.bookUrl || location.href);
    }
    const chapter = books.get(key)?.chapters.get(item.id);
    if (!chapter) throw new Error(`Chapter not found: ${item.name || item.id}`);
    return chapter;
}

function blobToDataUrl(blob: Blob): Promise<string> {
    return new Promise((resolve, reject) => {
        const reader = new FileReader();
        reader.onload = () => resolve(String(reader.result));
        reader.onerror = () => reject(reader.error);
        reader.readAsDataURL(blob);
    });
}

function escape(text: string): string {
    return text.replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

async function parseChapter(item: Record<string, string>) {
    const chapter = await findChapter(item);
    await chapter.Update();
    const pages = [...chapter.Entries.Value];
    if (!pages.length) throw new Error(`The chapter has no pages: ${item.name || item.id}`);
    const signal = new AbortController().signal;
    const images: { url: string; name: string; data: string }[] = [];
    const html: string[] = [];
    let index = 0;
    for (const page of pages) {
        index++;
        const blob = await page.Fetch(Priority.High, signal);
        const ext = (blob.type.split('/')[1] || 'jpg').replace('jpeg', 'jpg').replace(/[^a-z0-9]/g, '');
        const link = (page as unknown as { Link?: URL }).Link?.href || `page-${index}`;
        images.push({ url: link, name: `hn_page_${String(index).padStart(4, '0')}.${ext}`, data: await blobToDataUrl(blob) });
        html.push(`<div class="hn-page"><img src="${escape(link)}" alt="page ${index}" /></div>`);
    }
    return {
        chapterName: item.name || chapter.Title,
        contentText: '',
        contentHtml: html.join('\n'),
        images,
        contentCss: '.hn-page { margin: 0; padding: 0; text-align: center; } '
            + '.hn-page img { display: block; width: 100%; max-width: 100%; height: auto; margin: 0 auto; }',
    };
}

function errorResult(error: unknown, extra: Record<string, unknown> = {}) {
    return { error: message(error), stack: String((error as Error)?.stack ?? '').substring(0, 2000), log: errors.slice(-5), ...extra };
}

const target = window as unknown as Record<string, unknown>;
target.__HN_parseBook = async (url?: string) => {
    try {
        errors.length = 0;
        return JSON.stringify(await parseBook(url || location.href));
    } catch (error) {
        return JSON.stringify(errorResult(error));
    }
};
target.__HN_parseChapter = async (itemJson: string | Record<string, string>) => {
    try {
        const item = typeof itemJson === 'string' ? JSON.parse(itemJson) : itemJson;
        return JSON.stringify(await parseChapter(item));
    } catch (error) {
        return JSON.stringify(errorResult(error));
    }
};
target.__HN_parseChapterBatch = async (itemsJson: string | Record<string, string>[]) => {
    const items = typeof itemsJson === 'string' ? JSON.parse(itemsJson) : itemsJson;
    const results: string[] = [];
    for (const item of items) {
        results.push(await (target.__HN_parseChapter as (i: unknown) => Promise<string>)(item));
    }
    return JSON.stringify(results);
};
target.__HN_sites = () => JSON.stringify(entries.map(entry => ({
    id: entry.name, title: entry.scraper.Title, url: entry.scraper.URI.href,
})));
// Each step of reading a book, logged to the console (diagnostics).
target.__HN_trace = async (url: string) => {
    const entry = candidates(url)[0];
    if (!entry) return 'no website';
    console.log('[HN] trace: CreatePlugin', entry.name);
    const created = plugin(entry);
    console.log('[HN] trace: Initialize');
    await created.Initialize();
    console.log('[HN] trace: FetchManga');
    const manga = await entry.scraper.FetchManga(created, url);
    console.log('[HN] trace: FetchChapters', manga.Title);
    const chapters = await entry.scraper.FetchChapters(manga);
    console.log('[HN] trace: done', chapters.length);
    return `${manga.Title}: ${chapters.length} chapters`;
};
// Which website accepts an address, and why not (for checks and diagnostics).
target.__HN_check = (url: string, id?: string) => JSON.stringify(entries
    .filter(entry => !id || entry.name === id)
    .map(entry => {
        try {
            return { id: entry.name, url: entry.scraper.URI.href, accepts: entry.scraper.ValidateMangaURL(url) };
        } catch (error) {
            return { id: entry.name, url: entry.scraper.URI.href, error: message(error) };
        }
    })
    .filter(found => id || found.accepts || found.error));
target.__HN_info = () => JSON.stringify({ api: HN_API, sites: entries.length, ...((globalThis as Record<string, unknown>).__HN_META as object) });
target.__HN_API = HN_API;
target.__HN_READY = true;
console.log(`[HN] HaruNeko ready, ${entries.length} websites`);
