package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import kotlinx.coroutines.CancellationException

/** Opens a book with one script engine (the novel-downloader rules or WebToEpub); a seam for tests. */
internal fun interface ScriptEngineOpener {
    suspend fun open(engine: SiteEngine, url: String, context: SourceContext, page: SharedBookPage): BookInfo
}

/** The real script engines: `ReaderSessions` keeps the opened book so its chapters can be read later. */
internal object ReaderScriptEngines : ScriptEngineOpener {
    override suspend fun open(engine: SiteEngine, url: String, context: SourceContext, page: SharedBookPage): BookInfo =
        ReaderSessions.open(url, context, engine, page).book
}

/**
 * Reads one book page with the site engines in `steps`, in order, until one returns chapters (the
 * desktop's `parse_book`). An engine that throws or finds no chapters hands over to the next.
 *
 * - `native` is the built-in scraper that owns the URL, if any. A `LoginRequired` from it stops the
 *   fallback and reaches the pipeline, which opens the sign-in browser and starts over.
 * - The script engines share one web view page, loaded the first time one needs it.
 * - When nothing returns chapters, a built-in scraper's empty book is returned if there is one;
 *   otherwise the most telling failure is thrown (an engine saying it has no rule for the site
 *   ranks below any other error).
 * - Chapters are read by whichever engine read the book (`owner`).
 */
internal class SiteEngineChain(
    private val native: SourceAdapter?,
    internal val steps: List<SiteEngine>,
    private val scripts: ScriptEngineOpener = ReaderScriptEngines,
) : SourceAdapter {
    private val reader = ReaderAdapter()

    override val id: SourceId get() = native?.id ?: SourceId.READER
    override val tier: SourceTier get() = native?.tier ?: SourceTier.HTTP

    override fun matches(url: String): Boolean = native?.matches(url) ?: reader.matches(url)

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val page = SharedBookPage(url, context)
        var empty: BookInfo? = null
        val failures = ArrayList<Exception>()
        try {
            for ((position, engine) in steps.withIndex()) {
                val book: BookInfo? = try {
                    if (engine == SiteEngine.NATIVE) {
                        native?.parseBook(url, context) ?: continue
                    } else {
                        scripts.open(engine, url, context, page)
                    }
                } catch (e: CancellationException) {
                    throw e
                } catch (e: SourceException.LoginRequired) {
                    if (engine == SiteEngine.NATIVE) throw e
                    failures += e
                    null
                } catch (e: Exception) {
                    failures += e
                    context.logger.log(LogLevel.INFO, "${engine.label}: ${e.message ?: e}")
                    null
                }
                if (book != null && book.chapters.isNotEmpty()) return book
                if (book != null && empty == null) empty = book
                steps.drop(position + 1).firstOrNull { it != SiteEngine.NATIVE || native != null }?.let { next ->
                    context.logger.log(LogLevel.INFO, "${engine.label} found no chapters; trying ${next.label} next.")
                }
            }
        } finally {
            page.release()
        }
        empty?.let { return it }
        // Every engine said it has nothing for this site: the link is simply not supported.
        if (failures.isNotEmpty() && failures.all(::isMissingRule)) throw SourceException.UnsupportedUrl(url)
        val telling = failures.firstOrNull { !isMissingRule(it) } ?: failures.firstOrNull()
        throw telling?.let { ReaderBookSession.sourceError(it) }
            ?: SourceException.Parse("No site engine found chapters on $url.")
    }

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        owner(book).parseChapter(ref, book, context)

    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> =
        owner(book).parseChapters(refs, book, context)

    /** The built-in scraper for a book it read; a script engine's books come from the reader adapter. */
    override fun owner(book: BookInfo): SourceAdapter =
        if (native != null && book.source == native.id) native else reader

    private fun isMissingRule(error: Exception): Boolean =
        error is EngineMissException || error.message.orEmpty().contains("Not Found Rule", ignoreCase = true)
}
