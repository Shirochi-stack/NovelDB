package com.shirochi.npia.output

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceId
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.Base64
import java.util.zip.ZipFile
import javax.xml.parsers.DocumentBuilderFactory

private val PNG = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A, 1, 2, 3)

/** Shared fixture: one notice, two chapters (one image present, one missing), and a cover. */
internal fun makeWriterBundle(numbered: Boolean = true, cover: Boolean = true): BookBundle {
    val book = BookInfo(
        source = SourceId.NPIA, id = "123", url = "https://example.com/novel/123", title = "Tower & Me",
        author = "Writer <Kim>", synopsis = "Line one\nLine two", coverUrl = "https://example.com/c.jpg",
        tags = listOf("Fantasy", "Regression"), status = "Completed",
    )
    val image = ImageRef("https://example.com/a.png", "images/0002_01.png")
    val missing = ImageRef("https://example.com/b.png", "images/0002_02.png")
    val chapters = listOf(
        ChapterContent(ChapterRef("1", 1, "Start"), "<p>Hello&nbsp;world &amp; more<br>next</p>"),
        ChapterContent(
            ChapterRef("2", 2, "Pictures"),
            """<p><img alt="1" src="images/0002_01.png" width="100%"/>a</p><p><img alt="2" src="images/0002_02.png"/>b</p><p><img class="remote-image" src="https://example.com/r.png"/></p>""",
            listOf(image, missing),
        ),
        ChapterContent(ChapterRef("901", 1, "Notice: Hi", isNotice = true), "<p>notice body</p>"),
    )
    return BookBundle(
        book = book, chapters = chapters, images = mapOf("images/0002_01.png" to PNG),
        cover = if (cover) BookBundle.Cover(byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte()), "jpg") else null,
        settings = AppSettings(shared = SharedSettings(numberChapters = numbered)),
    )
}

class EpubWriterTest {
    @get:Rule val tmp = TemporaryFolder()

    private fun ZipFile.read(path: String): String {
        val entry = getEntry(path) ?: throw AssertionError("missing $path")
        return getInputStream(entry).use { it.readBytes().decodeToString() }
    }

    private fun assertWellFormed(xml: String, name: String) {
        val factory = DocumentBuilderFactory.newInstance().apply {
            isNamespaceAware = true
            setFeature("http://apache.org/xml/features/nonvalidating/load-external-dtd", false)
        }
        try {
            factory.newDocumentBuilder().apply { setErrorHandler(null) }.parse(xml.byteInputStream())
        } catch (e: Exception) {
            throw AssertionError("$name is not well-formed XML: ${e.message}")
        }
    }

    @Test
    fun structure() = runTest {
        val file = EpubWriter().write(makeWriterBundle(), "book", tmp.root)
        assertEquals("book.epub", file.name)
        assertFalse(File(tmp.root, "book.epub.part").exists())

        // The mimetype entry is first and stored: local header at offset 0, method 0, name at 30.
        val raw = file.readBytes()
        assertEquals(listOf(0x50, 0x4B, 0x03, 0x04), raw.take(4).map { it.toInt() and 0xFF })
        assertEquals(0, raw[8].toInt())
        assertEquals(0, raw[9].toInt())
        val nameLength = (raw[26].toInt() and 0xFF) or ((raw[27].toInt() and 0xFF) shl 8)
        val extraLength = (raw[28].toInt() and 0xFF) or ((raw[29].toInt() and 0xFF) shl 8)
        assertEquals("mimetype", raw.copyOfRange(30, 30 + nameLength).decodeToString())
        val contentStart = 30 + nameLength + extraLength
        assertEquals("application/epub+zip", raw.copyOfRange(contentStart, contentStart + 20).decodeToString())

        ZipFile(file).use { zip ->
            val paths = zip.entries().toList().map { it.name }
            assertEquals("mimetype", paths.first())
            assertEquals(
                setOf(
                    "mimetype", "META-INF/container.xml", "OEBPS/content.opf", "OEBPS/nav.xhtml", "OEBPS/toc.ncx",
                    "OEBPS/Styles/style.css", "OEBPS/Text/cover.xhtml", "OEBPS/Text/info.xhtml",
                    "OEBPS/Text/chapter_notice0001.xhtml", "OEBPS/Text/chapter0001.xhtml", "OEBPS/Text/chapter0002.xhtml",
                    "OEBPS/images/cover.jpg", "OEBPS/images/0002_01.png",
                ),
                paths.toSet(),
            )

            for (path in paths.filter { it.endsWith(".xhtml") || it.endsWith(".opf") || it.endsWith(".ncx") || it.endsWith(".xml") }) {
                assertWellFormed(zip.read(path), path)
            }

            val opf = zip.read("OEBPS/content.opf")
            assertTrue(opf.contains("<package version=\"3.0\""))
            assertTrue(opf.contains("<dc:title>Tower &amp; Me</dc:title>"))
            assertTrue(opf.contains("<dc:creator>Writer &lt;Kim&gt;</dc:creator>"))
            assertTrue(opf.contains("<dc:subject>Regression</dc:subject>"))
            assertTrue(opf.contains("<dc:language>ko</dc:language>"))
            assertTrue(opf.contains("properties=\"nav\""))
            assertTrue(opf.contains("href=\"images/cover.jpg\" media-type=\"image/jpeg\" properties=\"cover-image\""))
            assertTrue(opf.contains("href=\"images/0002_01.png\" media-type=\"image/png\""))
            assertTrue(opf.contains("property=\"dcterms:modified\""))
            // Spine: cover, info, notices, then chapters.
            val spine = opf.split("<itemref idref=\"").drop(1).map { it.substringBefore('"') }
            assertEquals(listOf("coverpage", "info", "chapter_notice0001", "chapter0001", "chapter0002"), spine)

            val nav = zip.read("OEBPS/nav.xhtml")
            assertTrue(nav.contains("epub:type=\"toc\""))
            assertTrue(nav.contains("<a href=\"Text/chapter0001.xhtml\">1. Start</a>"))
            assertTrue(nav.contains("<a href=\"Text/chapter_notice0001.xhtml\">Notice: Hi</a>"))
            assertTrue(zip.read("OEBPS/toc.ncx").contains("<content src=\"Text/chapter0002.xhtml\"/>"))

            val first = zip.read("OEBPS/Text/chapter0001.xhtml")
            assertTrue(first.startsWith("<?xml"))
            assertTrue(first.contains("<h1>1. Start</h1>"))
            assertTrue(first.contains("Hello&#160;world &amp; more<br/>next"))

            val second = zip.read("OEBPS/Text/chapter0002.xhtml")
            assertTrue(second.contains("src=\"../images/0002_01.png\""))
            assertFalse("missing images are dropped", second.contains("0002_02.png"))
            assertTrue(second.contains("src=\"https://example.com/r.png\""))

            assertTrue(zip.read("OEBPS/Text/cover.xhtml").contains("src=\"../images/cover.jpg\""))
            val info = zip.read("OEBPS/Text/info.xhtml")
            assertTrue(info.contains("Completed"))
            assertTrue(info.contains("Line one<br/>Line two"))
        }
    }

    @Test
    fun unnumberedWithRemoteCover() = runTest {
        val file = EpubWriter().write(makeWriterBundle(numbered = false, cover = false), "plain", tmp.root)
        ZipFile(file).use { zip ->
            assertTrue(zip.read("OEBPS/Text/chapter0001.xhtml").contains("<h1>Start</h1>"))
            assertTrue(zip.read("OEBPS/Text/cover.xhtml").contains("src=\"https://example.com/c.jpg\""))
            assertFalse(zip.read("OEBPS/content.opf").contains("cover-image"))
            assertNull(zip.getEntry("OEBPS/images/cover.jpg"))
        }
    }

    @Test
    fun xhtmlFragmentFixesEntitiesAndVoidTags() {
        assertEquals(
            "a &amp; b&#160;&amp;bogus; &lt;<br/><img src=\"x\"/><hr class=\"a\"/>",
            OutputSupport.xhtmlFragment("a & b&nbsp;&bogus; &lt;<br><img src=\"x\"><hr class=\"a\" />"),
        )
    }
}

class HtmlWriterTest {
    @get:Rule val tmp = TemporaryFolder()

    @Test
    fun singleFileEmbedsImages() = runTest {
        val file = HtmlWriter().write(makeWriterBundle(), "book", tmp.root)
        assertEquals("book.html", file.name)
        val html = file.readText()
        assertTrue(html.startsWith("<!DOCTYPE html>"))
        assertTrue(html.contains("<title>Tower &amp; Me</title>"))
        val png = Base64.getEncoder().encodeToString(PNG)
        assertTrue(html.contains("src=\"data:image/png;base64,$png\""))
        assertTrue("cover is embedded", html.contains("src=\"data:image/jpeg;base64,/9j/4A==\""))
        assertFalse("no local file references remain", html.contains("images/0002_"))
        assertTrue(html.contains("src=\"https://example.com/r.png\""))

        // TOC links resolve to sections, notices first.
        val notice = html.indexOf("<h2>Notice: Hi</h2>")
        val first = html.indexOf("<h2>1. Start</h2>")
        assertTrue(notice in 0 until first)
        assertTrue(html.contains("<a href=\"#notice-1\">Notice: Hi</a>"))
        assertTrue(html.contains("<section class=\"chapter\" id=\"notice-1\">"))
        assertTrue(html.contains("<a href=\"#chapter-2-3\">2. Pictures</a>"))
        assertTrue(html.contains("id=\"chapter-2-3\""))
    }

    @Test
    fun writersAreRegistered() {
        assertTrue(Writers.writer(com.shirochi.npia.core.OutputFormat.EPUB) is EpubWriter)
        assertTrue(Writers.writer(com.shirochi.npia.core.OutputFormat.HTML) is HtmlWriter)
    }
}
