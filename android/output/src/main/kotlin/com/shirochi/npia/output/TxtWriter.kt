package com.shirochi.npia.output

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.OutputFormat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/** Plain-text output. With `separateTxt`, writes one file per chapter into a folder. */
class TxtWriter : BookWriter {
    override val format = OutputFormat.TXT

    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = withContext(Dispatchers.IO) {
        val shared = bundle.settings.effective(bundle.book.source)
        val text = bundle.settings.text
        directory.mkdirs()

        fun render(chapter: ChapterContent): String {
            val title = if (shared.numberChapters && !chapter.ref.isNotice) "${chapter.ref.index}. ${chapter.ref.title}" else chapter.ref.title
            var body = HtmlText.plainText(chapter.html)
            if (text.stripLeadingSpaces) {
                body = body.lines().joinToString("\n") { it.trimStart(' ', '　', '\t') }
            }
            if (text.removeNewlines) {
                body = body.lines().filter { it.isNotBlank() }.joinToString("\n")
            } else if (text.blankParagraphLines) {
                body = body.lines().filter { it.isNotBlank() }.joinToString("\n\n")
            }
            return "$title\n\n$body\n"
        }

        if (shared.separateTxt) {
            val folder = File(directory, baseName).apply { mkdirs() }
            for (chapter in bundle.chapters) {
                File(folder, "%04d.txt".format(chapter.ref.index)).writeText(render(chapter))
            }
            return@withContext folder
        }

        val header = buildString {
            append(bundle.book.title).append('\n')
            bundle.book.author?.let { append(it).append('\n') }
            bundle.book.synopsis?.let { append('\n').append(it).append('\n') }
        }
        val file = File(directory, "$baseName.txt")
        file.writeText((listOf(header) + bundle.chapters.map(::render)).joinToString("\n\n"))
        file
    }
}

object HtmlText {
    private val blockBreaks = Regex("""(?i)<\s*(br|/p|/div|/h[1-6]|/li|/blockquote)\b[^>]*>""")
    private val scripts = Regex("""(?is)<(script|style)\b.*?</\1>""")
    private val tags = Regex("""<[^>]+>""")
    private val blankRuns = Regex("""\n{3,}""")
    private val entities = mapOf("&nbsp;" to " ", "&lt;" to "<", "&gt;" to ">", "&quot;" to "\"", "&#39;" to "'", "&apos;" to "'")

    /** Rough HTML-to-text for chapter bodies: block tags become newlines, tags are dropped, entities decoded. */
    fun plainText(html: String): String {
        var text = html.replace(blockBreaks, "\n").replace(scripts, "").replace(tags, "")
        for ((entity, value) in entities) text = text.replace(entity, value)
        text = text.replace("&amp;", "&").replace("\r\n", "\n").replace(blankRuns, "\n\n")
        return text.trim()
    }
}
