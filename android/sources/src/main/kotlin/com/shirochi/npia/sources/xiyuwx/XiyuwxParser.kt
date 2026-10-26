package com.shirochi.npia.sources.xiyuwx

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.websupport.WebHtml
import com.shirochi.npia.sources.websupport.WebRegex
import org.jsoup.nodes.Document
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import java.net.URI
import java.util.Base64

/** One catalog entry found on a book page. */
internal data class XiyuwxLink(val url: String, val name: String, val paid: Boolean)

/** One parsed chapter page (a chapter can span several pages). */
internal data class XiyuwxChapterPage(val lines: List<String>, val nextPage: String?)

/** Pure parsing for xiyuwx and its yywenxuan mirror (`_xiyuwx_*` in the desktop scraper). */
internal object XiyuwxParser {
    val hosts: Set<String> = setOf("xiyuwx.com", "www.xiyuwx.com", "yywenxuan.com", "www.yywenxuan.com")
    const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    const val MAX_PAGES = 30

    // URLs

    fun uri(url: String): URI? = runCatching { URI(url.trim()) }.getOrNull()

    private fun host(url: String): String = uri(url)?.host?.lowercase() ?: ""

    fun bookID(url: String): String {
        if (host(url) !in hosts) return ""
        val groups = WebRegex.firstMatch("^/(?:book/)?([1-9][0-9]*)(?:/|$)", rawPath(url), RegexOption.IGNORE_CASE)
        return groups?.getOrNull(1) ?: ""
    }

    fun matches(url: String): Boolean = bookID(url).isNotEmpty()

    fun siteName(url: String): String = if (host(url).endsWith("yywenxuan.com")) "YY Wenxuan" else "Xiyuwx"

    /** Path with its trailing slash preserved. */
    fun rawPath(url: String): String = uri(url)?.path ?: ""

    private fun origin(u: URI): String {
        val port = if (u.port != -1) ":${u.port}" else ""
        return "${u.scheme}://${u.host}$port"
    }

    /** `/<id>/` or `/book/<id>/` on the same host as `url`. */
    fun canonicalBookURL(url: String, bookID: String): String {
        val u = uri(url) ?: return url
        if (u.host == null) return url
        val prefix = if ((u.path ?: "").startsWith("/book/")) "book/" else ""
        return "${origin(u)}/$prefix$bookID/"
    }

    // Decoding

    /**
     * Some chapters contain invalid bytes despite declaring UTF-8; decode those lossily instead
     * of falling through to another charset.
     */
    fun decodePage(data: ByteArray): String {
        val head = String(data, 0, minOf(data.size, 4096), Charsets.UTF_8)
        val name = WebRegex.capture("charset\\s*=\\s*[\"']?([a-zA-Z0-9_-]+)", head, RegexOption.IGNORE_CASE)
        if (name != null) {
            val lowered = name.lowercase()
            if (lowered == "utf-8" || lowered == "utf8") return String(data, Charsets.UTF_8)
        }
        return WebHtml.decode(data)
    }

    /** The `_ii_rr` ordering map: base64 of comma separated integers. */
    fun decodePositions(encoded: String): List<Int>? {
        var padded = encoded.trim()
        while (padded.length % 4 != 0) padded += "="
        val data: ByteArray = try {
            Base64.getDecoder().decode(padded)
        } catch (e: IllegalArgumentException) {
            return null
        }
        val text = WebHtml.strict(data, Charsets.UTF_8) ?: return null
        val values = mutableListOf<Int>()
        for (part in text.split(",")) {
            val value = part.trim().toIntOrNull() ?: return null
            values.add(value)
        }
        return values.ifEmpty { null }
    }

    /**
     * Reorders shuffled paragraphs: `positions[0]` is the offset, every following value gives the
     * offset position of the paragraph to emit next. Null unless it is a permutation.
     */
    fun unshuffle(pieces: List<String>, positions: List<Int>): List<String>? {
        val offset = positions.firstOrNull() ?: return null
        val indexes: List<Int> = positions.drop(1).map { it - offset }
        if (indexes.size != pieces.size || indexes.sorted() != pieces.indices.toList()) return null
        return indexes.map { pieces[it] }
    }

    private val paragraphBreak = Regex("<br\\s*/?>\\s*<br\\s*/?>", RegexOption.IGNORE_CASE)

    /** Splits `raw` on `<br><br>` pairs (any spacing and self-closing form). */
    fun splitParagraphs(raw: String): List<String> = paragraphBreak.split(raw)

    /** Replaces only the first match (`re.sub(..., count=1)`). */
    fun replaceFirst(pattern: String, text: String, vararg options: RegexOption): String =
        Regex(pattern, options.toSet()).replaceFirst(text, "")

    // Documents

    fun parse(html: String, baseUrl: String?): Document {
        val document = WebHtml.parse(html, baseUrl)
        document.outputSettings().prettyPrint(false)
        return document
    }

    private const val VERIFICATION = "Just a moment|Checking your browser"

    fun isVerificationPage(html: String): Boolean =
        WebRegex.matches(VERIFICATION, parse(html, null).title(), RegexOption.IGNORE_CASE)

    // Book

    private const val BODY_MARKER = "\u6b63\u6587"
    private const val CHAPTER_LIKE =
        "\u7b2c.{1,12}[\u7ae0\u8282\u56de\u5377]|\u5e8f\u7ae0|\u6954\u5b50|\u524d\u8a00|\u540e\u8bb0|\u756a\u5916|\u6b63\u6587"
    private const val NAVIGATION =
        "^(?:\u4e0a\u4e00\u7ae0|\u4e0b\u4e00\u7ae0|\u8fd4\u56de\u76ee\u5f55|\u7ae0\u8282\u76ee\u5f55|\u5168\u90e8\u7ae0\u8282|\u76ee\u5f55)$"
    private const val LOCK_MARKUP = "\\bvip\\b|locked|paywall|\u4ed8\u8d39|\u6536\u8d39|\uD83D\uDD12"

    private val catalogSelectors = listOf(
        "#list a[href]", "#chapterlist a[href]", ".chapterlist a[href]",
        ".chapter-list a[href]", ".chapter_list a[href]",
        ".listmain a[href]", ".list-chapter a[href]",
        ".catalog a[href]", ".directory a[href]", "dl dd a[href]",
    )

    fun chapterLinks(document: Document, baseUrl: String, bookID: String): List<XiyuwxLink> {
        val base = uri(baseUrl) ?: return emptyList()
        val prefixMatch = WebRegex.firstMatch("^/(?:book/)?${Regex.escape(bookID)}/", rawPath(baseUrl), RegexOption.IGNORE_CASE)
        val prefix = prefixMatch?.firstOrNull() ?: "/$bookID/"

        var links: List<Element> = emptyList()
        var catalogFound = false
        // The template puts a reversed "latest chapters" preview before the full catalog;
        // read the links after the last body heading to keep reading order.
        val heading = WebHtml.select("#list dt", document).lastOrNull { WebHtml.text(it).contains(BODY_MARKER) }
        if (heading != null) {
            val found = mutableListOf<Element>()
            var sibling: Element? = heading.nextElementSibling()
            while (sibling != null) {
                val tag = sibling.tagName().lowercase()
                if (tag == "dt") break
                if (tag == "a" && WebHtml.attr(sibling, "href").isNotEmpty()) found.add(sibling)
                sibling = sibling.nextElementSibling()
            }
            links = found
            catalogFound = links.isNotEmpty()
        }
        if (links.isEmpty()) {
            for (selector in catalogSelectors) {
                links = WebHtml.select(selector, document)
                if (links.isNotEmpty()) {
                    catalogFound = true
                    break
                }
            }
        }
        if (links.isEmpty()) links = WebHtml.select("a[href]", document)

        val chapters = mutableListOf<XiyuwxLink>()
        val seen = mutableSetOf<String>()
        for (link in links) {
            val name = WebHtml.text(link)
            val joinedText = WebHtml.join(baseUrl, WebHtml.attr(link, "href")) ?: continue
            val joined = uri(joinedText) ?: continue
            val host = joined.host?.lowercase() ?: continue
            if (host !in hosts) continue
            val path = joined.path ?: ""
            if (!path.startsWith(prefix) || path.trimEnd('/') == prefix.trimEnd('/')) continue
            if (!WebRegex.matches("\\.(?:html?|shtml)$", path, RegexOption.IGNORE_CASE)) continue
            // The mirror hard-codes the main host in its links; fetch from the serving host.
            val query = joined.rawQuery?.let { "?$it" } ?: ""
            val fragment = joined.rawFragment?.let { "#$it" } ?: ""
            val href = origin(base) + (joined.rawPath ?: "") + query + fragment
            if (name.isEmpty() || href in seen) continue
            if (!catalogFound && !WebRegex.matches(CHAPTER_LIKE, name)) continue
            if (WebRegex.matches(NAVIGATION, name)) continue
            seen.add(href)
            var row: Element? = link.parent()
            while (row != null) {
                val tag = row.tagName().lowercase()
                if (tag == "dd" || tag == "li" || tag == "tr") break
                row = row.parent()
            }
            val marker = listOf(link.className(), row?.className() ?: "", WebHtml.attr(link, "data-vip"), WebHtml.attr(link, "data-paid"))
                .joinToString(" ")
            // Titles such as a "please subscribe" note are ordinary free chapters here;
            // only explicit lock markup is paid.
            val paid = WebRegex.matches(LOCK_MARKUP, marker, RegexOption.IGNORE_CASE)
            chapters.add(XiyuwxLink(href, name, paid))
        }
        return chapters
    }

    /** Null while the page is a verification page or lacks a title or catalog. */
    fun book(html: String, pageUrl: String, bookUrl: String, bookID: String): BookInfo? {
        val document = parse(html, pageUrl)
        if (WebRegex.matches(VERIFICATION, document.title(), RegexOption.IGNORE_CASE)) return null

        var name = WebHtml.text(WebHtml.selectFirst("#info h1, .book-info h1, .bookinfo h1, .info h1, h1", document))
        if (name.isEmpty()) {
            name = WebHtml.attr(WebHtml.selectFirst("meta[property=og:novel:book_name], meta[property=og:title]", document), "content")
        }
        val links = chapterLinks(document, pageUrl, bookID)
        if (name.isEmpty() || links.isEmpty()) return null

        var author = WebHtml.text(WebHtml.selectFirst("#info .author, .book-info .author, .bookinfo .author, .info .author, a[href*=/author/]", document))
        author = WebRegex.replace("^\u4f5c\u8005\\s*[:\uff1a]?\\s*", author, "").trim()
        if (author.isEmpty()) {
            author = WebHtml.attr(WebHtml.selectFirst("meta[property=og:novel:author], meta[name=author]", document), "content")
        }
        if (author.isEmpty()) {
            val info = WebHtml.text(WebHtml.selectFirst("#info, .book-info, .bookinfo", document))
            author = WebRegex.capture("\u4f5c\u8005\\s*[:\uff1a]\\s*([^\\s|]+)", info) ?: ""
        }

        val introNode = WebHtml.selectFirst("#intro, .intro, .book-intro, .bookintro, .description", document)
        var tags: List<String> = emptyList()
        var intro = WebHtml.text(introNode)
        if (introNode != null) {
            val tagLinks = WebHtml.select("a[href*=/tag/]", introNode)
            tags = tagLinks.map { WebHtml.text(it) }
            val last = tagLinks.lastOrNull()
            if (last != null) {
                val tail = StringBuilder()
                var node: Node? = last.nextSibling()
                while (node != null) {
                    tail.append(node.outerHtml())
                    node = node.nextSibling()
                }
                intro = WebHtml.text(parse(tail.toString(), null).body())
                val strip = "\u3001,\uff0c:\uff1a "
                intro = intro.trimStart { it in strip }
            }
        }

        var coverUrl: String? = null
        val cover = WebHtml.selectFirst("#fmimg img, .book-cover img, .bookimg img, .cover img", document)
        if (cover != null) coverUrl = WebHtml.join(pageUrl, WebHtml.attr(cover, "src"))
        val category = WebHtml.attr(WebHtml.selectFirst("meta[property=og:novel:category]", document), "content")
        val allTags = tags.filter { it.isNotEmpty() }.toMutableList()
        if (category.isNotEmpty() && category !in allTags) allTags.add(category)

        val chapters = links.mapIndexed { offset, link ->
            ChapterRef(id = link.url, index = offset + 1, title = link.name, url = link.url, isPaid = link.paid)
        }
        return BookInfo(
            source = SourceId.XIYUWX,
            id = bookID,
            url = bookUrl,
            title = name,
            author = author.ifEmpty { "Unknown" },
            synopsis = intro.ifEmpty { null },
            coverUrl = coverUrl,
            tags = allTags,
            language = "zh",
            chapters = chapters,
        )
    }

    // Chapter

    private const val NOISE_SELECTOR = "script, style, noscript, .ads, .advertisement, .read-ads, .chapter-ad, .bottom-ad, .ad"

    /** Null when the page has no readable chapter body (caller falls back to the browser). */
    fun chapterPage(html: String, pageUrl: String?): XiyuwxChapterPage? {
        val document = parse(html, pageUrl)
        val content = WebHtml.selectFirst(
            "#content, #chaptercontent, #chapterContent, .chapter-content, .chapterContent, .read-content, .readContent, .content",
            document,
        ) ?: return null
        val shuffled = WebHtml.selectFirst("#chapter", content)
        val rendered = WebHtml.selectFirst("#ad", content)
        // Reader mode leaves the scrambled source beside the rendered copy; read only one.
        val renderedText = if (rendered != null) WebHtml.text(rendered) else ""
        var shuffleScript = ""
        for (script in WebHtml.select("script", document)) {
            val data = script.data()
            if (data.contains("_ii_rr(")) {
                shuffleScript = data
                break
            }
        }

        var lines: MutableList<String> = mutableListOf()
        if (rendered != null && renderedText.isNotEmpty()) {
            WebHtml.removeAll(NOISE_SELECTOR, rendered)
            lines = WebHtml.lines(rendered).toMutableList()
        } else if (shuffled != null && shuffleScript.isNotEmpty()) {
            val encoded = WebRegex.capture("_ii_rr\\s*\\(\\s*['\"]([^'\"]+)", shuffleScript) ?: return null
            val positions = decodePositions(encoded) ?: return null
            var raw = shuffled.html()
            raw = replaceFirst("\\[.*?\u4f5c\u8005.*?\u63d0\u793a.*?\\]", raw, RegexOption.DOT_MATCHES_ALL)
            val ordered = unshuffle(splitParagraphs(raw), positions) ?: return null
            for (piece in ordered) {
                val text = WebHtml.text(parse(piece, null).body()).replace('\u00A0', ' ')
                val collapsed = WebRegex.replace("\\s+", text, " ").trim()
                if (collapsed.isNotEmpty()) lines.add(collapsed)
            }
        } else if (shuffled != null) {
            // A shuffled source without its ordering map is unusable; let the browser render it.
            return null
        } else {
            WebHtml.removeAll(NOISE_SELECTOR, content)
            lines = WebHtml.lines(content).toMutableList()
        }
        if (lines.size > 1 && WebRegex.matches("^(?:\u4e66\\s*)?\u7b2c\\s*[0-9]+\u7ae0", lines[0])) {
            lines.removeAt(0)
        }
        if (lines.isEmpty()) return null
        return XiyuwxChapterPage(lines, nextPage(document, pageUrl))
    }

    private const val NEXT_LABEL = "\u4e0b\u4e00\u9875"

    private fun nextPage(document: Document, pageUrl: String?): String? {
        if (pageUrl == null) return null
        val pageHost = uri(pageUrl)?.host
        val stem = WebRegex.replace("(?:_[0-9]+)?\\.html$", rawPath(pageUrl), "", RegexOption.IGNORE_CASE)
        val pattern = "^" + Regex.escape(stem) + "_[0-9]+\\.html$"
        for (link in WebHtml.select("a[href]", document)) {
            if (WebHtml.text(link) != NEXT_LABEL) continue
            val candidate = WebHtml.join(pageUrl, WebHtml.attr(link, "href")) ?: continue
            if (uri(candidate)?.host != pageHost) continue
            if (WebRegex.matches(pattern, rawPath(candidate), RegexOption.IGNORE_CASE)) return candidate
        }
        return null
    }

    fun chapterHtml(lines: List<String>): String = lines.joinToString("\n") { "<p>${WebHtml.escape(it)}</p>" }
}
