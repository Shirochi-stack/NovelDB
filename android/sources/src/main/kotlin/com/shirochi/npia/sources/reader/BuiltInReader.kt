package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.sources.npia.NpiaAdapter
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.put
import java.util.Base64

/**
 * The Catalog reader's way to the built-in scrapers (`window.NovelDBTransport` with `{op: "book"}` or
 * `{op: "chapter"}`): a book whose site a built-in scraper owns (npia, sfc, ntk, ...) is read with it,
 * as a download would, instead of the page's generic engine, which cannot read those sites' chapters.
 *
 * - `{op: "book", url}` -> `{bookname, author, engine, chapters: [{name}]}`
 * - `{op: "chapter", url, index}` -> `{chapterName, contentHtml}` (pictures inline as data: addresses
 *   when the scraper holds their bytes, else their own addresses)
 * - `{error, skip: true}` when no built-in scraper owns the address: the page tries its own engines.
 * - With `engines: true`, the app's site engines that list the site instead (gallery-dl, HaruNeko,
 *   FanFicFare, ...: those a download would try, the generic fallback left to the page), or `{skip}`.
 */
internal object BuiltInReader {
    private val books = object : LinkedHashMap<String, Pair<SourceAdapter, BookInfo>>(8, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Pair<SourceAdapter, BookInfo>>?) = size > 6
    }

    /** The built-in scraper that owns the address; `reader` is the rule engines', not a built-in one. */
    private fun adapterFor(url: String): SourceAdapter? =
        SourceCatalog.adapters.firstOrNull { it.id != SourceId.READER && it.matches(url) }

    /**
     * The app's site engines that list the address (a download's own order), none of the built-in or generic
     * ones. On sites the generic reader handles first, keep Tako available if the generic book lookup fails.
     */
    private fun enginesFor(url: String, context: SourceContext): SourceAdapter? {
        val chain = SourceCatalog.resolve(url, context.settings.engines) as? SiteEngineChain ?: return null
        val steps = if (SourceCatalog.genericFirst(url)) {
            chain.steps.filter { it == SiteEngine.TAKO && PythonEngines.listsBundled(it, url) }
        } else {
            chain.steps.filter { it != SiteEngine.NATIVE && it != SiteEngine.GENERIC }
        }
        return if (steps.isEmpty()) null else SiteEngineChain(null, steps)
    }

    suspend fun handle(op: String, details: JsonObject, context: SourceContext): String {
        val url = (details["url"] as? JsonPrimitive)?.contentOrNull.orEmpty()
        val engines = (details["engines"] as? JsonPrimitive)?.booleanOrNull == true
        val adapter = (if (engines) enginesFor(url, context) else adapterFor(url))
            ?: return buildJsonObject { put("error", if (engines) "no site engine lists this site" else "no built-in scraper for this site"); put("skip", true) }.toString()
        val reader = (details["reader"] as? JsonPrimitive)?.contentOrNull
        val page = (details["page"] as? JsonPrimitive)?.intOrNull
        val key = when {
            engines -> "engines|$url"
            adapter is NpiaAdapter && reader != null -> "$reader|$url"
            else -> url
        }
        return try {
            when (op) {
                "book" -> if (adapter is NpiaAdapter && reader != null && page != null)
                    npiaPage(key, url, page, context) else book(key, url, adapter, context)
                else -> chapter(key, url, (details["index"] as? JsonPrimitive)?.intOrNull ?: -1, adapter, context,
                    paged = adapter is NpiaAdapter && reader != null)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            buildJsonObject { put("error", (e.message ?: e.toString()).lineSequence().first().take(300)) }.toString()
        }
    }

    private suspend fun npiaPage(key: String, url: String, page: Int, context: SourceContext): String {
        require(page in 0 until 5000)
        val opened = if (page == 0) NpiaAdapter().let { it to it.openBook(it.canonicalUrl(url), context) }
            else synchronized(books) { books[key] } ?: error("the reader was closed")
        val npia = opened.first as NpiaAdapter
        val book = opened.second
        val added = npia.chapterPage(book.id, page, book.chapters.size, book.chapters.map { it.id }.toMutableSet(), context)
        if (page == 0 && added.isEmpty()) error("no chapters found")
        synchronized(books) { books[key] = npia to book.copy(chapters = book.chapters + added) }
        return buildJsonObject {
            put("chapters", buildJsonArray { added.forEach { add(buildJsonObject { put("name", it.title) }) } })
            put("more", added.isNotEmpty() && page < 4999)
        }.toString()
    }

    private suspend fun open(key: String, url: String, adapter: SourceAdapter, context: SourceContext): BookInfo {
        synchronized(books) { books[key] }?.let { return it.second }
        val book = adapter.parseBook(adapter.canonicalUrl(url), context)
        synchronized(books) { books[key] = adapter to book }
        return book
    }

    private suspend fun book(key: String, url: String, adapter: SourceAdapter, context: SourceContext): String {
        val book = open(key, url, adapter, context)
        if (book.chapters.isEmpty()) return buildJsonObject { put("error", "no chapters found") }.toString()
        return buildJsonObject {
            put("bookname", book.title)
            put("author", book.author.orEmpty())
            put("engine", (adapter as? SiteEngineChain)?.steps?.firstOrNull()?.label ?: adapter.id.raw)
            put("chapters", buildJsonArray {
                book.chapters.forEach { chapter ->
                    add(buildJsonObject {
                        put("name", chapter.title)
                        if (adapter is SiteEngineChain) chapter.url?.let { put("url", it) }
                    })
                }
            })
        }.toString()
    }

    private suspend fun chapter(key: String, url: String, index: Int, adapter: SourceAdapter, context: SourceContext,
                                paged: Boolean): String {
        val known = synchronized(books) { books[key] }
        val book = known?.second ?: if (paged) error("the reader was closed") else open(key, url, adapter, context)
        val adapter = known?.first ?: synchronized(books) { books[key]?.first } ?: adapter
        val ref = book.chapters.getOrNull(index) ?: return buildJsonObject { put("error", "no such chapter") }.toString()
        return when (val result = adapter.parseChapter(ref, book, context)) {
            is ChapterResult.Content -> {
                var html = result.content.html
                // The scraper names its pictures by their file names in a download: the reader shows them inline.
                for (image in result.content.images) {
                    val bytes = image.data
                    val shown = if (bytes != null) "data:${mime(image.filename)};base64," + Base64.getEncoder().encodeToString(bytes) else image.url
                    html = html.replace(image.filename, shown)
                }
                buildJsonObject {
                    put("chapterName", ref.title)
                    put("contentHtml", html)
                    if (adapter is SiteEngineChain && result.content.images.isNotEmpty() &&
                        html.replace(Regex("<[^>]+>"), "").isBlank()
                    ) put("comic", true)
                }.toString()
            }
            is ChapterResult.Refused -> buildJsonObject { put("error", "This chapter is locked: ${result.reason}"); put("locked", true) }.toString()
            is ChapterResult.Failed -> buildJsonObject { put("error", result.message) }.toString()
        }
    }

    private fun mime(name: String): String = when (name.substringAfterLast('.', "").lowercase()) {
        "png" -> "image/png"
        "gif" -> "image/gif"
        "webp" -> "image/webp"
        "avif" -> "image/avif"
        else -> "image/jpeg"
    }
}
