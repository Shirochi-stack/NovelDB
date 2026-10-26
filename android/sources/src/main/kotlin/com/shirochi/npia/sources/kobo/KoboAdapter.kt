package com.shirochi.npia.sources.kobo

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.core.WebProfile
import com.shirochi.npia.core.evaluate
import com.shirochi.npia.core.waitFor
import com.shirochi.npia.core.withPage
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import java.net.URI

/**
 * kobo: reads a book the signed-in account already owns through Kobo's web reader, which is driven
 * in a web page (`WebProfile.EXTERNAL`). Books that are not in the account's library are refused.
 * Rakuten Books JP links need the Rakuten Kobo Desktop app on a PC and are refused here.
 */
class KoboAdapter : SourceAdapter {
    override val id = SourceId.KOBO
    override val tier = SourceTier.LIGHT_BROWSER

    override fun matches(url: String): Boolean = KoboParsing.matches(url)

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        if (KoboParsing.isRakutenUrl(url)) {
            context.logger.log(LogLevel.INFO, "[kobo] Rakuten Books JP link: only the PC Kobo Desktop app can read it")
            return KoboParsing.rakutenBook(url)
        }
        val meta = context.web.withPage(WebProfile.EXTERNAL) { page -> discover(page, url) }
        val book = KoboParsing.makeBook(url, meta)
        if (meta.readUrl.isEmpty()) {
            context.logger.log(LogLevel.WARNING, "[kobo] This account has no Read Now access to this volume. Check that the book is in My Books.")
        }
        context.logger.log(LogLevel.INFO, "[kobo] Book: ${book.title} by ${book.author ?: "?"} - 1 volume")
        return book
    }

    private suspend fun discover(page: WebPageDriving, url: String): KoboMeta {
        page.load(url, WebLoadState.DOM_CONTENT_LOADED, 45)
        // The JP storefront renders a hidden desktop/mobile title before its visible title.
        // The first h1 still contains the real title.
        val hasTitle = page.waitFor("document.querySelector('h1')", 30.0)
        if (!hasTitle) throw SourceException.Parse("The Kobo book page did not load.")
        page.waitFor(
            "document.querySelector('a[href*=\"readnow.kobo.com\"], [data-kobo-gizmo-config*=\"readNowUrl\"]')",
            8.0,
        )
        var meta = KoboMeta.from(page.evaluate<JsonElement>(META_JS))
        if (meta.readUrl.isEmpty()) {
            meta = meta.copy(readUrl = KoboParsing.ownedReadUrl(page.html()))
        }
        return meta
    }

    // Chapter

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        if (ref.id == KoboParsing.RAKUTEN_CHAPTER_ID) {
            return ChapterResult.Refused(
                ref,
                "Rakuten Books JP volumes are read through the Rakuten Kobo Desktop app on a PC, which is not available on Android",
            )
        }
        if (ref.isPaid) {
            return ChapterResult.Refused(ref, "This book is not in the signed-in Kobo account's library (only owned books can be read)")
        }
        val readUrl = ref.url
        if (readUrl == null || !KoboParsing.isReaderUrl(readUrl)) {
            return ChapterResult.Refused(ref, "No valid Kobo Read Now link for this book")
        }
        val horizontal = context.settings.sources.koboHorizontalLayout
        val logger = context.logger
        val content = context.web.withPage(WebProfile.EXTERNAL) { page ->
            extract(page, readUrl, ref, horizontal, logger)
        }
        return ChapterResult.Content(content)
    }

    /** Walks the reader's table of contents, snapshots every source section, and assembles the book. */
    private suspend fun extract(
        page: WebPageDriving,
        readUrl: String,
        ref: ChapterRef,
        horizontalLayout: Boolean,
        logger: JobLogging,
    ): ChapterContent {
        logger.log(LogLevel.INFO, "[kobo] Opening owned volume in Kobo Web Reader: ${ref.title}")
        page.load(readUrl, WebLoadState.DOM_CONTENT_LOADED, 45)
        val opened = page.waitFor(visibleExpression(CONTENTS_BUTTON), 45.0)
        if (!opened) {
            val current = page.currentUrl()
            val host = current?.let { runCatching { URI(it).host }.getOrNull() }
            if (host != KoboParsing.READER_HOST) throw SourceException.LoginRequired(SourceId.KOBO)
            throw SourceException.Blocked("The Kobo reader did not open; check that the book is in My Books")
        }

        val toc = loadToc(page)
        val entries = ArrayList<KoboEntry>()
        for (item in toc.entries) {
            val path = KoboParsing.resolvePath(item.href, toc.base)
            if (path.isNotEmpty()) entries.add(KoboEntry(item.title, path))
        }
        if (entries.isEmpty()) throw SourceException.Parse("Kobo reader table of contents is empty.")

        val collected = LinkedHashMap<String, KoboSnapshotSection>()
        val snapshots = ArrayList<List<String>>()
        for ((index, entry) in entries.withIndex()) {
            currentCoroutineContext().ensureActive()
            openContents(page)
            val count = page.evaluate<Int>(clickLinkJs(index))
            if (count < 0) throw SourceException.Parse("Kobo reader table of contents changed.")
            val ready = page.waitFor(sectionReadyExpression(entry.path), 20.0)
            if (!ready) throw SourceException.Parse("Kobo reader did not load: ${entry.title}")
            delay(450)
            val keys = Json.encodeToString(ListSerializer(String.serializer()), collected.keys.toList())
            val known = KoboParsing.jsString(keys)
            val snapshot = page.evaluate<KoboSnapshot>("const __fn = $SNAPSHOT_JS; return await __fn(JSON.parse($known));")
            snapshots.add(snapshot.paths)
            for (section in snapshot.sections) collected[section.path] = section
            logger.log(LogLevel.INFO, "[kobo] Read source section ${index + 1}/${entries.size}: ${entry.title}")
        }
        return KoboParsing.buildContent(ref, entries, collected, snapshots, horizontalLayout)
    }

    private suspend fun openContents(page: WebPageDriving) {
        val clicked = page.evaluate<Boolean>(
            "const e = document.querySelector(${KoboParsing.jsString(CONTENTS_BUTTON)}); if (!e) return false; e.click(); return true;",
        )
        if (!clicked) throw SourceException.Parse("Kobo reader contents button is missing.")
        val shown = page.waitFor(visibleExpression(CONTENTS_DIALOG), 12.0)
        if (!shown) throw SourceException.Parse("Kobo reader contents did not open.")
    }

    private suspend fun loadToc(page: WebPageDriving): KoboToc {
        openContents(page)
        val labels = page.evaluate<List<String>>(
            """
            const d = document.querySelector(${KoboParsing.jsString(CONTENTS_DIALOG)});
            if (!d) return [];
            return [...d.querySelectorAll('[role="link"]')].map(a => (a.textContent || '').trim());
            """.trimIndent(),
        )
        if (labels.isEmpty()) throw SourceException.Parse("Kobo reader did not expose its source contents.")
        page.evaluate<Int>(clickLinkJs(0))
        val loaded = page.waitFor(
            "[...document.querySelectorAll('iframe[data-chapterurl]')].some(f => { try { return !!f.contentDocument?.querySelector('nav a[href]'); } catch (_) { return false; } })",
            15.0,
        )
        if (!loaded) throw SourceException.Parse("Kobo reader source contents were not loaded.")
        delay(1000)
        val toc = page.evaluate<KoboToc?>(TOC_JS) ?: throw SourceException.Parse("Kobo reader source contents were not loaded.")
        if (labels.size >= toc.entries.size) {
            return toc.copy(entries = toc.entries.mapIndexed { position, entry -> entry.copy(title = labels[position]) })
        }
        return toc
    }

    internal companion object {
        // JavaScript

        const val CONTENTS_BUTTON =
            "[role=\"button\"][aria-label=\"Open table of contents\"], [role=\"button\"][aria-label=\"\u76EE\u6B21\u3092\u8868\u793A\"]"
        const val CONTENTS_DIALOG =
            "[role=\"dialog\"][aria-label=\"Table of contents\"], [role=\"dialog\"][aria-label=\"\u76EE\u6B21\"]"

        fun visibleExpression(selector: String): String =
            "(() => { const e = document.querySelector(${KoboParsing.jsString(selector)}); if (!e) return false; " +
                "const r = e.getBoundingClientRect(); return r.width > 0 && r.height > 0; })()"

        fun sectionReadyExpression(path: String): String =
            "[...document.querySelectorAll('iframe[data-chapterurl]')].some(f => { try { return f.dataset.chapterurl === " +
                "${KoboParsing.jsString(path)} && !!f.contentDocument?.body?.innerHTML?.trim(); } catch (_) { return false; } })"

        /** Clicks the nth link in the contents dialog; returns the link count, or -1 when missing. */
        fun clickLinkJs(index: Int): String =
            """
            const d = document.querySelector(${KoboParsing.jsString(CONTENTS_DIALOG)});
            if (!d) return -1;
            const links = d.querySelectorAll('[role="link"]');
            if ($index >= links.length) return -1;
            links[$index].click();
            return links.length;
            """.trimIndent()

        val META_JS = """
            const text = (v) => typeof v === 'string' ? v : (Array.isArray(v) ? text(v[0]) : ((v && (v.name || v.url)) || ''));
            const meta = (name) => document.querySelector(
              `meta[property="${'$'}{name}"],meta[name="${'$'}{name}"]`)?.content || '';
            const json = [...document.querySelectorAll('script[type="application/ld+json"]')]
              .map(s => { try { return JSON.parse(s.textContent); } catch (_) { return null; } })
              .flatMap(x => Array.isArray(x) ? x : [x])
              .find(x => x && (x['@type'] === 'Book' || x['@type'] === 'Product')) || {};
            const read = [...document.querySelectorAll('a[href]')]
              .find(a => /^https:\/\/readnow\.kobo\.com\//.test(a.href)
                && /read now/i.test(a.innerText || a.textContent || ''));
            return {
              title: document.querySelector('h1')?.textContent?.trim() || '',
              author: text(json.author)
                || document.querySelector('a[href*="/author/"]')?.textContent?.trim()
                || meta('book:author') || '',
              cover: meta('og:image') || text(json.image) ||
                [...document.images].find(x => /cover/i.test((x.alt || '') + ' ' + (x.src || '')))?.src || '',
              description: meta('og:description') || meta('description') || '',
              publisher: text(json.publisher),
              readUrl: read?.href || '',
              language: document.documentElement.lang || 'en',
            };
        """.trimIndent()

        val TOC_JS = """
            for (const frame of document.querySelectorAll('iframe[data-chapterurl]')) {
              let doc;
              try { doc = frame.contentDocument; } catch (_) { continue; }
              const nav = doc?.querySelector('nav');
              if (!nav) continue;
              const entries = [...nav.querySelectorAll('a[href]')].map(a => ({
                title: a.innerText?.trim() || a.textContent?.trim() || '',
                href: a.getAttribute('href') || ''
              })).filter(x => x.title && /\.xhtml?(?:#|${'$'})/i.test(x.href));
              if (entries.length > 1) return { base: frame.dataset.chapterurl, entries };
            }
            return null;
        """.trimIndent()

        val SNAPSHOT_JS = """
            async (known) => {
              const seen = new Set(known);
              const frames = [...document.querySelectorAll('iframe[data-chapterurl]')];
              const paths = frames.map(f => f.dataset.chapterurl).filter(Boolean);
              const sections = [];
              for (const frame of frames) {
                const path = frame.dataset.chapterurl;
                if (!path || seen.has(path)) continue;
                let doc;
                try { doc = frame.contentDocument; } catch (_) { continue; }
                if (!doc?.body || !doc.body.innerHTML.trim()) continue;
                const text = doc.body.innerText || '';
                const encoded = [...text].filter(c => {
                  const value = c.codePointAt(0);
                  return value >= 0x1f60 && value <= 0x1fff;
                }).length;
                if (/^en\b/i.test(frame.lang || doc.documentElement.lang || '') &&
                    text.length > 50 && encoded > text.length / 20) continue;
                const clone = doc.body.cloneNode(true);
                const images = [];
                const originals = [...doc.body.querySelectorAll('img')];
                const copies = [...clone.querySelectorAll('img')];
                for (let i = 0; i < originals.length; i++) {
                  const source = originals[i].currentSrc || originals[i].src || '';
                  if (!source) continue;
                  copies[i].setAttribute('src', source);
                  let data = source.startsWith('data:image/') ? source : null;
                  if (!data) {
                    try {
                      const response = await doc.defaultView.fetch(source, {
                        credentials: 'include'
                      });
                      if (response.ok) {
                        const blob = await response.blob();
                        data = await new Promise(resolve => {
                          const reader = new FileReader();
                          reader.onload = () => resolve(reader.result);
                          reader.onerror = () => resolve(null);
                          reader.readAsDataURL(blob);
                        });
                      }
                    } catch (_) {}
                  }
                  if (!data && originals[i].complete &&
                      originals[i].naturalWidth && originals[i].naturalHeight) {
                    try {
                      const canvas = doc.createElement('canvas');
                      canvas.width = originals[i].naturalWidth;
                      canvas.height = originals[i].naturalHeight;
                      canvas.getContext('2d').drawImage(originals[i], 0, 0);
                      data = canvas.toDataURL('image/png');
                    } catch (_) {}
                  }
                  images.push({url: source, data});
                }
                sections.push({path, html: clone.innerHTML, images});
              }
              return {paths, sections};
            }
        """.trimIndent()
    }
}
