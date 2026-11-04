package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.HttpFetching
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
 * `{op: "file", url: "file:///..."}` hands over a Library EPUB (only one in [libraryFolder]) for the reader.
 */
object CatalogTransport {
    /** The app's download folder: the one place the reader may open files from. */
    @Volatile var libraryFolder: File? = null

    private fun libraryFile(url: String): String {
        val folder = libraryFolder?.canonicalFile ?: return error("no Library folder")
        val file = runCatching { File(URI(url)).canonicalFile }.getOrNull() ?: return error("not a file address")
        if (!file.name.endsWith(".epub", ignoreCase = true) || !file.path.startsWith(folder.path + File.separator) || !file.isFile) {
            return error("only EPUBs in the Library can be opened")
        }
        // The whole book goes to the page at once (as base64): a huge one would not fit.
        if (file.length() > 80L * 1024 * 1024) return error("this EPUB is too big to read here (over 80 MB)")
        return buildJsonObject {
            put("status", 200)
            put("responseBase64", Base64.getEncoder().encodeToString(file.readBytes()))
        }.toString()
    }

    private fun error(message: String) = buildJsonObject { put("error", message) }.toString()

    suspend fun handle(requestJson: String, http: HttpFetching, web: WebEngineProviding, context: SourceContext? = null): String {
        val details = runCatching { Json.parseToJsonElement(requestJson) as? JsonObject }.getOrNull()
        val op = (details?.get("op") as? JsonPrimitive)?.contentOrNull
        if (details != null && context != null && (op == "book" || op == "chapter")) return BuiltInReader.handle(op, details, context)
        if (details != null && op == "file") return libraryFile((details["url"] as? JsonPrimitive)?.contentOrNull.orEmpty())
        return WebToEpubTransport.handle(requestJson, http, web)
    }
}
