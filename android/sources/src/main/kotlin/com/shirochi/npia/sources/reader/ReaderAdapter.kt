package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull
import java.io.IOException
import java.net.URI
import java.net.URL
import java.text.Normalizer
import java.util.Locale

/**
 * Generic sites driven by the desktop's `rules-lib.js` (novel-downloader rules).
 *
 * The rules run in QuickJS (`ReaderRuntime`) against a small DOM built from the fetched page,
 * the way `external_scraper.py` runs them in a browser page through `bridge.js`. A book whose
 * page the rules cannot read without the site's own scripts falls back to a web view
 * (`ReaderWebFallback`).
 */
class ReaderAdapter : SourceAdapter {
    override val id = SourceId.READER
    override val tier = SourceTier.HTTP

    /** Asks the rules bundle whether a rule exists for the URL's host. */
    override fun matches(url: String): Boolean = ReaderRules.supports(url)

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo =
        ReaderSessions.open(url, context).book

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        ReaderSessions.session(book, context).chapter(ref)
}

// Rule lookup

/**
 * A shared runtime that only answers "is there a rule for this host?" and converts HTML to
 * blocks. It never touches the network.
 */
internal object ReaderRules {
    private var runtime: ReaderRuntime? = null
    private var unavailable = false
    private val hosts = HashMap<String, Boolean>()

    @Synchronized
    fun supports(url: String): Boolean {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return false
        val scheme = uri.scheme?.lowercase()
        if (scheme != "http" && scheme != "https") return false
        val host = uri.host?.lowercase()?.takeIf { it.isNotEmpty() } ?: return false
        hosts[host]?.let { return it }
        val runtime = loadedRuntime() ?: return false
        val supported = runCatching { runtime.callSync("hasRule", listOf(url.trim())) }.getOrNull() == "true"
        hosts[host] = supported
        return supported
    }

    /** Splits chapter HTML into text and image blocks (`_reader_html_blocks`). */
    suspend fun blocks(html: String, baseUrl: String, attachments: Map<String, String> = emptyMap()): List<RuleBlock> {
        val runtime = synchronized(this) { loadedRuntime() }
            ?: throw ReaderRuntime.ScriptError.loadFailed("rules unavailable")
        return RuleBlock.decodeList(runtime.call("htmlBlocks", listOf(html, baseUrl, attachments), 60))
    }

    /** Lock held. */
    private fun loadedRuntime(): ReaderRuntime? {
        runtime?.let { return it }
        if (unavailable) return null
        runtime = runCatching { ReaderRuntime.createBlocking(OfflineHttp) }.getOrElse {
            unavailable = true
            null
        }
        return runtime
    }

    private object OfflineHttp : HttpFetching {
        override suspend fun send(request: HttpRequest): HttpResponse = throw IOException("offline")
    }
}

// Sessions

/** Open books. Rule modules keep the book page they loaded with, so each book keeps its own runtime for its chapters. */
internal object ReaderSessions {
    private const val LIMIT = 2
    private val mutex = Mutex()
    private val sessions = HashMap<String, ReaderBookSession>()
    private val recent = ArrayList<ReaderBookSession>()
    private val opening = HashMap<String, CompletableDeferred<ReaderBookSession>>()

    suspend fun open(url: String, context: SourceContext): ReaderBookSession {
        val pending = CompletableDeferred<ReaderBookSession>()
        mutex.withLock { opening[url] = pending }
        try {
            val session = ReaderBookSession.open(url, context)
            mutex.withLock { store(session, setOf(url, session.book.url)) }
            pending.complete(session)
            return session
        } catch (e: Throwable) {
            pending.completeExceptionally(e)
            throw e
        } finally {
            mutex.withLock { if (opening[url] === pending) opening.remove(url) }
        }
    }

    suspend fun session(book: BookInfo, context: SourceContext): ReaderBookSession {
        val waiting = mutex.withLock {
            sessions[book.url]?.let { return it }
            opening[book.url]
        }
        return waiting?.await() ?: open(book.url, context)
    }

    /** Lock held. */
    private fun store(session: ReaderBookSession, keys: Set<String>) {
        for (key in keys) {
            sessions[key]?.takeIf { it !== session }?.let(::retire)
            sessions[key] = session
        }
        recent.removeAll { it === session }
        recent += session
        while (recent.size > LIMIT) retire(recent[0])
    }

    /** Lock held. */
    private fun retire(session: ReaderBookSession) {
        sessions.entries.removeAll { it.value === session }
        recent.removeAll { it === session }
        session.close()
    }
}

/** One opened book: its metadata, the rule's chapter list, and the engine that parsed it. */
internal class ReaderBookSession(
    val book: BookInfo,
    private val engine: Engine,
    private val chapters: Map<String, ChapterInfo>,
) {
    sealed class Engine {
        class Script(val runtime: ReaderRuntime) : Engine()
        class Web(val web: ReaderWebFallback) : Engine()
    }

    data class ChapterInfo(val url: String, val name: String, val isVIP: Boolean, val isPaid: Boolean?)

    suspend fun chapter(ref: ChapterRef): ChapterResult {
        val info = chapters[ref.id]
            ?: return ChapterResult.Failed(ref, "Chapter ${ref.index} is not in the book's chapter list.")
        val blocks = try {
            when (engine) {
                is Engine.Script -> ReaderChapterPayload.decode(
                    engine.runtime.call("parseChapter", listOf(info.url, info.name, info.isVIP, info.isPaid), 300),
                ).blocks
                is Engine.Web -> engine.web.chapter(info)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            if (ref.isPaid) return ChapterResult.Refused(ref, PAID_REFUSAL)
            throw sourceError(e)
        }
        ReaderChapterBuilder.content(ref, blocks)?.let { return ChapterResult.Content(it) }
        if (ref.isPaid) return ChapterResult.Refused(ref, PAID_REFUSAL)
        return ChapterResult.Failed(ref, "The chapter page had no text.")
    }

    fun close() {
        when (engine) {
            is Engine.Script -> engine.runtime.close()
            is Engine.Web -> engine.web.closeLater()
        }
    }

    companion object {
        private const val PAID_REFUSAL = "The site did not return this paid chapter for the signed-in account."

        suspend fun open(url: String, context: SourceContext): ReaderBookSession {
            val scriptError: Exception = try {
                val runtime = ReaderRuntime.create(context.http, context.logger)
                try {
                    val payload = ReaderBookPayload.decode(
                        runtime.call("openBook", listOf(url, null, null, emptyMap<String, String>()), 900),
                    )
                    if (payload.chapters.isNotEmpty()) return make(payload, url, Engine.Script(runtime))
                    runtime.close()
                    SourceException.Parse("The reader rules found no chapters on $url.")
                } catch (e: Throwable) {
                    runtime.close()
                    throw e
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                e
            }
            context.logger.log(LogLevel.INFO, "[Reader] Script engine failed (${scriptError.message}); trying a web view.")
            try {
                val (payload, web) = ReaderWebFallback.openBook(url, context)
                if (payload.chapters.isNotEmpty()) return make(payload, url, Engine.Web(web))
                web.close()
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                context.logger.log(LogLevel.DEBUG, "[Reader] Web view fallback failed: ${e.message}")
            }
            throw sourceError(scriptError)
        }

        fun make(payload: ReaderBookPayload, requested: String, engine: Engine): ReaderBookSession {
            val pageUrl = payload.pageUrl?.takeIf { it.isNotEmpty() } ?: requested
            val bookUrl = payload.bookUrl?.takeIf { it.isNotEmpty() }?.let { resolveUrl(pageUrl, it) } ?: requested
            val refs = ArrayList<ChapterRef>()
            val infos = LinkedHashMap<String, ChapterInfo>()
            payload.chapters.forEachIndexed { offset, chapter ->
                val index = offset + 1
                val raw = chapter.url ?: ""
                var id = raw.ifEmpty { "$index" }
                if (infos.containsKey(id)) id += "#$index"
                val name = chapter.name?.trim() ?: ""
                refs += ChapterRef(
                    id = id,
                    index = index,
                    title = name.ifEmpty { "Chapter $index" },
                    url = if (raw.isEmpty()) null else resolveUrl(pageUrl, raw),
                    isPaid = chapter.isVIP ?: false,
                )
                infos[id] = ChapterInfo(raw, name, chapter.isVIP ?: false, chapter.isPaid)
            }
            val book = BookInfo(
                source = SourceId.READER,
                id = bookUrl,
                url = bookUrl,
                title = payload.bookname?.trim()?.ifEmpty { null } ?: bookUrl,
                author = payload.author?.trim()?.ifEmpty { null },
                synopsis = payload.introduction?.trim()?.ifEmpty { null },
                coverUrl = payload.coverUrl?.takeIf { it.isNotEmpty() }?.let { resolveUrl(pageUrl, it) },
                tags = payload.tags.orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull },
                language = payload.language?.ifEmpty { null } ?: "zh",
                chapters = refs,
            )
            return ReaderBookSession(book, engine, infos)
        }

        fun sourceError(error: Exception): Exception {
            if (error is SourceException) return error
            val message = error.message ?: error.toString()
            if (message.startsWith("HTTP 401") || message.startsWith("HTTP 403") || message.startsWith("HTTP 429")) {
                return SourceException.Blocked(message)
            }
            return SourceException.Parse(message)
        }
    }
}

/** `reference` resolved against `base`, like `URL(string:relativeTo:)`; `reference` itself when it cannot be resolved. */
internal fun resolveUrl(base: String, reference: String): String =
    runCatching { URL(URL(base), reference).toString() }.getOrDefault(reference)

// Payloads

private val payloadJson = Json {
    ignoreUnknownKeys = true
    coerceInputValues = true
    isLenient = true
    explicitNulls = false
}

/** `__ND_parseBook()` output from `bridge.js`, plus `pageUrl`/`charset` from `reader_host.js`. */
@Serializable
internal data class ReaderBookPayload(
    val bookUrl: String? = null,
    val bookname: String? = null,
    val author: String? = null,
    val introduction: String? = null,
    val coverUrl: String? = null,
    val tags: List<JsonElement>? = null,
    val language: String? = null,
    val pageUrl: String? = null,
    val chapters: List<Chapter> = emptyList(),
) {
    @Serializable
    data class Chapter(
        val url: String? = null,
        val name: String? = null,
        val number: Double? = null,
        val sectionName: String? = null,
        val isVIP: Boolean? = null,
        val isPaid: Boolean? = null,
    )

    companion object {
        fun decode(json: String): ReaderBookPayload = try {
            payloadJson.decodeFromString(serializer(), json)
        } catch (e: Exception) {
            throw SourceException.Parse("Unexpected book data from the reader rules: ${e.message}")
        }
    }
}

internal class ReaderChapterPayload(val chapterName: String?, val blocks: List<RuleBlock>) {
    companion object {
        fun decode(json: String): ReaderChapterPayload {
            val obj = payloadJson.parseToJsonElement(json) as? kotlinx.serialization.json.JsonObject
                ?: throw SourceException.Parse("Unexpected chapter data from the reader rules.")
            return ReaderChapterPayload(
                (obj["chapterName"] as? JsonPrimitive)?.contentOrNull,
                RuleBlock.decodeList(obj["blocks"] ?: JsonArray(emptyList())),
            )
        }
    }
}

/** One `('text'|'img', value)` pair from `reader_host.js`. */
internal sealed class RuleBlock {
    data class Text(val text: String) : RuleBlock()
    data class Image(val url: String) : RuleBlock()

    companion object {
        fun decodeList(json: String): List<RuleBlock> = decodeList(payloadJson.parseToJsonElement(json))

        fun decodeList(element: JsonElement): List<RuleBlock> = (element as? JsonArray).orEmpty().mapNotNull { item ->
            val pair = item as? JsonArray ?: return@mapNotNull null
            val kind = (pair.getOrNull(0) as? JsonPrimitive)?.contentOrNull
            val value = (pair.getOrNull(1) as? JsonPrimitive)?.contentOrNull ?: return@mapNotNull null
            if (kind == "img" && (value.startsWith("http://", true) || value.startsWith("https://", true))) Image(value) else Text(value)
        }
    }
}

// Chapter HTML

/** Builds chapter XHTML the way `_reader_chapter_result` does on the desktop. */
internal object ReaderChapterBuilder {
    const val CSS_CLASS = "reader-content"
    private val imageExtensions = setOf("jpg", "jpeg", "png", "gif", "webp", "avif", "bmp")

    fun content(ref: ChapterRef, blocks: List<RuleBlock>): ChapterContent? {
        var list = blocks
        val first = list.firstOrNull()
        if (first is RuleBlock.Text && normalizedTitle(first.text) == normalizedTitle(ref.title)) list = list.drop(1)
        val parts = ArrayList<String>()
        val images = ArrayList<ImageRef>()
        val filenames = HashMap<String, String>()
        var hasText = false
        for (block in list) {
            when (block) {
                is RuleBlock.Text -> {
                    hasText = true
                    parts += "<p>${escape(block.text)}</p>"
                }
                is RuleBlock.Image -> {
                    val filename = filenames.getOrPut(block.url) {
                        imageFilename(block.url, ref.index, images.size + 1).also { images += ImageRef(block.url, it) }
                    }
                    parts += "<p class=\"img\"><img src=\"${escape(filename)}\" alt=\"\"/></p>"
                }
            }
        }
        if (!hasText && images.isEmpty()) return null
        val html = "<div class=\"$CSS_CLASS\">\n" + parts.joinToString("\n") + "\n</div>"
        return ChapterContent(ref, html, images)
    }

    /** `_normalize_title_for_match`: NFKC, no whitespace, case-folded. */
    fun normalizedTitle(value: String): String =
        Normalizer.normalize(value, Normalizer.Form.NFKC).filterNot { it.isWhitespace() }.lowercase(Locale.ROOT)

    fun imageFilename(url: String, chapter: Int, number: Int): String {
        val path = runCatching { URL(url).path }.getOrDefault("")
        val last = path.substringAfterLast('/')
        val ext = if (last.contains('.')) last.substringAfterLast('.').lowercase() else ""
        val known = if (ext in imageExtensions) ext else "jpg"
        return String.format(Locale.ROOT, "images/%04d_%02d.%s", chapter, number, known)
    }

    fun escape(text: String): String {
        val out = StringBuilder(text.length)
        for (c in text) {
            when (c) {
                '&' -> out.append("&amp;")
                '<' -> out.append("&lt;")
                '>' -> out.append("&gt;")
                '"' -> out.append("&quot;")
                else -> out.append(c)
            }
        }
        return out.toString()
    }
}
