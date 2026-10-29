package com.shirochi.npia.sources.http

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.HttpStatusException
import com.shirochi.npia.core.SourceException
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.nodes.TextNode
import java.net.URI
import java.net.URL
import java.net.URLDecoder
import java.net.URLEncoder

/** Small helpers shared by the HTTP-only adapters (sfc, nweb, jara, yeduji, maru). */
object HttpSourceSupport {
    /**
     * Desktop Chrome user agent (`_KR_UA`). The sites serve their desktop layouts to it,
     * which is what the selectors expect.
     */
    const val DESKTOP_USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

    /** GET that throws on HTTP errors. */
    suspend fun get(url: String, headers: Map<String, String>, http: HttpFetching, timeoutSeconds: Long = 30): HttpResponse {
        val response = http.send(HttpRequest(url, headers = headers, timeoutSeconds = timeoutSeconds))
        if (response.status !in 200 until 400) throw HttpStatusException(response.status, url)
        return response
    }

    // URLs

    /** Lower-cased host, or "" when the URL has none. */
    fun host(url: String): String =
        (runCatching { URI(url.trim()).host }.getOrNull() ?: runCatching { URL(url.trim()).host }.getOrNull() ?: "").lowercase()

    /** Decoded path, or "". */
    fun path(url: String): String =
        runCatching { URI(url.trim()).path }.getOrNull() ?: runCatching { URL(url.trim()).path }.getOrNull() ?: ""

    fun scheme(url: String): String? = runCatching { URI(url.trim()).scheme }.getOrNull()

    fun query(url: String, name: String): String? {
        val raw = runCatching { URI(url.trim()).rawQuery }.getOrNull() ?: url.substringAfter('?', "").substringBefore('#')
        if (raw.isEmpty()) return null
        for (item in raw.split('&')) {
            val key = decode(item.substringBefore('='))
            if (key == name) return decode(item.substringAfter('=', "")).trim()
        }
        return null
    }

    private fun decode(value: String): String = runCatching { URLDecoder.decode(value, "UTF-8") }.getOrDefault(value)

    fun encode(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")

    /** `href` resolved against `base`, or null when it is not a valid URL. */
    fun resolve(href: String, base: String?): String? {
        val trimmed = href.trim()
        if (trimmed.isEmpty()) return null
        return runCatching { if (base == null) URL(trimmed).toString() else URL(URL(base), trimmed).toString() }.getOrNull()
    }

    fun isPositiveInteger(value: String?): Boolean =
        !value.isNullOrEmpty() && value[0] != '0' && value.all { it in '0'..'9' }

    // Regex

    /** First capture group of `pattern` in `text`. */
    fun firstMatch(pattern: String, text: String, vararg options: RegexOption): String? =
        Regex(pattern, options.toSet()).find(text)?.groupValues?.getOrNull(1)

    /** Capture groups (from group 1) of every match. */
    fun allMatches(pattern: String, text: String, vararg options: RegexOption): List<List<String>> =
        Regex(pattern, options.toSet()).findAll(text).map { it.groupValues.drop(1) }.toList()

    suspend fun sleep(seconds: Double) {
        if (seconds > 0) delay((seconds * 1000).toLong())
    }

    // JSON

    val json = Json { ignoreUnknownKeys = true; isLenient = true }

    fun jsonObject(body: ByteArray): JsonObject =
        runCatching { json.parseToJsonElement(body.decodeToString()) }.getOrNull() as? JsonObject
            ?: throw SourceException.Parse("expected a JSON object")

    /** JSON value as a string, accepting numbers too. */
    fun string(value: JsonElement?): String = when (value) {
        is JsonPrimitive -> if (value is JsonNull) "" else value.content
        else -> ""
    }

    fun int(value: JsonElement?): Int {
        val text = string(value).trim()
        return text.toIntOrNull() ?: text.toDoubleOrNull()?.toInt() ?: when (text) {
            "true" -> 1
            else -> 0
        }
    }

    fun double(value: JsonElement?): Double = string(value).trim().toDoubleOrNull() ?: 0.0

    fun obj(value: JsonElement?): JsonObject? = value as? JsonObject

    fun array(value: JsonElement?): JsonArray? = value as? JsonArray

    /** A JSON boolean, or null when the value is not one. */
    fun bool(value: JsonElement?): Boolean? =
        (value as? JsonPrimitive)?.takeIf { !it.isString && it !is JsonNull }?.content?.toBooleanStrictOrNull()

    // Text

    /** Decodes the common HTML entities (for titles taken from JSON APIs). */
    fun unescapeHtml(value: String): String {
        if (!value.contains('&')) return value
        var out = value
        for ((entity, replacement) in listOf(
            "&lt;" to "<", "&gt;" to ">", "&quot;" to "\"", "&#39;" to "'", "&#x27;" to "'", "&apos;" to "'", "&nbsp;" to "\u00a0",
        )) {
            out = out.replace(entity, replacement)
        }
        out = Regex("&#(x?)([0-9A-Fa-f]+);").replace(out) { match ->
            val radix = if (match.groupValues[1].isEmpty()) 10 else 16
            val code = match.groupValues[2].toIntOrNull(radix)
            if (code != null && Character.isValidCodePoint(code)) String(Character.toChars(code)) else match.value
        }
        return out.replace("&amp;", "&")
    }

    /** Text nodes of `element`, trimmed, one per line (BeautifulSoup `get_text('\n', strip=True)`). */
    fun textLines(element: Element): List<String> {
        val lines = mutableListOf<String>()
        fun walk(node: Node) {
            for (child in node.childNodes()) {
                if (child is TextNode) {
                    val value = child.wholeText.trim()
                    if (value.isNotEmpty()) lines += value
                } else {
                    walk(child)
                }
            }
        }
        walk(element)
        return lines
    }
}
