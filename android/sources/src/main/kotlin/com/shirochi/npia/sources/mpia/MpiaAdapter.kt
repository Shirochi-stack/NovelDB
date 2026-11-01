package com.shirochi.npia.sources.mpia

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
import com.shirochi.npia.sources.http.ReaderBlocks
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
import kotlin.math.min

/** Waits and limits of the mpia scraper. The defaults are the desktop's; tests shrink them. */
internal data class MpiaTiming(
    val loadTimeoutSeconds: Long = 45,
    val detailPolls: Int = 20,
    val contentPolls: Int = 60,
    val pollMillis: Long = 700,
    /** Pause between chapter-list pages, like the desktop `_mpia_wait_list_interval`. */
    val listIntervalMillis: Long = 500,
    val maxListPages: Int = 1000,
    val maxPages: Int = 3,
)

/**
 * mpia: the mpia site's webnovels the signed-in account can read, through the site's own web reader in a real
 * browser engine. The book and chapter list come from the site's JSON API; the chapter text is drawn by
 * the site's wasm reader onto a canvas and rebuilt from the reader's own output. A chapter the account
 * cannot read is refused; nothing is ever purchased.
 *
 * Port of `_mpia_*` in the desktop scraper. This side navigates, paces and builds results; the page
 * logic is shared with iOS in `hard-sources.js`.
 */
class MpiaAdapter internal constructor(
    private val timing: MpiaTiming,
) : SourceAdapter {
    constructor() : this(MpiaTiming())

    override val id = SourceId.MPIA
    override val tier = SourceTier.HEAVY_BROWSER

    override fun matches(url: String): Boolean = MpiaSite.matches(url)

    override val sites: Set<String> = setOf(MpiaSite.DOMAIN)

    /** A viewer or legacy link reads under the book-detail URL so it downloads like any other. */
    override fun canonicalUrl(url: String): String {
        val novelId = MpiaSite.novelId(url)
        return if (novelId.isEmpty()) url else MpiaSite.bookUrl(novelId)
    }

    private fun log(context: SourceContext, level: LogLevel, message: String) = context.logger.log(level, "[mpia] $message")

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val novelId = MpiaSite.novelId(url)
        if (novelId.isEmpty()) throw SourceException.UnsupportedUrl(url)
        val page = openPage(context.web)
        try {
            loadQuietly(page, MpiaSite.bookUrl(novelId), context)
            var detail: MpiaDetail? = null
            for (poll in 0 until timing.detailPolls) {
                val answer = page.hard<MpiaDetail>("mpia.detail(${HardScripts.literal(novelId)})")
                if (answer.ok) {
                    detail = answer
                    break
                }
                delay(timing.pollMillis)
            }
            val info = detail ?: throw SourceException.Blocked(
                "the mpia book page did not return its details; sign in or clear the site's security check in the Browser tab, then retry",
            )
            log(
                context, LogLevel.INFO,
                if (info.login) "Signed in: free chapters and the ones you own are included." else "Not signed in: only free chapters download. Sign in in Settings > Enter App.",
            )

            val rows = mutableListOf<MpiaChapterRow>()
            val seen = HashSet<String>()
            var pageNo = 1
            while (pageNo <= timing.maxListPages) {
                val listing = page.hard<MpiaChapterList>("mpia.chapters(${HardScripts.literal(novelId)}, $pageNo)")
                if (!listing.ok) {
                    if (rows.isEmpty()) throw SourceException.Parse("the mpia chapter list could not be read (${listing.error.ifEmpty { "page $pageNo" }})")
                    log(context, LogLevel.WARNING, "Stopped reading the chapter list at page $pageNo: ${listing.error}")
                    break
                }
                var added = 0
                for (row in listing.list) if (row.id.isNotEmpty() && seen.add(row.id)) {
                    rows += row
                    added += 1
                }
                if (added == 0 || listing.list.isEmpty() || (listing.total > 0 && rows.size >= listing.total)) break
                pageNo += 1
                delay(timing.listIntervalMillis)
            }
            if (rows.isEmpty()) throw SourceException.Parse("no chapters were found for this mpia book")

            val chapters = rows
                .sortedWith(compareBy({ it.order }, { it.id.toLongOrNull() ?: 0L }))
                .mapIndexed { index, row ->
                    ChapterRef(
                        id = row.id, index = index + 1,
                        title = row.title.ifEmpty { "Chapter ${row.order}" }, url = row.url, isPaid = row.isPaid,
                    )
                }
            val book = BookInfo(
                source = SourceId.MPIA, id = novelId, url = MpiaSite.bookUrl(novelId),
                title = info.title.ifEmpty { "mpia $novelId" }, author = info.author.ifEmpty { null },
                synopsis = info.synopsis.ifEmpty { null }, coverUrl = info.cover.ifEmpty { null }, tags = info.tags,
                status = if (info.finished) "Completed" else "Ongoing", language = "ko", chapters = chapters,
            )
            log(context, LogLevel.INFO, "Book: ${book.title} by ${book.author ?: "?"} - ${chapters.size} chapters")
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
            min(context.shared(SourceId.MPIA).threads, timing.maxPages),
        ) { page -> prepare(page) }

        suspend fun chapter(ref: ChapterRef): ChapterResult = pool.withPage { page -> read(page, ref) }

        suspend fun close() = pool.closeAll()

        private suspend fun read(page: WebPageDriving, ref: ChapterRef): ChapterResult {
            val url = ref.url ?: return ChapterResult.Failed(ref, "missing chapter URL")
            try {
                page.load(url, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
            } catch (e: WebLoadTimeoutException) {
                // A timeout can come after the viewer committed; go on only if this is the requested chapter.
                val current = page.currentUrl().orEmpty()
                if (MpiaSite.novelId(current) != MpiaSite.novelId(url)) return ChapterResult.Failed(ref, "page load timed out")
            }

            var ready = false
            for (poll in 0 until timing.contentPolls) {
                val state = page.hard<MpiaReaderState>("mpia.readerState()")
                if (state.denied) {
                    return ChapterResult.Refused(ref, "locked or not bought: this mpia chapter needs a purchase, rental, or sign-in")
                }
                if (state.ready) {
                    ready = true
                    break
                }
                delay(timing.pollMillis)
            }
            if (!ready) return ChapterResult.Failed(ref, "the mpia reader did not finish drawing the chapter")

            val args = JsonArray(listOf(buildJsonObject { put("chapterIndex", ref.index) })).toString()
            val extract = hardJson.decodeFromString(MpiaExtract.serializer(), page.hardTask("mpia.extract", args))
            if (extract.locked) return ChapterResult.Refused(ref, "locked or not bought (the mpia reader denied this chapter)")
            if (extract.contentHtml.isBlank() && extract.images.isEmpty()) {
                return ChapterResult.Failed(ref, extract.error.ifEmpty { "no chapter content was extracted" })
            }
            return buildResult(ref, extract, context)
        }

        private suspend fun buildResult(ref: ChapterRef, extract: MpiaExtract, context: SourceContext): ChapterResult {
            val images = if (context.settings.images.saveImageUrlsOnly) {
                extract.images.map { ImageRef(it.url, it.name) }
            } else {
                downloadImages(extract.images, MpiaSite.WWW_ORIGIN, context.http, context.shared(SourceId.MPIA).imageWorkers)
            }
            val shown = extract.chapterName.takeIf {
                it.isNotBlank() && ReaderBlocks.normalizeTitle(it) != ReaderBlocks.normalizeTitle(ref.title)
            } ?: ref.title
            val named = ref.copy(title = shown)
            return ChapterResult.Content(ChapterContent(named, extract.contentHtml, images))
        }
    }

    private suspend fun downloadImages(
        images: List<MpiaImage>,
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
                mapOf("User-Agent" to MpiaSite.USER_AGENT, "Referer" to referer, "Accept" to "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"),
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
        page.emulateDesktop(MpiaSite.USER_AGENT, MpiaSite.VIEWPORT_WIDTH, MpiaSite.VIEWPORT_HEIGHT)
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
