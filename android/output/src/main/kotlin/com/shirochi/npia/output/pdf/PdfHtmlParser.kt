package com.shirochi.npia.output.pdf

/** A styled piece of text inside one paragraph. */
data class PdfRun(val text: String, val bold: Boolean = false, val italic: Boolean = false)

sealed class PdfBlock {
    /** `heading` is the h1-h6 level, null for body text. */
    data class Paragraph(val runs: List<PdfRun>, val heading: Int? = null) : PdfBlock()

    data class Image(val src: String) : PdfBlock()
}

/**
 * Turns a chapter's XHTML fragment into paragraphs and images for the PDF layout (port of the iOS
 * `PDFHTMLParser`). Ruby annotations, scripts, and styles are dropped; whitespace collapses as in
 * HTML. A `<br>` becomes U+2028 inside its paragraph.
 */
object PdfHtmlParser {
    const val LINE_BREAK = '\u2028'

    private val blockTags = setOf(
        "p", "div", "li", "ul", "ol", "blockquote", "section", "article", "header", "footer",
        "table", "tr", "figure", "figcaption", "center", "hr", "pre", "dl", "dt", "dd", "aside", "nav", "body",
    )
    private val skippedTags = setOf("script", "style", "rt", "rp", "head", "title", "noscript")

    fun blocks(html: String): List<PdfBlock> {
        val blocks = mutableListOf<PdfBlock>()
        val runs = mutableListOf<PdfRun>()
        var bold = 0
        var italic = 0
        var skip = 0
        var heading: Int? = null

        fun isCollapsible(c: Char) = c == ' ' || c == LINE_BREAK

        fun flush() {
            while (runs.isNotEmpty() && runs.first().text.all(::isCollapsible)) runs.removeAt(0)
            while (runs.isNotEmpty() && runs.last().text.all(::isCollapsible)) runs.removeAt(runs.lastIndex)
            if (runs.isNotEmpty()) {
                runs[0] = runs[0].copy(text = runs[0].text.trimStart(' ', LINE_BREAK))
                runs[runs.lastIndex] = runs.last().copy(text = runs.last().text.trimEnd(' ', LINE_BREAK))
                blocks += PdfBlock.Paragraph(runs.toList(), heading)
            }
            runs.clear()
        }

        fun appendText(raw: String) {
            if (skip != 0 || raw.isEmpty()) return
            var text = collapse(decodeEntities(raw))
            val previousEndsBlank = runs.lastOrNull()?.text?.lastOrNull()?.let { it == ' ' || it == LINE_BREAK } ?: true
            if (previousEndsBlank && text.startsWith(" ")) text = text.substring(1)
            if (text.isEmpty()) return
            val last = runs.lastOrNull()
            if (last != null && last.bold == (bold > 0) && last.italic == (italic > 0)) {
                runs[runs.lastIndex] = last.copy(text = last.text + text)
            } else {
                runs += PdfRun(text, bold > 0, italic > 0)
            }
        }

        fun appendLineBreak() {
            val isBold = bold > 0
            val isItalic = italic > 0
            runs.lastOrNull()?.let { last ->
                // Drop the space before the break so lines do not start or end with one.
                if (last.text.endsWith(" ")) runs[runs.lastIndex] = last.copy(text = last.text.dropLast(1))
            }
            val last = runs.lastOrNull()
            if (last != null && last.bold == isBold && last.italic == isItalic) {
                runs[runs.lastIndex] = last.copy(text = last.text + LINE_BREAK)
            } else {
                runs += PdfRun(LINE_BREAK.toString(), isBold, isItalic)
            }
        }

        var index = 0
        while (index < html.length) {
            val open = html.indexOf('<', index)
            if (open < 0) {
                appendText(html.substring(index))
                break
            }
            appendText(html.substring(index, open))
            if (html.startsWith("<!--", open)) {
                val end = html.indexOf("-->", open)
                index = if (end < 0) html.length else end + 3
                continue
            }
            val close = html.indexOf('>', open)
            if (close < 0) {
                appendText(html.substring(open))
                break
            }
            val tag = html.substring(open + 1, close)
            index = close + 1

            val closing = tag.startsWith("/")
            val body = if (closing) tag.substring(1) else tag
            val name = body.takeWhile { it.isLetterOrDigit() || it == ':' }.lowercase()
            val selfClosing = tag.endsWith("/")
            if (name.isEmpty()) continue

            if (name in skippedTags) {
                if (!selfClosing) skip = maxOf(0, skip + if (closing) -1 else 1)
                continue
            }
            if (skip != 0) continue

            when (name) {
                "br" -> appendLineBreak()
                "b", "strong" -> if (!selfClosing) bold = maxOf(0, bold + if (closing) -1 else 1)
                "i", "em", "cite" -> if (!selfClosing) italic = maxOf(0, italic + if (closing) -1 else 1)
                "h1", "h2", "h3", "h4", "h5", "h6" -> {
                    flush()
                    heading = if (closing) null else name.substring(1).toInt()
                }
                "img", "image" -> {
                    if (closing) continue
                    val src = attribute(tag, listOf("src", "xlink:href", "href")) ?: continue
                    flush()
                    blocks += PdfBlock.Image(decodeEntities(src))
                }
                else -> if (name in blockTags) flush()
            }
        }
        flush()
        return blocks
    }

    /** HTML whitespace collapsing; keeps no-break and ideographic spaces. */
    fun collapse(text: String): String {
        val out = StringBuilder(text.length)
        var lastWasSpace = false
        for (c in text) {
            if (c == ' ' || c == '\n' || c == '\t' || c == '\r' || c == '\u000C') {
                if (!lastWasSpace) out.append(' ')
                lastWasSpace = true
            } else {
                out.append(c)
                lastWasSpace = false
            }
        }
        return out.toString()
    }

    fun attribute(tag: String, names: List<String>): String? {
        for (name in names) {
            val regex = Regex("""(?:^|\s)${Regex.escape(name)}\s*=\s*(["'])(.*?)\1""", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
            val value = regex.find(tag)?.groupValues?.get(2)
            if (!value.isNullOrEmpty()) return value
        }
        return null
    }

    private val namedEntities = mapOf(
        "amp" to "&", "lt" to "<", "gt" to ">", "quot" to "\"", "apos" to "'", "nbsp" to "\u00a0",
        "hellip" to "\u2026", "mdash" to "\u2014", "ndash" to "\u2013", "lsquo" to "\u2018",
        "rsquo" to "\u2019", "ldquo" to "\u201c", "rdquo" to "\u201d", "middot" to "\u00b7",
        "laquo" to "\u00ab", "raquo" to "\u00bb", "copy" to "\u00a9", "times" to "\u00d7",
    )

    fun decodeEntities(text: String): String {
        if ('&' !in text) return text
        val out = StringBuilder(text.length)
        var i = 0
        while (i < text.length) {
            val c = text[i]
            val semicolon = if (c == '&') text.indexOf(';', i).takeIf { it in (i + 1)..(i + 11) } else null
            if (semicolon == null) {
                out.append(c)
                i += 1
                continue
            }
            val entity = text.substring(i + 1, semicolon)
            val replacement = when {
                entity.startsWith("#x") || entity.startsWith("#X") -> entity.substring(2).toIntOrNull(16)?.let(::codePoint)
                entity.startsWith("#") -> entity.substring(1).toIntOrNull()?.let(::codePoint)
                else -> namedEntities[entity]
            }
            if (replacement != null) {
                out.append(replacement)
                i = semicolon + 1
            } else {
                out.append(c)
                i += 1
            }
        }
        return out.toString()
    }

    private fun codePoint(value: Int): String? =
        if (Character.isValidCodePoint(value) && value !in 0xD800..0xDFFF) String(Character.toChars(value)) else null
}
