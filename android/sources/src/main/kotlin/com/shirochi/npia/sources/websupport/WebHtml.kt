package com.shirochi.npia.sources.websupport

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.HttpStatusException
import org.jsoup.Jsoup
import org.jsoup.nodes.Document
import org.jsoup.nodes.Element
import org.jsoup.nodes.Node
import org.jsoup.nodes.TextNode
import java.net.URI
import java.nio.ByteBuffer
import java.nio.charset.Charset
import java.nio.charset.CharacterCodingException
import java.nio.charset.CodingErrorAction

/** Helpers shared by the web-engine sources (BeautifulSoup and `html` module stand-ins). */
internal object WebHtml {
    /** `html.escape(value, quote=True)`. */
    fun escape(value: String): String {
        val result = StringBuilder(value.length)
        for (character in value) {
            when (character) {
                '&' -> result.append("&amp;")
                '<' -> result.append("&lt;")
                '>' -> result.append("&gt;")
                '"' -> result.append("&quot;")
                '\'' -> result.append("&#x27;")
                else -> result.append(character)
            }
        }
        return result.toString()
    }

    /** One `<p>` per non-empty line, escaped. */
    fun paragraphs(lines: List<String>): String =
        lines.map { it.trim() }.filter { it.isNotEmpty() }.joinToString("\n") { "<p>${escape(it)}</p>" }

    val GB18030: Charset = Charset.forName("GB18030")

    private val charsetPattern = Regex("""charset\s*=\s*["']?([a-zA-Z0-9_-]+)""", RegexOption.IGNORE_CASE)

    /** Decodes page bytes using the declared charset, then UTF-8, then GB18030. */
    fun decode(data: ByteArray): String {
        val head = String(data, 0, minOf(data.size, 4096), Charsets.UTF_8)
        charsetPattern.find(head)?.groupValues?.get(1)?.let { name ->
            encoding(name)?.let { charset -> strict(data, charset)?.let { return it } }
        }
        strict(data, Charsets.UTF_8)?.let { return it }
        strict(data, GB18030)?.let { return it }
        return String(data, Charsets.UTF_8)
    }

    fun encoding(name: String): Charset? = when (name.lowercase()) {
        "utf-8", "utf8" -> Charsets.UTF_8
        "gbk", "gb2312", "gb18030" -> GB18030
        "sjis" -> Charset.forName("Shift_JIS")
        "latin1" -> Charsets.ISO_8859_1
        else -> runCatching { Charset.forName(name) }.getOrNull()
    }

    /** Decodes only when every byte is valid in `charset` (Swift's `String(data:encoding:)`). */
    fun strict(data: ByteArray, charset: Charset): String? = try {
        charset.newDecoder()
            .onMalformedInput(CodingErrorAction.REPORT)
            .onUnmappableCharacter(CodingErrorAction.REPORT)
            .decode(ByteBuffer.wrap(data)).toString()
    } catch (e: CharacterCodingException) {
        null
    }

    fun parse(html: String, baseUrl: String? = null): Document = Jsoup.parse(html, baseUrl ?: "")

    /** `node.get_text(' ', strip=True)`: whitespace collapsed, trimmed. */
    fun text(element: Element?): String = element?.text()?.trim() ?: ""

    /**
     * `node.get_text('\n', strip=True)`: one line per text run and `<br>`/block boundary, each
     * trimmed, empty lines dropped.
     */
    fun lines(element: Element?): List<String> {
        if (element == null) return emptyList()
        val buffer = StringBuilder()
        appendLines(element, buffer)
        return buffer.split("\n").map { it.replace('\u00A0', ' ').trim() }.filter { it.isNotEmpty() }
    }

    private val blockTags = setOf(
        "p", "div", "br", "li", "ul", "ol", "dd", "dt", "dl", "tr", "table", "section", "article",
        "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "header", "footer", "hr",
    )

    private fun appendLines(node: Node, buffer: StringBuilder) {
        for (child in node.childNodes()) {
            if (child is TextNode) {
                buffer.append('\n').append(child.wholeText).append('\n')
            } else if (child is Element) {
                val tag = child.tagName().lowercase()
                if (tag in setOf("script", "style", "noscript", "template")) continue
                val block = tag in blockTags
                if (block) buffer.append('\n')
                appendLines(child, buffer)
                if (block) buffer.append('\n')
            }
        }
    }

    /** Removes every element matching `selector` inside `element`. */
    fun removeAll(selector: String, element: Element) {
        runCatching { element.select(selector).remove() }
    }

    fun attr(element: Element?, name: String): String = element?.attr(name)?.trim() ?: ""

    fun selectFirst(selector: String, element: Element): Element? = runCatching { element.selectFirst(selector) }.getOrNull()

    fun select(selector: String, element: Element): List<Element> = runCatching { element.select(selector).toList() }.getOrDefault(emptyList())

    /** `urllib.parse.urljoin`. */
    fun join(base: String?, href: String): String? {
        val trimmed = href.trim()
        if (trimmed.isEmpty()) return null
        return runCatching {
            if (base.isNullOrEmpty()) URI(trimmed).toString() else URI(base).resolve(URI(trimmed.replace(" ", "%20"))).toString()
        }.getOrNull()
    }
}

/** Small regex helpers (Python `re` stand-ins). */
internal object WebRegex {
    fun firstMatch(pattern: String, text: String, vararg options: RegexOption): List<String?>? {
        val match = Regex(pattern, options.toSet()).find(text) ?: return null
        return match.groups.map { it?.value }
    }

    /** First capture group (or the whole match when the pattern has no groups). */
    fun capture(pattern: String, text: String, vararg options: RegexOption): String? {
        val groups = firstMatch(pattern, text, *options) ?: return null
        return if (groups.size > 1) groups[1] else groups[0]
    }

    fun matches(pattern: String, text: String, vararg options: RegexOption): Boolean =
        Regex(pattern, options.toSet()).containsMatchIn(text)

    fun allMatches(pattern: String, text: String, vararg options: RegexOption): List<List<String?>> =
        Regex(pattern, options.toSet()).findAll(text).map { m -> m.groups.map { it?.value } }.toList()

    fun replace(pattern: String, text: String, replacement: String, vararg options: RegexOption): String =
        Regex(pattern, options.toSet()).replace(text, replacement)
}

/** GET that throws `HttpStatusException` for non-2xx responses. */
internal suspend fun HttpFetching.fetchOk(url: String, headers: Map<String, String> = emptyMap()): HttpResponse {
    val response = get(url, headers)
    if (response.status !in 200..299) throw HttpStatusException(response.status, url)
    return response
}
