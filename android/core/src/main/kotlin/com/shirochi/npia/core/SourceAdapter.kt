package com.shirochi.npia.core

import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.net.URI

/** Everything an adapter may use while scraping one job. */
class SourceContext(
    val http: HttpFetching,
    val web: WebEngineProviding,
    val secrets: SecretStore,
    val settings: AppSettings,
    val logger: JobLogging = NullLogger,
    /**
     * Called once per chapter as soon as its result is known, from any thread. Adapters that
     * override `parseChapters` must call it too, or progress jumps from 0 to done at the end.
     */
    val onChapter: (ChapterResult) -> Unit = {},
) {
    fun shared(source: SourceId): SharedSettings = settings.effective(source)
}

/**
 * One source. Mirrors `_<source>_parse_book` / `_parse_chapter` / `_parse_chapter_batch` in the
 * desktop `external_scraper.py` (and `downloader_core.py` for npia), and `SourceAdapter` in the
 * iOS `NpiaKit`.
 */
interface SourceAdapter {
    val id: SourceId
    val tier: SourceTier

    fun matches(url: String): Boolean

    /**
     * Hosts of the sites this adapter downloads from, as a person browses them (no API or image
     * hosts), lower case. A host covers its subdomains. Empty for adapters that take any page the
     * script engines list (`reader`) and for sources that are not ported yet.
     */
    val sites: Set<String> get() = emptySet()

    /** True when `host` (lower case, no port) belongs to one of this adapter's sites. */
    fun ownsSite(host: String): Boolean = sites.any { host == it || host.endsWith(".$it") }

    suspend fun parseBook(url: String, context: SourceContext): BookInfo

    suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult

    /**
     * The adapter that read `book`. An adapter that hands a URL over to other site engines when its
     * own parsing fails (the engine chain) names the one that succeeded, so the pipeline applies
     * that source's settings; every other adapter is its own owner.
     */
    fun owner(book: BookInfo): SourceAdapter = this

    /**
     * Defaults to `parseChapter` with `threads` concurrent workers, each pausing for the rate
     * limit after every chapter like the desktop scraper; override for batch APIs.
     */
    suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> =
        parseConcurrently(refs, context) { ref -> parseChapter(ref, book, context) }
}

/**
 * Runs `read` over `refs` with `threads` workers. Each worker sleeps one rate-limit delay after a
 * chapter (except the last), reports it through `onChapter`, and failures become `Failed`.
 */
suspend fun SourceAdapter.parseConcurrently(
    refs: List<ChapterRef>,
    context: SourceContext,
    read: suspend (ChapterRef) -> ChapterResult,
): List<ChapterResult> = coroutineScope {
    val shared = context.shared(id)
    val permits = Semaphore(shared.threads.coerceAtLeast(1))
    val last = refs.lastOrNull()
    refs.map { ref ->
        async {
            permits.withPermit {
                val result = try {
                    read(ref)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    ChapterResult.Failed(ref, e.message ?: e.toString())
                }
                context.onChapter(result)
                if (ref !== last && result !is ChapterResult.Refused) shared.pauseBetweenChapters()
                result
            }
        }
    }.awaitAll()
}

/** Lower-cased host without a leading `www.`, or "" when the URL has none. */
fun bareHost(url: String): String {
    val host = runCatching { URI(url.trim()).host }.getOrNull()?.lowercase() ?: return ""
    return host.removePrefix("www.")
}
