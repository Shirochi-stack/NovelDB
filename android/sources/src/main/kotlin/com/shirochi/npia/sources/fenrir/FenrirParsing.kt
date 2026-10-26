package com.shirochi.npia.sources.fenrir

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.websupport.WebHtml
import com.shirochi.npia.sources.websupport.WebRegex
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.nodes.TextNode
import org.jsoup.parser.Parser
import java.net.URI
import java.text.Normalizer
import java.time.Duration
import java.time.Instant
import java.time.ZonedDateTime
import java.time.format.DateTimeFormatter
import java.util.Locale

/** One ordered piece of reader content. */
internal sealed class FenrirBlock {
    data class Text(val value: String) : FenrirBlock()
    data class Image(val url: String) : FenrirBlock()
}

/** Pure parsing helpers for the fenrir source (port of `_fenrir_*` in external_scraper.py). */
internal object FenrirParsing {
    const val ORIGIN = "https://fenrirealm.com"
    val hosts = setOf("fenrirealm.com", "www.fenrirealm.com")
    const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"

    /** `_FENRIR_HIDDEN_CSS`. */
    const val HIDDEN_CSS = """clip\s*:\s*rect\(\s*0|clip-path\s*:\s*inset\(\s*50%|display\s*:\s*none|visibility\s*:\s*hidden|font-size\s*:\s*0(?:px)?\b"""

    /** `_FENRIR_INVISIBLE_CHARS`. */
    const val INVISIBLE_PATTERN = "[\u00AD\u200B-\u200F\u202A-\u202E\u2060-\u2064\uFEFF]"

    // URLs

    /** `(series_slug, chapter_slug)` for a fenrir URL; empty strings when it is not one. */
    fun urlParts(url: String): Pair<String, String> {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return "" to ""
        val host = uri.host?.lowercase() ?: return "" to ""
        if (host !in hosts) return "" to ""
        val parts = (uri.path ?: "").split("/").filter { it.isNotEmpty() }
        if (parts.size < 2 || parts[0] != "series") return "" to ""
        return parts[1] to (if (parts.size > 2) parts[2] else "")
    }

    fun isFenrirUrl(url: String): Boolean = urlParts(url).first.isNotEmpty()

    /** Chapter refs carry the reader id after a pipe: `<slug>|<uuid or id>`. */
    fun refId(slug: String, chapterId: String): String = "$slug|$chapterId"

    /** `(slug, chapterId)`. */
    fun splitRefId(id: String): Pair<String, String> {
        val bar = id.lastIndexOf('|')
        if (bar < 0) return id to ""
        return id.substring(0, bar) to id.substring(bar + 1)
    }

    /** Percent-encodes everything outside the URL path character set. */
    fun encodePath(value: String): String {
        val allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~!$&'()*+,;=:@/"
        val result = StringBuilder()
        for (byte in value.toByteArray(Charsets.UTF_8)) {
            val code = byte.toInt() and 0xFF
            val char = code.toChar()
            if (code < 0x80 && allowed.indexOf(char) >= 0) {
                result.append(char)
            } else {
                result.append('%').append(String.format(Locale.ROOT, "%02X", code))
            }
        }
        return result.toString()
    }

    // JSON value helpers (Python truthiness and str())

    fun isTruthy(value: JsonElement?): Boolean {
        if (value == null || value is JsonNull) return false
        return when (value) {
            is JsonPrimitive -> when {
                value.isString -> value.content.isNotEmpty()
                value.booleanOrNull != null -> value.booleanOrNull == true
                else -> (value.content.toDoubleOrNull() ?: 0.0) != 0.0
            }
            is JsonArray -> value.isNotEmpty()
            is JsonObject -> value.isNotEmpty()
        }
    }

    fun string(value: JsonElement?): String {
        if (value == null || value is JsonNull || value !is JsonPrimitive) return ""
        if (value.isString) return value.content
        val bool = value.booleanOrNull
        if (bool != null) return if (bool) "1" else "0"
        val number = value.content.toDoubleOrNull() ?: return ""
        if (number == Math.rint(number) && Math.abs(number) < 1e15) return number.toLong().toString()
        return number.toString()
    }

    fun double(value: JsonElement?): Double {
        if (value == null || value is JsonNull || value !is JsonPrimitive) return 0.0
        if (value.isString) return value.content.trim().toDoubleOrNull() ?: 0.0
        val bool = value.booleanOrNull
        if (bool != null) return if (bool) 1.0 else 0.0
        return value.content.toDoubleOrNull() ?: 0.0
    }

    fun integer(value: JsonElement?): Int? {
        if (value == null || value !is JsonPrimitive || value.isString || value is JsonNull) return null
        val number = value.content.toDoubleOrNull() ?: return null
        if (number != Math.rint(number) || Math.abs(number) >= 1e15) return null
        return number.toInt()
    }

    fun obj(value: JsonElement?): JsonObject = value as? JsonObject ?: JsonObject(emptyMap())

    fun collapseSpaces(value: String): String = WebRegex.replace("""\s+""", value, " ").trim()

    // Chapter rows

    fun chapterSlug(row: JsonObject): String {
        var slug = string(row["slug"]).trim()
        val number = row["number"]
        if (slug.isEmpty() && number != null && number !is JsonNull) {
            val numberText = string(number)
            slug = if (isTruthy(row["part"])) "$numberText.${string(row["part"])}" else numberText
        }
        return slug
    }

    fun chapterName(row: JsonObject): String {
        var name = collapseSpaces(string(row["name"]))
        val title = collapseSpaces(string(row["title"]))
        if (title.isNotEmpty() && !name.lowercase().contains(title.lowercase())) {
            name = if (name.isEmpty()) title else "$name - $title"
        }
        if (name.isEmpty()) return "Chapter ${string(row["number"])}".trim()
        return name
    }

    /** True when a chapter costs coins and this account has not got it. */
    fun paidLocked(row: JsonObject): Boolean {
        val locked = obj(row["locked"])
        val bought = obj(row["bought"])
        val price = double(locked["price"])
        return price > 0 &&
            !isTruthy(locked["unlocked_at"]) &&
            !isTruthy(bought["bought_at"]) &&
            !isTruthy(row["bypass"])
    }

    // Book

    /** Builds the book from the series info object and the chapter rows. Null when no chapter has a slug. */
    fun makeBook(info: JsonObject, rows: List<JsonObject>, slug: String): BookInfo? {
        val ordered: List<JsonObject> = rows.withIndex().sortedWith(
            Comparator { left, right ->
                val leftIndex: Double = integer(left.value["index"])?.toDouble() ?: 1e9
                val rightIndex: Double = integer(right.value["index"])?.toDouble() ?: 1e9
                if (leftIndex != rightIndex) return@Comparator leftIndex.compareTo(rightIndex)
                val leftNumber = double(left.value["number"])
                val rightNumber = double(right.value["number"])
                if (leftNumber != rightNumber) return@Comparator leftNumber.compareTo(rightNumber)
                left.index.compareTo(right.index)
            },
        ).map { it.value }

        val seriesUrl = "$ORIGIN/series/$slug"
        val chapters = mutableListOf<ChapterRef>()
        for (row in ordered) {
            val rowSlug = chapterSlug(row)
            if (rowSlug.isEmpty()) continue
            var chapterId = string(row["uuid"])
            if (chapterId.isEmpty()) chapterId = string(row["id"])
            chapters += ChapterRef(
                id = refId(rowSlug, chapterId),
                index = chapters.size + 1,
                title = chapterName(row),
                url = "$seriesUrl/${encodePath(rowSlug)}",
                isPaid = paidLocked(row),
            )
        }
        if (chapters.isEmpty()) return null

        val descriptionHtml = string(info["description"]).trim()
        val description = htmlBlocks(descriptionHtml, "$ORIGIN/")
            .filterIsInstance<FenrirBlock.Text>()
            .joinToString("\n") { it.value }
        var cover = string(info["cover"]).trim()
        if (cover.isNotEmpty()) WebHtml.join("$ORIGIN/", cover)?.let { cover = it }
        val user = obj(info["user"])
        var author = string(user["name"]).trim()
        if (author.isEmpty()) author = string(user["username"]).trim()
        if (author.isEmpty()) author = "Unknown"
        fun names(key: String): List<String> {
            val array = info[key] as? JsonArray ?: return emptyList()
            return array.mapNotNull { item ->
                val dict = item as? JsonObject ?: return@mapNotNull null
                if (!isTruthy(dict["name"])) null else string(dict["name"]).trim()
            }
        }
        val genres = names("genres")
        val tags = genres + names("tags").filter { it !in genres }
        var title = string(info["title"]).trim()
        if (title.isEmpty()) title = slug
        val status = string(info["status"])
        return BookInfo(
            source = SourceId.FENRIR,
            id = slug,
            url = seriesUrl,
            title = title,
            author = author,
            synopsis = description.ifEmpty { null },
            coverUrl = cover.ifEmpty { null },
            tags = tags,
            status = status.ifEmpty { null },
            language = "en",
            chapters = chapters,
        )
    }

    // Content

    fun stripInvisible(value: String): String = WebRegex.replace(INVISIBLE_PATTERN, value, "")

    /** Drops hidden anti-copy blocks (CSS-hidden classes, aria-hidden, inline hidden styles). */
    fun cleanHtml(content: String): String {
        val doc = runCatching { WebHtml.parse(content) }.getOrNull() ?: return stripInvisible(content)
        doc.outputSettings().prettyPrint(false)
        val body = doc.body() ?: return stripInvisible(content)
        val hidden = mutableSetOf<String>()
        for (style in WebHtml.select("style", doc)) {
            val css = style.data()
            for (rule in WebRegex.allMatches("""([^{}]+)\{([^{}]*)\}""", css)) {
                if (rule.size < 3) continue
                val selector = rule[1] ?: continue
                val ruleBody = rule[2] ?: continue
                if (!WebRegex.matches(HIDDEN_CSS, ruleBody, RegexOption.IGNORE_CASE)) continue
                for (match in WebRegex.allMatches("""\.([\w-]+)""", selector)) {
                    if (match.size > 1) match[1]?.let { hidden += it }
                }
            }
            runCatching { style.remove() }
        }
        for (node in WebHtml.select("*", body)) {
            if (node === body) continue
            val classes = WebHtml.attr(node, "class").split(' ', '\t', '\n').filter { it.isNotEmpty() }.toSet()
            val inline = node.attr("style")
            val isHidden = classes.any { it in hidden } ||
                WebHtml.attr(node, "aria-hidden") == "true" ||
                node.hasAttr("hidden") ||
                WebRegex.matches(HIDDEN_CSS, inline, RegexOption.IGNORE_CASE)
            if (isHidden && node.parent() != null) runCatching { node.remove() }
        }
        return stripInvisible(body.html())
    }

    private val blockTags = setOf(
        "p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "tr", "section", "article", "center", "pre",
    )

    private fun isHttp(url: String): Boolean {
        val scheme = runCatching { URI(url).scheme }.getOrNull()?.lowercase()
        return scheme == "http" || scheme == "https"
    }

    /** `_reader_html_blocks`: ordered text and image blocks; raw newlines in text are line breaks. */
    fun htmlBlocks(fragment: String, base: String = ""): List<FenrirBlock> {
        val doc = runCatching { WebHtml.parse(fragment) }.getOrNull() ?: return emptyList()
        val body = doc.body() ?: return emptyList()
        for (tag in listOf("script", "style", "noscript", "button")) WebHtml.removeAll(tag, body)
        val blocks = mutableListOf<FenrirBlock>()
        val line = StringBuilder()

        fun flush() {
            var text = line.toString().replace('\u00A0', ' ')
            text = WebRegex.replace("[ \\t\\r\\f\\x0B]+", text, " ").trim()
            line.setLength(0)
            if (text.isNotEmpty()) blocks += FenrirBlock.Text(text)
        }

        fun walk(node: Node) {
            for (child in node.childNodes()) {
                if (child is TextNode) {
                    val parts = child.wholeText.split("\n")
                    parts.forEachIndexed { offset, part ->
                        if (offset > 0) flush()
                        line.append(part)
                    }
                } else if (child is Element) {
                    val name = child.tagName().lowercase()
                    if (name == "br") {
                        flush()
                    } else if (name == "img") {
                        var source = WebHtml.attr(child, "data-src")
                        if (source.isEmpty()) source = WebHtml.attr(child, "data-original")
                        if (source.isEmpty()) source = WebHtml.attr(child, "src")
                        val url = if (source.isEmpty()) null else WebHtml.join(base.ifEmpty { null }, source)
                        if (url != null && isHttp(url)) {
                            flush()
                            blocks += FenrirBlock.Image(url)
                        }
                    } else if (name in blockTags) {
                        flush()
                        walk(child)
                        flush()
                    } else {
                        walk(child)
                    }
                }
            }
        }
        walk(body)
        flush()
        return blocks
    }

    fun plainBlocks(text: String): List<FenrirBlock> =
        text.replace("\r\n", "\n").split("\n")
            .map { it.replace('\u00A0', ' ').trim() }
            .filter { it.isNotEmpty() }
            .map { FenrirBlock.Text(it) }

    /** Decodes a reader document (ProseMirror style) keeping paragraph boundaries. */
    fun documentBlocks(root: JsonObject): List<FenrirBlock> {
        val blocks = mutableListOf<FenrirBlock>()
        val line = StringBuilder()
        fun flush() {
            val text = stripInvisible(line.toString()).trim()
            line.setLength(0)
            if (text.isNotEmpty()) blocks += FenrirBlock.Text(text)
        }
        fun walk(item: JsonElement?) {
            val node = item as? JsonObject ?: return
            val kind = string(node["type"])
            val boundary = kind in setOf("paragraph", "heading", "blockquote", "listItem", "codeBlock")
            if (boundary) flush()
            when (kind) {
                "text" -> line.append(string(node["text"]))
                "hardBreak" -> flush()
                "image" -> {
                    flush()
                    val src = string(obj(node["attrs"])["src"])
                    val url = if (src.isEmpty()) null else WebHtml.join("$ORIGIN/", src)
                    if (url != null && isHttp(url)) blocks += FenrirBlock.Image(url)
                }
            }
            (node["content"] as? JsonArray)?.forEach { walk(it) }
            if (boundary) flush()
        }
        walk(root)
        flush()
        return blocks
    }

    /** `casefold(NFKC(unescape(value)))` without whitespace, for comparing titles. */
    fun normalizeTitle(value: String): String {
        val unescaped = runCatching { Parser.unescapeEntities(value, false) }.getOrDefault(value)
        val folded = Normalizer.normalize(unescaped, Normalizer.Form.NFKC)
        return WebRegex.replace("""\s+""", folded, "").lowercase()
    }

    fun imageExtension(url: String): String {
        val path = runCatching { URI(url).path }.getOrNull() ?: ""
        val last = path.substringAfterLast('/')
        val ext = if (last.contains('.')) last.substringAfterLast('.').lowercase() else ""
        return if (ext in setOf("jpg", "jpeg", "png", "gif", "webp", "avif", "svg")) ext else "jpg"
    }

    /** `_reader_chapter_result` for the output: paragraphs plus image references. */
    fun chapterContent(input: List<FenrirBlock>, ref: ChapterRef): ChapterContent? {
        val blocks = input.toMutableList()
        val first = blocks.firstOrNull()
        if (first is FenrirBlock.Text && normalizeTitle(first.value) == normalizeTitle(ref.title)) {
            blocks.removeAt(0)
        }
        if (blocks.isEmpty()) return null
        val parts = mutableListOf<String>()
        val images = mutableListOf<ImageRef>()
        val seen = mutableMapOf<String, String>()
        for (block in blocks) {
            when (block) {
                is FenrirBlock.Text -> parts += "<p>${WebHtml.escape(block.value)}</p>"
                is FenrirBlock.Image -> {
                    var filename: String? = seen[block.url]
                    if (filename == null) {
                        val name = String.format(Locale.ROOT, "images/%04d_%02d", ref.index, images.size + 1) +
                            "." + imageExtension(block.url)
                        seen[block.url] = name
                        filename = name
                        images += ImageRef(block.url, name)
                    }
                    parts += "<p class=\"img\"><img src=\"${WebHtml.escape(filename)}\" alt=\"\"/></p>"
                }
            }
        }
        val html = "<div class=\"fenrir-content\">\n" + parts.joinToString("\n") + "\n</div>"
        return ChapterContent(ref, html, images)
    }

    /** `_fenrir_build_chapter`: null when the payload carries no readable content. */
    fun buildChapter(payload: JsonObject, ref: ChapterRef): ChapterContent? {
        val content = payload["content"]
        val text: String? = (content as? JsonPrimitive)?.takeIf { it.isString }?.content
        val document: JsonObject? = when {
            content is JsonObject -> content
            text != null -> runCatching { Json.parseToJsonElement(text) as? JsonObject }.getOrNull()
            else -> null
        }
        if (document != null && string(document["type"]) == "doc") {
            val blocks = documentBlocks(document)
            if (blocks.isEmpty()) return null
            return chapterContent(blocks, ref)
        }
        if (text == null) return null
        val blocks = if (string(payload["content_format"]) == "text") {
            plainBlocks(stripInvisible(text))
        } else {
            htmlBlocks(cleanHtml(text), "$ORIGIN/")
        }
        if (blocks.isEmpty()) return null
        return chapterContent(blocks, ref)
    }

    // Retry-After

    /** Honours Retry-After seconds or an HTTP date; 30 seconds when unusable. */
    fun retryDelay(value: String?, now: Instant = Instant.now()): Double {
        val text = value?.trim() ?: return 30.0
        if (WebRegex.matches("^[0-9]+$", text)) {
            val seconds = text.toDoubleOrNull()
            return if (seconds != null && seconds.isFinite()) seconds else 30.0
        }
        val date = runCatching { ZonedDateTime.parse(text, DateTimeFormatter.RFC_1123_DATE_TIME).toInstant() }.getOrNull()
            ?: return 30.0
        return maxOf(0.0, Duration.between(now, date).toMillis() / 1000.0)
    }
}
