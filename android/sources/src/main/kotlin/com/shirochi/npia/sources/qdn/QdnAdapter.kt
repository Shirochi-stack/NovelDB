package com.shirochi.npia.sources.qdn

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.JobLogging
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
import com.shirochi.npia.sources.hard.hardTask
import com.shirochi.npia.sources.http.ReaderBlock
import com.shirochi.npia.sources.http.ReaderBlocks
import com.shirochi.npia.sources.session.SiteSessions
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.min

/** Waits and limits of the qdn scraper. The defaults are the desktop's; tests shrink them. */
internal data class QdnTiming(
    /** Spacing between chapter page loads across all workers: the firewall blocks bursts. */
    val minGapMillis: Long = 2_500,
    /** Pauses after a firewall block, one per retry; when they run out the chapter fails. */
    val firewallCooldownSeconds: List<Long> = listOf(60, 180, 420, 900),
    val loadTimeoutSeconds: Long = 45,
    val bookPolls: Int = 45,
    val chapterPolls: Int = 60,
    val pollMillis: Long = 1_000,
    /** The reader decrypts a bought chapter in the page, which takes 10-60 s. */
    val decryptPolls: Int = 500,
    val decryptPollMillis: Long = 300,
    val extractPolls: Int = 240,
    val extractPollMillis: Long = 500,
    val decodePolls: Int = 600,
    val decodePollMillis: Long = 500,
    val referenceSlices: Int = 200,
    val referenceSliceMillis: Long = 8_000,
    /** Pages open at once, whatever `threads` says: each is a full desktop reader. */
    val maxPages: Int = 3,
)

/** Spaces page loads at least `minGapMillis` apart, however many workers ask. */
internal class QdnPacer(private val minGapMillis: Long, private val clock: () -> Long = { System.nanoTime() / 1_000_000 }) {
    private val gate = Mutex()
    private var last = Long.MIN_VALUE / 2

    suspend fun pace() = gate.withLock {
        val wait = last + minGapMillis - clock()
        if (wait > 0) delay(wait)
        last = clock()
    }
}

/**
 * qdn: the chapter list and free chapters from the desktop reader page, and bought chapters with the
 * user's browser session. The reader encrypts a bought chapter and the site's own script decrypts it
 * in a real rendering engine (a web view is one); the text is then drawn with scrambled fonts, which
 * `hard-sources.js` reads off the screen and matches against a reference font. Chapters the account has
 * not bought are refused; nothing is ever purchased.
 *
 * Port of `_qdn_*` and `qdn_font_decoder.py` in the desktop scraper. The Kotlin side navigates, keeps
 * the session, paces loads and builds results; the page logic is shared with iOS.
 */
class QdnAdapter internal constructor(
    private val timing: QdnTiming,
    private val cookies: () -> SharedCookieStore?,
) : SourceAdapter {
    constructor() : this(QdnTiming(), { SharedCookies.store })

    override val id = SourceId.QDN
    override val tier = SourceTier.HEAVY_BROWSER

    private val pacer = QdnPacer(timing.minGapMillis)

    /** Sign-in and verification prompts already shown for a book; the second try goes on without asking. */
    private val prompted: MutableSet<String> = ConcurrentHashMap.newKeySet()

    override fun matches(url: String): Boolean = QdnSite.matches(url)

    override fun canonicalUrl(url: String): String = if (matches(url)) QdnSite.canonicalUrl(url) else url

    /** The desktop, book and phone hosts are all subdomains of one domain. */
    override val sites: Set<String> = setOf(QdnSite.DOMAIN)

    private fun sessions(context: SourceContext) = SiteSessions(context.secrets, cookies)

    private fun log(context: SourceContext, level: LogLevel, message: String) = context.logger.log(level, "[qdn] $message")

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val bookId = QdnSite.bookId(url) ?: throw SourceException.UnsupportedUrl(url)
        val sessions = sessions(context)
        val restored = sessions.restoreOnce(QdnSite.session)
        if (restored > 0) log(context, LogLevel.INFO, "Restored $restored saved login cookie(s).")

        val page = openPage(context.web)
        try {
            val state = loadBook(page, QdnSite.bookUrl(bookId), context.logger)
            when (state.state) {
                "verification" -> throw verification(bookId, context)
                "blocked" -> throw SourceException.Blocked("qdn's firewall blocked the request; wait a while and try again")
                "ready" -> log(context, LogLevel.INFO, "Reading as a desktop browser (${state.viewport.width} x ${state.viewport.height} px).")
                else -> throw SourceException.Parse(
                    "the book page did not render (${state.title.ifEmpty { state.url }}: ${state.text.take(120)})",
                )
            }

            var status = sessions.status(QdnSite.session)
            if (status.present.isNotEmpty()) {
                // A remembered login can have expired; the site renews it from its remember-me cookie
                // only when these are absent, so expired copies must go.
                val live = runCatching { page.hard<QdnUserInfo>("qdn.userInfo()") }.getOrNull()?.live
                if (live == false) {
                    for (name in QdnSite.LOGIN_COOKIES) cookies()?.remove(QdnSite.ORIGIN + "/", name)
                    log(context, LogLevel.WARNING, "The saved login has expired. Sign in again in Settings > Enter App if VIP chapters stay locked.")
                    status = sessions.status(QdnSite.session)
                }
            }

            val info = page.hard<QdnBookPage>("qdn.bookInfo()")
            if (info.title.isEmpty()) throw SourceException.Parse("could not extract the book title")
            if (info.chapters.isEmpty()) throw SourceException.Parse("no chapters found")
            var entries = info.chapters
            if (info.totalChapters > entries.size && info.catalogUrl.isNotEmpty()) {
                // A long book's page lists only its first chapters; the catalog page lists them all.
                val all = fullCatalog(page, info.catalogUrl, bookId, context)
                if (all != null && all.size > entries.size) {
                    log(context, LogLevel.INFO, "Read the full catalog: ${all.size} chapters (the book page listed ${entries.size}).")
                    entries = all
                } else {
                    log(context, LogLevel.WARNING, "The book has ${info.totalChapters} chapters but only ${entries.size} could be listed.")
                }
            }
            val chapters = entries.mapIndexed { offset, entry ->
                ChapterRef(
                    id = QdnSite.chapterId(entry.url) ?: (offset + 1).toString(),
                    index = offset + 1,
                    title = entry.name,
                    url = entry.url,
                    isPaid = entry.isVip,
                )
            }
            val book = BookInfo(
                source = SourceId.QDN, id = bookId, url = QdnSite.bookUrl(bookId), title = info.title,
                author = info.author.ifEmpty { null }, synopsis = info.description.ifEmpty { null },
                coverUrl = info.coverUrl.ifEmpty { null }, tags = info.tags, language = "zh", chapters = chapters,
            )
            log(context, LogLevel.INFO, "Book: ${book.title} by ${book.author ?: "?"} - ${chapters.size} chapters")

            if (status.usable) {
                prompted.remove("session:$bookId")
                sessions.snapshot(QdnSite.session)
            } else if (wantsBoughtChapters(chapters, context)) {
                if (promptOnce("session:$bookId")) {
                    // Bought chapters need the user's login: let the app open its sign-in browser, then start over.
                    log(context, LogLevel.INFO, "Not signed in and VIP chapters are selected; asking for a sign-in.")
                    throw SourceException.LoginRequired(SourceId.QDN)
                }
                log(context, LogLevel.WARNING, "Not signed in: VIP chapters will be skipped as previews.")
            }
            return book
        } finally {
            closeQuietly(page)
        }
    }

    /** Every chapter from the catalog page, or null when it could not be read. */
    private suspend fun fullCatalog(page: WebPageDriving, catalogUrl: String, bookId: String, context: SourceContext): List<QdnCatalogEntry>? {
        try {
            page.load(catalogUrl, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
        } catch (e: WebLoadTimeoutException) {
            log(context, LogLevel.WARNING, "Catalog load warning: ${e.message}")
        }
        for (poll in 0 until timing.bookPolls) {
            val state = try {
                page.hard<QdnCatalogState>("qdn.catalogState()")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                QdnCatalogState()
            }
            when (state.state) {
                "ready" -> return page.hard<QdnCatalog>("qdn.catalogInfo()").chapters
                "verification" -> throw verification(bookId, context)
                "blocked" -> throw SourceException.Blocked("qdn's firewall blocked the request; wait a while and try again")
            }
            delay(timing.pollMillis)
        }
        return null
    }

    private fun wantsBoughtChapters(chapters: List<ChapterRef>, context: SourceContext): Boolean {
        val shared = context.shared(SourceId.QDN)
        val from = shared.rangeFrom ?: Int.MIN_VALUE
        val to = shared.rangeTo ?: Int.MAX_VALUE
        return !shared.skipPaid && chapters.any { it.isPaid && it.index in from..to }
    }

    /** True the first time for `key`, false the second (and clears it, so a later job asks again). */
    private fun promptOnce(key: String): Boolean = if (prompted.remove(key)) false else prompted.add(key)

    private fun verification(bookId: String, context: SourceContext): SourceException {
        log(context, LogLevel.WARNING, "qdn asked for a verification (captcha).")
        // The sign-in browser shows the book page, where the user can complete the check.
        return if (promptOnce("verify:$bookId")) {
            SourceException.LoginRequired(SourceId.QDN)
        } else {
            SourceException.Blocked("qdn still asks for a verification: open qdn in the Browser tab, complete it, then try again")
        }
    }

    private suspend fun loadBook(page: WebPageDriving, bookUrl: String, logger: JobLogging): QdnPageState {
        try {
            page.load(bookUrl, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
        } catch (e: WebLoadTimeoutException) {
            logger.log(LogLevel.WARNING, "[qdn] Page load warning: ${e.message}")
        }
        var state = QdnPageState()
        repeat(timing.bookPolls) {
            state = try {
                page.hard("qdn.pageState()")
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                QdnPageState()
            }
            if (state.state != "waiting") return state
            delay(timing.pollMillis)
        }
        return state
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
            sessions(context).snapshot(QdnSite.session)
        }
    }

    /** The pages and the decoder one download uses. */
    private inner class Run(private val context: SourceContext) {
        private val pool = WebPagePool(
            context.web, WebProfile.EXTERNAL,
            min(context.shared(SourceId.QDN).threads, timing.maxPages),
        ) { page -> prepare(page) }
        val decoder = QdnDecoder(context.web, context.logger, timing)

        suspend fun chapter(ref: ChapterRef): ChapterResult = pool.withPage { page -> read(page, ref) }

        suspend fun close() {
            pool.closeAll()
            decoder.close()
        }

        private suspend fun read(page: WebPageDriving, ref: ChapterRef): ChapterResult {
            val url = ref.url ?: return ChapterResult.Failed(ref, "missing chapter URL")
            val loaded = loadChapter(page, ref, url) ?: return ChapterResult.Failed(
                ref, "blocked by qdn's firewall; wait a while, then download the rest",
            )
            if (!loaded.ready && waitReady(page) == null) return ChapterResult.Failed(ref, "timed out waiting for the chapter")
            val encryption = page.hard<QdnEncryptionState>("qdn.encryptionState()")
            if (encryption.encrypted) return decodeEncrypted(page, ref, encryption.words)
            return fromRead(ref, page.hard("qdn.readChapter()"))
        }

        /** Loads the chapter, waiting out the firewall. Null when the cooldowns are used up. */
        private suspend fun loadChapter(page: WebPageDriving, ref: ChapterRef, url: String): QdnChapterState? {
            var attempt = 0
            while (true) {
                pacer.pace()
                try {
                    page.load(url, WebLoadState.DOM_CONTENT_LOADED, timing.loadTimeoutSeconds)
                } catch (e: WebLoadTimeoutException) {
                    log(context, LogLevel.WARNING, "Page load warning: ${e.message}")
                }
                val state = page.hard<QdnChapterState>("qdn.chapterState()")
                if (!state.blocked) return state
                val pause = timing.firewallCooldownSeconds.getOrNull(attempt)
                if (pause == null) {
                    log(context, LogLevel.WARNING, "Still blocked by qdn's firewall; giving up on ${ref.title}.")
                    return null
                }
                log(context, LogLevel.WARNING, "qdn's firewall is blocking requests. Pausing ${pause / 60} min before retrying ${ref.title} (${attempt + 1}/${timing.firewallCooldownSeconds.size}).")
                delay(pause * 1_000)
                attempt += 1
            }
        }

        private suspend fun waitReady(page: WebPageDriving): QdnChapterState? {
            repeat(timing.chapterPolls) {
                val state = page.hard<QdnChapterState>("qdn.chapterState()")
                if (state.ready) return state
                delay(timing.pollMillis)
            }
            return null
        }

        private fun fromRead(ref: ChapterRef, read: QdnRead): ChapterResult = when (read.error) {
            null ->
                if (read.lines.isEmpty()) {
                    ChapterResult.Failed(ref, "empty content")
                } else {
                    ChapterResult.Content(ReaderBlocks.content(read.lines.map { ReaderBlock.Text(it) }, ref, "qdn-content"))
                }
            "locked" -> {
                val detail = when (read.reason) {
                    "login" -> "preview only: the app is not signed in to qdn (Settings > Enter App)"
                    "purchase" -> "preview only: this account has not bought the chapter"
                    else -> "locked, or sign-in required"
                }
                val counts = if (read.expected > 0) " (${read.chars}/${read.expected} characters)" else ""
                ChapterResult.Refused(ref, detail + counts)
            }
            "verification" -> ChapterResult.Failed(ref, "qdn asks for a verification: open qdn in the Browser tab, complete it, then retry")
            else -> ChapterResult.Failed(ref, read.error)
        }

        // A bought chapter: wait for the reader to decrypt it, read what is painted, decode the glyphs.
        private suspend fun decodeEncrypted(page: WebPageDriving, ref: ChapterRef, words: Int): ChapterResult = coroutineScope {
            // The reference takes a few seconds to build; do it while the reader decrypts.
            val warm = async {
                try {
                    decoder.prepare()
                    null
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    e
                }
            }
            var state = QdnEncryptionState()
            for (poll in 0 until timing.decryptPolls) {
                state = page.hard("qdn.encryptionState()")
                if (state.rendered || state.failed || state.blocked) break
                delay(timing.decryptPollMillis)
            }
            val failure = warm.await()
            when {
                state.blocked -> return@coroutineScope ChapterResult.Failed(ref, "blocked by qdn's firewall")
                state.failed -> return@coroutineScope ChapterResult.Failed(
                    ref, "the reader could not decrypt this chapter (章节加载失败); retry, or open it once in the Browser tab",
                )
                !state.rendered -> return@coroutineScope ChapterResult.Failed(ref, "timed out decrypting the chapter")
                failure != null -> return@coroutineScope ChapterResult.Failed(ref, "could not prepare the glyph decoder: ${failure.message}")
            }

            var decoded: QdnDecoded
            try {
                decoded = extractAndDecode(page)
                if (decoded.issues > 0) {
                    log(context, LogLevel.INFO, "Capture unstable or fonts missing; retrying once.")
                    val retry = runCatching { extractAndDecode(page) }.getOrNull()
                    if (retry != null && quality(retry) >= quality(decoded)) decoded = retry
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                return@coroutineScope ChapterResult.Failed(ref, "could not decode the chapter: ${e.message}")
            }
            val lines = decoded.lines
            if (lines.isEmpty()) return@coroutineScope ChapterResult.Failed(ref, "no decoded content")
            val stats = decoded.stats
            val textLength = lines.sumOf { it.length } + lines.size - 1
            val warnings = listOf(
                "uncertain_glyphs" to stats.lowConfidence,
                "suspicious_passthrough" to stats.suspiciousPassthrough,
                "missing_fonts" to stats.missingFonts.size,
                "unstable_paragraphs" to stats.unstableParagraphs.size,
                "incomplete" to if (words >= 500 && textLength < words * 0.8) 1 else 0,
            ).filter { it.second > 0 }
            if (warnings.isNotEmpty()) {
                log(context, LogLevel.WARNING, "Saving best available text for ${ref.title}: " + warnings.joinToString(", ") { "${it.first}=${it.second}" })
            }
            log(context, LogLevel.INFO, "Decoded font-encrypted chapter: ${lines.size} paragraphs, ${stats.decoded} glyphs (${stats.verifiedGlyphs} verified, ${stats.shapeMatchedGlyphs} shape matched" + (if (stats.lowConfidence > 0) ", ${stats.lowConfidence} uncertain" else "") + ").")
            ChapterResult.Content(ReaderBlocks.content(lines.map { ReaderBlock.Text(it) }, ref, "qdn-content"))
        }

        /** Higher is better: something decoded, then fewer capture problems, then more text. */
        private fun quality(decoded: QdnDecoded): Long =
            (if (decoded.lines.isNotEmpty()) 1_000_000_000L else 0L) - decoded.issues * 1_000_000L + decoded.lines.sumOf { it.length }

        private suspend fun extractAndDecode(page: WebPageDriving): QdnDecoded {
            val known = decoder.knownFonts().joinToString(",") { HardScripts.literal(it) }
            val payload = page.hardTask(
                "qdn.extract", "[{\"known\":[$known]}]",
                pollMillis = timing.extractPollMillis, maxPolls = timing.extractPolls,
            )
            return decoder.decode(payload)
        }
    }

    /** A page in the desktop reader's shape: desktop user agent and viewport, with the shared script at document start. */
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
        page.emulateDesktop(QdnSite.USER_AGENT, QdnSite.VIEWPORT_WIDTH, QdnSite.VIEWPORT_HEIGHT)
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
