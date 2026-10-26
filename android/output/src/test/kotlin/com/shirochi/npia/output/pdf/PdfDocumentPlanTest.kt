package com.shirochi.npia.output.pdf

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.output.BookBundle
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PdfDocumentPlanTest {
    private fun bundle(chapters: List<ChapterContent>, images: Map<String, ByteArray> = emptyMap(), settings: AppSettings = AppSettings()) =
        BookBundle(
            BookInfo(SourceId.READER, "7", "https://example.com/7", "Test Book", author = "Writer", tags = listOf("fantasy"), status = "Ongoing", language = "en"),
            chapters, images, null, settings,
        )

    @Test
    fun parserBlocks() {
        val html = "<h2>Title</h2><p>Hello <b>bold</b>  and<br/>next &#x41;&#66;&lt;</p><ruby>\u6f22<rt>kan</rt></ruby><img src='x.png'><script>bad()</script><!-- note -->"
        assertEquals(
            listOf(
                PdfBlock.Paragraph(listOf(PdfRun("Title")), heading = 2),
                PdfBlock.Paragraph(listOf(PdfRun("Hello "), PdfRun("bold", bold = true), PdfRun(" and${PdfHtmlParser.LINE_BREAK}next AB<"))),
                PdfBlock.Paragraph(listOf(PdfRun("\u6f22"))),
                PdfBlock.Image("x.png"),
            ),
            PdfHtmlParser.blocks(html),
        )
    }

    @Test
    fun italicAndEntities() {
        val blocks = PdfHtmlParser.blocks("<p><em>a</em>&hellip;&amp;&unknown;</p>")
        assertEquals(listOf(PdfBlock.Paragraph(listOf(PdfRun("a", italic = true), PdfRun("\u2026&&unknown;")))), blocks)
    }

    @Test
    fun imageLookupFallsBackToLeafName() {
        val plan = PdfDocumentPlan(bundle(emptyList(), mapOf("images/a.webp" to byteArrayOf(1))))
        assertArrayEquals(byteArrayOf(1), plan.image("../images/a.webp"))
        assertArrayEquals(byteArrayOf(1), plan.image("../Images/a.webp"))
        assertNull(plan.image("b.webp"))
    }

    @Test
    fun numbersChaptersAndHidesStripTitles() {
        val strip = ChapterContent(
            ChapterRef("1", 1, "Episode"), "<div class=\"ntk-webtoon-page\"><img src=\"images/1.png\"/></div>",
            listOf(ImageRef("https://example.com/1.png", "images/1.png")),
        )
        val notice = ChapterContent(ChapterRef("n", 0, "Notice", isNotice = true), "<p>Read me</p>")
        val base = AppSettings()
        val settings = base.copy(shared = base.shared.copy(numberChapters = true), images = base.images.copy(longImageLayout = true))
        val plan = PdfDocumentPlan(bundle(listOf(notice, strip), settings = settings))
        assertTrue(plan.longImageLayout)
        assertEquals(listOf("Notice", "1. Episode"), plan.chapters.map { it.title })
        assertEquals(listOf(true, false), plan.chapters.map { it.showTitle })
        assertEquals(listOf("Ongoing", "fantasy"), plan.details)
        assertFalse(PdfDocumentPlan(bundle(listOf(strip))).longImageLayout)
    }

    @Test
    fun counterLayoutNeedsToc() {
        val base = AppSettings()
        assertFalse(PdfDocumentPlan(bundle(emptyList(), settings = base.copy(text = base.text.copy(pdfCounterLayout = true)))).counterLayout)
        assertTrue(PdfDocumentPlan(bundle(emptyList(), settings = base.copy(text = base.text.copy(pdfToc = true, pdfCounterLayout = true)))).counterLayout)
    }
}
