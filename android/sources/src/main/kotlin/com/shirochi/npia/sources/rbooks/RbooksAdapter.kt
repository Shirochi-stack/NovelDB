package com.shirochi.npia.sources.rbooks

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SharedCookieStore
import com.shirochi.npia.core.SharedCookies
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
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.ReaderBlocks
import com.shirochi.npia.sources.session.SiteSessions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.min

/** Waits and limits of the rbooks scraper. The defaults are the desktop's; tests shrink them. */
internal data class RbooksTiming(
    val loadTimeoutSeconds: Long = 45,
    val productPolls: Int = 20,
    val libraryPolls: Int = 15,
    val apiPolls: Int = 30,
    /** One discovery call stays well inside a web view's script timeout. */
    val discoverBudgetMillis: Long = 20_000,
    val discoverCalls: Int = 600,
    val apiRecoveries: Int = 3,
    /** The viewer must keep the same text length for this many polls before it counts as drawn. */
    val contentPolls: Int = 43,
    val stablePolls: Int = 2,
    val contentAttempts: Int = 3,
    val pollMillis: Long = 700,
    val maxPages: Int = 3,
)

/**
 * rbooks: webnovel episodes and ebook volumes the signed-in account can read, through the site's web
 * viewer in a real browser engine. The episode list comes from the book API, which only a real
 * browser navigation can reach. A chapter the account does not own (or that the site only opens in
 * its own apps) is refused; nothing is ever purchased. Comic pages come back as chapter images.
 *
 * Port of `_rbooks_*` in the desktop scraper (without its Windows-only PC viewer export). The Kotlin
 * side navigates, keeps the session, paces loads and builds results; the page logic is shared with iOS.
 */
class RbooksAdapter internal constructor(
    private val timing: RbooksTiming,
    private val cookies: () -> SharedCookieStore?,
) : SourceAdapter {
    constructor() : this(RbooksTiming(), { SharedCookies.store })

    override val id = SourceId.RBOOKS
    override val tier = SourceTier.HEAVY_BROWSER

    /** Sign-in and verification prompts already shown for a book; the second try goes on without asking. */
    private val prompted: MutableSet<String> = ConcurrentHashMap.newKeySet()

    override fun matches(url: String): Boolean = RbooksSite.matches(url)

    override val sites: Set<String> = setOf(RbooksSite.DOMAIN)

    private fun log(context: SourceContext, level: LogLevel, message: String) = context.logger.log(level, "[rbooks] $message")

    private fun sessions(context: SourceContext) = SiteSessions(context.secrets, cookies)

    /** True the first time for `key`, false the second (and clears it, so a later job asks again). */
    private fun promptOnce(key: String): Boolean = if (prompted.remove(key)) false else prompted.add(key)

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        var bookId = RbooksSite.bookId(url)
        if (bookId.isEmpty()) throw SourceException.UnsupportedUrl(url)
        val sessions = sessions(context)
        val restored = sessions.restoreOnce(RbooksSite.session)
        if (restored > 0) log(context, LogLevel.INFO, "Restored $restored saved login cookie(s).")

        val page = openPage(context.web)
        try {
            if (RbooksSite.isLibrary(url)) {
                val single = libraryBook(page, url, bookId, context)
                if (single is Library.Single) return single.book
                bookId = (single as Library.Product).bookId
            }
            val book = productBook(page, bookId, context)
            if (sessions.status(RbooksSite.session).present.isNotEmpty()) sessions.snapshot(RbooksSite.session)
            return book
        } finally {
            closeQuietly(page)
        }
    }

    private sealed class Library {
        class Single(val book: BookInfo) : Library()
        class Product(val bookId: String) : Library()
    }

    /** A library URL is either the reader of an owned book (one chapter) or points at the product page. */
    private suspend fun libraryBook(page: WebPageDriving, url: String, requestedId: String, context: SourceContext): Library {
        loadQuietly(page, url, context)
        if (RbooksSite.isAccountGate(page.currentUrl().orEmpty())) throw signInNeeded(requestedId, context, "the library needs a login")
        if (!sessions(context).status(RbooksSite.session).usable) {
            throw signInNeeded(requestedId, context, "library links need a signed-in session")
        }
        var probe = RbooksLibraryProbe()
        for (poll in 0 until timing.libraryPolls) {
            probe = page.hard("rbooks.libraryProbe()")
            if (probe.viewer || probe.product.isNotEmpty()) break
            delay(timing.pollMillis)
        }
        if (probe.viewer) {
            val title = probe.title.ifEmpty { "rbooks $requestedId" }
            log(context, LogLevel.INFO, "Library reader: $title")
            return Library.Single(
                BookInfo(
                    source = SourceId.RBOOKS, id = requestedId, url = url, title = title, language = "ko",
                    chapters = listOf(ChapterRef(requestedId, 1, title, url)),
                ),
            )
        }
        if (probe.product.isEmpty()) {
            throw SourceException.Parse("the library page opened, but no readable book or product link was found; check the rbooks login in Settings > Enter App")
        }
        val productId = RbooksSite.bookId(probe.product)
        log(context, LogLevel.INFO, "Library book resolves to ${RbooksSite.bookUrl(productId)}")
        if (probe.unavailable) {
            log(
                context, LogLevel.WARNING,
                "The library marks this title unavailable" + if (probe.adult) " as an adult (19+) title; complete or renew adult verification in the Browser tab." else ".",
            )
        }
        return Library.Product(productId)
    }

    private suspend fun productBook(page: WebPageDriving, bookId: String, context: SourceContext): BookInfo {
        val bookUrl = RbooksSite.bookUrl(bookId)
        log(context, LogLevel.INFO, "Opening $bookUrl")
        loadQuietly(page, bookUrl, context)
        var ready = false
        for (poll in 0 until timing.productPolls) {
            if (RbooksSite.isAccountGate(page.currentUrl().orEmpty())) throw signInNeeded(bookId, context, "the book page needs a login or adult verification")
            if (page.hard<Boolean>("rbooks.productReady()")) {
                ready = true
                break
            }
            delay(timing.pollMillis)
        }
        if (RbooksSite.isAccountGate(page.currentUrl().orEmpty())) throw signInNeeded(bookId, context, "the book page needs a login or adult verification")
        if (!ready && titleOf(page).contains("just a moment", ignoreCase = true)) {
            throw verification(bookId, context)
        }
        val meta = page.hard<RbooksProduct>("rbooks.productInfo()")
        meta.diagnostics.forEach { log(context, LogLevel.INFO, it) }

        var links = meta.links
        val titleById = meta.titleById.toMutableMap()
        var discoveryError = ""
        if (meta.seriesId.isNotEmpty()) {
            val found = discoverEpisodes(context, meta.seriesId, meta.links, bookId)
            found.diagnostics.forEach { log(context, LogLevel.INFO, it) }
            if (found.links.size > links.size) links = found.links
            titleById += found.titleById
            discoveryError = found.error
            if (found.error.isNotEmpty()) log(context, LogLevel.WARNING, "Episode API warning: ${found.error}")
        }

        val chapters = mutableListOf<ChapterRef>()
        val seen = HashSet<String>()
        for ((offset, link) in links.withIndex()) {
            val chapterUrl = HttpSourceSupport.resolve(link, bookUrl) ?: continue
            val chapterId = RbooksSite.bookId(chapterUrl)
            if (chapterId.isEmpty() || !seen.add(chapterId)) continue
            val title = titleById[chapterId]?.takeIf { it.isNotBlank() } ?: "Episode ${offset + 1}"
            // The catalog does not say whether this account owns an episode; the viewer decides when it opens.
            chapters += ChapterRef(chapterId, chapters.size + 1, title, chapterUrl)
        }
        if (chapters.isEmpty()) {
            if (discoveryError.isNotEmpty()) {
                throw SourceException.Blocked("episode discovery failed ($discoveryError): complete rbooks' verification in the Browser tab and retry")
            }
            throw SourceException.Parse("no webnovel episodes or linked ebook volumes were found on this book page")
        }
        val book = BookInfo(
            source = SourceId.RBOOKS, id = bookId, url = bookUrl, title = meta.title.ifEmpty { "rbooks $bookId" },
            author = meta.author.ifEmpty { null }, synopsis = meta.synopsis.ifEmpty { null },
            coverUrl = meta.cover.ifEmpty { null }, tags = meta.tags, language = "ko", chapters = chapters,
        )
        log(context, LogLevel.INFO, "Book: ${book.title} by ${book.author ?: "?"} - ${chapters.size} chapters")
        return book
    }

    /**
     * The episode list from the book API: open the API as a top-level page (the only thing its bot check
     * lets through), then let the page script walk the series in slices.
     */
    private suspend fun discoverEpisodes(context: SourceContext, seriesId: String, rendered: List<String>, bookId: String): RbooksDiscovery {
        val api = openPage(context.web)
        try {
            var recoveries = 0
            var state: JsonElement = JsonNull
            var start = seriesId
            var reopen = true
            var result = RbooksDiscovery()
            for (call in 0 until timing.discoverCalls) {
                if (reopen) {
                    openApi(api, start, bookId, context)
                    reopen = false
                }
                val input = buildJsonObject {
                    put("seriesId", seriesId)
                    put("renderedLinks", JsonArray(rendered.map { JsonPrimitive(it) }))
                    put("budgetMs", timing.discoverBudgetMillis)
                    if (state !is JsonNull) put("state", state)
                }
                result = api.hard("rbooks.discover($input)")
                if (result.done) return result
                result.state?.let { state = it }
                progress(state)?.let { (found, total) -> log(context, LogLevel.INFO, "Episode list: $found of ${if (total > 0) total.toString() else "?"} read so far.") }
                if (!result.stalled) continue
                recoveries += 1
                if (recoveries > timing.apiRecoveries) {
                    // The API keeps failing: keep what was found.
                    val giveUp = buildJsonObject { put("state", state); put("giveUp", true) }
                    return api.hard<RbooksDiscovery>("rbooks.discover($giveUp)")
                }
                log(context, LogLevel.WARNING, "The episode API stopped answering; reopening it ($recoveries/${timing.apiRecoveries}).")
                start = ((state as? JsonObject)?.get("nextId") as? JsonPrimitive)?.content ?: seriesId
                reopen = true
                delay(timing.pollMillis * 3)
            }
            return result.copy(error = result.error.ifEmpty { "the episode list did not finish" })
        } finally {
            closeQuietly(api)
        }
    }

    /** (episodes found, episodes announced) from a discovery state, for the progress line. */
    private fun progress(state: JsonElement): Pair<Int, Int>? {
        val values = state as? JsonObject ?: return null
        val found = (values["ids"] as? JsonArray)?.size ?: return null
        return found to (((values["total"] as? JsonPrimitive)?.content)?.toIntOrNull() ?: 0)
    }

    private suspend fun openApi(api: WebPageDriving, id: String, bookId: String, context: SourceContext) {
        loadQuietly(api, "${RbooksSite.API_ORIGIN}/books/$id", context)
        var state = RbooksApiState()
        repeat(timing.apiPolls) {
            state = api.hard("rbooks.apiState()")
            if (state.json) return
            delay(timing.pollMillis)
        }
        if (state.challenge) throw verification(bookId, context)
        throw SourceException.Blocked("the rbooks book API did not return JSON (${state.title.ifEmpty { state.url }})")
    }

    private fun signInNeeded(bookId: String, context: SourceContext, why: String): SourceException {
        log(context, LogLevel.WARNING, "Sign-in needed: $why.")
        return if (promptOnce("login:$bookId")) {
            SourceException.LoginRequired(SourceId.RBOOKS)
        } else {
            SourceException.Blocked("rbooks still asks you to sign in or verify your age: do it in Settings > Enter App, then retry")
        }
    }

    private fun verification(bookId: String, context: SourceContext): SourceException {
        log(context, LogLevel.WARNING, "rbooks asked for a browser verification (Cloudflare).")
        return if (promptOnce("verify:$bookId")) {
            SourceException.LoginRequired(SourceId.RBOOKS)
        } else {
            SourceException.Blocked("rbooks still asks for a verification: open the book in the Browser tab, complete it, then retry")
        }
    }

    private suspend fun titleOf(page: WebPageDriving): String =
        runCatching { page.evaluateJson("return document.title;").trim('"') }.getOrDefault("")

    // Chapters

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val run = Run(context, book)
        try {
            return run.chapter(ref)
        } finally {
            run.close()
        }
    }

    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> {
        if (refs.isEmpty()) return emptyList()
        val run = Run(context, book)
        try {
            return parseConcurrently(refs, context) { ref -> run.chapter(ref) }
        } finally {
            run.close()
            sessions(context).snapshot(RbooksSite.session)
        }
    }

    /** The pages one download uses. */
    private inner class Run(private val context: SourceContext, private val book: BookInfo) {
        private val pool = WebPagePool(
            context.web, WebProfile.EXTERNAL,
            min(context.shared(SourceId.RBOOKS).threads, timing.maxPages),
        ) { page -> prepare(page) }
        private val libraryGate = Mutex()
        private var libraryPage: WebPageDriving? = null
        private var owned: Set<String>? = null
        private var ownedAsked = false

        suspend fun chapter(ref: ChapterRef): ChapterResult = pool.withPage { page -> read(page, ref) }

        suspend fun close() {
            pool.closeAll()
            libraryGate.withLock { libraryPage?.let { closeQuietly(it) }; libraryPage = null }
        }

        private suspend fun read(page: WebPageDriving, ref: ChapterRef): ChapterResult {
            val url = ref.url ?: return ChapterResult.Failed(ref, "missing chapter URL")
            try {
                page.load(url, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
            } catch (e: WebLoadTimeoutException) {
                // A timeout can come after the viewer itself committed. Go on only if this is the requested viewer.
                val current = page.currentUrl().orEmpty()
                if (!RbooksSite.isViewerUrl(current) || RbooksSite.bookId(current) != RbooksSite.bookId(url)) {
                    return ChapterResult.Failed(ref, "page load timed out")
                }
            }
            val current = page.currentUrl().orEmpty()
            if (current.isNotEmpty() && !RbooksSite.isViewerUrl(current)) {
                return if (RbooksSite.isAccountGate(current)) {
                    ChapterResult.Refused(ref, "signed out of rbooks: sign in in Settings > Enter App, then download again")
                } else {
                    ChapterResult.Refused(ref, "locked or not bought (rbooks redirected to ${HttpSourceSupport.host(current)})")
                }
            }

            var ready = false
            for (attempt in 1..timing.contentAttempts) {
                if (waitForContent(page)) {
                    ready = true
                    break
                }
                val wall = page.hard<RbooksWall>("rbooks.accessWall()")
                if (wall.wall && wall.refused) return refused(ref)
                if (wall.wall) return ChapterResult.Refused(ref, "locked or not bought (purchase or sign-in markers on the page)")
                log(context, LogLevel.WARNING, "Content validation failed for ${ref.title} (attempt $attempt/${timing.contentAttempts}).")
                if (attempt < timing.contentAttempts) {
                    try {
                        page.load(url, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
                    } catch (e: WebLoadTimeoutException) {
                        log(context, LogLevel.WARNING, "Reload warning for ${ref.title}: ${e.message}")
                    }
                }
            }
            if (!ready) return ChapterResult.Failed(ref, "the viewer did not finish drawing")

            val extract = page.hard<RbooksExtract?>(
                "rbooks.extract(${buildJsonObject { put("fallbackTitle", ref.title); put("chapterIndex", ref.index) }})",
            )
            if (extract == null || extract.content.isBlank()) {
                val wall = page.hard<RbooksWall>("rbooks.accessWall()")
                return if (wall.wall) ChapterResult.Refused(ref, "locked or not bought") else ChapterResult.Failed(ref, "no chapter content was extracted")
            }
            return buildResult(ref, extract)
        }

        /** The viewer's text must be long enough and keep its length: it is drawn in pieces. */
        private suspend fun waitForContent(page: WebPageDriving): Boolean {
            var last = -1
            var stable = 0
            repeat(timing.contentPolls) {
                val state = page.hard<RbooksContentState>("rbooks.contentState()")
                if (state.refused) return false
                if (state.ready && state.length >= 500) {
                    if (state.length == last) {
                        stable += 1
                        if (stable >= timing.stablePolls) return true
                    } else {
                        stable = 0
                        last = state.length
                    }
                } else {
                    stable = 0
                    last = -1
                }
                delay(timing.pollMillis)
            }
            return false
        }

        private suspend fun buildResult(ref: ChapterRef, extract: RbooksExtract): ChapterResult {
            val settings = context.settings
            val referer = book.url
            val images = if (settings.images.saveImageUrlsOnly) {
                extract.images.map { ImageRef(it.url, it.name) }
            } else {
                downloadImages(extract.images, referer, context.http, context.shared(SourceId.RBOOKS).imageWorkers)
            }
            // A series title is never a usable episode title: keep the catalog's.
            val shown = extract.title.takeIf {
                it.isNotBlank() && ReaderBlocks.normalizeTitle(it) != ReaderBlocks.normalizeTitle(book.title)
            } ?: ref.title
            val named = ref.copy(title = shown)
            return ChapterResult.Content(ChapterContent(named, "<div class=\"rbooks-content\">${extract.content}</div>", images))
        }

        /** The viewer refused to open the title: the library says whether the account owns it. */
        private suspend fun refused(ref: ChapterRef): ChapterResult {
            val owned = ownedIds()
            val signedIn = sessions(context).status(RbooksSite.session).usable
            return when {
                owned == null && !signedIn -> ChapterResult.Refused(
                    ref, "the rbooks library could not confirm a session: sign in in Settings > Enter App, then download again",
                )
                owned == null -> ChapterResult.Failed(ref, "the rbooks library could not verify access to this volume; retry")
                ref.id !in owned -> ChapterResult.Refused(ref, "not in this account's library: buy or rent it on rbooks first")
                else -> ChapterResult.Refused(
                    ref,
                    "owned, but rbooks opens it only in its own apps or PC viewer (the web viewer says it is unsupported); it cannot be exported on a phone",
                )
            }
        }

        /** Volumes of this book the signed-in account owns, asked from the library page; null when it could not be asked. */
        private suspend fun ownedIds(): Set<String>? = libraryGate.withLock {
            if (ownedAsked) return@withLock owned
            ownedAsked = true
            val ids = (book.chapters.map { it.id } + book.id).distinct().take(500)
            try {
                val page = libraryPage ?: openPage(context.web).also { libraryPage = it }
                loadQuietly(page, "${RbooksSite.LIBRARY_ORIGIN}/", context)
                val reply = page.hard<RbooksOwned>("rbooks.ownedIds(${HardScripts.literal(ids.joinToString(","))}.split(','))")
                owned = reply.owned?.toSet()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                log(context, LogLevel.WARNING, "Library ownership lookup warning: ${e.message}")
            }
            owned
        }
    }

    private suspend fun downloadImages(
        images: List<RbooksImage>,
        referer: String,
        http: HttpFetching,
        workers: Int,
    ): List<ImageRef> = coroutineScope {
        val permits = Semaphore(workers.coerceIn(1, 8))
        images.map { image ->
            async {
                permits.withPermit {
                    ImageRef(image.url, image.name, fetchImage(image.url, referer, http))
                }
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
                mapOf("User-Agent" to RbooksSite.USER_AGENT, "Referer" to referer, "Accept" to "image/avif,image/webp,image/apng,image/*,*/*;q=0.8"),
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

    /** A page in the desktop browser's shape, with the shared script at document start. */
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
        page.emulateDesktop(RbooksSite.USER_AGENT, RbooksSite.VIEWPORT_WIDTH, RbooksSite.VIEWPORT_HEIGHT)
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
