package com.shirochi.npia.output

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef

/** Helpers shared by the EPUB and HTML writers. */
internal object OutputSupport {
    /** Stylesheet the desktop npia downloader embeds in every EPUB. */
    val css = """
        div.svg_outer { display: block; margin: 0; padding: 0; text-align: left; }
        div.svg_inner { display: block; text-align: center; }
        h1, h2 { text-align: center; margin-bottom: 10%; margin-top: 10%; }
        h3, h4, h5, h6 { text-align: center; margin-bottom: 15%; margin-top: 10%; }
        ol, ul { padding-left: 8%; }
        body { margin: 2%; }
        p { overflow-wrap: break-word; }
        dd, dt, dl { padding: 0; margin: 0; }
        img { display: block; min-height: 1em; max-height: 100%; max-width: 100%; padding: 0; margin: 2% auto; }
        img.inline { display: inline; min-height: 1em; margin-bottom: 0; margin-top: 0; }
        .thumbcaption { display: block; font-size: 0.9em; padding-right: 5%; padding-left: 5%; }
        hr { color: black; background-color: black; height: 2px; }
        a:link { text-decoration: none; color: #0B0080; }
        a:visited { text-decoration: none; }
        a:hover, a:active { text-decoration: underline; }
        table { width: 90%; border-collapse: collapse; }
        table, th, td { border: 1px solid black; }
    """.trimIndent()

    fun escape(text: String): String {
        val out = StringBuilder(text.length)
        for (ch in text) {
            when (ch) {
                '&' -> out.append("&amp;")
                '<' -> out.append("&lt;")
                '>' -> out.append("&gt;")
                '"' -> out.append("&quot;")
                '\'' -> out.append("&#39;")
                else -> out.append(ch)
            }
        }
        return out.toString()
    }

    /** Heading text: "N. title" with `numberChapters`, never for notices (matches `_numbered_title`). */
    fun displayTitle(ref: ChapterRef, numbered: Boolean): String = when {
        ref.isNotice -> ref.title
        ref.title.isEmpty() -> "Chapter ${ref.index}"
        numbered -> "${ref.index}. ${ref.title}"
        else -> ref.title
    }

    /** Notices first, otherwise the order the pipeline gave. */
    fun readingOrder(chapters: List<ChapterContent>): List<ChapterContent> =
        chapters.filter { it.ref.isNotice } + chapters.filter { !it.ref.isNotice }

    fun mimeType(filename: String): String = when (filename.substringAfterLast('/').substringAfterLast('.', "").lowercase()) {
        "jpg", "jpeg" -> "image/jpeg"
        "png" -> "image/png"
        "gif" -> "image/gif"
        "webp" -> "image/webp"
        "avif" -> "image/avif"
        "svg" -> "image/svg+xml"
        else -> "application/octet-stream"
    }

    private val imgTag = Regex("""<img\b[^>]*?\bsrc=(["'])(.*?)\1[^>]*>""", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))

    /**
     * Rewrites `<img src>` values that name local images. Images the bundle lacks (failed
     * downloads) are dropped so the output never points at a missing file; remote URLs stay.
     */
    fun rewriteImages(html: String, localNames: Set<String>, available: Map<String, ByteArray>, replacement: (String) -> String): String =
        imgTag.replace(html) { match ->
            val tag = match.value
            val srcRange = match.groups[2]!!.range
            val src = match.groupValues[2]
            when {
                available.containsKey(src) -> {
                    val start = match.range.first
                    tag.substring(0, srcRange.first - start) + replacement(src) + tag.substring(srcRange.last + 1 - start)
                }
                // A local image that was never downloaded.
                src in localNames || !(src.contains(':') || src.startsWith("//")) -> ""
                else -> tag
            }
        }

    private val xmlEntities = setOf("amp", "lt", "gt", "quot", "apos")
    private val namedEntities = mapOf(
        "nbsp" to 160, "middot" to 183, "hellip" to 8230, "lsquo" to 8216, "rsquo" to 8217, "ldquo" to 8220,
        "rdquo" to 8221, "ndash" to 8211, "mdash" to 8212, "copy" to 169, "reg" to 174, "trade" to 8482,
        "laquo" to 171, "raquo" to 187, "times" to 215, "deg" to 176, "bull" to 8226,
    )
    private val entity = Regex("""&(#[0-9]+;|#[xX][0-9a-fA-F]+;|([A-Za-z][A-Za-z0-9]*);)?""")
    private val voidTag = Regex(
        """<(br|hr|img|meta|link|input|col|area|source|wbr)\b((?:[^>"']|"[^"]*"|'[^']*')*?)\s*/?>""",
        RegexOption.IGNORE_CASE,
    )

    /**
     * Makes an HTML body fragment well-formed enough for XHTML: HTML-only entities become numeric,
     * stray `&` is escaped, and void elements are self-closed.
     */
    fun xhtmlFragment(html: String): String {
        val out = entity.replace(html) { match ->
            val name = match.groups[2]?.value
            when {
                match.groups[1] == null -> "&amp;"
                name == null -> match.value
                name in xmlEntities -> match.value
                else -> namedEntities[name]?.let { "&#$it;" } ?: "&amp;$name;"
            }
        }
        return voidTag.replace(out) { "<${it.groupValues[1]}${it.groupValues[2]}/>" }
    }
}
