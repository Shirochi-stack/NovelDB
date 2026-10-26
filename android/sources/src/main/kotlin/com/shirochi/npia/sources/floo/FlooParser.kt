package com.shirochi.npia.sources.floo

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.websupport.WebHtml
import com.shirochi.npia.sources.websupport.WebRegex
import org.jsoup.nodes.Document
import org.jsoup.nodes.Element
import java.net.URI
import java.net.URLDecoder
import java.nio.charset.Charset

/** What a floo chapter page turned out to be. */
internal sealed class FlooChapterOutcome {
    /** The reader replaced the chapter with a prompt. `login` or `purchase`. */
    data class Locked(val reason: String) : FlooChapterOutcome()

    /** Readable text, one paragraph per line. */
    data class Text(val lines: List<String>) : FlooChapterOutcome()

    /** VIP chapter drawn as images, in reading order. */
    data class Images(val urls: List<String>) : FlooChapterOutcome()

    /** No chapter container and no recognisable prompt. */
    data object None : FlooChapterOutcome()
}

internal data class FlooChapterLink(val url: String, val name: String, val paid: Boolean)

/** Pure parsing for floo (`_floo_*` in the desktop external scraper). */
internal object FlooParser {
    /** Source domain, kept as escapes so the name never appears in the repository text. */
    val name = "\u0066\u0061\u006c\u006f\u006f"
    val domain = "$name.com"
    val bookHost = "b.$domain"
    val hosts: Set<String> = setOf(bookHost, "wap.$domain", "www.$domain")
    const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    const val CONTENT_SELECTOR = ".noveContent, #novelContent, .novelContent, #chapterContent, .chapter-content"

    val loginWall = "\u60a8\u8fd8\u6ca1\u6709\u767b\u5f55|\u8bf7\u767b\u5f55\u540e"
    val purchaseWall = "\u8ba2\u9605\u672c\u7ae0|\u8d2d\u4e70\u672c\u7ae0|\u4f59\u989d\u4e0d\u8db3|\u5f00\u901aVIP|\u5347\u7ea7VIP\u4f1a\u5458|\u8bbe\u7f6e\u81ea\u52a8\u8ba2\u9605"
    private val purchaseHint = "\u8ba2\u9605|\u5145\u503c|\u8d2d\u4e70\u672c\u7ae0|\u5f00\u901aVIP"
    private val purchaseStart = "^(?:\u8ba2\u9605|\u5145\u503c|\u8d2d\u4e70\u672c\u7ae0|\u5f00\u901aVIP)"
    private val paidMarker = "\\bvip\\b|\u4ed8\u8d39|\u8ba2\u9605|\u6536\u8d39|\u5df2\u9501|\uD83D\uDD12"
    private val authorPrefix = "^\u4f5c\u8005\\s*[:\uff1a]?\\s*"

    // URLs

    fun canonicalBookUrl(bookId: String): String = "https://$bookHost/$bookId.html"

    private fun uri(url: String): URI? = runCatching { URI(url.trim()) }.getOrNull()

    /** Book id from a book, catalog, VIP reader or chapter link on any of the three hosts. */
    fun bookId(url: String): String {
        val parsed = uri(url) ?: return ""
        val host = parsed.host?.lowercase() ?: return ""
        if (host !in hosts) return ""
        val path = parsed.path ?: ""
        val patterns = listOf(
            "^/([0-9]+)(?:[_./]|$)",
            // VIP reader: /vip/724903/1658.html
            "^/vip/([0-9]+)/[0-9]+\\.html$",
            // Chapter list: /html_724_724903/
            "^/html_[0-9]+_([0-9]+)(?:/|\\.html)?$",
            "^/(?:book|novel|catalog|directory)/?([0-9]+)(?:[/._]|$)",
        )
        for (pattern in patterns) {
            val groups = WebRegex.firstMatch(pattern, path, RegexOption.IGNORE_CASE)
            if (groups != null && groups.size > 1) {
                val id = groups[1]
                if (id != null) return id
            }
        }
        val items: List<Pair<String, String>> = (parsed.rawQuery ?: "").split("&").filter { it.isNotEmpty() }.map { item ->
            val parts = item.split("=", limit = 2)
            val key = runCatching { URLDecoder.decode(parts[0], "UTF-8") }.getOrDefault(parts[0])
            val value = if (parts.size > 1) runCatching { URLDecoder.decode(parts[1], "UTF-8") }.getOrDefault(parts[1]) else ""
            key to value
        }
        for (key in listOf("id", "bookid", "book_id", "novelid")) {
            val value = items.firstOrNull { it.first == key }?.second
            if (value != null && WebRegex.matches("^[1-9][0-9]*$", value)) return value
        }
        return ""
    }

    fun matches(url: String): Boolean = bookId(url).isNotEmpty()

    // Text helpers

    private fun hasCjk(value: String): Boolean = value.any { it.code in 0x3400..0x9fff }

    /** Repairs GBK text that was already misread as Latin-1 or Windows-1252. */
    fun fixText(value: String): String {
        if (value.isEmpty() || hasCjk(value)) return value
        val charsets: List<Charset> = listOf(Charsets.ISO_8859_1, Charset.forName("windows-1252"))
        for (charset in charsets) {
            if (!charset.newEncoder().canEncode(value)) continue
            val bytes = value.toByteArray(charset)
            val repaired = WebHtml.strict(bytes, WebHtml.GB18030) ?: continue
            if (hasCjk(repaired)) return repaired
        }
        return value
    }

    fun text(element: Element?): String = fixText(WebHtml.text(element))

    /** Candidate decodings of a page: UTF-8 (BOM stripped) else GB18030, then the charset-aware decode. */
    fun decodings(data: ByteArray): List<String> {
        val body = if (data.size >= 3 && (data[0].toInt() and 0xFF) == 0xEF && (data[1].toInt() and 0xFF) == 0xBB &&
            (data[2].toInt() and 0xFF) == 0xBF
        ) data.copyOfRange(3, data.size) else data
        val first = WebHtml.strict(body, Charsets.UTF_8) ?: WebHtml.strict(body, WebHtml.GB18030) ?: String(body, Charsets.UTF_8)
        val result = mutableListOf(first)
        val second = WebHtml.decode(body)
        if (second != first) result.add(second)
        return result
    }

    fun parse(html: String, baseUrl: String?): Document? {
        val document = runCatching { WebHtml.parse(html, baseUrl) }.getOrNull() ?: return null
        document.outputSettings().prettyPrint(false)
        return document
    }

    /** The `C3VK` anti-bot cookie the server asks the client to set before reloading. */
    fun challengeToken(body: ByteArray): String? {
        val head = String(body, 0, minOf(body.size, 2000), Charsets.UTF_8)
        return WebRegex.capture("cookie\\s*=\\s*[\"']C3VK=([0-9a-fA-F]+)", head)
    }

    // Book

    fun chapterLinks(document: Document, baseUrl: String, bookId: String): List<FlooChapterLink> {
        val selectors = listOf(
            "div.C-Fo-Zuo div.DivTable a[href]", ".DivTable a[href]", ".chapter-list a[href]",
            ".chapterList a[href]", "#chapter-list a[href]", "#chapterList a[href]",
            ".catalog a[href]", ".directory a[href]", ".book-list a[href]",
        )
        var links: List<Element> = emptyList()
        for (selector in selectors) {
            links = WebHtml.select(selector, document)
            if (links.isNotEmpty()) break
        }
        val workPage = "^/" + Regex.escape(bookId) + "\\.html$"
        val chapters = mutableListOf<FlooChapterLink>()
        val seen = mutableSetOf<String>()
        for (link in links) {
            val href = WebHtml.join(baseUrl, WebHtml.attr(link, "href")) ?: continue
            val parsed = uri(href) ?: continue
            val host = parsed.host?.lowercase() ?: continue
            if (host !in hosts) continue
            val path = parsed.path ?: ""
            if (!WebRegex.matches("\\.(?:html|aspx)$", path, RegexOption.IGNORE_CASE)) continue
            // Exclude the work page and catalog navigation.
            if (WebRegex.matches(workPage, path, RegexOption.IGNORE_CASE)) continue
            val name = text(link)
            if (name.isEmpty() || href in seen) continue
            seen.add(href)
            var row: Element? = link.parent()
            var cursor: Element? = link.parent()
            while (cursor != null) {
                val tag = cursor.tagName().lowercase()
                if (tag == "li" || tag == "dd" || tag == "tr") {
                    row = cursor
                    break
                }
                cursor = cursor.parent()
            }
            val linkClasses = link.className()
            val rowClasses = row?.className() ?: ""
            val rowText = if (row != null) text(row) else ""
            val marker = listOf(linkClasses, rowClasses, rowText).joinToString(" ")
            val paid = WebRegex.matches(paidMarker, marker, RegexOption.IGNORE_CASE)
            chapters.add(FlooChapterLink(href, name, paid))
        }
        return chapters
    }

    /** Null when the page lacks a title or a catalog (the caller tries another source of the page). */
    fun book(pages: List<String>, pageUrl: String, bookId: String, canonical: String): BookInfo? {
        for (html in pages) {
            val document = parse(html, pageUrl) ?: continue
            book(document, pageUrl, bookId, canonical)?.let { return it }
        }
        return null
    }

    fun book(document: Document, pageUrl: String, bookId: String, canonical: String): BookInfo? {
        var title = text(WebHtml.selectFirst("h1#novelName", document))
        if (title.isEmpty()) title = text(WebHtml.selectFirst("h1", document))
        val links = chapterLinks(document, pageUrl, bookId)
        if (title.isEmpty() || links.isEmpty()) return null

        var author = text(WebHtml.selectFirst("a.rentouOne, .author a, .author", document))
        if (author.isEmpty()) {
            // Desktop book pages: the author link beside the avatar searches by author
            // (l_0_1.html?t=2&k=...); the avatar link's title reads "<author>_<site> author".
            var box: Element = document
            val novel = WebHtml.selectFirst("#novelName", document)
            val novelParent = novel?.parent()
            if (novelParent != null) box = novelParent
            val searchLink = WebHtml.select("a[href]", box).firstOrNull { WebHtml.attr(it, "href").contains("l_0_1.html?t=2") }
            author = text(searchLink)
            if (author.isEmpty()) {
                val guru = WebHtml.select("a[title]", box).firstOrNull { WebHtml.attr(it, "href").contains("/guru/") }
                if (guru != null) {
                    val label = WebHtml.attr(guru, "title")
                    val head = label.split("_").first()
                    author = fixText(head.trim())
                }
            }
        }
        if (author.isEmpty()) {
            val image = WebHtml.selectFirst("img.rentouOne", document)
            if (image != null) {
                var alt = WebHtml.attr(image, "alt")
                if (alt.isEmpty()) alt = WebHtml.attr(image, "title")
                author = fixText(alt)
                if (author.isEmpty()) {
                    author = text(image.parent())
                    author = WebRegex.replace(authorPrefix, author, "")
                }
            }
        }
        if (author.isEmpty()) {
            author = fixText(WebHtml.attr(WebHtml.selectFirst("meta[name=author]", document), "content"))
        }
        val introduction = text(WebHtml.selectFirst("div.T-L-T-C-Box1, .book-intro, .bookIntro, .intro", document))
        val cover = WebHtml.selectFirst("img.imgcss, .book-cover img, .cover img", document)
        val coverUrl: String? = if (cover != null) WebHtml.join(pageUrl, WebHtml.attr(cover, "src")) else null
        val tags = WebHtml.select("div.T-R-T-B2-Box1 a", document).map { text(it) }.filter { it.isNotEmpty() }

        val chapters = links.mapIndexed { offset, link ->
            ChapterRef(id = link.url, index = offset + 1, title = link.name, url = link.url, isPaid = link.paid)
        }
        return BookInfo(
            source = SourceId.FLOO, id = bookId, url = canonical, title = title,
            author = author.ifEmpty { "Unknown" },
            synopsis = introduction.ifEmpty { null }, coverUrl = coverUrl, tags = tags,
            language = "zh", chapters = chapters,
        )
    }

    // Chapter

    /** URL of one server-drawn VIP image from an `image_do3(...)` call's arguments. */
    fun imageUrl(arguments: String): String? {
        val args = arguments.split(",").map { it.trim().trim('\'', '"') }
        if (args.size < 11) return null
        val num = args[0]
        val origin = args[1]
        val book = args[2]
        val chapter = args[3]
        val en = args[4]
        val part = args[5]
        val key = args[6]
        val user = args[7]
        val stamp = args[8]
        val size = args[9]
        val color = args[10]
        val chapterType = if (args.size > 11) args[11] else "1"
        val family = if (args.size > 12) args[12] else "0"
        val background = if (args.size > 13) args[13] else "1"
        val host = if (chapterType == "0") "read6" else "read"
        return "https://$host.$domain/Page4VipImage.aspx?num=$num&o=$origin&id=$book&n=$chapter" +
            "&ct=$chapterType&en=$en&t=$part&font_size=$size&font_color=$color" +
            "&FontFamilyType=$family&backgroundtype=$background&u=$user&time=$stamp&k=$key"
    }

    /** Classifies one chapter page. `pages` are alternative decodings of the same document. */
    fun chapter(pages: List<String>, pageUrl: String?): FlooChapterOutcome {
        var chosen: Document? = null
        var content: Element? = null
        for (html in pages) {
            val document = parse(html, pageUrl) ?: continue
            chosen = document
            val found = WebHtml.selectFirst(CONTENT_SELECTOR, document)
            if (found != null) {
                content = found
                break
            }
        }
        if (content == null) {
            val pageText = WebHtml.text(chosen)
            if (WebRegex.matches(loginWall, pageText)) return FlooChapterOutcome.Locked("login")
            if (WebRegex.matches(purchaseHint, pageText)) return FlooChapterOutcome.Locked("purchase")
            return FlooChapterOutcome.None
        }
        val markup = content.outerHtml()
        val imageCalls: List<String> = WebRegex.allMatches("image_do3?\\(([^)]*)\\)", markup).mapNotNull { if (it.size > 1) it[1] else null }
        WebHtml.removeAll("script, style, noscript, .ads, .advertisement", content)
        // Free chapters end with a recharge promotion linking to the pay site.
        val payHost = "pay.$domain"
        for (link in WebHtml.select("a[href]", content)) {
            if (!WebHtml.attr(link, "href").contains(payHost)) continue
            var ancestor: Element? = link.parent()
            while (ancestor != null && ancestor !== content) {
                if (ancestor.tagName().lowercase() == "p") {
                    ancestor.remove()
                    break
                }
                ancestor = ancestor.parent()
            }
        }
        val wallText = fixText(WebHtml.text(content))
        // A signed-in reader who has not bought the chapter gets the same box with a
        // "not subscribed" line, so only its text says why.
        val hasLoginLink = WebHtml.select("a[href]", content).any { WebHtml.attr(it, "href").contains("regist/login") }
        if (hasLoginLink || WebRegex.matches(loginWall, wallText)) return FlooChapterOutcome.Locked("login")

        // VIP text is drawn as images. The server HTML only has loading placeholders plus
        // image_do3(...) calls; the site script builds each URL from those arguments.
        val imageUrls = imageCalls.mapNotNull { imageUrl(it) }.toMutableList()
        // A rendered browser page has already moved the URL into CSS.
        if (imageUrls.isEmpty()) {
            for (part in WebHtml.select(".con_img [style]", content)) {
                val style = WebHtml.attr(part, "style")
                if (!style.contains("Page4VipImage")) continue
                val raw = WebRegex.capture("url\\(\\s*[\"']?([^\"')]+Page4VipImage[^\"')]*)", style) ?: continue
                val resolved = WebHtml.join(pageUrl ?: "https://$bookHost/", raw) ?: continue
                imageUrls.add(resolved)
            }
        }
        if (imageUrls.isNotEmpty()) return FlooChapterOutcome.Images(imageUrls)

        // An image slot without an image, or a subscription offer, is what a signed-in
        // reader sees before buying the chapter.
        if (WebHtml.select(".con_img, .c_c3, .c_c4", content).isNotEmpty() || WebRegex.matches(purchaseWall, wallText)) {
            return FlooChapterOutcome.Locked("purchase")
        }
        val lines = WebHtml.lines(content).map { fixText(it) }
        val joined = lines.joinToString("\n")
        if (joined.isEmpty() || WebRegex.matches(purchaseStart, joined)) return FlooChapterOutcome.Locked("purchase")
        return FlooChapterOutcome.Text(lines)
    }

    fun chapterHtml(lines: List<String>): String = lines.joinToString("\n") { "<p>${WebHtml.escape(it)}</p>" }

    // Images

    /** File extension for image bytes, or null when the bytes are not an image (e.g. a login page). */
    fun imageExtension(data: ByteArray): String? {
        fun at(i: Int): Int = if (i < data.size) data[i].toInt() and 0xFF else -1
        if (data.size >= 4 && at(0) == 0x47 && at(1) == 0x49 && at(2) == 0x46) return "gif"
        if (data.size >= 4 && at(0) == 0x89 && at(1) == 0x50 && at(2) == 0x4E && at(3) == 0x47) return "png"
        if (data.size >= 3 && at(0) == 0xFF && at(1) == 0xD8 && at(2) == 0xFF) return "jpg"
        if (data.size >= 12 && at(0) == 0x52 && at(1) == 0x49 && at(2) == 0x46 && at(3) == 0x46 &&
            at(8) == 0x57 && at(9) == 0x45 && at(10) == 0x42 && at(11) == 0x50
        ) return "webp"
        return null
    }

    /** `images/<chapter 4 digits>_<n 2 digits>.<ext>`. */
    fun imageFilename(chapterIndex: Int, number: Int, ext: String): String =
        "images/${chapterIndex.toString().padStart(4, '0')}_${number.toString().padStart(2, '0')}.$ext"

    fun imageHtml(filenames: List<String>): String =
        filenames.joinToString("\n") { "<p><img src=\"${WebHtml.escape(it)}\" alt=\"\"/></p>" }
}
