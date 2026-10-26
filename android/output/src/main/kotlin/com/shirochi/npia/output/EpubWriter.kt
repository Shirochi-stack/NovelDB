package com.shirochi.npia.output

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.OutputFormat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.time.Instant
import java.time.temporal.ChronoUnit
import java.util.UUID
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * EPUB 3 (with an EPUB 2 `toc.ncx` for older readers), laid out like the desktop
 * `epub_generator.py` and the iOS `EPUBWriter`: `OEBPS/content.opf`, `Styles/style.css`,
 * `Text/cover.xhtml`, an info page, then `Text/chapter_notice0001.xhtml`... and
 * `Text/chapter0001.xhtml`... Images keep their `ImageRef.filename` under `OEBPS/`.
 */
class EpubWriter : BookWriter {
    override val format = OutputFormat.EPUB

    internal class Page(val id: String, val filename: String, val title: String, val xhtml: String)

    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = withContext(Dispatchers.IO) {
        directory.mkdirs()
        val file = File(directory, "$baseName.epub")
        if (file.exists()) file.delete()

        val book = bundle.book
        val numbered = bundle.settings.effective(book.source).numberChapters
        val identifier = "urn:uuid:" + UUID.randomUUID().toString().lowercase()
        val coverName = bundle.cover?.let { "images/cover." + it.fileExtension.lowercase() }

        // Chapters.
        val localNames = bundle.chapters.flatMap { chapter -> chapter.images.map { it.filename } }.toSet()
        val pages = mutableListOf<Page>()
        val used = mutableSetOf<String>()
        var noticeNumber = 0
        for (chapter in OutputSupport.readingOrder(bundle.chapters)) {
            var filename: String
            if (chapter.ref.isNotice) {
                noticeNumber++
                filename = "chapter_notice${pad4(noticeNumber)}.xhtml"
            } else {
                var number = maxOf(1, chapter.ref.index)
                filename = "chapter${pad4(number)}.xhtml"
                while (filename in used) {
                    number++
                    filename = "chapter${pad4(number)}.xhtml"
                }
            }
            used.add(filename)
            val title = OutputSupport.displayTitle(chapter.ref, numbered)
            val body = OutputSupport.rewriteImages(chapter.html, localNames, bundle.images) { "../$it" }
            val xhtml = document(title, "<h1>${OutputSupport.escape(title)}</h1>" + OutputSupport.xhtmlFragment(body))
            pages.add(Page(filename.removeSuffix(".xhtml"), filename, title, xhtml))
        }

        val temp = File(directory, "$baseName.epub.part")
        ZipOutputStream(temp.outputStream().buffered()).use { zip ->
            // The mimetype entry must come first and be stored uncompressed.
            zip.add("mimetype", "application/epub+zip".encodeToByteArray(), compressed = false)
            zip.add("META-INF/container.xml", CONTAINER.encodeToByteArray())
            zip.add("OEBPS/Styles/style.css", OutputSupport.css.encodeToByteArray())

            val coverImage = when {
                coverName != null -> """<div style="text-align:center"><img alt="Cover" src="../$coverName"/></div>"""
                book.coverUrl != null -> """<div style="text-align:center"><img alt="Cover" src="${OutputSupport.escape(book.coverUrl!!)}"/></div>"""
                else -> ""
            }
            zip.add("OEBPS/Text/cover.xhtml", document("Cover", coverImage).encodeToByteArray())
            zip.add("OEBPS/Text/info.xhtml", document(book.title, infoBody(book)).encodeToByteArray())
            for (page in pages) zip.add("OEBPS/Text/${page.filename}", page.xhtml.encodeToByteArray())

            val compressImages = bundle.settings.images.zipCompressImages
            val cover = bundle.cover
            if (cover != null && coverName != null) zip.add("OEBPS/$coverName", cover.data, compressImages)
            val imageNames = bundle.images.keys.filter { it != coverName }.sorted()
            for (name in imageNames) zip.add("OEBPS/$name", bundle.images.getValue(name), compressImages)

            zip.add("OEBPS/nav.xhtml", nav(pages, book).encodeToByteArray())
            zip.add("OEBPS/toc.ncx", ncx(pages, book, identifier).encodeToByteArray())
            zip.add("OEBPS/content.opf", opf(pages, book, identifier, coverName, imageNames).encodeToByteArray())
        }
        if (!temp.renameTo(file)) {
            temp.copyTo(file, overwrite = true)
            temp.delete()
        }
        file
    }

    private fun ZipOutputStream.add(path: String, data: ByteArray, compressed: Boolean = true) {
        val entry = ZipEntry(path)
        if (compressed) {
            entry.method = ZipEntry.DEFLATED
        } else {
            entry.method = ZipEntry.STORED
            entry.size = data.size.toLong()
            entry.compressedSize = data.size.toLong()
            entry.crc = CRC32().apply { update(data) }.value
        }
        putNextEntry(entry)
        write(data)
        closeEntry()
    }

    internal companion object {
        private fun pad4(n: Int) = n.toString().padStart(4, '0')

        val CONTAINER = """
            <?xml version="1.0" encoding="UTF-8"?>
            <container version="1.0" xmlns="urn:oasis:names:tc:opendocument:xmlns:container">
                <rootfiles>
                    <rootfile full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>
                </rootfiles>
            </container>
        """.trimIndent()

        fun document(title: String, body: String): String = listOf(
            """<?xml version="1.0" encoding="utf-8"?>""",
            """<!DOCTYPE html>""",
            """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">""",
            """<head><title>${OutputSupport.escape(title)}</title><link href="../Styles/style.css" type="text/css" rel="stylesheet"/></head>""",
            "<body>$body</body></html>",
        ).joinToString("\n")

        fun infoBody(book: BookInfo): String {
            val esc = OutputSupport::escape
            val html = StringBuilder("<h1>${esc(book.title)}</h1>")
            book.author?.let { html.append("<p><b>Author:</b> ${esc(it)}</p>") }
            book.status?.takeIf { it.isNotEmpty() }?.let { html.append("<p><b>Status:</b> ${esc(it)}</p>") }
            if (book.tags.isNotEmpty()) html.append("<p><b>Tags:</b> ${esc(book.tags.joinToString(", "))}</p>")
            html.append("<p><b>Source:</b> ${esc(book.url)}</p>")
            book.synopsis?.takeIf { it.isNotEmpty() }?.let { synopsis ->
                html.append("<hr/><p>${synopsis.split("\n").joinToString("<br/>") { esc(it) }}</p>")
            }
            return html.toString()
        }

        fun nav(pages: List<Page>, book: BookInfo): String {
            val items = pages.joinToString("\n") { """<li><a href="Text/${it.filename}">${OutputSupport.escape(it.title)}</a></li>""" }
            return listOf(
                """<?xml version="1.0" encoding="utf-8"?>""",
                """<!DOCTYPE html>""",
                """<html xmlns="http://www.w3.org/1999/xhtml" xmlns:epub="http://www.idpf.org/2007/ops">""",
                """<head><title>${OutputSupport.escape(book.title)}</title></head>""",
                """<body>""",
                """<nav epub:type="toc" id="toc"><h1>Contents</h1><ol>""",
                items,
                """</ol></nav>""",
                """<nav epub:type="landmarks" hidden="hidden"><ol>""",
                """<li><a epub:type="cover" href="Text/cover.xhtml">Cover</a></li>""",
                """<li><a epub:type="toc" href="#toc">Contents</a></li>""",
                """</ol></nav>""",
                """</body></html>""",
            ).joinToString("\n")
        }

        fun ncx(pages: List<Page>, book: BookInfo, identifier: String): String {
            val points = pages.mapIndexed { offset, page ->
                listOf(
                    """    <navPoint id="navPoint-${offset + 1}" playOrder="${offset + 1}">""",
                    """        <navLabel><text>${OutputSupport.escape(page.title)}</text></navLabel>""",
                    """        <content src="Text/${page.filename}"/>""",
                    """    </navPoint>""",
                ).joinToString("\n")
            }
            return listOf(
                """<?xml version="1.0" encoding="UTF-8"?>""",
                """<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1">""",
                """<head>""",
                """    <meta name="dtb:uid" content="$identifier"/>""",
                """    <meta name="dtb:depth" content="1"/>""",
                """    <meta name="dtb:totalPageCount" content="0"/>""",
                """    <meta name="dtb:maxPageNumber" content="0"/>""",
                """</head>""",
                """<docTitle><text>${OutputSupport.escape(book.title)}</text></docTitle>""",
                """<navMap>""",
                points.joinToString("\n"),
                """</navMap>""",
                """</ncx>""",
            ).joinToString("\n")
        }

        fun opf(pages: List<Page>, book: BookInfo, identifier: String, coverName: String?, imageNames: List<String>): String {
            val esc = OutputSupport::escape
            val manifest = mutableListOf(
                """<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>""",
                """<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>""",
                """<item id="css" href="Styles/style.css" media-type="text/css"/>""",
                """<item id="coverpage" href="Text/cover.xhtml" media-type="application/xhtml+xml"/>""",
                """<item id="info" href="Text/info.xhtml" media-type="application/xhtml+xml"/>""",
            )
            val spine = mutableListOf("""<itemref idref="coverpage"/>""", """<itemref idref="info"/>""")
            for (page in pages) {
                manifest.add("""<item id="${page.id}" href="Text/${page.filename}" media-type="application/xhtml+xml"/>""")
                spine.add("""<itemref idref="${page.id}"/>""")
            }
            if (coverName != null) {
                manifest.add("""<item id="cover-image" href="${esc(coverName)}" media-type="${OutputSupport.mimeType(coverName)}" properties="cover-image"/>""")
            }
            imageNames.forEachIndexed { offset, name ->
                manifest.add("""<item id="img_${offset + 1}" href="${esc(name)}" media-type="${OutputSupport.mimeType(name)}"/>""")
            }

            val metadata = mutableListOf(
                """<dc:identifier id="BookId">$identifier</dc:identifier>""",
                "<dc:title>${esc(book.title)}</dc:title>",
                "<dc:language>${esc(book.language)}</dc:language>",
            )
            book.author?.let { metadata.add("<dc:creator>${esc(it)}</dc:creator>") }
            book.synopsis?.takeIf { it.isNotEmpty() }?.let { metadata.add("<dc:description>${esc(it)}</dc:description>") }
            for (tag in book.tags) metadata.add("<dc:subject>${esc(tag)}</dc:subject>")
            metadata.add("<dc:source>${esc(book.url)}</dc:source>")
            val modified = Instant.now().truncatedTo(ChronoUnit.SECONDS).toString()
            metadata.add("""<meta property="dcterms:modified">$modified</meta>""")
            if (coverName != null) metadata.add("""<meta name="cover" content="cover-image"/>""")

            return listOf(
                """<?xml version="1.0" encoding="utf-8"?>""",
                """<package version="3.0" unique-identifier="BookId" xmlns="http://www.idpf.org/2007/opf">""",
                """<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">""",
                "    " + metadata.joinToString("\n    "),
                """</metadata>""",
                """<manifest>""",
                "    " + manifest.joinToString("\n    "),
                """</manifest>""",
                """<spine toc="ncx">""",
                "    " + spine.joinToString("\n    "),
                """</spine>""",
                """<guide>""",
                """    <reference type="cover" title="Cover" href="Text/cover.xhtml"/>""",
                """</guide>""",
                """</package>""",
            ).joinToString("\n")
        }
    }
}
