package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SiteOptOuts
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.WebEngineProviding
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put
import java.io.File
import java.net.URI
import java.util.Base64

/**
 * The network of the Catalog's generic search page (`window.NovelDBTransport`): the same transport
 * the engines' page scripts use, so requests carry the web view's cookies and `{op: "window"}` runs
 * a script in a hidden page of the address (a site's bot check, HaruNeko's window scripts).
 * `{op: "book"}` and `{op: "chapter"}` read a book with the built-in scraper of its site (`BuiltInReader`).
 * `{op: "file", url: "file:///..."}` hands over a Library EPUB or TXT from [libraryFolder].
 */
object CatalogTransport {
    /** The app's download folder: the one place the reader may open files from. */
    @Volatile var libraryFolder: File? = null

    /** A file this small goes to the page whole, whatever part it asked for: one request reads the book. */
    private const val WHOLE_BYTES = 8L * 1024 * 1024
    /** The most one answer carries (as base64): the page asks for a book's files one at a time. */
    private const val PART_BYTES = 48L * 1024 * 1024

    /**
     * A Library EPUB or TXT for the reader's page. With `offset` (negative: from the end) and `length`
     * the answer is that part, with the `offset` it starts at and the file's `size`: a comic's EPUB is
     * hundreds of megabytes, and the page reads its directory and then one file of it at a time (a TXT:
     * a part after the other). No book is refused for its size. Without them, or for a small file, the
     * whole file (no `offset` in the answer).
     */
    internal fun libraryFile(url: String, offset: Long? = null, length: Long? = null): String {
        val folder = libraryFolder?.canonicalFile ?: return error("no Library folder")
        val file = runCatching { File(URI(url)).canonicalFile }.getOrNull() ?: return error("not a file address")
        if (file.extension.lowercase() !in setOf("epub", "txt") || !file.path.startsWith(folder.path + File.separator) || !file.isFile) {
            return error("only EPUB and TXT files in the Library can be opened")
        }
        val size = file.length()
        if (offset == null || size <= WHOLE_BYTES) {
            val bytes = runCatching { file.readBytes() }.getOrNull() ?: return error("the book could not be opened")
            return buildJsonObject {
                put("status", 200)
                put("size", size)
                put("responseBase64", Base64.getEncoder().encodeToString(bytes))
            }.toString()
        }
        val start = (if (offset < 0) size + offset else offset).coerceIn(0, size)
        val count = (length ?: (size - start)).coerceIn(0, minOf(size - start, PART_BYTES)).toInt()
        val bytes = runCatching {
            java.io.RandomAccessFile(file, "r").use { input ->
                input.seek(start)
                ByteArray(count).also { input.readFully(it) }
            }
        }.getOrNull() ?: return error("the book could not be opened")
        return buildJsonObject {
            put("status", 200)
            put("size", size)
            put("offset", start)
            put("responseBase64", Base64.getEncoder().encodeToString(bytes))
        }.toString()
    }

    private fun error(message: String) = buildJsonObject { put("error", message) }.toString()

    suspend fun handle(requestJson: String, http: HttpFetching, web: WebEngineProviding, context: SourceContext? = null): String {
        val details = runCatching { Json.parseToJsonElement(requestJson) as? JsonObject }.getOrNull()
        val op = (details?.get("op") as? JsonPrimitive)?.contentOrNull
        // A site whose owner opted out: no reader, search or page request reaches it.
        if (SiteOptOuts.matches((details?.get("url") as? JsonPrimitive)?.contentOrNull.orEmpty())) {
            context?.logger?.log(LogLevel.ERROR, SiteOptOuts.MESSAGE)
            return error(SiteOptOuts.MESSAGE)
        }
        if (details != null && op == "catalogData") return CatalogData.handle(details)
        if (details != null && op == "libraryLive") return LibraryLive.handle(details)
        if (details != null && context != null && (op == "book" || op == "chapter")) return BuiltInReader.handle(op, details, context)
        if (details != null && op == "file") {
            fun number(name: String) = (details[name] as? JsonPrimitive)?.contentOrNull?.toDoubleOrNull()?.toLong()
            return libraryFile((details["url"] as? JsonPrimitive)?.contentOrNull.orEmpty(), number("offset"), number("length"))
        }
        return WebToEpubTransport.handle(requestJson, http, web)
    }
}
