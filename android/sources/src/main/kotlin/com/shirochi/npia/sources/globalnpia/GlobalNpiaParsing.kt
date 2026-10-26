package com.shirochi.npia.sources.globalnpia

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.websupport.WebHtml
import com.shirochi.npia.sources.websupport.WebRegex
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.jsoup.nodes.Element
import java.io.ByteArrayOutputStream
import java.net.URI
import java.util.Base64
import java.util.Locale

/** Pure helpers for the global_npia JSON API and chapter HTML (no network). */
internal object GlobalNpiaParsing {
    const val HOST: String = "global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    const val API_HOST: String = "api-global.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    const val IMAGE_HOST: String = "pv-gn.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    const val COOKIE_DOMAIN: String = ".\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    val BASE: String = "https://$HOST"
    val API: String = "https://$API_HOST"
    val imageCookieNames: List<String> = listOf("CloudFront-Policy", "CloudFront-Key-Pair-Id", "CloudFront-Signature")

    // URLs

    private fun uri(url: String): URI? = runCatching { URI(url.trim()) }.getOrNull()

    fun matches(url: String): Boolean {
        val parsed = uri(url) ?: return false
        if ((parsed.host ?: "").lowercase() != HOST) return false
        return WebRegex.matches("^/novel/\\d+(?:/|$)", parsed.path ?: "", RegexOption.IGNORE_CASE)
    }

    fun novelId(url: String): String =
        WebRegex.capture("/novel/(\\d+)", uri(url)?.path ?: "", RegexOption.IGNORE_CASE) ?: ""

    fun episodeId(url: String): String =
        WebRegex.capture("/viewer/(\\d+)", uri(url)?.path ?: "", RegexOption.IGNORE_CASE) ?: ""

    // JSON helpers

    fun dict(value: JsonElement?): JsonObject? = value as? JsonObject

    /** The value when it is a JSON string, else null (Swift `as? String`). */
    fun stringOrNull(value: JsonElement?): String? {
        val primitive = value as? JsonPrimitive ?: return null
        return if (primitive.isString) primitive.content else null
    }

    fun string(value: JsonElement?): String {
        if (value == null || value is JsonNull) return ""
        if (value is JsonPrimitive) {
            if (value.isString) return value.content
            return when (value.content) {
                "true" -> "1"
                "false" -> "0"
                else -> value.content
            }
        }
        return value.toString()
    }

    fun safeInt(value: JsonElement?, fallback: Int = 0): Int {
        if (value == null || value is JsonNull) return fallback
        val primitive = value as? JsonPrimitive ?: return fallback
        if (primitive.isString) return primitive.content.trim().toIntOrNull() ?: fallback
        return when (primitive.content) {
            "true" -> 1
            "false" -> 0
            else -> primitive.content.toDoubleOrNull()?.toInt() ?: fallback
        }
    }

    fun pickStrings(items: JsonElement?, keys: List<String>): List<String> {
        val list = items as? JsonArray ?: return emptyList()
        val values = mutableListOf<String>()
        for (item in list) {
            val text = stringOrNull(item)
            if (text != null) {
                val trimmed = text.trim()
                if (trimmed.isNotEmpty()) values.add(trimmed)
                continue
            }
            val obj = item as? JsonObject ?: continue
            for (key in keys) {
                val value = stringOrNull(obj[key]) ?: continue
                val trimmed = value.trim()
                if (trimmed.isNotEmpty()) {
                    values.add(trimmed)
                    break
                }
            }
        }
        return values
    }

    fun payloadMessage(payload: JsonElement?): String {
        val obj = payload as? JsonObject ?: return if (payload == null) "" else string(payload)
        val result = dict(obj["result"]) ?: JsonObject(emptyMap())
        val values: List<JsonElement?> = listOf(
            obj["errmsg"], obj["message"], obj["error"], obj["code"],
            result["errmsg"], result["message"], result["name"], result["code"],
        )
        return values.map { string(it) }.filter { it.isNotEmpty() }.joinToString(" ").trim()
    }

    /** The server's basic-advertisement episode gate. */
    fun adRequired(status: Int, payload: JsonElement?): Boolean {
        val obj = payload as? JsonObject ?: return false
        val result = dict(obj["result"]) ?: JsonObject(emptyMap())
        var code = string(obj["code"])
        if (code.isEmpty()) code = string(result["code"])
        code = code.trim()
        var name = string(result["name"])
        if (name.isEmpty()) name = string(obj["name"])
        name = name.uppercase()
        val message = payloadMessage(payload).lowercase()
        val codeMatch = code == "0010" || code == "10"
        val gated = codeMatch && (name == "NOVEL_ERROR" || message.contains("advertisement"))
        return status >= 400 && (gated || message.contains("basic advertisement"))
    }

    fun accessDenied(status: Int, payload: JsonElement?): Boolean {
        if (status == 401 || status == 403) return true
        val message = payloadMessage(payload)
        val pattern = "login|logged in|purchase|paid|permission|forbidden|access denied|not available|\uAD6C\uB9E4|\uB85C\uADF8\uC778|\uAD8C\uD55C"
        return WebRegex.matches(pattern, message, RegexOption.IGNORE_CASE)
    }

    fun authExpired(status: Int, payload: JsonElement?): Boolean {
        val message = payloadMessage(payload).lowercase()
        if (status == 401 || status == 403) return true
        if (message.contains("logged in") || message.contains("login")) return true
        return message.contains("token") && message.contains("expire")
    }

    fun hasSavedLogin(cookies: Map<String, String>): Boolean {
        val userKey = (cookies["USERKEY"] ?: "").trim()
        val tKey = (cookies["TKEY"] ?: "").trim()
        val loginKey = (cookies["LOGINKEY"] ?: "").trim()
        val isLogin = (cookies["ISLOGIN"] ?: "").trim().lowercase()
        val marker = isLogin !in setOf("", "0", "false", "n", "no")
        return userKey.isNotEmpty() && (tKey.isNotEmpty() || (loginKey.isNotEmpty() && marker))
    }

    /** `result.LOGINAT` from `/v1/login/refresh`, URL-decoded, or null. */
    fun loginToken(status: Int, payload: JsonElement?): String? {
        val obj = payload as? JsonObject ?: return null
        val result = dict(obj["result"])
        val statusCode = string(obj["statusCode"])
        if (status != 200 || !(statusCode.isEmpty() || statusCode == "200")) return null
        val token = stringOrNull(result?.get("LOGINAT")) ?: return null
        if (token.trim().isEmpty()) return null
        return percentDecode(token) ?: token
    }

    /** Swift `removingPercentEncoding`: decodes `%XX` only (not `+`); null on a malformed sequence. */
    fun percentDecode(value: String): String? {
        if (!value.contains('%')) return value
        val out = ByteArrayOutputStream()
        var i = 0
        while (i < value.length) {
            val c = value[i]
            if (c == '%') {
                if (i + 2 >= value.length) return null
                val byte = value.substring(i + 1, i + 3).toIntOrNull(16) ?: return null
                out.write(byte)
                i += 3
            } else {
                out.write(c.toString().toByteArray(Charsets.UTF_8))
                i++
            }
        }
        return WebHtml.strict(out.toByteArray(), Charsets.UTF_8)
    }

    // Book

    data class Episode(val episodeNo: String, val number: Int, val title: String)

    /** Sorted, de-duplicated episodes (by `epi_num`, then source order). */
    fun episodes(list: JsonArray): List<Episode> {
        val keyed = mutableListOf<Triple<Int, Int, JsonObject>>()
        list.forEachIndexed { offset, item ->
            val obj = item as? JsonObject ?: return@forEachIndexed
            keyed.add(Triple(safeInt(obj["epi_num"], fallback = offset + 1), offset, obj))
        }
        val sorted = keyed.sortedWith(compareBy<Triple<Int, Int, JsonObject>> { it.first }.thenBy { it.second })
        val seen = mutableSetOf<String>()
        val result = mutableListOf<Episode>()
        for (entry in sorted) {
            val obj = entry.third
            val episodeNo = intString(obj["episode_no"]) ?: continue
            if (!seen.add(episodeNo)) continue
            val number = safeInt(obj["epi_num"], fallback = result.size + 1)
            var title = string(obj["epi_title"]).trim()
            if (title.isEmpty()) title = "Episode $number"
            result.add(Episode(episodeNo, number, title))
        }
        return result
    }

    private fun intString(value: JsonElement?): String? {
        if (value == null || value is JsonNull) return null
        val primitive = value as? JsonPrimitive ?: return null
        if (primitive.isString) return primitive.content.trim().toLongOrNull()?.toString()
        return when (primitive.content) {
            "true" -> "1"
            "false" -> "0"
            else -> primitive.content.toDoubleOrNull()?.toLong()?.toString()
        }
    }

    fun makeBook(novelId: String, novelPayload: JsonObject, episodeList: JsonArray): BookInfo {
        val result = dict(novelPayload["result"]) ?: throw SourceException.Parse("metadata payload has no novel")
        val novel = dict(result["novel"]) ?: throw SourceException.Parse("metadata payload has no novel")
        val bookUrl = "$BASE/novel/$novelId"
        val found = episodes(episodeList)
        if (found.isEmpty()) throw SourceException.Parse("no episodes were found")
        val chapters = found.mapIndexed { position, episode ->
            ChapterRef(
                id = episode.episodeNo, index = position + 1, title = episode.title,
                url = "$BASE/viewer/${episode.episodeNo}", isPaid = false,
            )
        }
        var author = ""
        val writers = result["writer_list"] as? JsonArray
        val firstWriter = writers?.firstOrNull() as? JsonObject
        if (firstWriter != null) author = string(firstWriter["writer_name"]).trim()

        val tags = pickStrings(result["tag_list"], listOf("tag_name", "name", "title")) +
            pickStrings(novel["tag_list"], listOf("tag_name", "name", "title"))
        val categories = mutableListOf<String>()
        categories += pickStrings(result["cate_list"], listOf("cate_name", "name", "title"))
        categories += pickStrings(novel["cate_list"], listOf("cate_name", "name", "title"))
        categories += pickStrings(result["genre_list"], listOf("genre_name", "name", "title"))
        categories += pickStrings(novel["genre_list"], listOf("genre_name", "name", "title"))
        val subjects = mutableListOf<String>()
        for (value in categories + tags) if (value !in subjects) subjects.add(value)

        var cover = string(novel["novel_full_img"])
        if (cover.isEmpty()) cover = string(novel["novel_img"])
        val coverUrl: String? = when {
            cover.startsWith("//") -> "https:$cover"
            cover.isNotEmpty() -> WebHtml.join(bookUrl, cover)
            else -> null
        }
        val synopsis = string(novel["novel_story"]).trim()
        var title = string(novel["novel_name"]).trim()
        if (title.isEmpty()) title = "Novel $novelId"
        val complete = string(novel["flag_complete"]) == "1"
        return BookInfo(
            source = SourceId.GLOBAL_NPIA, id = novelId, url = bookUrl, title = title,
            author = author.ifEmpty { null },
            synopsis = synopsis.ifEmpty { null },
            coverUrl = coverUrl, tags = subjects, status = if (complete) "Completed" else "Ongoing",
            language = "en", chapters = chapters,
        )
    }

    // Ticket / content payloads

    fun looksLikeJwt(value: String): Boolean {
        val parts = value.split(".")
        if (parts.size != 3 || parts.any { it.isEmpty() }) return false
        for (part in parts) {
            var text = part.replace("-", "+").replace("_", "/")
            if (text.length % 4 == 1) return false
            while (text.length % 4 != 0) text += "="
            try {
                Base64.getDecoder().decode(text)
            } catch (e: IllegalArgumentException) {
                return false
            }
        }
        return true
    }

    data class TicketToken(val token: String, val directUrl: String)

    /** (token, directUrl) using the desktop's strict order; both empty when absent. */
    fun extractTicketToken(payload: JsonElement?): TicketToken {
        val result = dict(dict(payload)?.get("result")) ?: JsonObject(emptyMap())
        var fallback = ""
        val keys = listOf("_t", "t", "token")
        for (key in keys) {
            val value = stringOrNull(result[key])
            if (!value.isNullOrEmpty()) {
                if (looksLikeJwt(value)) return TicketToken(value, "")
                if (fallback.isEmpty()) fallback = value
            }
        }
        for (name in result.keys.sorted()) {
            val nested = result[name] as? JsonObject ?: continue
            for (key in keys) {
                val value = stringOrNull(nested[key])
                if (!value.isNullOrEmpty()) {
                    if (looksLikeJwt(value)) return TicketToken(value, "")
                    if (fallback.isEmpty()) fallback = value
                }
            }
        }
        val strings = mutableListOf<String>()
        collectStrings(payload, strings)
        for (value in strings) {
            if (!(value.startsWith("http://") || value.startsWith("https://"))) continue
            val parsed = uri(value) ?: continue
            if ((parsed.host ?: "") != API_HOST || !(parsed.path ?: "").endsWith("/v1/novel/episode/content")) continue
            val token = queryValue(parsed.rawQuery, "_t")
            if (token.isNotEmpty()) {
                if (looksLikeJwt(token)) return TicketToken(token, value)
                if (fallback.isEmpty()) fallback = token
            }
        }
        return TicketToken(fallback, "")
    }

    private fun queryValue(rawQuery: String?, name: String): String {
        if (rawQuery.isNullOrEmpty()) return ""
        for (pair in rawQuery.split("&")) {
            val eq = pair.indexOf('=')
            val key = if (eq >= 0) pair.substring(0, eq) else pair
            if ((percentDecode(key) ?: key) == name) {
                val raw = if (eq >= 0) pair.substring(eq + 1) else ""
                return percentDecode(raw) ?: raw
            }
        }
        return ""
    }

    private fun collectStrings(value: JsonElement?, output: MutableList<String>) {
        when (value) {
            is JsonObject -> for (key in value.keys.sorted()) collectStrings(value[key], output)
            is JsonArray -> for (item in value) collectStrings(item, output)
            is JsonPrimitive -> if (value.isString) output.add(value.content)
            else -> {}
        }
    }

    fun signedImageCookies(ticket: JsonElement?): Map<String, String> {
        val signed = dict(dict(dict(ticket)?.get("result"))?.get("signed_key")) ?: return emptyMap()
        val cookies = linkedMapOf<String, String>()
        for (name in imageCookieNames) {
            val value = string(signed[name])
            if (value.isNotEmpty()) cookies[name] = value
        }
        return cookies
    }

    fun rawContent(payload: JsonElement?): String {
        val obj = dict(payload) ?: JsonObject(emptyMap())
        val result = dict(obj["result"]) ?: JsonObject(emptyMap())
        val data = dict(result["data"])
        if (data != null) {
            fun sortKey(key: String): Pair<Int, Int> {
                val digits = WebRegex.capture("(\\d+)$", key)?.toIntOrNull() ?: 0
                return Pair(if (key == "epi_content") 0 else 1, digits)
            }
            val keys = data.keys.filter { it.startsWith("epi_content") }.sortedWith(
                compareBy<String> { sortKey(it).first }.thenBy { sortKey(it).second }.thenBy { it },
            )
            val joined = StringBuilder()
            for (key in keys) {
                val text = stringOrNull(data[key])
                if (!text.isNullOrEmpty()) joined.append(text)
            }
            val trimmed = joined.toString().trim()
            if (trimmed.isNotEmpty()) return trimmed
        }
        val candidates: List<JsonElement?> = listOf(result["content"], result["html"], result["text"], obj["content"])
        for (candidate in candidates) {
            val text = stringOrNull(candidate) ?: continue
            if (text.trim().isNotEmpty()) return text.trim()
        }
        return ""
    }

    // Chapter HTML

    fun bestSrcset(value: String): String {
        val text = value.trim()
        if (text.startsWith("data:")) return text
        var best = ""
        var bestScore = Double.NEGATIVE_INFINITY
        for (raw in text.split(",")) {
            val bits = raw.split(' ', '\t', '\n').filter { it.isNotEmpty() }
            val first = bits.firstOrNull() ?: continue
            var score = 1.0
            if (bits.size > 1) {
                val last = bits.last().lowercase()
                val number = last.dropLast(1).toDoubleOrNull()
                if (last.endsWith("w") && number != null) {
                    score = number
                } else if (last.endsWith("x") && number != null) {
                    score = number * 10000.0
                }
            }
            if (score > bestScore) {
                best = first
                bestScore = score
            }
        }
        return best
    }

    fun normalizeUrl(value: String, base: String): String {
        val text = value.trim().replace("&amp;", "&")
        if (text.isEmpty() || text.startsWith("blob:")) return ""
        if (text.startsWith("data:")) return text
        val joined = WebHtml.join(base, text) ?: return ""
        val hash = joined.indexOf('#')
        return if (hash >= 0) joined.substring(0, hash) else joined
    }

    fun imageExtension(url: String): String {
        if (url.startsWith("data:")) {
            return when (url.split(";").firstOrNull()?.lowercase() ?: "") {
                "data:image/png" -> "png"
                "data:image/gif" -> "gif"
                "data:image/webp" -> "webp"
                "data:image/avif" -> "avif"
                "data:image/svg+xml" -> "svg"
                else -> "jpg"
            }
        }
        val path = uri(url)?.path ?: ""
        val last = path.substringAfterLast('/')
        val dot = last.lastIndexOf('.')
        val ext = if (dot > 0) last.substring(dot + 1).lowercase() else ""
        if (ext.length in 2..5 && ext.all { it.isLetterOrDigit() }) return ext
        return "jpg"
    }

    fun dataUriBytes(uri: String): ByteArray? {
        if (!uri.startsWith("data:")) return null
        val comma = uri.indexOf(',')
        if (comma < 0) return null
        val header = uri.substring(0, comma)
        val payload = uri.substring(comma + 1)
        if (header.contains(";base64")) {
            return try {
                Base64.getDecoder().decode(payload)
            } catch (e: IllegalArgumentException) {
                null
            }
        }
        return percentDecode(payload)?.toByteArray(Charsets.UTF_8)
    }

    private val backgroundPattern = Regex("url\\(\\s*([\"']?)(.*?)\\1\\s*\\)", RegexOption.IGNORE_CASE)

    /** Rewrites `url(...)` in a style string through `transform` (empty result becomes `none`). */
    fun rewriteBackgrounds(style: String, transform: (String) -> String): String =
        backgroundPattern.replace(style) { match ->
            val replaced = transform(match.groupValues[2])
            if (replaced.isEmpty()) "none" else "url(\"$replaced\")"
        }

    data class Asset(val url: String, val filename: String)

    /** Distinct remote assets in order, with the local filename assigned to each. */
    data class ProcessedChapter(val html: String, val assets: List<Asset>)

    private val lazyAttributes = listOf("data-original", "data-lazy-src", "data-src", "data-url", "data-image", "data-cfsrc")
    private val sourceAttributes = listOf("data-srcset", "srcset")

    /** Cleans the episode HTML: resolves lazy/picture images, assigns local filenames, strips scripts. */
    fun processChapterHtml(raw: String, chapterUrl: String, chapterIndex: Int): ProcessedChapter? {
        val document = WebHtml.parse(raw, chapterUrl)
        document.outputSettings().prettyPrint(false)
        val body: Element = document.body() ?: return null

        fun chooseSource(image: Element): String {
            val parent = image.parent()
            if (parent != null && parent.tagName().lowercase() == "picture") {
                for (source in WebHtml.select("source", parent)) {
                    for (attribute in sourceAttributes) {
                        val candidate = bestSrcset(WebHtml.attr(source, attribute))
                        if (candidate.isNotEmpty()) return normalizeUrl(candidate, chapterUrl)
                    }
                }
            }
            var candidate = bestSrcset(WebHtml.attr(image, "data-srcset"))
            if (candidate.isNotEmpty()) return normalizeUrl(candidate, chapterUrl)
            for (attribute in lazyAttributes) {
                candidate = WebHtml.attr(image, attribute)
                if (candidate.isNotEmpty()) return normalizeUrl(candidate, chapterUrl)
            }
            candidate = bestSrcset(WebHtml.attr(image, "srcset"))
            if (candidate.isNotEmpty()) return normalizeUrl(candidate, chapterUrl)
            return normalizeUrl(WebHtml.attr(image, "src"), chapterUrl)
        }

        val filenames = mutableMapOf<String, String>()
        val ordered = mutableListOf<Asset>()
        fun register(url: String): String {
            filenames[url]?.let { return it }
            val number = ordered.size + 1
            val name = String.format(Locale.ROOT, "images/%04d_%02d.", chapterIndex, number) + imageExtension(url)
            filenames[url] = name
            ordered.add(Asset(url, name))
            return name
        }

        for (image in WebHtml.select("img", body)) {
            val source = chooseSource(image)
            if (source.isNotEmpty()) image.attr("src", register(source))
            for (attribute in lazyAttributes + sourceAttributes) image.removeAttr(attribute)
        }
        WebHtml.removeAll("source", body)
        for (picture in WebHtml.select("picture", body)) {
            val inner = picture.html()
            picture.before(inner)
            picture.remove()
        }
        for (element in WebHtml.select("[style]", body)) {
            val style = WebHtml.attr(element, "style")
            val rewritten = rewriteBackgrounds(style) { raw ->
                val normalized = normalizeUrl(raw, chapterUrl)
                if (normalized.isEmpty()) "" else register(normalized)
            }
            if (rewritten != style) element.attr("style", rewritten)
        }
        WebHtml.removeAll("script, noscript, iframe", body)

        var html = body.html().trim()
        if (html.isEmpty()) return null
        html = WebRegex.replace("<(br|hr|img)((?:\\s[^>]*?)?)\\s*/?>", html, "<$1$2/>", RegexOption.IGNORE_CASE)
        html = WebRegex.replace("\\s+/>", html, "/>")
        html = html.replace("&nbsp;", "&#160;")
        return ProcessedChapter("<div class=\"global-npia-content\">$html</div>", ordered)
    }
}
