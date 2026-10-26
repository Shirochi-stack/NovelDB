package com.shirochi.npia.sources.http

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ImageRef
import org.jsoup.Jsoup
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.nodes.TextNode
import java.text.Normalizer

/** One paragraph or image of a chapter, in reading order. */
sealed class ReaderBlock {
    data class Text(val value: String) : ReaderBlock()
    data class Image(val url: String) : ReaderBlock()
}

/**
 * Port of the desktop `_reader_html_blocks`, `_reader_plain_blocks` and
 * `_reader_chapter_result` helpers shared by the HTTP sources.
 */
object ReaderBlocks {
    private val blockTags = setOf(
        "p", "div", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6",
        "table", "tr", "section", "article", "center", "pre",
    )

    /**
     * Splits reader HTML into blocks. Raw newlines inside text nodes are line breaks,
     * as on readers whose paragraphs use `white-space: pre-line`.
     */
    fun fromHtml(fragment: String, baseUrl: String?): List<ReaderBlock> {
        val document = Jsoup.parseBodyFragment(fragment, baseUrl ?: "")
        document.select("script, style, noscript, button").remove()
        val blocks = mutableListOf<ReaderBlock>()
        val line = StringBuilder()

        fun flush() {
            val text = collapseSpaces(line.toString().replace('\u00a0', ' '))
            line.setLength(0)
            if (text.isNotEmpty()) blocks += ReaderBlock.Text(text)
        }

        fun walk(node: Node) {
            for (child in node.childNodes()) {
                if (child is TextNode) {
                    child.wholeText.split("\n").forEachIndexed { index, part ->
                        if (index > 0) flush()
                        line.append(part)
                    }
                    continue
                }
                if (child !is Element) continue
                when (val name = child.tagName().lowercase()) {
                    "br" -> flush()
                    "img" -> {
                        val src = listOf(child.attr("data-src"), child.attr("data-original"), child.attr("src"))
                            .map { it.trim() }
                            .firstOrNull { it.isNotEmpty() } ?: ""
                        val url = resolve(src, baseUrl)
                        if (url != null) {
                            flush()
                            blocks += ReaderBlock.Image(url)
                        }
                    }
                    else -> if (name in blockTags) {
                        flush()
                        walk(child)
                        flush()
                    } else {
                        walk(child)
                    }
                }
            }
        }

        walk(document.body())
        flush()
        return blocks
    }

    fun fromPlainText(text: String): List<ReaderBlock> =
        text.replace("\r\n", "\n")
            .split("\n")
            .map { it.replace('\u00a0', ' ').trim() }
            .filter { it.isNotEmpty() }
            .map { ReaderBlock.Text(it) }

    /** Builds the chapter body. A leading paragraph that repeats the chapter title is dropped. */
    fun content(blocks: List<ReaderBlock>, ref: ChapterRef, cssClass: String): ChapterContent {
        var list = blocks
        val first = list.firstOrNull()
        if (first is ReaderBlock.Text && normalizeTitle(first.value) == normalizeTitle(ref.title)) {
            list = list.drop(1)
        }
        val parts = mutableListOf<String>()
        val images = mutableListOf<ImageRef>()
        val names = mutableMapOf<String, String>()
        for (block in list) {
            when (block) {
                is ReaderBlock.Text -> parts += "<p>${escape(block.value)}</p>"
                is ReaderBlock.Image -> {
                    val name = names.getOrPut(block.url) {
                        imageFilename(block.url, ref.index, images.size + 1).also { images += ImageRef(block.url, it) }
                    }
                    parts += "<p class=\"img\"><img src=\"${escape(name)}\" alt=\"\"/></p>"
                }
            }
        }
        val html = "<div class=\"$cssClass\">\n" + parts.joinToString("\n") + "\n</div>"
        return ChapterContent(ref, html, images)
    }

    /** `images/0001_03.jpg`: chapter index, image number, and the URL's extension. */
    fun imageFilename(url: String, chapter: Int, number: Int): String {
        val last = HttpSourceSupport.path(url).substringAfterLast('/')
        var ext = if (last.contains('.')) last.substringAfterLast('.').lowercase() else ""
        if (ext.isEmpty() || ext.length > 5 || !ext.all { it.isLetterOrDigit() }) ext = "jpg"
        return "images/${chapter.toString().padStart(4, '0')}_${number.toString().padStart(2, '0')}.$ext"
    }

    /** NFKC, no whitespace, lower case: the desktop `_normalize_title_for_match`. */
    fun normalizeTitle(value: String): String =
        Normalizer.normalize(value, Normalizer.Form.NFKC).filterNot { it.isWhitespace() }.lowercase()

    fun collapseSpaces(value: String): String =
        value.replace(Regex("[ \\t\\r\\f\\x0B]+"), " ").trim()

    fun escape(value: String, quote: Boolean = true): String {
        var out = value.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        if (quote) out = out.replace("\"", "&quot;").replace("'", "&#x27;")
        return out
    }

    /** Paragraphs for a synopsis, one `<p>` per non-blank line. */
    fun paragraphs(text: String): String =
        text.split("\n").filter { it.isNotBlank() }.joinToString("") { "<p>${escape(it)}</p>" }

    /** Absolute http(s) URL for `value`, or null. */
    fun resolve(value: String, base: String?): String? {
        val url = HttpSourceSupport.resolve(value, base) ?: return null
        val scheme = HttpSourceSupport.scheme(url)?.lowercase() ?: url.substringBefore(':').lowercase()
        return if (scheme == "http" || scheme == "https") url else null
    }
}
