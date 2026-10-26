package com.shirochi.npia.sources.nweb

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.http.RoutedHttp
import com.shirochi.npia.sources.http.content
import com.shirochi.npia.sources.http.isRefused
import com.shirochi.npia.sources.http.makeContext
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.awt.Color
import java.awt.Rectangle
import java.awt.image.BufferedImage
import java.io.ByteArrayOutputStream
import javax.imageio.ImageIO

class NwebAdapterTest {
    private val domain = NwebAdapter.DOMAIN

    private fun listHtml(page: Int): String {
        val rows = (if (page == 1) listOf(3, 2) else listOf(1)).joinToString("") { volume ->
            """
            <li class="volumeComment"><a class="list_item" href="/webnovel/detail?novelId=55&amp;volumeNo=$volume">
              <p class="subj"><span class="bullet_wrap"><em>UP</em></span>${volume}화  제목 $volume</p><span class="date">24.01.0$volume</span></a></li>
            """
        }
        return """
            <html><head><meta property="og:image" content="https://img.example.com/og.jpg"></head><body>
            <div class="section_area_info">
              <div class="thumbnail"><img src="//img.example.com/cover.jpg"></div>
              <h2 class="title">시험 작품</h2>
              <div class="info_top"><div class="info_group"><span class="item">판타지</span></div></div>
              <a href="/search?target=author&query=x">글쓴이</a>
              <span class="bullet_comp">완결</span>
              <p class="summary">첫 줄<br>둘째 줄 <a href="#">더보기</a></p>
            </div>
            <div class="end_tag_area"><div class="tag_collection"><a class="tag">#회귀</a><a class="tag">#판타지</a></div></div>
            <div class="cont_sub"><div class="component_head"><h3 class="title">작품 회차 (3)</h3></div>
            <ul>$rows</ul></div>
            </body></html>
        """.trimIndent()
    }

    private fun episodeHtml(text: String) = "<div class=\"detail_view_content\"><p>$text</p><p>둘째 문단</p></div>"

    private val listUrl get() = "https://novel.$domain/webnovel/list?novelId=55"

    @Test
    fun matches() {
        val adapter = NwebAdapter()
        assertTrue(adapter.matches("https://novel.$domain/webnovel/list?novelId=55"))
        assertTrue(adapter.matches("https://m.novel.$domain/best/detail?novelId=55&volumeNo=2"))
        assertTrue(adapter.matches("https://series.$domain/novel/detail.series?productNo=99"))
        assertFalse(adapter.matches("https://novel.$domain/webnovel/list?novelId=0"))
        assertFalse(adapter.matches("https://novel.$domain/webnovel/weekday"))
        assertEquals("challenge", NwebAdapter.novelParts("https://novel.$domain/challenge/detail?novelId=7&volumeNo=3")?.tier)
    }

    @Test
    fun parseBookReadsAllListPages() = runTest {
        val http = RoutedHttp()
        http.on("/webnovel/list?novelId=55", listHtml(1))
        http.on("/webnovel/list?novelId=55&page=2", listHtml(2))
        http.on("/webnovel/detail?novelId=55", episodeHtml("본문"))
        val book = NwebAdapter().parseBook("https://novel.$domain/webnovel/detail?novelId=55&volumeNo=2", makeContext(http))
        assertEquals("시험 작품", book.title)
        assertEquals("글쓴이", book.author)
        assertEquals("완결", book.status)
        assertEquals("첫 줄\n둘째 줄", book.synopsis)
        assertEquals("https://img.example.com/cover.jpg", book.coverUrl)
        assertEquals(listOf("판타지", "회귀"), book.tags)
        assertEquals(listOf("1화 제목 1", "2화 제목 2", "3화 제목 3"), book.chapters.map { it.title })
        assertEquals(listOf("1", "2", "3"), book.chapters.map { it.id })
        assertEquals("https://novel.$domain/webnovel/detail?novelId=55&volumeNo=1", book.chapters[0].url)
    }

    @Test
    fun loginRedirect() = runTest {
        val http = RoutedHttp()
        http.on("/webnovel/list?novelId=55", "<html></html>", finalUrl = "https://nid.$domain/login")
        try {
            NwebAdapter().parseBook(listUrl, makeContext(http))
            fail("expected login error")
        } catch (e: SourceException.LoginRequired) {
            assertEquals(SourceId.NWEB, e.source)
        }
        http.on("/webnovel/detail?novelId=55", "<html></html>", finalUrl = "https://nid.$domain/login")
        val ref = ChapterRef("1", 1, "1화", "https://novel.$domain/webnovel/detail?novelId=55&volumeNo=1")
        val book = BookInfo(SourceId.NWEB, "55", listUrl, "t")
        assertTrue(isRefused(NwebAdapter().parseChapter(ref, book, makeContext(http))))
    }

    @Test
    fun chapterDropsRepeatedTitle() = runTest {
        val http = RoutedHttp()
        http.on("/webnovel/detail?novelId=55", episodeHtml("1화 제목 1"))
        val ref = ChapterRef("1", 1, "1화 제목 1", "https://novel.$domain/webnovel/detail?novelId=55&volumeNo=1")
        val book = BookInfo(SourceId.NWEB, "55", listUrl, "t")
        val body = content(NwebAdapter().parseChapter(ref, book, makeContext(http)))
        assertEquals("<div class=\"nweb-novel-content\">\n<p>둘째 문단</p>\n</div>", body?.html)
    }

    @Test
    fun seriesResolvesToWebEdition() {
        val page = "<a href=\"//novel.$domain/best/list?novelId=321\">웹소설 보기</a> totalCount=40"
        assertEquals("https://novel.$domain/best/list?novelId=321", NwebAdapter.seriesWebEdition(page))
        assertNull(NwebAdapter.seriesWebEdition("<a href=\"/other\">x</a>"))
    }

    // Logo banners

    private fun png(width: Int, height: Int, mark: Rectangle): ByteArray {
        val image = BufferedImage(width, height, BufferedImage.TYPE_INT_RGB)
        val graphics = image.createGraphics()
        graphics.color = Color.WHITE
        graphics.fillRect(0, 0, width, height)
        graphics.color = Color.BLACK
        graphics.fill(mark)
        graphics.dispose()
        return ByteArrayOutputStream().also { ImageIO.write(image, "png", it) }.toByteArray()
    }

    @Test
    fun signatureFromImage() {
        val banner = NwebImageSignature.fromImageData(png(400, 100, Rectangle(20, 20, 100, 60)))
        assertNotNull(banner)
        assertEquals(4.0, banner!!.aspect, 0.001)
        assertTrue(banner.isBanner)
        assertEquals(64 * 8, banner.pixels.size)
        assertTrue(banner.pixels.any { it < 100 })
        val same = NwebImageSignature.fromImageData(png(800, 200, Rectangle(40, 40, 200, 120)))!!
        assertTrue(banner.matches(same))
        val other = NwebImageSignature.fromImageData(png(400, 100, Rectangle(280, 20, 100, 60)))!!
        assertFalse(banner.matches(other))
        val tall = NwebImageSignature.fromImageData(png(100, 150, Rectangle(0, 0, 0, 0)))!!
        assertFalse(tall.isBanner)
        assertFalse(banner.matches(tall))
    }

    @Test
    fun logoBannerIsDetectedAndDropped() = runTest {
        val logo = png(400, 100, Rectangle(20, 20, 100, 60))
        val http = RoutedHttp()
        http.on("/webnovel/list?novelId=55", listHtml(1))
        http.on("/webnovel/list?novelId=55&page=2", listHtml(2))
        for (volume in 1..3) {
            http.on(
                "/webnovel/detail?novelId=55&volumeNo=$volume",
                "<div class=\"detail_view_content\"><p>본문 $volume</p><img src=\"https://img.example.com/$volume/illust.png\"><p>끝</p>" +
                    "<img src=\"https://img.example.com/$volume/logo_end.png?type=w1\"></div>",
            )
            http.onData("img.example.com/$volume/logo_end.png", logo)
            http.onData("img.example.com/$volume/illust.png", png(100, 150, Rectangle(10, 10, 50, 50)))
        }
        val adapter = NwebAdapter()
        val context = makeContext(http)
        val book = adapter.parseBook(listUrl, context)
        val body = content(adapter.parseChapter(book.chapters[1], book, context))
        assertNotNull(body)
        assertEquals(listOf("illust.png"), body!!.images.map { it.url.substringAfterLast('/') })
        assertFalse(body.html.contains("logo_end"))
    }
}
