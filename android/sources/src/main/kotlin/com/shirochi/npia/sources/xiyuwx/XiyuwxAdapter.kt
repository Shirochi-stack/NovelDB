package com.shirochi.npia.sources.xiyuwx

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.core.WebProfile
import com.shirochi.npia.core.withPage
import com.shirochi.npia.sources.websupport.fetchOk
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlin.random.Random

/**
 * xiyuwx and its yywenxuan mirror. HTTP first (GB18030-safe decoding, multi-page chapters, the
 * `_ii_rr` shuffle decoder); the web engine is the fallback when HTTP fails or the site shows a
 * verification page. Port of `_xiyuwx_*` in the desktop external scraper.
 */
class XiyuwxAdapter : SourceAdapter {
    override val id = SourceId.XIYUWX
    override val tier = SourceTier.LIGHT_BROWSER

    override fun matches(url: String): Boolean = XiyuwxParser.matches(url)

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val bookID = XiyuwxParser.bookID(url)
        if (bookID.isEmpty()) throw SourceException.UnsupportedUrl(url)
        val site = XiyuwxParser.siteName(url)
        val bookUrl = XiyuwxParser.canonicalBookURL(url, bookID)
        val candidates = mutableListOf(url)
        if (bookUrl != url) candidates.add(bookUrl)

        for (target in candidates) {
            try {
                val page = fetch(target, bookUrl, context.http)
                val book = XiyuwxParser.book(page.html, page.url, bookUrl, bookID)
                if (book != null) {
                    context.logger.log(LogLevel.INFO, "[$site] Book: ${book.title} - ${book.chapters.size} chapters")
                    return book
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                context.logger.log(LogLevel.WARNING, "[$site] Book request failed: ${e.message ?: e}")
            }
        }

        var blocked = false
        try {
            val found: BookInfo? = context.web.withPage(WebProfile.EXTERNAL) { page ->
                var result: BookInfo? = null
                for (target in candidates) {
                    val rendered = renderedHtml(page, target)
                    if (XiyuwxParser.isVerificationPage(rendered.html)) break
                    val book = XiyuwxParser.book(rendered.html, rendered.url, bookUrl, bookID)
                    if (book != null) {
                        result = book
                        break
                    }
                }
                result
            }
            if (found != null) return found
        } catch (e: CancellationException) {
            throw e
        } catch (e: SourceException.Blocked) {
            blocked = true
        } catch (e: Exception) {
            context.logger.log(LogLevel.WARNING, "[$site] Browser book request failed: ${e.message ?: e}")
        }
        if (blocked) throw SourceException.Blocked("$site is showing a security verification page.")
        throw SourceException.Parse("$site: book title or chapter catalog was not found.")
    }

    // Chapter

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val shared = context.shared(id)
        if (ref.isPaid && shared.skipPaid) {
            return ChapterResult.Refused(ref, "Paid chapter skipped (skip paid is on).")
        }
        val url = ref.url ?: return ChapterResult.Failed(ref, "Chapter has no URL.")
        val site = XiyuwxParser.siteName(url)

        val lines = httpChapter(url, book.url, context, shared) ?: browserChapter(url, context, shared)
        pause(shared)
        if (lines == null) {
            if (ref.isPaid) return ChapterResult.Refused(ref, "This chapter is locked for the signed-in account.")
            context.logger.log(LogLevel.WARNING, "[$site] Could not read ${ref.title}")
            return ChapterResult.Failed(ref, "Could not read this chapter.")
        }
        return ChapterResult.Content(ChapterContent(ref, XiyuwxParser.chapterHtml(lines)))
    }

    // Fetching

    internal data class FetchedPage(val html: String, val url: String)

    internal companion object {
        fun headers(referer: String): Map<String, String> = mapOf(
            "User-Agent" to XiyuwxParser.USER_AGENT,
            "Accept" to "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language" to "zh-CN,zh;q=0.9",
            "Referer" to referer,
        )

        suspend fun fetch(url: String, referer: String, http: HttpFetching): FetchedPage {
            val response = http.fetchOk(url, headers(referer))
            return FetchedPage(XiyuwxParser.decodePage(response.body), response.url)
        }

        /** Loads `url` in the page; waits briefly while a verification interstitial clears. */
        suspend fun renderedHtml(page: WebPageDriving, url: String): FetchedPage {
            page.load(url, WebLoadState.DOM_CONTENT_LOADED, 30)
            var html = page.html()
            var attempts = 0
            while (XiyuwxParser.isVerificationPage(html) && attempts < 40) {
                delay(500)
                html = page.html()
                attempts++
            }
            if (XiyuwxParser.isVerificationPage(html)) throw SourceException.Blocked("verification page")
            val finalUrl = page.currentUrl() ?: url
            return FetchedPage(html, finalUrl)
        }

        /** Reads every page of a chapter over HTTP. Null when any page cannot be read. */
        suspend fun httpChapter(start: String, referer: String, context: SourceContext, shared: SharedSettings): List<String>? {
            val all = mutableListOf<String>()
            val seen = mutableSetOf<String>()
            var current: String? = start
            for (index in 0 until XiyuwxParser.MAX_PAGES) {
                val url = current ?: return null
                if (url in seen) return null
                if (index > 0) pause(shared)
                seen.add(url)
                val page = try {
                    fetch(url, referer, context.http)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    return null
                }
                val parsed = XiyuwxParser.chapterPage(page.html, page.url) ?: return null
                all.addAll(parsed.lines)
                if (parsed.nextPage == null) return all
                current = parsed.nextPage
            }
            context.logger.log(LogLevel.WARNING, "[${XiyuwxParser.siteName(start)}] Chapter has too many pages")
            return null
        }

        /** Same as `httpChapter` but rendered by the web engine (passes JS verification). */
        suspend fun browserChapter(start: String, context: SourceContext, shared: SharedSettings): List<String>? = try {
            context.web.withPage(WebProfile.EXTERNAL) { page ->
                readRendered(page, start, shared)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            context.logger.log(LogLevel.WARNING, "[${XiyuwxParser.siteName(start)}] Browser chapter failed: ${e.message ?: e}")
            null
        }

        private suspend fun readRendered(page: WebPageDriving, start: String, shared: SharedSettings): List<String>? {
            val all = mutableListOf<String>()
            val seen = mutableSetOf<String>()
            var current: String? = start
            for (index in 0 until XiyuwxParser.MAX_PAGES) {
                val url = current ?: return null
                if (url in seen) return null
                if (index > 0) pause(shared)
                seen.add(url)
                val rendered = renderedHtml(page, url)
                val parsed = XiyuwxParser.chapterPage(rendered.html, rendered.url) ?: return null
                all.addAll(parsed.lines)
                if (parsed.nextPage == null) return all
                current = parsed.nextPage
            }
            return null
        }

        /** `_sleep_interval`: a random pause between `intervalMin` and `intervalMax` seconds. */
        suspend fun pause(shared: SharedSettings) {
            val low = maxOf(0.0, shared.intervalMin)
            val high = maxOf(low, shared.intervalMax)
            val seconds = if (high > low) Random.nextDouble(low, high) else low
            if (seconds <= 0) return
            delay((seconds * 1000).toLong())
        }
    }
}
