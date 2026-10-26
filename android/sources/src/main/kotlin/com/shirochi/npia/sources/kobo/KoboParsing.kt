package com.shirochi.npia.sources.kobo

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.websupport.WebHtml
import com.shirochi.npia.sources.websupport.WebRegex
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import org.jsoup.nodes.Document
import java.io.ByteArrayOutputStream
import java.net.URI
import java.security.MessageDigest
import java.util.Base64

/** Book metadata read from the store page. Missing or mistyped fields decode as "". */
internal data class KoboMeta(
    val title: String = "",
    val author: String = "",
    val cover: String = "",
    val description: String = "",
    val publisher: String = "",
    val readUrl: String = "",
    val language: String = "",
) {
    companion object {
        fun from(element: JsonElement?): KoboMeta {
            val obj = element as? JsonObject ?: return KoboMeta()
            fun field(name: String): String {
                val value = obj[name] as? JsonPrimitive ?: return ""
                return if (value.isString) value.content else ""
            }
            return KoboMeta(
                title = field("title"),
                author = field("author"),
                cover = field("cover"),
                description = field("description"),
                publisher = field("publisher"),
                readUrl = field("readUrl"),
                language = field("language"),
            )
        }

        fun decode(text: String): KoboMeta = from(runCatching { Json.parseToJsonElement(text) }.getOrNull())
    }
}

/** One table-of-contents entry of the reader. */
@Serializable
internal data class KoboTocEntry(val title: String, val href: String)

@Serializable
internal data class KoboToc(val base: String, val entries: List<KoboTocEntry>)

@Serializable
internal data class KoboSnapshotImage(val url: String, val data: String? = null)

@Serializable
internal data class KoboSnapshotSection(val path: String, val html: String, val images: List<KoboSnapshotImage> = emptyList())

@Serializable
internal data class KoboSnapshot(val paths: List<String>, val sections: List<KoboSnapshotSection>)

/** A reader section to collect: its contents title and resolved path. */
internal data class KoboEntry(val title: String, val path: String)

/** Pure helpers for the kobo source (port of `is_kobo`, `_kobo_*` and `KoboWebReader`). */
internal object KoboParsing {
    const val READER_HOST = "readnow.kobo.com"
    const val DEFAULT_TOC_BASE = "OEBPS/Text/toc.xhtml"
    const val RAKUTEN_CHAPTER_ID = "kobo-desktop"

    private fun uri(url: String): URI? = runCatching { URI(url.trim()) }.getOrNull()

    // URLs

    fun isRakutenUrl(url: String): Boolean {
        val parsed = uri(url) ?: return false
        if (parsed.host?.lowercase() != "books.rakuten.co.jp") return false
        return parsed.scheme == "https" && WebRegex.matches("^/rk/[0-9a-f]{32}/?$", parsed.path ?: "", RegexOption.IGNORE_CASE)
    }

    fun matches(url: String): Boolean {
        val parsed = uri(url) ?: return false
        val host = (parsed.host ?: "").lowercase()
        if (host == "books.rakuten.co.jp") return isRakutenUrl(url)
        if (host != "kobo.com" && host != "www.kobo.com") return false
        if (parsed.scheme != "http" && parsed.scheme != "https") return false
        return WebRegex.matches("^/(?:[a-z]{2}/[a-z]{2}/)?ebook/[^/]+/?$", parsed.path ?: "", RegexOption.IGNORE_CASE)
    }

    fun isReaderUrl(url: String): Boolean {
        val parsed = uri(url) ?: return false
        return parsed.scheme == "https" && parsed.host == READER_HOST
    }

    /** Reads the owned-book action embedded by localized stores (`isInLibrary` and `readNowUrl`). */
    fun ownedReadUrl(productHtml: String): String {
        val doc = WebHtml.parse(productHtml)
        for (element in WebHtml.select("[data-kobo-gizmo-config]", doc)) {
            val raw = element.attr("data-kobo-gizmo-config")
            val config = runCatching { Json.parseToJsonElement(raw).jsonObject }.getOrNull() ?: continue
            val owned = config["isInLibrary"] as? JsonPrimitive ?: continue
            if (owned.isString || owned.content != "true") continue
            val readValue = config["readNowUrl"] as? JsonPrimitive
            val readUrl = if (readValue != null && readValue.isString) readValue.content else ""
            if (isReaderUrl(readUrl)) return readUrl
        }
        return ""
    }

    // Book

    fun bookId(url: String): String {
        val path = uri(url)?.path ?: ""
        val last = path.split("/").lastOrNull { it.isNotEmpty() } ?: ""
        return last.ifEmpty { "kobo" }
    }

    /**
     * Rakuten Books JP volumes are read from the local Kobo Desktop app on the PC; that path does
     * not exist on Android, so the book is listed but its single chapter is refused.
     */
    fun rakutenBook(url: String): BookInfo {
        val title = "Rakuten Kobo ebook"
        val chapter = ChapterRef(RAKUTEN_CHAPTER_ID, 1, title, url = url, isPaid = false)
        return BookInfo(SourceId.KOBO, bookId(url), url, title, language = "ja", chapters = listOf(chapter))
    }

    fun makeBook(url: String, meta: KoboMeta): BookInfo {
        val title = meta.title.ifEmpty { "Kobo ebook" }
        val readUrl = meta.readUrl
        val chapterUrl = readUrl.ifEmpty { url }
        val chapter = ChapterRef("kobo-1", 1, title, url = chapterUrl, isPaid = readUrl.isEmpty())
        val language = if (meta.language.isEmpty()) "en" else meta.language.split("-").first()
        return BookInfo(
            source = SourceId.KOBO,
            id = bookId(url),
            url = url,
            title = title,
            author = meta.author.ifEmpty { null },
            synopsis = meta.description.ifEmpty { null },
            coverUrl = meta.cover.ifEmpty { null },
            language = language.ifEmpty { "en" },
            chapters = listOf(chapter),
        )
    }

    // Reader paths

    /** Resolves a table-of-contents href against the section it came from (`KoboWebReader._path`). */
    fun resolvePath(href: String, base: String = DEFAULT_TOC_BASE): String {
        var rest = href.substringBefore('#').substringBefore('?')
        if (rest.startsWith("//") || WebRegex.matches("^[A-Za-z][A-Za-z0-9+.-]*:", rest)) return ""
        rest = percentDecode(rest) ?: rest
        var path = rest.trimStart('/')
        if (path.isEmpty()) return ""
        if (!(path.startsWith("OEBPS/") || path.startsWith("OPS/"))) {
            val directory = base.split("/").toMutableList()
            if (directory.isNotEmpty()) directory.removeAt(directory.size - 1)
            path = (directory + path).joinToString("/")
        }
        return normalize(path)
    }

    /** Swift `removingPercentEncoding`: null when an escape is malformed; `+` stays `+`. */
    private fun percentDecode(value: String): String? {
        if ('%' !in value) return value
        val out = ByteArrayOutputStream()
        var index = 0
        while (index < value.length) {
            val character = value[index]
            if (character == '%') {
                if (index + 3 > value.length) return null
                val byte = value.substring(index + 1, index + 3).toIntOrNull(16) ?: return null
                out.write(byte)
                index += 3
            } else {
                val bytes = character.toString().toByteArray(Charsets.UTF_8)
                out.write(bytes, 0, bytes.size)
                index++
            }
        }
        return WebHtml.strict(out.toByteArray(), Charsets.UTF_8)
    }

    /** `posixpath.normpath` for relative paths. */
    fun normalize(path: String): String {
        val stack = ArrayList<String>()
        for (part in path.split("/").filter { it.isNotEmpty() }) {
            if (part == ".") continue
            if (part == ".." && stack.isNotEmpty() && stack.last() != "..") {
                stack.removeAt(stack.size - 1)
            } else {
                stack.add(part)
            }
        }
        return if (stack.isEmpty()) "." else stack.joinToString("/")
    }

    fun anchor(path: String): String {
        val digest = MessageDigest.getInstance("SHA-1").digest(path.toByteArray(Charsets.UTF_8))
        val hex = digest.joinToString("") { "%02x".format(it.toInt() and 0xFF) }
        return "kobo-" + hex.take(12)
    }

    /** Merges overlapping reader windows without losing spine order (`KoboWebReader._ordered_paths`). */
    fun orderedPaths(snapshots: List<List<String>>): List<String> {
        val edges = LinkedHashMap<String, LinkedHashSet<String>>()
        val indegree = LinkedHashMap<String, Int>()
        val firstSeen = HashMap<String, Int>()
        for (snapshot in snapshots) {
            val paths = snapshot.distinct()
            for (path in paths) {
                if (path !in firstSeen) firstSeen[path] = firstSeen.size
                edges.getOrPut(path) { LinkedHashSet() }
                if (path !in indegree) indegree[path] = 0
            }
            for (position in 0 until paths.size - 1) {
                val left = paths[position]
                val right = paths[position + 1]
                val out = edges.getValue(left)
                if (right !in out) {
                    out.add(right)
                    indegree[right] = (indegree[right] ?: 0) + 1
                }
            }
        }
        val ready = indegree.filter { it.value == 0 }.keys.toMutableList()
        val result = ArrayList<String>()
        while (ready.isNotEmpty()) {
            var best = 0
            for (position in 1 until ready.size) {
                if ((firstSeen[ready[position]] ?: 0) < (firstSeen[ready[best]] ?: 0)) best = position
            }
            val path = ready.removeAt(best)
            result.add(path)
            for (next in edges[path] ?: emptySet<String>()) {
                val left = (indegree[next] ?: 0) - 1
                indegree[next] = left
                if (left == 0) ready.add(next)
            }
        }
        if (result.size != indegree.size) throw SourceException.Parse("Kobo reader returned inconsistent section order.")
        return result
    }

    // Section HTML

    /** Strips scripts, frames and event handlers; points links to other sections at their anchors. */
    fun cleanSection(source: String, path: String, anchors: Map<String, String>, imageNames: Map<String, String>): String {
        val doc = WebHtml.parse(source)
        val body = doc.body() ?: return WebHtml.escape(source)
        for (tag in listOf("script", "iframe", "object", "embed")) WebHtml.removeAll(tag, body)
        for (node in WebHtml.select("*", body)) {
            val names = node.attributes().asList().map { it.key }
            for (name in names) {
                if (name.lowercase().startsWith("on")) node.removeAttr(name)
            }
        }
        for (link in WebHtml.select("a[href]", body)) {
            val target = resolvePath(link.attr("href"), path)
            anchors[target]?.let { link.attr("href", "#$it") }
        }
        for (image in WebHtml.select("img[src]", body)) {
            imageNames[image.attr("src")]?.let { image.attr("src", it) }
        }
        doc.outputSettings().syntax(Document.OutputSettings.Syntax.xml)
        return body.html()
    }

    fun sectionText(html: String): String {
        val body = WebHtml.parse(html).body() ?: return ""
        return WebHtml.lines(body).joinToString("\n")
    }

    /** True when most of the text sits in the private block the reader uses for font-encoded text. */
    fun isFontEncoded(text: String, entryTitles: List<String>): Boolean {
        if (text.isEmpty()) return false
        var encoded = 0
        var total = 0
        var index = 0
        while (index < text.length) {
            val point = text.codePointAt(index)
            total++
            if (point in 0x1f60..0x1fff) encoded++
            index += Character.charCount(point)
        }
        return encoded * 100 > total && entryTitles.any { it.startsWith("Chapter") }
    }

    fun imageExtension(dataUrl: String): String {
        if (!dataUrl.startsWith("data:")) return "jpg"
        val head = dataUrl.substringBefore(';')
        val mime = head.substringAfterLast('/').lowercase()
        val map = mapOf("jpeg" to "jpg", "png" to "png", "gif" to "gif", "webp" to "webp", "svg+xml" to "svg")
        return map[mime] ?: "jpg"
    }

    fun imageBytes(dataUrl: String): ByteArray? {
        if (!dataUrl.startsWith("data:")) return null
        val comma = dataUrl.indexOf(',')
        if (comma < 0) return null
        val header = dataUrl.substring(0, comma)
        val payload = dataUrl.substring(comma + 1)
        if (";base64" in header) return runCatching { Base64.getMimeDecoder().decode(payload) }.getOrNull()
        return percentDecode(payload)?.toByteArray(Charsets.UTF_8)
    }

    /** Builds the chapter from the collected sections, in spine order. */
    fun buildContent(
        ref: ChapterRef,
        entries: List<KoboEntry>,
        collected: Map<String, KoboSnapshotSection>,
        snapshots: List<List<String>>,
        horizontalLayout: Boolean,
    ): ChapterContent {
        val missing = entries.filter { it.path !in collected }.map { it.title }
        if (missing.isNotEmpty()) throw SourceException.Parse("Kobo reader did not load: " + missing.take(3).joinToString(", "))
        val ordered = orderedPaths(snapshots)
        val uncaptured = ordered.filter { it !in collected }
        if (uncaptured.isNotEmpty()) {
            throw SourceException.Parse("Kobo reader did not render every source section: " + uncaptured.take(3).joinToString(", "))
        }
        val anchors = LinkedHashMap<String, String>()
        for (path in ordered) anchors[path] = anchor(path)

        val images = ArrayList<ImageRef>()
        val imageNames = HashMap<String, String>()
        for (path in ordered) {
            val section = collected[path] ?: continue
            for (image in section.images) {
                val data = image.data ?: ""
                if (image.url.startsWith("blob:") && data.isEmpty()) {
                    throw SourceException.Parse("A Kobo reader image did not load in $path")
                }
                if (image.url.isEmpty() || image.url in imageNames) continue
                val name = "images/%04d_%03d".format(ref.index, images.size + 1) + "." + imageExtension(data)
                imageNames[image.url] = name
                images.add(ImageRef(image.url, name, imageBytes(data)))
            }
        }

        val bodies = StringBuilder()
        val textParts = ArrayList<String>()
        for (path in ordered) {
            val section = collected[path] ?: continue
            val id = anchors[path] ?: continue
            val clean = cleanSection(section.html, path, anchors, imageNames)
            bodies.append("<section class=\"kobo-source-section\" id=\"").append(id).append("\">").append(clean).append("</section>")
            textParts.add(sectionText(clean))
        }
        if (isFontEncoded(textParts.joinToString("\n\n"), entries.map { it.title })) {
            throw SourceException.Parse("Kobo reader text is still font-encoded; a readable EPUB cannot be produced.")
        }
        val style = if (horizontalLayout) " style=\"writing-mode:horizontal-tb\"" else ""
        val html = "<div class=\"kobo-content\"$style>$bodies</div>"
        return ChapterContent(ref, html, images)
    }

    /** A JSON string literal usable inside JavaScript source. */
    fun jsString(value: String): String = JsonPrimitive(value).toString()
}
