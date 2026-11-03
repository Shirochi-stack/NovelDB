package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.SourceCatalog
import kotlinx.coroutines.CancellationException
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonArray
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
 */
internal object BuiltInReader {
    private val books = object : LinkedHashMap<String, Pair<SourceAdapter, BookInfo>>(8, 0.75f, true) {
        override fun removeEldestEntry(eldest: MutableMap.MutableEntry<String, Pair<SourceAdapter, BookInfo>>?) = size > 6
    }

    /** The built-in scraper that owns the address; `reader` is the rule engines', not a built-in one. */
    private fun adapterFor(url: String): SourceAdapter? =
        SourceCatalog.adapters.firstOrNull { it.id != SourceId.READER && it.matches(url) }

    suspend fun handle(op: String, details: JsonObject, context: SourceContext): String {
        val url = (details["url"] as? JsonPrimitive)?.contentOrNull.orEmpty()
        val adapter = adapterFor(url) ?: return buildJsonObject { put("error", "no built-in scraper for this site"); put("skip", true) }.toString()
        return try {
            when (op) {
                "book" -> book(url, adapter, context)
                else -> chapter(url, (details["index"] as? JsonPrimitive)?.intOrNull ?: -1, adapter, context)
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            buildJsonObject { put("error", (e.message ?: e.toString()).lineSequence().first().take(300)) }.toString()
        }
    }

    private suspend fun open(url: String, adapter: SourceAdapter, context: SourceContext): BookInfo {
        synchronized(books) { books[url] }?.let { return it.second }
        val book = adapter.parseBook(adapter.canonicalUrl(url), context)
        synchronized(books) { books[url] = adapter to book }
        return book
    }

    private suspend fun book(url: String, adapter: SourceAdapter, context: SourceContext): String {
        val book = open(url, adapter, context)
        if (book.chapters.isEmpty()) return buildJsonObject { put("error", "no chapters found") }.toString()
        return buildJsonObject {
            put("bookname", book.title)
            put("author", book.author.orEmpty())
            put("engine", adapter.id.raw)
            put("chapters", buildJsonArray { book.chapters.forEach { add(buildJsonObject { put("name", it.title) }) } })
        }.toString()
    }

    private suspend fun chapter(url: String, index: Int, adapter: SourceAdapter, context: SourceContext): String {
        val book = open(url, adapter, context)
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
