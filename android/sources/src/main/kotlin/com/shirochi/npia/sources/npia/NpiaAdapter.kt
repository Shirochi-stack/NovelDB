package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.HttpStatusException
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.coroutines.ensureActive
import kotlin.coroutines.coroutineContext
import kotlin.math.min
import kotlin.math.pow

/**
 * npia over plain HTTP, ported from `downloader_core.py` (`fetch_metadata`, `fetch_chapter_list`,
 * `download_chapter_content`, `fetch_notice_ids`) and `npia_auth.py`, following the iOS adapter.
 *
 * The `LOGINKEY` session cookie is sent as a `Cookie` header on every request, so it works with
 * any `HttpFetching`; `cookies` additionally hands it to a shared jar when the app provides one.
 *
 * TODO(Phase 5): `settings.npia.useBrowserFallback` (the desktop viewer-page fallback for chapters
 * the HTTP payload cannot deliver) needs the web engine and device testing.
 */
class NpiaAdapter(
    private val cookies: NpiaCookieSetting = NpiaCookieSetting { },
    /** Seconds to wait before retry `attempt` (1-based); tests pass zero. */
    private val retryDelay: (Int) -> Double = { min(8.0, 2.0.pow(it - 1)) },
) : SourceAdapter {
    override val id = SourceId.NPIA
    override val tier = SourceTier.HTTP
    override val sites: Set<String> = setOf(NpiaParser.HOST)

    /** Key from the last `parseBook`; chapters fall back to the saved key when it is unset. */
    @Volatile private var sessionKey: String? = null

    override fun matches(url: String): Boolean = NpiaParser.novelId(url) != null

    private fun loginKey(context: SourceContext): String? =
        sessionKey ?: context.secrets.get(SecretKey.NPIA_LOGIN_KEY)?.takeIf { it.isNotEmpty() }

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val novelId = NpiaParser.novelId(url) ?: throw SourceException.UnsupportedUrl(url)
        val key = NpiaAuth.prepare(context, cookies)
        sessionKey = key

        val pageUrl = NpiaParser.novelUrl(novelId)
        val page = context.http.send(HttpRequest(pageUrl, headers = NpiaAuth.headers(key)))
        if (page.status != 200) throw HttpStatusException(page.status, pageUrl)
        if (page.text.isBlank()) throw SourceException.Parse("empty novel page")
        val meta = NpiaParser.parseMetadata(page.text, novelId)
        context.logger.log(LogLevel.INFO, "Metadata acquired: ${meta.title} by ${meta.author}")

        val notices = if (context.settings.npia.includeNotices) {
            NpiaParser.parseNotices(page.text).mapIndexed { offset, notice ->
                ChapterRef(notice.id, offset + 1, notice.title, NpiaParser.viewerUrl(notice.id), isNotice = true)
            }
        } else {
            emptyList()
        }

        val chapters = fetchChapterList(novelId, key, context)
        if (chapters.isEmpty()) throw SourceException.Parse("no chapters found")

        return BookInfo(
            source = SourceId.NPIA, id = novelId, url = pageUrl, title = meta.title, author = meta.author,
            synopsis = meta.synopsis.ifEmpty { null }, coverUrl = meta.coverUrl,
            tags = meta.tags, status = meta.status, language = "ko", chapters = chapters, notices = notices,
        )
    }

    private suspend fun fetchChapterList(novelId: String, key: String, context: SourceContext): List<ChapterRef> {
        val url = "https://${NpiaParser.HOST}/proc/episode_list"
        val headers = NpiaAuth.headers(key, referer = NpiaParser.novelUrl(novelId), form = true)
        val chapters = mutableListOf<ChapterRef>()
        val seen = mutableSetOf<String>()
        var page = 0
        // The desktop loop runs until a page adds nothing; the cap only guards against a server
        // that keeps returning new ids forever.
        while (page < 5000) {
            coroutineContext.ensureActive()
            val body = NpiaAuth.formBody(listOf("novel_no" to novelId, "sort" to "DOWN", "page" to page.toString()))
            val response = context.http.send(HttpRequest(url, "POST", headers, body))
            if (response.status != 200) {
                throw SourceException.Parse("chapter list page $page returned HTTP ${response.status}; stopping to avoid an incomplete book")
            }
            if (response.text.contains("Authentication required")) {
                if (chapters.isEmpty()) throw SourceException.LoginRequired(SourceId.NPIA)
                context.logger.log(LogLevel.WARNING, "Authentication required during chapter scan; keeping ${chapters.size} chapters.")
                break
            }
            val rows = NpiaParser.parseEpisodePage(response.text)
            if (rows.isEmpty()) break
            var added = 0
            for (row in rows) {
                if (!seen.add(row.id)) continue
                chapters.add(ChapterRef(row.id, chapters.size + 1, row.title, NpiaParser.viewerUrl(row.id), isPaid = row.isPaid))
                added++
            }
            if (added == 0 && page > 0) break
            page++
        }
        context.logger.log(LogLevel.INFO, "Found ${chapters.size} chapters.")
        return chapters
    }

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val url = "https://${NpiaParser.HOST}/proc/viewer_data/${ref.id}"
        val headers = NpiaAuth.headers(loginKey(context), referer = NpiaParser.viewerUrl(ref.id), form = true)
        val attempts = context.shared(SourceId.NPIA).maxRetries.coerceIn(1, 5)
        var lastProblem = ""

        for (attempt in 1..attempts) {
            coroutineContext.ensureActive()
            if (attempt > 1) {
                val wait = retryDelay(attempt - 1)
                if (wait > 0) delay((wait * 1000).toLong())
            }
            val response: HttpResponse = try {
                context.http.send(HttpRequest(url, "POST", headers, ByteArray(0)))
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                lastProblem = e.message ?: e.toString()
                continue
            }
            if (response.status == 429) return ChapterResult.Failed(ref, "HTTP 429 rate limit")
            if (response.status != 200) {
                lastProblem = "HTTP ${response.status}"
                continue
            }
            val text = response.text
            if (text.isBlank()) {
                // The viewer answers unpurchased PLUS chapters with an empty body.
                if (ref.isPaid) return ChapterResult.Refused(ref, "not purchased or no PLUS subscription")
                lastProblem = "empty response body"
                continue
            }
            when (val payload = NpiaParser.validatePayload(text)) {
                is NpiaParser.Payload.Blocked -> return ChapterResult.Refused(ref, payload.reason)
                is NpiaParser.Payload.Invalid -> {
                    if (ref.isPaid && attempt == attempts) return ChapterResult.Refused(ref, payload.reason)
                    lastProblem = payload.reason
                }
                is NpiaParser.Payload.Segments -> {
                    val options = NpiaParser.RenderOptions(
                        stripLeadingSpaces = context.settings.text.stripLeadingSpaces,
                        removeNewlines = context.settings.text.removeNewlines,
                        saveImageUrlsOnly = context.settings.images.saveImageUrlsOnly,
                        imagePrefix = (if (ref.isNotice) "n" else "") + ref.index.toString().padStart(4, '0'),
                        fontMapping = NpiaFontMapping.mapping,
                    )
                    val rendered = NpiaParser.renderSegments(payload.segments, options)
                    return ChapterResult.Content(ChapterContent(ref, rendered.html, rendered.images))
                }
            }
        }
        return ChapterResult.Failed(ref, "failed after $attempts attempts ($lastProblem)")
    }
}
