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

    suspend fun parseBook(url: String, context: SourceContext): BookInfo

    suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult

    /** Defaults to `parseChapter` with `threads` concurrent calls; override for batch APIs. */
    suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> =
        coroutineScope {
            val permits = Semaphore(context.shared(id).threads.coerceAtLeast(1))
            refs.map { ref ->
                async {
                    permits.withPermit {
                        try {
                            parseChapter(ref, book, context)
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            ChapterResult.Failed(ref, e.message ?: e.toString())
                        }
                    }
                }
            }.awaitAll()
        }
}

/** Lower-cased host without a leading `www.`, or "" when the URL has none. */
fun bareHost(url: String): String {
    val host = runCatching { URI(url.trim()).host }.getOrNull()?.lowercase() ?: return ""
    return host.removePrefix("www.")
}
