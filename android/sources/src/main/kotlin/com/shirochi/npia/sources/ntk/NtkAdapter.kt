package com.shirochi.npia.sources.ntk

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebLoadTimeoutException
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.core.WebPagePool
import com.shirochi.npia.core.WebProfile
import com.shirochi.npia.core.parseConcurrently
import com.shirochi.npia.sources.hard.HardScripts
import com.shirochi.npia.sources.hard.hard
import com.shirochi.npia.sources.hard.hardJson
import com.shirochi.npia.sources.hard.hardTask
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.min

/** Waits and limits of the ntk scraper. The defaults are the desktop's; tests shrink them. */
internal data class NtkTiming(
    val loadTimeoutSeconds: Long = 45,
    val pageStatePolls: Int = 30,
    val chapterStatePolls: Int = 20,
    val pollMillis: Long = 700,
    val maxPages: Int = 3,
)

/**
 * ntk: a free webtoon/novel aggregator on numbered mirror hosts, behind a Cloudflare / anti-bot gate.
 * The native side opens the sign-in browser so the user clears the check once; after that this reads the
 * book, the chapter list, and each chapter in the verified session. A novel chapter runs the site's own
 * ad-acknowledgement handshake and v3 decrypt in-page; a webtoon chapter scrapes its image pages.
 * Nothing is bought — the site has no VIP concept.
 *
 * Port of `_ntk_*` (browser path) in the desktop scraper. This side navigates, paces and builds results;
 * the page logic is shared with iOS in `hard-sources.js`.
 */
class NtkAdapter internal constructor(
    private val timing: NtkTiming,
) : SourceAdapter {
    constructor() : this(NtkTiming())

    override val id = SourceId.NTK
    override val tier = SourceTier.HEAVY_BROWSER

    override fun matches(url: String): Boolean = NtkSite.matches(url)

    /** The numbered mirror host keeps moving, so the site is recognised by its name pattern. */
    override fun ownsSite(host: String): Boolean = NtkSite.ownsSite(host)

    /** A chapter link reads under the book-index URL so it downloads the whole book. */
    override fun canonicalUrl(url: String): String = if (matches(url)) NtkSite.indexUrl(url) else url

    private val prompted: MutableSet<String> = ConcurrentHashMap.newKeySet()

    /** First call for a key arms the once-only sign-in prompt; the next gives up. */
    private fun promptOnce(key: String): Boolean = if (prompted.remove(key)) false else prompted.add(key)

    private fun log(context: SourceContext, level: LogLevel, message: String) = context.logger.log(level, "[ntk] $message")

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val novelId = NtkSite.novelId(url)
        if (novelId.isEmpty()) throw SourceException.UnsupportedUrl(url)
        val kind = NtkSite.kind(url)
        val indexUrl = NtkSite.indexUrl(url)
        val args = buildJsonObject { put("novelId", novelId); put("kind", kind) }.toString()
        val page = openPage(context.web)
        try {
            loadQuietly(page, indexUrl, context)
            var ready = false
            for (poll in 0 until timing.pageStatePolls) {
                when (page.hard<NtkPageState>("ntk.pageState($args)").state) {
                    "ready" -> ready = true
                    "verification" -> {
                        if (promptOnce("verify:$novelId")) throw SourceException.LoginRequired(SourceId.NTK)
                        throw SourceException.Blocked("the site's security check is still up; clear it in the Browser tab, then retry")
                    }
                    "blocked" -> throw SourceException.Blocked("the site's firewall blocked the page; clear its security check in the Browser tab, then retry")
                    else -> Unit
                }
                if (ready) break
                delay(timing.pollMillis)
            }
            if (!ready) throw SourceException.Blocked("the book page did not render its chapter list")

            val info = page.hard<NtkBookInfo>("ntk.bookInfo($args)")
            if (info.blocked) throw SourceException.Blocked("the book page was blocked by the site's security check")

            val listArgs = JsonArray(listOf(buildJsonObject { put("novelId", novelId); put("kind", kind) })).toString()
            val listing = hardJson.decodeFromString(NtkChapterListResult.serializer(), page.hardTask("ntk.chapterList", listArgs))
            if (!listing.ok || listing.chapters.isEmpty()) {
                throw SourceException.Parse("no chapters were found for this book (${listing.error.ifEmpty { "empty list" }})")
            }

            val chapters = listing.chapters.map { entry ->
                ChapterRef(
                    id = entry.episodeId.ifEmpty { entry.url }, index = entry.number,
                    title = entry.name.ifEmpty { "Chapter ${entry.number}" }, url = entry.url,
                )
            }
            val book = BookInfo(
                source = SourceId.NTK, id = novelId, url = indexUrl,
                title = info.title.ifEmpty { "$kind $novelId" }, author = info.author.ifEmpty { null },
                synopsis = info.synopsis.ifEmpty { null }, coverUrl = info.cover.ifEmpty { null }, tags = info.tags,
                language = "ko", chapters = chapters,
            )
            log(context, LogLevel.INFO, "Book: ${book.title} by ${book.author ?: "?"} - ${chapters.size} $kind chapters")
            return book
        } finally {
            closeQuietly(page)
        }
    }

    // Chapters

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val run = Run(context)
        try {
            return run.chapter(ref)
        } finally {
            run.close()
        }
    }

    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> {
        if (refs.isEmpty()) return emptyList()
        val run = Run(context)
        try {
            return parseConcurrently(refs, context) { ref -> run.chapter(ref) }
        } finally {
            run.close()
        }
    }

    /** The pages one download uses. */
    private inner class Run(private val context: SourceContext) {
        private val pool = WebPagePool(
            context.web, WebProfile.EXTERNAL,
            min(context.shared(SourceId.NTK).threads, timing.maxPages),
        ) { page -> prepare(page) }

        suspend fun chapter(ref: ChapterRef): ChapterResult = pool.withPage { page -> read(page, ref) }

        suspend fun close() = pool.closeAll()

        private suspend fun read(page: WebPageDriving, ref: ChapterRef): ChapterResult {
            val url = ref.url ?: return ChapterResult.Failed(ref, "missing chapter URL")
            val kind = NtkSite.kind(url)
            try {
                page.load(url, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
            } catch (e: WebLoadTimeoutException) {
                val current = page.currentUrl().orEmpty()
                if (NtkSite.novelId(current) != NtkSite.novelId(url)) return ChapterResult.Failed(ref, "page load timed out")
            }

            var sawReady = false
            for (poll in 0 until timing.chapterStatePolls) {
                val state = page.hard<NtkChapterState>("ntk.chapterState()")
                if (state.blocked) return ChapterResult.Failed(ref, "the site's security check blocked this chapter; clear it in the Browser tab, then retry")
                if (state.ready) {
                    sawReady = true
                    break
                }
                delay(timing.pollMillis)
            }
            if (!sawReady) return ChapterResult.Failed(ref, "the chapter page did not finish loading")

            val args = JsonArray(listOf(buildJsonObject { put("chapterUrl", url); put("name", ref.title) })).toString()
            val entry = if (kind == "webtoon") "ntk.webtoon" else "ntk.chapter"
            val result = hardJson.decodeFromString(NtkChapterResult.serializer(), page.hardTask(entry, args))
            if (!result.ok) {
                if (result.blocked) return ChapterResult.Failed(ref, "the site's security check blocked this chapter; clear it in the Browser tab, then retry")
                return ChapterResult.Failed(ref, result.error.ifEmpty { result.stage.ifEmpty { "the chapter could not be read" } })
            }
            if (result.contentHtml.isBlank() && result.images.isEmpty()) {
                return ChapterResult.Failed(ref, "no chapter content was extracted")
            }
            return buildResult(ref, url, result, context)
        }

        private suspend fun buildResult(ref: ChapterRef, url: String, result: NtkChapterResult, context: SourceContext): ChapterResult {
            val images = when {
                result.images.isEmpty() -> emptyList()
                context.settings.images.saveImageUrlsOnly -> result.images.map { ImageRef(it.url, it.name) }
                else -> downloadImages(result.images, NtkSite.origin(url), context.http, context.shared(SourceId.NTK).imageWorkers)
            }
            return ChapterResult.Content(ChapterContent(ref, result.contentHtml, images))
        }
    }

    private suspend fun downloadImages(
        images: List<NtkImage>,
        referer: String,
        http: HttpFetching,
        workers: Int,
    ): List<ImageRef> = coroutineScope {
        val permits = Semaphore(workers.coerceIn(1, 8))
        images.map { image ->
            async {
                permits.withPermit { ImageRef(image.url, image.name, fetchImage(image.url, referer, http)) }
            }
        }.awaitAll()
    }

    /** The bytes, or null (the image stage tries again without a referer). */
    private suspend fun fetchImage(url: String, referer: String, http: HttpFetching): ByteArray? {
        if (url.startsWith("data:", ignoreCase = true)) {
            return runCatching { Base64.getMimeDecoder().decode(url.substringAfter("base64,")) }.getOrNull()
        }
        return try {
            val response = http.get(
                url,
                mapOf("User-Agent" to NtkSite.USER_AGENT, "Referer" to referer, "Accept" to "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"),
            )
            response.body.takeIf { response.status in 200..299 && it.isNotEmpty() }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            null
        }
    }

    // Pages

    private suspend fun loadQuietly(page: WebPageDriving, url: String, context: SourceContext) {
        try {
            page.load(url, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
        } catch (e: WebLoadTimeoutException) {
            log(context, LogLevel.WARNING, "Page load warning: ${e.message}")
        }
    }

    private suspend fun openPage(web: WebEngineProviding): WebPageDriving {
        val page = web.newPage(WebProfile.EXTERNAL)
        try {
            prepare(page)
        } catch (e: Throwable) {
            closeQuietly(page)
            throw e
        }
        return page
    }

    private suspend fun prepare(page: WebPageDriving) {
        page.emulateDesktop(NtkSite.USER_AGENT, NtkSite.VIEWPORT_WIDTH, NtkSite.VIEWPORT_HEIGHT)
        page.addInitScript(HardScripts.source)
    }

    private suspend fun closeQuietly(page: WebPageDriving) {
        try {
            page.close()
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            // Already gone.
        }
    }
}
