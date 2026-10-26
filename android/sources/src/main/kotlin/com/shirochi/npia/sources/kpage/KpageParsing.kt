package com.shirochi.npia.sources.kpage

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.sources.websupport.WebHtml
import com.shirochi.npia.sources.websupport.WebRegex
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.doubleOrNull
import org.jsoup.nodes.Document
import org.jsoup.parser.Parser
import java.net.URI
import java.net.URLDecoder
import java.util.Base64

// Pure parsing/building helpers for the kpage source (port of the desktop `_kpage_*` helpers).

// Models

internal data class KpageMeta(
    val title: String,
    val author: String,
    val cover: String,
    val description: String,
)

internal data class KpageEpisode(
    val productId: Int,
    val order: Int,
    val shortName: String,
    val fullName: String,
    val isFree: Boolean,
    val isAccessible: Boolean,
    val slideType: String,
)

internal data class KpageImageFile(
    val no: Int,
    val url: String,
    val width: Int,
    val height: Int,
    val filename: String,
)

internal sealed class KpageViewerOutcome {
    data class Locked(val reason: String) : KpageViewerOutcome()
    data class Unavailable(val reason: String) : KpageViewerOutcome()
    data class Images(val files: List<KpageImageFile>) : KpageViewerOutcome()
    data class Text(val baseUrl: String, val secureUrls: List<String>) : KpageViewerOutcome()
}

internal data class KpageParagraph(
    val plain: String,
    val html: String,
    val type: String,
    val style: JsonObject,
    val attrs: JsonObject,
    val chunkIndex: Int,
    val contentId: Int,
    val paragraphId: Int,
)

internal data class KpageOutput(val text: String, val html: String)

internal data class KpageImageChapter(val html: String, val images: List<ImageRef>)

// Loose JSON helpers (Python truthiness semantics)

internal object KpageJson {
    val parser = Json { ignoreUnknownKeys = true; isLenient = false }

    fun parse(data: ByteArray): JsonElement? =
        runCatching { parser.parseToJsonElement(data.decodeToString()) }.getOrNull()

    fun isBool(value: JsonElement?): Boolean =
        value is JsonPrimitive && value !is JsonNull && !value.isString && value.booleanOrNull != null

    fun isNumber(value: JsonElement?): Boolean =
        value is JsonPrimitive && value !is JsonNull && !value.isString && value.booleanOrNull == null && value.doubleOrNull != null

    fun truthy(value: JsonElement?): Boolean = when {
        value == null || value is JsonNull -> false
        value is JsonPrimitive && value.isString -> value.content.isNotEmpty()
        value is JsonPrimitive -> {
            val bool = value.booleanOrNull
            if (bool != null) bool else (value.doubleOrNull ?: 0.0) != 0.0
        }
        value is JsonArray -> value.isNotEmpty()
        value is JsonObject -> value.isNotEmpty()
        else -> true
    }

    /** `str(value)` for scalars; empty for anything else. */
    fun string(value: JsonElement?): String {
        if (value == null || value is JsonNull || value !is JsonPrimitive) return ""
        if (value.isString) return value.content
        val bool = value.booleanOrNull
        if (bool != null) return if (bool) "True" else "False"
        val content = value.content
        if (content.contains('.') || content.contains('e') || content.contains('E')) {
            return value.doubleOrNull?.toString() ?: ""
        }
        return content.toLongOrNull()?.toInt()?.toString() ?: (value.doubleOrNull?.toInt()?.toString() ?: "")
    }

    fun int(value: JsonElement?): Int {
        if (value == null || value is JsonNull || value !is JsonPrimitive) return 0
        if (value.isString) return value.content.trim().toIntOrNull() ?: 0
        val bool = value.booleanOrNull
        if (bool != null) return if (bool) 1 else 0
        return value.content.toLongOrNull()?.toInt() ?: (value.doubleOrNull?.toInt() ?: 0)
    }

    fun dict(value: JsonElement?): JsonObject? = value as? JsonObject

    fun list(value: JsonElement?): JsonArray? = value as? JsonArray

    /** First truthy raw value among `keys` (Python `a or b or c`). */
    fun firstTruthy(map: JsonObject, keys: List<String>): JsonElement? {
        for (key in keys) {
            if (truthy(map[key])) return map[key]
        }
        return null
    }

    fun unescape(text: String): String = runCatching { Parser.unescapeEntities(text, false) }.getOrDefault(text)

    /** Swift `dict[a] ?? dict[b]`: the first key that is present, even when it holds null. */
    fun either(map: JsonObject, vararg keys: String): JsonElement? {
        for (key in keys) {
            if (map.containsKey(key)) return map[key]
        }
        return null
    }
}

// Parsing namespace

internal object KpageParsing {
    // URL

    fun seriesId(url: String): String? = WebRegex.capture("""/content/(\d+)""", url)

    fun viewerIds(url: String): Pair<String, String>? {
        val groups = WebRegex.firstMatch("""/content/(\d+)/viewer/(\d+)""", url) ?: return null
        if (groups.size < 3) return null
        val series = groups[1] ?: return null
        val product = groups[2] ?: return null
        return Pair(series, product)
    }

    // Book metadata

    fun cleanTitle(raw: String): String =
        WebRegex.replace("\\s*[-\u2013]\\s*(웹소설|웹툰).*$", raw, "", RegexOption.IGNORE_CASE).trim()

    fun parseMetadata(html: String): KpageMeta {
        val doc = runCatching { WebHtml.parse(html) }.getOrNull() ?: return KpageMeta("", "", "", "")
        fun og(property: String): String = WebHtml.attr(WebHtml.selectFirst("meta[property=og:$property]", doc), "content")
        var title = cleanTitle(og("title"))
        if (title.isEmpty()) {
            val h2 = WebHtml.selectFirst("h2", doc)
            if (h2 != null) title = WebHtml.text(h2)
        }
        val author = findAuthor(doc, title)
        return KpageMeta(title, author, og("image"), og("description"))
    }

    private fun findAuthor(doc: Document, title: String): String {
        for (element in WebHtml.select("span, div, a", doc)) {
            val previous = element.previousElementSibling() ?: continue
            val text = WebHtml.text(element)
            val previousIsTitle = previous.tagName().lowercase() == "h2" || WebHtml.text(previous) == title
            if (previousIsTitle && text.isNotEmpty() && text.length < 50 && !text.contains("|")) {
                return text
            }
        }
        // Fallback: the first short line after the title in the page text.
        if (title.isEmpty()) return ""
        val body = doc.body() ?: return ""
        val bodyText = WebHtml.lines(body).joinToString("\n")
        val at = bodyText.indexOf(title)
        if (at < 0) return ""
        val after = bodyText.substring(at + title.length).take(200)
        val lines = after.split("\n").map { it.trim() }.filter { it.isNotEmpty() }
        val first = lines.firstOrNull()
        if (first != null && first.length < 50) return first
        return ""
    }

    // Episode list

    fun titleNumber(title: String): Int {
        val value = WebRegex.capture("(\\d+)\\s*화", title) ?: return 0
        return value.toIntOrNull() ?: 0
    }

    /** Port of `hasUserAccessFlag` from the in-page episode script. */
    fun hasUserAccessFlag(value: JsonElement): Boolean = walkAccess(value, "")

    private const val SKIP_KEY_PATTERN = "price|count|blocked|lock|free_change"
    private const val ACCESS_KEY_PATTERN = "purchas|bought|owned|rented|rental|ticket|pass|usable|useable|access|viewable|readable"
    private const val ACCESS_TRUE_PATTERN = "^(yes|y|true|1|buy|bought|purchase|purchased|yes[_-]?purchase|yes[_-]?purchased|rent|rented|rental|yes[_-]?rent|yes[_-]?rented|ticket|pass|owned|available|viewable|readable|accessible)$"
    private const val ACCESS_FALSE_PATTERN = "^(false|no|n|none|null|0|not[_-]?purchased|not[_-]?rented|no[_-]?purchase|no[_-]?rent|unowned|unavailable|expired)$"

    private fun walkAccess(value: JsonElement, path: String): Boolean {
        val entries: List<Pair<String, JsonElement>> = when (value) {
            is JsonObject -> value.entries.map { Pair(it.key, it.value) }
            is JsonArray -> value.mapIndexed { index, item -> Pair(index.toString(), item) }
            else -> return false
        }
        for ((rawKey, item) in entries) {
            val key = rawKey.lowercase()
            val full = if (path.isEmpty()) key else "$path.$key"
            if (WebRegex.matches(SKIP_KEY_PATTERN, full)) continue
            if (WebRegex.matches(ACCESS_KEY_PATTERN, full)) {
                if (KpageJson.isBool(item)) {
                    if ((item as JsonPrimitive).booleanOrNull == true) return true
                } else if (KpageJson.isNumber(item)) {
                    if (((item as JsonPrimitive).doubleOrNull ?: 0.0) > 0) return true
                } else if (item is JsonPrimitive && item.isString) {
                    val lowered = item.content.trim().lowercase()
                    if (WebRegex.matches(ACCESS_TRUE_PATTERN, lowered)) return true
                    if (WebRegex.matches(ACCESS_FALSE_PATTERN, lowered)) continue
                }
            }
            if (item is JsonObject || item is JsonArray) {
                if (walkAccess(item, full)) return true
            }
        }
        return false
    }

    fun parseEpisodes(json: ByteArray): List<KpageEpisode> {
        val root = KpageJson.parse(json) as? JsonObject ?: return emptyList()
        val result = KpageJson.dict(root["result"]) ?: JsonObject(emptyMap())
        val list = KpageJson.list(result["list"]) ?: JsonArray(emptyList())
        val episodes = mutableListOf<KpageEpisode>()
        for (entry in list) {
            val map = entry as? JsonObject ?: continue
            val item = KpageJson.dict(map["item"]) ?: JsonObject(emptyMap())
            val productId = KpageJson.int(item["product_id"])
            if (productId == 0) continue
            val order = KpageJson.int(item["order_value"])
            val fullTitle = KpageJson.string(item["title"])
            var shortName = fullTitle
            val match = WebRegex.capture("([0-9]+화.*)$", fullTitle)
            if (match != null) {
                shortName = match
            } else if (fullTitle.isEmpty()) {
                shortName = "Episode $order"
            }
            val isFree = KpageJson.truthy(item["is_free"])
            val accessible = isFree || hasUserAccessFlag(item)
            episodes += KpageEpisode(
                productId = productId, order = order, shortName = shortName, fullName = fullTitle,
                isFree = isFree, isAccessible = accessible, slideType = KpageJson.string(item["slide_type"]),
            )
        }
        return sortEpisodes(episodes)
    }

    fun sortKey(episode: KpageEpisode): Pair<Long, Int> {
        var order = episode.order
        if (order == 0) {
            order = titleNumber(if (episode.fullName.isEmpty()) episode.shortName else episode.fullName)
        }
        return Pair(if (order == 0) 1_000_000_000_000L else order.toLong(), episode.productId)
    }

    /** Oldest to newest; stable for equal keys (`sortedWith` is stable). */
    fun sortEpisodes(episodes: List<KpageEpisode>): List<KpageEpisode> =
        episodes.sortedWith(compareBy<KpageEpisode>({ sortKey(it).first }, { sortKey(it).second }))

    // Viewer data

    fun parseViewer(data: ByteArray, status: Int): KpageViewerOutcome {
        val root = KpageJson.parse(data) as? JsonObject ?: return KpageViewerOutcome.Unavailable("non-json response")
        val roots = mutableListOf(root)
        for (key in listOf("result", "data", "body")) {
            val sub = root[key] as? JsonObject
            if (sub != null) roots += sub
        }
        fun findProp(names: List<String>): JsonElement? {
            for (candidate in roots) {
                for (name in names) {
                    val value = candidate[name]
                    if (value != null && value !is JsonNull) return value
                }
            }
            return null
        }
        val viewerData = KpageJson.dict(findProp(listOf("viewerData", "viewer_data"))) ?: JsonObject(emptyMap())
        val rawBase = KpageJson.string(KpageJson.either(viewerData, "atsServerUrl", "ats_server_url"))
        var baseUrl = rawBase
        if (rawBase.lowercase().startsWith("http:")) {
            baseUrl = "https:" + rawBase.substring(5)
        }
        val contents: JsonArray = KpageJson.list(viewerData["contentsList"])
            ?: KpageJson.list(viewerData["contents_list"])
            ?: KpageJson.list(viewerData["contents"]) ?: JsonArray(emptyList())
        val imageData: JsonObject = KpageJson.dict(viewerData["imageDownloadData"])
            ?: KpageJson.dict(viewerData["image_download_data"]) ?: JsonObject(emptyMap())
        val imageFiles: JsonArray = KpageJson.list(imageData["files"])
            ?: KpageJson.list(imageData["fileList"])
            ?: KpageJson.list(imageData["file_list"])
            ?: KpageJson.list(viewerData["imageFiles"])
            ?: KpageJson.list(viewerData["image_files"]) ?: JsonArray(emptyList())

        val message = findProp(listOf("message", "msg"))
        val messageKey = findProp(listOf("message_key", "messageKey"))
        val resultCode = findProp(listOf("result_code", "resultCode"))
        val messageText = KpageJson.string(message)
        val keyText = KpageJson.string(messageKey)
        val accessText = "$messageText $keyText".lowercase()
        val denyPattern = "(not[_-]?purchased|purchase|rental|login|adult|age|auth|permission|forbidden|locked|구매|대여|로그인|성인|권한|인증|열람)"
        if (status == 401 || status == 403 || WebRegex.matches(denyPattern, accessText, RegexOption.IGNORE_CASE)) {
            val reason = if (keyText.isNotEmpty()) keyText else if (messageText.isNotEmpty()) messageText else "access denied"
            return KpageViewerOutcome.Locked(reason)
        }

        if ((baseUrl.isEmpty() || contents.isEmpty()) && imageFiles.isEmpty()) {
            val code = KpageJson.string(resultCode)
            return KpageViewerOutcome.Unavailable(
                if (code.isEmpty()) "missing viewer resources" else "api returned no viewer resources (code $code)",
            )
        }

        if (imageFiles.isNotEmpty()) {
            val files = mutableListOf<KpageImageFile>()
            imageFiles.forEachIndexed { offset, entry ->
                val map = entry as? JsonObject ?: return@forEachIndexed
                val url = KpageJson.string(KpageJson.either(map, "secureUrl", "secure_url"))
                if (url.isEmpty()) return@forEachIndexed
                val number = KpageJson.int(map["no"])
                files += KpageImageFile(
                    no = if (number != 0) number else offset + 1,
                    url = url,
                    width = KpageJson.int(map["width"]),
                    height = KpageJson.int(map["height"]),
                    filename = WebRegex.capture("filename=([^&]+)", url) ?: "",
                )
            }
            return KpageViewerOutcome.Images(files)
        }

        val urls = mutableListOf<String>()
        for (entry in contents) {
            val map = entry as? JsonObject ?: continue
            val secure = KpageJson.string(KpageJson.either(map, "secureUrl", "secure_url"))
            if (secure.isNotEmpty()) urls += secure
        }
        return KpageViewerOutcome.Text(baseUrl, urls)
    }

    // Paragraph extraction

    private fun colorValue(style: JsonObject, keys: List<String>): String {
        var color = KpageJson.string(KpageJson.firstTruthy(style, keys))
        if (color.isNotEmpty() && !color.startsWith("#") && !color.startsWith("rgb")) color = "#$color"
        return color
    }

    private fun sizeValue(style: JsonObject): String? {
        val size = KpageJson.firstTruthy(style, listOf("fontSize", "size")) ?: return null
        return if (KpageJson.isNumber(size)) "font-size:${KpageJson.string(size)}px" else "font-size:${KpageJson.string(size)}"
    }

    private fun styleToCss(style: JsonObject): String {
        val parts = mutableListOf<String>()
        val color = colorValue(style, listOf("color", "fontColor", "textColor"))
        if (color.isNotEmpty()) parts += "color:$color"
        val background = colorValue(style, listOf("backgroundColor", "highlight"))
        if (background.isNotEmpty()) parts += "background-color:$background"
        sizeValue(style)?.let { parts += it }
        val align = KpageJson.string(KpageJson.firstTruthy(style, listOf("textAlign", "align")))
        if (align.isNotEmpty()) parts += "text-align:${align.lowercase()}"
        val decoration = KpageJson.string(
            KpageJson.firstTruthy(style, listOf("textDecoration", "text-decoration", "textDecorationLine", "text-decoration-line")),
        ).lowercase()
        val lineThrough = KpageJson.truthy(style["lineThrough"]) || KpageJson.truthy(style["line-through"]) ||
            KpageJson.truthy(style["strikethrough"]) || KpageJson.truthy(style["strike"]) ||
            decoration.contains("line-through")
        if (lineThrough) parts += "text-decoration:line-through"
        return parts.joinToString("; ")
    }

    private fun rawString(value: JsonElement?): String? =
        (value as? JsonPrimitive)?.takeIf { it.isString }?.content

    private fun applyTags(fragment: String, style: JsonObject): String {
        var result = fragment
        if (KpageJson.truthy(style["bold"]) || rawString(style["fontWeight"]) == "bold") result = "<b>$result</b>"
        if (KpageJson.truthy(style["italic"]) || rawString(style["fontStyle"]) == "italic") result = "<i>$result</i>"
        if (KpageJson.truthy(style["underline"])) result = "<u>$result</u>"
        return result
    }

    private fun wrapWithStyle(fragment: String, style: JsonObject, attrs: JsonObject?): String {
        val cssParts = mutableListOf<String>()
        val attrStyle = KpageJson.string(attrs?.get("style")).trim()
        if (attrStyle.isNotEmpty()) cssParts += attrStyle.trimEnd(';')
        val css = styleToCss(style)
        if (css.isNotEmpty()) cssParts += css
        var result = applyTags(fragment, style)
        var attrParts = ""
        if (attrs != null) {
            val cls = KpageJson.string(KpageJson.firstTruthy(attrs, listOf("class", "className")))
            if (cls.isNotEmpty()) attrParts += " class=\"${WebHtml.escape(cls)}\""
        }
        if (cssParts.isNotEmpty()) attrParts += " style=\"${WebHtml.escape(cssParts.joinToString("; "))}\""
        if (attrParts.isNotEmpty()) result = "<span$attrParts>$result</span>"
        return result
    }

    private fun safeEscape(text: String): String = WebHtml.escape(KpageJson.unescape(text))

    private fun children(node: JsonObject): List<JsonObject> =
        (KpageJson.list(node["childParagraphList"]) ?: JsonArray(emptyList())).mapNotNull { it as? JsonObject }

    private fun collectHtml(node: JsonObject, isRoot: Boolean): String {
        val nodeType = KpageJson.string(node["type"]).uppercase()
        if (nodeType == "BR") return "<br />"
        if (nodeType == "IMG") return ""
        val text = rawString(node["text"]) ?: ""
        val kids = children(node)
        val style = KpageJson.dict(node["style"]) ?: JsonObject(emptyMap())
        val attrs = KpageJson.dict(node["attributes"]) ?: JsonObject(emptyMap())
        val strike = nodeType == "S" || nodeType == "STRIKE" || nodeType == "DEL"

        if (text.isNotEmpty() && kids.isEmpty()) {
            var fragment = wrapWithStyle(safeEscape(text), style, if (isRoot) null else attrs)
            if (strike) fragment = "<s>$fragment</s>"
            return fragment
        }
        val parts = mutableListOf<String>()
        if (text.isNotEmpty()) parts += safeEscape(text)
        for (child in kids) {
            val piece = collectHtml(child, false)
            if (piece.isNotEmpty()) parts += piece
        }
        var result = wrapWithStyle(parts.joinToString(""), style, if (isRoot) null else attrs)
        if (strike) result = "<s>$result</s>"
        return result
    }

    private fun collectText(node: JsonObject): List<String> {
        val parts = mutableListOf<String>()
        val text = (rawString(node["text"]) ?: "").trim()
        if (text.isNotEmpty()) parts += KpageJson.unescape(text)
        for (child in children(node)) parts += collectText(child)
        return parts
    }

    private fun nodeHasImage(node: JsonObject): Boolean {
        if (KpageJson.string(node["type"]).uppercase() == "IMG") return true
        return children(node).any { nodeHasImage(it) }
    }

    /** Port of `_kpage_extract_from_json` (without the CSS download). */
    fun extractParagraphs(chunks: List<ByteArray>, skipLastPage: Boolean, keepFiller: Boolean): List<KpageParagraph> {
        val parsed = mutableListOf<Pair<Int, List<KpageParagraph>>>()
        chunks.forEachIndexed { chunkIndex, raw ->
            val root = KpageJson.parse(raw) as? JsonObject ?: return@forEachIndexed
            val info = KpageJson.dict(root["contentInfo"]) ?: JsonObject(emptyMap())
            val contentId = KpageJson.int(info["contentId"])
            val list = KpageJson.list(info["paragraphList"]) ?: JsonArray(emptyList())
            val chunkParagraphs = mutableListOf<KpageParagraph>()
            for (entry in list) {
                val paragraph = entry as? JsonObject ?: continue
                val plain = collectText(paragraph).joinToString("")
                val fragment = collectHtml(paragraph, true)
                val hasText = plain.trim().isNotEmpty()
                if (nodeHasImage(paragraph) && !hasText) continue
                if (hasText || fragment.lowercase().contains("<br")) {
                    chunkParagraphs += KpageParagraph(
                        plain = plain, html = fragment,
                        type = KpageJson.string(paragraph["type"]).uppercase(),
                        style = KpageJson.dict(paragraph["style"]) ?: JsonObject(emptyMap()),
                        attrs = KpageJson.dict(paragraph["attributes"]) ?: JsonObject(emptyMap()),
                        chunkIndex = chunkIndex, contentId = contentId,
                        paragraphId = KpageJson.int(paragraph["id"]),
                    )
                }
            }
            if (chunkParagraphs.isNotEmpty()) parsed += Pair(chunkIndex, chunkParagraphs)
        }

        var skipLastIndex: Int? = null
        if (skipLastPage && !keepFiller && parsed.size > 1) {
            skipLastIndex = parsed[parsed.size - 1].first
        }
        val all = mutableListOf<KpageParagraph>()
        for ((index, paragraphs) in parsed) {
            if (!keepFiller) {
                if (skipLastIndex != null && skipLastIndex == index) continue
                val combined = paragraphs.joinToString(" ") { it.plain }
                if (isColophonChunk(combined)) continue
            }
            all += paragraphs
        }
        return all.sortedWith(compareBy<KpageParagraph>({ it.chunkIndex }, { it.contentId }, { it.paragraphId }))
    }

    // Colophon

    private fun compact(text: String): String = text.filterNot { it.isWhitespace() }

    fun isColophonChunk(text: String): Boolean {
        val compactText = compact(text)
        val lower = text.lowercase()
        val lowerCompact = compactText.lowercase()
        val strong = listOf("ISBN", "UCI", "ⓒ", "©", "저작권법", "재가공할 수 없습니다", "서면 허락", "이 책의 내용을 이용하지 못합니다")
        val publisher = listOf("발행인", "발행처", "펴낸곳", "펴낸 곳", "기획/편집", "기획 / 편집", "책임편집", "표지", "주소")
        val contact = listOf("블로그", "트위터", "투고", "blog.\u006e\u0061\u0076\u0065\u0072.com", "dreambook", "samyangcnc")

        fun has(marker: String): Boolean {
            val markerCompact = compact(marker)
            return text.contains(marker) || compactText.contains(markerCompact) ||
                lower.contains(marker.lowercase()) || lowerCompact.contains(markerCompact.lowercase())
        }
        val strongHits = strong.count { has(it) }
        val publisherHits = publisher.count { has(it) }
        val contactHits = contact.count { has(it) }
        if (strongHits >= 1 && (publisherHits + contactHits) >= 2) return true
        if (publisherHits >= 3 && contactHits >= 1) return true
        if (publisherHits >= 2 && contactHits >= 2) return true
        return false
    }

    // Output

    /** `_kpage_strip_headings`: drops leading blank and "제N화" heading paragraphs. */
    fun stripHeadings(paragraphs: List<KpageParagraph>): List<KpageParagraph> {
        val cleaned = paragraphs.toMutableList()
        while (cleaned.isNotEmpty()) {
            val text = cleaned.first().plain.trim()
            if (text.isEmpty() || text == "&nbsp;") {
                cleaned.removeAt(0)
            } else if (WebRegex.matches("^제\\d+화\\.?$", text)) {
                cleaned.removeAt(0)
            } else {
                break
            }
        }
        return cleaned
    }

    fun headingTitle(paragraphs: List<KpageParagraph>, fallback: String): String {
        val headingTypes = setOf("h1", "h2", "h3", "h4", "h5", "h6", "head", "heading", "title")
        for (paragraph in paragraphs) {
            val plain = paragraph.plain.trim()
            if (plain.isNotEmpty() && paragraph.type.lowercase() in headingTypes) return plain
        }
        return fallback
    }

    private fun tagFor(type: String): String {
        val tag = type.lowercase()
        val direct = setOf("h1", "h2", "h3", "h4", "h5", "h6", "p", "div", "blockquote", "li")
        if (tag in direct) return tag
        if (tag == "head" || tag == "heading" || tag == "title") return "h3"
        return "p"
    }

    private fun attrsToHtml(attrs: JsonObject, style: JsonObject, extraClass: String): String {
        var result = ""
        var cls = KpageJson.string(KpageJson.firstTruthy(attrs, listOf("class", "className")))
        if (extraClass.isNotEmpty()) cls = "$cls $extraClass".trim()
        if (cls.isNotEmpty()) result += " class=\"${WebHtml.escape(cls)}\""

        val parts = mutableListOf<String>()
        val rawStyle = KpageJson.string(attrs["style"]).trim()
        if (rawStyle.isNotEmpty()) parts += rawStyle.trimEnd(';')
        val attrAlign = KpageJson.string(attrs["align"])
        if (attrAlign.isNotEmpty()) parts += "text-align:${attrAlign.lowercase()}"

        val color = colorValue(style, listOf("color", "fontColor", "textColor"))
        if (color.isNotEmpty()) parts += "color:$color"
        val background = colorValue(style, listOf("backgroundColor", "highlight"))
        if (background.isNotEmpty()) parts += "background-color:$background"
        sizeValue(style)?.let { parts += it }
        val textAlign = KpageJson.string(KpageJson.firstTruthy(style, listOf("textAlign", "align")))
        if (textAlign.isNotEmpty()) parts += "text-align:${textAlign.lowercase()}"
        if (KpageJson.truthy(style["lineThrough"]) || KpageJson.truthy(style["strikethrough"]) || KpageJson.truthy(style["strike"])) {
            parts += "text-decoration:line-through"
        }
        val nonEmpty = parts.filter { it.isNotEmpty() }
        if (nonEmpty.isNotEmpty()) result += " style=\"${WebHtml.escape(nonEmpty.joinToString("; "))}\""
        return result
    }

    const val PAGE_BREAK = "<div class=\"kpage-page-break\">&#160;</div>"

    /** `_kpage_build_output`: plain text and XHTML fragment. */
    fun buildOutput(paragraphs: List<KpageParagraph>): KpageOutput {
        val textParts = mutableListOf<String>()
        val htmlParts = mutableListOf<String>()
        var previousChunk: Int? = null
        var firstHeadingSeen = false
        var pendingHeadingBreak = false
        var insertedHeadingBreak = false

        fun appendBreak() {
            if (htmlParts.isNotEmpty() && htmlParts[htmlParts.size - 1] != PAGE_BREAK) htmlParts += PAGE_BREAK
        }

        for (paragraph in paragraphs) {
            val hasText = paragraph.plain.trim().isNotEmpty()
            val hasBreak = paragraph.html.lowercase().contains("<br")
            if (!hasText && !hasBreak) continue
            val tag = tagFor(paragraph.type)
            val previous = previousChunk
            if (previous != null && previous != paragraph.chunkIndex) appendBreak()
            val isBlank = !hasText && hasBreak
            val isHeading = tag in setOf("h1", "h2", "h3", "h4", "h5", "h6")
            if (pendingHeadingBreak && !insertedHeadingBreak && !isBlank && !isHeading) {
                appendBreak()
                insertedHeadingBreak = true
                pendingHeadingBreak = false
            }
            var extraClass = ""
            if (isHeading && !firstHeadingSeen) {
                firstHeadingSeen = true
                pendingHeadingBreak = true
                extraClass = "kpage-source-heading"
            }
            textParts += paragraph.plain
            val attributes = attrsToHtml(paragraph.attrs, paragraph.style, extraClass)
            htmlParts += "<$tag$attributes>${paragraph.html}</$tag>"
            previousChunk = paragraph.chunkIndex
        }
        return KpageOutput(textParts.joinToString("\n"), htmlParts.joinToString("\n"))
    }

    // Image chapters

    private fun pad(number: Int, width: Int): String = number.toString().padStart(width, '0')

    private fun percentDecode(text: String): String =
        runCatching { URLDecoder.decode(text.replace("+", "%2B"), "UTF-8") }.getOrDefault(text)

    /** Output file extension for an image file entry (defaults to jpg). */
    fun imageExtension(file: KpageImageFile): String {
        var name = percentDecode(file.filename)
        name = name.split("?")[0].split("&")[0]
        name = WebRegex.replace("[^A-Za-z0-9._-]+", name, "_")
        name = name.trim('.', '_')
        val dot = name.lastIndexOf('.')
        if (dot < 0) return "jpg"
        val ext = name.substring(dot + 1).lowercase()
        if (ext.isNotEmpty() && ext.length <= 5 && WebRegex.matches("^[a-z0-9]+$", ext)) return ext
        return "jpg"
    }

    private fun looseEncode(text: String): String {
        val builder = StringBuilder()
        for (character in text) {
            if (character.code < 0x80 && character != ' ' && character != '"' && character != '<' && character != '>' &&
                character != '\\' && character != '^' && character != '`' && character != '{' && character != '|' && character != '}'
            ) {
                builder.append(character)
            } else {
                character.toString().toByteArray(Charsets.UTF_8).forEach { byte ->
                    builder.append('%').append(String.format("%02X", byte.toInt() and 0xFF))
                }
            }
        }
        return builder.toString()
    }

    /** `_kpage_build_image_chapter`: html and images with local file names. */
    fun buildImageChapter(files: List<KpageImageFile>, chapterName: String, chapterIndex: Int, baseUrl: String? = null): KpageImageChapter? {
        val ordered = files.sortedWith(compareBy<KpageImageFile>({ it.no }, { it.url }))
        val images = mutableListOf<ImageRef>()
        val htmlParts = mutableListOf("<div class=\"kpage-image-chapter\" style=\"text-align:center\">")
        ordered.forEachIndexed { offset, file ->
            val position = offset + 1
            val imageUrlText = KpageJson.unescape(file.url.trim())
            if (imageUrlText.isEmpty()) return@forEachIndexed
            val resolved: URI? = runCatching { URI(imageUrlText) }.getOrNull()
                ?: runCatching { URI(looseEncode(imageUrlText)) }.getOrNull()
            var resolvedText: String? = resolved?.toString()
            if (resolved != null && resolved.scheme == null && baseUrl != null) {
                resolvedText = WebHtml.join(baseUrl, imageUrlText)
            }
            val imageUrl = resolvedText ?: return@forEachIndexed
            val local = "images/${pad(chapterIndex, 4)}_${pad(position, 2)}.${imageExtension(file)}"
            images += ImageRef(url = imageUrl, filename = local)
            val alt = "$chapterName image $position"
            var sizeAttrs = ""
            if (file.width > 0) sizeAttrs += " width=\"${file.width}\""
            if (file.height > 0) sizeAttrs += " height=\"${file.height}\""
            htmlParts += "<div class=\"kpage-image-page\" style=\"margin:0 auto 0.5rem\">" +
                "<img src=\"${WebHtml.escape(local)}\" alt=\"${WebHtml.escape(alt)}\"$sizeAttrs" +
                " style=\"display:block;max-width:100%;height:auto;margin:0 auto\"/></div>"
        }
        htmlParts += "</div>"
        if (images.isEmpty()) return null
        return KpageImageChapter(htmlParts.joinToString("\n"), images)
    }

    // Duplicate boilerplate images

    private fun removeImagePage(html: String, filename: String): String {
        val escaped = Regex.escape(WebHtml.escape(filename))
        val wrapper = "\\s*<div class=\"kpage-image-page\"[^>]*>\\s*<img\\b[^>]*\\bsrc=[\"']$escaped[\"'][^>]*/?>\\s*</div>"
        var result = WebRegex.replace(wrapper, html, "", RegexOption.IGNORE_CASE)
        val bare = "\\s*<img\\b[^>]*\\bsrc=[\"']$escaped[\"'][^>]*/?>"
        result = WebRegex.replace(bare, result, "", RegexOption.IGNORE_CASE)
        return result
    }

    /**
     * Port of the desktop duplicate cover/boilerplate image removal: images among the first
     * `maxPositions` of a chapter whose bytes repeat in another chapter are removed from every
     * later chapter (the first copy in reading order is kept). Needs `ImageRef.data`.
     */
    fun removeDuplicateLeadingImages(contents: List<ChapterContent>, maxPositions: Int): List<ChapterContent> {
        val limit = maxPositions.coerceIn(1, 10)
        val occurrences = LinkedHashMap<String, MutableList<Pair<Int, String>>>()
        contents.forEachIndexed { chapterOffset, content ->
            for (image in content.images.take(limit)) {
                val bytes = image.data ?: continue
                if (bytes.isEmpty()) continue
                val key = Base64.getEncoder().encodeToString(bytes)
                occurrences.getOrPut(key) { mutableListOf() } += Pair(chapterOffset, image.filename)
            }
        }
        val duplicateNames = mutableSetOf<String>()
        for (items in occurrences.values) {
            val chapterCount = items.map { it.first }.toSet().size
            if (chapterCount < 2) continue
            for (item in items.drop(1)) duplicateNames += item.second
        }
        if (duplicateNames.isEmpty()) return contents
        return contents.map { content ->
            var html = content.html
            for (name in duplicateNames) {
                if (content.images.any { it.filename == name }) html = removeImagePage(html, name)
            }
            content.copy(html = html, images = content.images.filter { it.filename !in duplicateNames })
        }
    }
}
