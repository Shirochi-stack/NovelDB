package com.shirochi.npia.sources.floo

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.HttpStatusException
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebProfile
import com.shirochi.npia.core.waitFor
import com.shirochi.npia.core.withPage
import com.shirochi.npia.sources.websupport.fetchOk
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlin.random.Random

/**
 * floo: free chapters over HTTP; chapters that need the signed-in account (VIP) are read through
 * the web engine's profile, and bought VIP chapters, which the site serves as server-drawn images,
 * come back as chapter images. Chapters the account has not bought are refused; nothing is ever
 * purchased. Port of `_floo_*` in the desktop external scraper and the iOS `FlooAdapter`.
 *
 * The desktop additionally turns the VIP images back into text with `floo_image_reader`
 * (template matching against glyphs rendered by Windows GDI+ with a system font). None of that
 * exists on Android, so the images are returned as is.
 */
class FlooAdapter : SourceAdapter {
    override val id = SourceId.FLOO
    override val tier = SourceTier.LIGHT_BROWSER

    override fun matches(url: String): Boolean = FlooParser.matches(url)

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val bookId = FlooParser.bookId(url)
        if (bookId.isEmpty()) throw SourceException.UnsupportedUrl(url)
        val canonical = FlooParser.canonicalBookUrl(bookId)
        val candidates = mutableListOf(canonical)
        if (url != canonical) candidates.add(url)

        for (target in candidates) {
            try {
                val response = fetch(target, context.http)
                val book = FlooParser.book(FlooParser.decodings(response.body), response.url, bookId, canonical)
                if (book != null) {
                    context.logger.log(LogLevel.INFO, "[floo] Book: ${book.title} - ${book.chapters.size} chapters")
                    return book
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                context.logger.log(LogLevel.WARNING, "[floo] Book request failed: ${e.message}")
            }
        }

        // The HTTP response can lack the catalog while the browser version renders it.
        try {
            val found: BookInfo? = context.web.withPage(WebProfile.EXTERNAL) { page ->
                var result: BookInfo? = null
                for (target in candidates) {
                    page.load(target, WebLoadState.DOM_CONTENT_LOADED, 30)
                    var html = page.html()
                    var pageUrl = page.currentUrl() ?: target
                    var book = FlooParser.book(listOf(html), pageUrl, bookId, canonical)
                    if (book == null) {
                        try {
                            page.waitFor(
                                "document.querySelector('div.C-Fo-Zuo div.DivTable a, .chapter-list a, .chapterList a, .catalog a')",
                                timeoutSeconds = 10.0,
                            )
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            // Fall through and parse whatever rendered.
                        }
                        html = page.html()
                        pageUrl = page.currentUrl() ?: target
                        book = FlooParser.book(listOf(html), pageUrl, bookId, canonical)
                    }
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
        } catch (e: Exception) {
            context.logger.log(LogLevel.WARNING, "[floo] Browser book request failed: ${e.message}")
        }
        throw SourceException.Parse("floo: book title or chapter catalog was not found.")
    }

    // Chapter

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val shared = context.shared(id)
        if (ref.isPaid && shared.skipPaid) {
            return ChapterResult.Refused(ref, "Paid chapter skipped (skip paid is on).")
        }
        val url = ref.url ?: return ChapterResult.Failed(ref, "Chapter has no URL.")

        var outcome: FlooChapterOutcome = FlooChapterOutcome.None
        // A guest preview of a paid chapter can look like full text, so paid chapters are
        // always read in the signed-in web profile.
        if (!ref.isPaid) {
            try {
                val response = fetch(url, context.http)
                outcome = FlooParser.chapter(FlooParser.decodings(response.body), response.url)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Try the browser below.
            }
        }
        if (needsBrowser(outcome)) {
            browserOutcome(url, context)?.let { outcome = it }
        }

        return when (val result = outcome) {
            is FlooChapterOutcome.Text -> ChapterResult.Content(ChapterContent(ref, FlooParser.chapterHtml(result.lines)))
            is FlooChapterOutcome.Images -> imageChapter(ref, result.urls, url, context.http)
            is FlooChapterOutcome.Locked ->
                if (result.reason == "login") {
                    ChapterResult.Refused(ref, "Sign in to floo in the app to read this chapter.")
                } else {
                    ChapterResult.Refused(ref, "This chapter has not been bought by the signed-in account.")
                }
            FlooChapterOutcome.None -> ChapterResult.Failed(ref, "Could not read this chapter.")
        }
    }

    companion object {
        internal fun headers(): Map<String, String> = mapOf(
            "User-Agent" to FlooParser.USER_AGENT,
            "Accept" to "text/html,application/xhtml+xml,*/*;q=0.8",
            "Accept-Language" to "zh-CN,zh;q=0.9",
            "Referer" to "https://${FlooParser.bookHost}/",
        )

        /** GET that follows the site's `C3VK` cookie challenge (up to three requests in all). */
        internal suspend fun fetch(url: String, http: HttpFetching): HttpResponse {
            var response = http.fetchOk(url, headers())
            repeat(2) {
                val token = FlooParser.challengeToken(response.body) ?: return response
                val cookie = "C3VK=$token"
                response = http.fetchOk(url, headers() + ("Cookie" to cookie))
            }
            return response
        }

        internal fun needsBrowser(outcome: FlooChapterOutcome): Boolean = when (outcome) {
            FlooChapterOutcome.None, is FlooChapterOutcome.Locked -> true
            is FlooChapterOutcome.Text, is FlooChapterOutcome.Images -> false
        }

        /** Renders the chapter in the signed-in web profile. Null when the page could not be loaded. */
        internal suspend fun browserOutcome(url: String, context: SourceContext): FlooChapterOutcome? = try {
            context.web.withPage(WebProfile.EXTERNAL) { page ->
                page.load(url, WebLoadState.DOM_CONTENT_LOADED, 30)
                try {
                    page.waitFor("document.querySelector('${FlooParser.CONTENT_SELECTOR}')", timeoutSeconds = 8.0)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    // Parse whatever rendered.
                }
                val html = page.html()
                val finalUrl = page.currentUrl() ?: url
                FlooParser.chapter(listOf(html), finalUrl)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            context.logger.log(LogLevel.WARNING, "[floo] Browser chapter failed: ${e.message}")
            null
        }

        /** Downloads a bought VIP chapter's images. A non-image answer means the account cannot read it. */
        internal suspend fun imageChapter(ref: ChapterRef, urls: List<String>, referer: String, http: HttpFetching): ChapterResult {
            val images = mutableListOf<ImageRef>()
            for ((offset, url) in urls.withIndex()) {
                val requestHeaders = headers().toMutableMap()
                requestHeaders["Referer"] = referer
                requestHeaders["Accept"] = "image/gif,image/png,image/jpeg,image/*;q=0.8,*/*;q=0.5"
                val data: ByteArray = try {
                    http.fetchOk(url, requestHeaders).body
                } catch (e: CancellationException) {
                    throw e
                } catch (e: HttpStatusException) {
                    if (e.status == 401 || e.status == 403) {
                        return ChapterResult.Refused(ref, "The signed-in account cannot read this chapter's images.")
                    }
                    return ChapterResult.Failed(ref, "Could not download chapter image ${offset + 1}: ${e.message}")
                } catch (e: Exception) {
                    return ChapterResult.Failed(ref, "Could not download chapter image ${offset + 1}: ${e.message}")
                }
                val ext = FlooParser.imageExtension(data)
                    ?: return ChapterResult.Refused(ref, "This chapter has not been bought by the signed-in account.")
                val filename = FlooParser.imageFilename(ref.index, offset + 1, ext)
                images.add(ImageRef(url, filename, data))
            }
            val html = FlooParser.imageHtml(images.map { it.filename })
            return ChapterResult.Content(ChapterContent(ref, html, images))
        }

        /** `_sleep_interval`: a random pause between `intervalMin` and `intervalMax` seconds. */
        internal suspend fun pause(shared: SharedSettings) = shared.pauseBetweenChapters()
    }
}
