package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.WebEngineProviding
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * The network of the Catalog's generic search page (`window.NovelDBTransport`): the same transport
 * the engines' page scripts use, so requests carry the web view's cookies and `{op: "window"}` runs
 * a script in a hidden page of the address (a site's bot check, HaruNeko's window scripts).
 * `{op: "book"}` and `{op: "chapter"}` read a book with the built-in scraper of its site (`BuiltInReader`).
 */
object CatalogTransport {
    suspend fun handle(requestJson: String, http: HttpFetching, web: WebEngineProviding, context: SourceContext? = null): String {
        val details = runCatching { Json.parseToJsonElement(requestJson) as? JsonObject }.getOrNull()
        val op = (details?.get("op") as? JsonPrimitive)?.contentOrNull
        if (details != null && context != null && (op == "book" || op == "chapter")) return BuiltInReader.handle(op, details, context)
        return WebToEpubTransport.handle(requestJson, http, web)
    }
}
