package com.shirochi.npia.output

import com.shirochi.npia.core.OutputFormat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.Base64

/**
 * One self-contained `.html` file: cover, info, a linked table of contents, then every chapter.
 * Local images are embedded as base64 `data:` URIs.
 */
class HtmlWriter : BookWriter {
    override val format = OutputFormat.HTML

    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = withContext(Dispatchers.IO) {
        directory.mkdirs()
        val file = File(directory, "$baseName.html")
        val temp = File(directory, "$baseName.html.part")
        temp.writeText(render(bundle))
        if (!temp.renameTo(file)) {
            temp.copyTo(file, overwrite = true)
            temp.delete()
        }
        file
    }

    companion object {
        fun dataUri(data: ByteArray, filename: String): String =
            "data:${OutputSupport.mimeType(filename)};base64,${Base64.getEncoder().encodeToString(data)}"

        fun render(bundle: BookBundle): String {
            val book = bundle.book
            val numbered = bundle.settings.effective(book.source).numberChapters
            val esc = OutputSupport::escape
            val chapters = OutputSupport.readingOrder(bundle.chapters)
            val localNames = bundle.chapters.flatMap { chapter -> chapter.images.map { it.filename } }.toSet()

            val html = StringBuilder()
            html.append("<!DOCTYPE html>\n<html lang=\"${esc(book.language)}\">\n<head>\n<meta charset=\"utf-8\"/>\n")
            html.append("<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/>\n")
            html.append("<title>${esc(book.title)}</title>\n<style>\n${OutputSupport.css}\n")
            html.append("body { max-width: 48em; margin: 0 auto; padding: 1em; line-height: 1.7; }\n")
            html.append(".chapter { page-break-before: always; }\n</style>\n</head>\n<body>\n\n")

            val cover = bundle.cover
            if (cover != null) {
                html.append("""<div class="cover" style="text-align:center"><img alt="Cover" src="${dataUri(cover.data, "cover." + cover.fileExtension)}"/></div>""").append('\n')
            } else if (book.coverUrl != null) {
                html.append("""<div class="cover" style="text-align:center"><img alt="Cover" src="${esc(book.coverUrl!!)}"/></div>""").append('\n')
            }

            html.append("<header class=\"info\"><h1>${esc(book.title)}</h1>\n")
            book.author?.let { html.append("<p><b>Author:</b> ${esc(it)}</p>\n") }
            book.status?.takeIf { it.isNotEmpty() }?.let { html.append("<p><b>Status:</b> ${esc(it)}</p>\n") }
            if (book.tags.isNotEmpty()) html.append("<p><b>Tags:</b> ${esc(book.tags.joinToString(", "))}</p>\n")
            book.synopsis?.takeIf { it.isNotEmpty() }?.let { synopsis ->
                html.append("<p class=\"synopsis\">${synopsis.split("\n").joinToString("<br/>") { esc(it) }}</p>\n")
            }
            html.append("</header>\n")

            val anchors = chapters.mapIndexed { offset, chapter ->
                if (chapter.ref.isNotice) "notice-${offset + 1}" else "chapter-${chapter.ref.index}-${offset + 1}"
            }
            html.append("<nav class=\"toc\"><h2>Contents</h2><ol>\n")
            chapters.forEachIndexed { offset, chapter ->
                html.append("<li><a href=\"#${anchors[offset]}\">${esc(OutputSupport.displayTitle(chapter.ref, numbered))}</a></li>\n")
            }
            html.append("</ol></nav>\n")

            chapters.forEachIndexed { offset, chapter ->
                val title = OutputSupport.displayTitle(chapter.ref, numbered)
                val body = OutputSupport.rewriteImages(chapter.html, localNames, bundle.images) { name ->
                    dataUri(bundle.images.getValue(name), name)
                }
                html.append("<section class=\"chapter\" id=\"${anchors[offset]}\">\n<h2>${esc(title)}</h2>\n$body\n</section>\n")
            }
            html.append("</body>\n</html>\n")
            return html.toString()
        }
    }
}
