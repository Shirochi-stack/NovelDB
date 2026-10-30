package com.shirochi.npia.sources.xiyuwx

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.sources.websupport.WebHtml
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.Collections
import java.util.concurrent.atomic.AtomicInteger

class XiyuwxAdapterTests {
    companion object {
        private const val BOOK_HTML =
            "<html><head><title>Test Book</title>\n" +
                "<meta property=\"og:novel:category\" content=\"Fantasy\"></head><body>\n" +
                "<div id=\"info\"><h1>Test Book</h1><p class=\"author\">\u4f5c\u8005\uff1aWriter</p></div>\n" +
                "<div id=\"fmimg\"><img src=\"/cover.jpg\"></div>\n" +
                "<div id=\"intro\"><a href=\"/tag/a/\">Tag1</a>\u3001<a href=\"/tag/b/\">Tag2</a>\uff1aA short story.</div>\n" +
                "<div id=\"list\"><dl>\n" +
                "<dt>Latest</dt>\n" +
                "<a href=\"/book/123/3.html\">\u7b2c3\u7ae0 C</a>\n" +
                "<dt>\u6b63\u6587</dt>\n" +
                "<a href=\"/book/123/1.html\">\u7b2c1\u7ae0 A</a>\n" +
                "<a class=\"vip\" href=\"https://www.xiyuwx.com/book/123/2.html\">\u7b2c2\u7ae0 B</a>\n" +
                "<a href=\"/book/123/\">Back</a>\n" +
                "<a href=\"/book/123/1.html\">\u7b2c1\u7ae0 A</a>\n" +
                "</dl></div></body></html>"

        private const val PLAIN_CHAPTER =
            "<html><body><div id=\"content\"><p>\u7b2c12\u7ae0 Title</p><div class=\"ad\">Buy now</div>Line one<br>Line &amp; two<script>var x = 1;</script></div>\n" +
                "<a href=\"/book/123/5_2.html\">\u4e0b\u4e00\u9875</a><a href=\"/book/123/6.html\">\u4e0b\u4e00\u7ae0</a></body></html>"

        private const val SECOND_PAGE = "<html><body><div id=\"content\">Line three</div></body></html>"

        private const val SHUFFLED_CHAPTER =
            "<html><body><div id=\"content\"><div id=\"chapter\">[\u4f5c\u8005\u7684\u63d0\u793a\uff1ahello]Second<br/><br/>Third<br/><br/>First</div></div>\n" +
                "<script>var a = _ii_rr('MTAwLDEwMiwxMDAsMTAx');</script></body></html>"

        private fun context(http: HttpFetching, web: WebEngineProviding, skipPaid: Boolean = false): SourceContext {
            val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, skipPaid = skipPaid))
            return SourceContext(http, web, InMemorySecretStore(), settings)
        }

        private fun ref(url: String, paid: Boolean = false) = ChapterRef(id = url, index = 1, title = "Chapter", url = url, isPaid = paid)

        private fun book() = BookInfo(SourceId.XIYUWX, "123", "https://xiyuwx.com/book/123/", "Test Book")
    }

    // URL matching

    @Test
    fun matchesBothHostsAndBookPaths() {
        for (text in listOf(
            "https://xiyuwx.com/123/", "https://www.xiyuwx.com/book/45/", "https://yywenxuan.com/9",
            "https://www.yywenxuan.com/book/9/3.html", "https://xiyuwx.com/12/7_2.html",
        )) {
            assertTrue(text, XiyuwxAdapter().matches(text))
        }
    }

    @Test
    fun rejectsOtherHostsAndPaths() {
        for (text in listOf(
            "https://example.com/123/", "https://xiyuwx.com/", "https://xiyuwx.com/0/", "https://xiyuwx.com/book/abc/",
            "https://xiyuwx.com.evil.com/12/", "https://xiyuwx.com/author/12/",
        )) {
            assertFalse(text, XiyuwxAdapter().matches(text))
        }
    }

    @Test
    fun bookIdAndSiteName() {
        assertEquals("123", XiyuwxParser.bookID("https://xiyuwx.com/book/123/4.html"))
        assertEquals("YY Wenxuan", XiyuwxParser.siteName("https://www.yywenxuan.com/1/"))
        assertEquals("Xiyuwx", XiyuwxParser.siteName("https://xiyuwx.com/1/"))
        assertEquals("https://xiyuwx.com/book/123/", XiyuwxParser.canonicalBookURL("https://xiyuwx.com/book/123/4.html?x=1", "123"))
    }

    // Decoder

    @Test
    fun decodePositions() {
        assertEquals(listOf(100, 102, 100, 101), XiyuwxParser.decodePositions("MTAwLDEwMiwxMDAsMTAx"))
        assertEquals(listOf(7, 8, 7), XiyuwxParser.decodePositions("Nyw4LDc"))
        assertNull(XiyuwxParser.decodePositions("!!!"))
        assertNull(XiyuwxParser.decodePositions("YWJj"))
    }

    @Test
    fun unshuffleRequiresPermutation() {
        assertEquals(listOf("a", "b", "c"), XiyuwxParser.unshuffle(listOf("b", "c", "a"), listOf(100, 102, 100, 101)))
        assertNull(XiyuwxParser.unshuffle(listOf("b", "c", "a"), listOf(100, 100, 100, 101)))
        assertNull(XiyuwxParser.unshuffle(listOf("b", "c"), listOf(100, 102, 100, 101)))
        assertEquals(emptyList<String>(), XiyuwxParser.unshuffle(emptyList(), listOf(5)))
    }

    @Test
    fun splitParagraphs() {
        assertEquals(listOf("a", "b", "c"), XiyuwxParser.splitParagraphs("a<br><br>b<BR/> <br />c"))
        assertEquals(listOf("a<br>b"), XiyuwxParser.splitParagraphs("a<br>b"))
    }

    @Test
    fun decodePageKeepsChineseInGb18030() {
        val text = "<html><head><meta charset=\"gbk\"></head><body>\u4e2d\u6587</body></html>"
        val data = text.toByteArray(WebHtml.GB18030)
        assertTrue(XiyuwxParser.decodePage(data).contains("\u4e2d\u6587"))
        val broken = "<meta charset=\"utf-8\">".toByteArray(Charsets.UTF_8) +
            byteArrayOf(0xff.toByte(), 0xe4.toByte(), 0xb8.toByte(), 0xad.toByte())
        assertTrue(XiyuwxParser.decodePage(broken).contains("\u4e2d"))
    }

    // Book

    @Test
    fun bookParsing() {
        val pageUrl = "https://yywenxuan.com/book/123/"
        val book = XiyuwxParser.book(BOOK_HTML, pageUrl, pageUrl, "123")
        assertNotNull(book)
        book!!
        assertEquals("Test Book", book.title)
        assertEquals("Writer", book.author)
        assertEquals("A short story.", book.synopsis)
        assertEquals(listOf("Tag1", "Tag2", "Fantasy"), book.tags)
        assertEquals("https://yywenxuan.com/cover.jpg", book.coverUrl)
        assertEquals("zh", book.language)
        assertEquals(listOf(1, 2), book.chapters.map { it.index })
        assertEquals(listOf("\u7b2c1\u7ae0 A", "\u7b2c2\u7ae0 B"), book.chapters.map { it.title })
        assertEquals(listOf(false, true), book.chapters.map { it.isPaid })
        // The mirror keeps chapters on the serving host.
        assertEquals("https://yywenxuan.com/book/123/2.html", book.chapters[1].url)
    }

    @Test
    fun bookParsingRejectsVerificationAndEmptyCatalog() {
        val pageUrl = "https://xiyuwx.com/book/123/"
        val challenge = "<html><head><title>Just a moment...</title></head><body><h1>Wait</h1></body></html>"
        assertNull(XiyuwxParser.book(challenge, pageUrl, pageUrl, "123"))
        assertTrue(XiyuwxParser.isVerificationPage(challenge))
        assertFalse(XiyuwxParser.isVerificationPage(BOOK_HTML))
        val noCatalog = "<html><body><h1>Title</h1></body></html>"
        assertNull(XiyuwxParser.book(noCatalog, pageUrl, pageUrl, "123"))
    }

    // Chapter pages

    @Test
    fun plainChapterCleanupAndNextPage() {
        val page = XiyuwxParser.chapterPage(PLAIN_CHAPTER, "https://xiyuwx.com/book/123/5.html")
        assertNotNull(page)
        assertEquals(listOf("Line one", "Line & two"), page!!.lines)
        assertEquals("https://xiyuwx.com/book/123/5_2.html", page.nextPage)
        assertEquals("<p>Line one</p>\n<p>Line &amp; two</p>", XiyuwxParser.chapterHtml(page.lines))
    }

    @Test
    fun shuffledChapterIsReordered() {
        val page = XiyuwxParser.chapterPage(SHUFFLED_CHAPTER, "https://xiyuwx.com/book/123/8.html")
        assertNotNull(page)
        assertEquals(listOf("First", "Second", "Third"), page!!.lines)
        assertNull(page.nextPage)
    }

    @Test
    fun shuffledChapterWithoutMapIsUnusable() {
        val html = "<html><body><div id=\"content\"><div id=\"chapter\">a<br><br>b</div></div></body></html>"
        assertNull(XiyuwxParser.chapterPage(html, null))
    }

    @Test
    fun renderedCopyWinsOverShuffledSource() {
        val html = "<html><body><div id=\"content\"><div id=\"chapter\">x<br><br>y</div><div id=\"ad\">Real one<br>Real two</div></div></body></html>"
        val page = XiyuwxParser.chapterPage(html, null)
        assertNotNull(page)
        assertEquals(listOf("Real one", "Real two"), page!!.lines)
    }

    @Test
    fun noContentReturnsNull() {
        assertNull(XiyuwxParser.chapterPage("<html><body><p>nothing</p></body></html>", null))
    }

    // Adapter flows

    @Test
    fun parseBookOverHttp() = runTest {
        val http = XiyuwxStubHttp(mapOf("https://xiyuwx.com/book/123/" to (200 to BOOK_HTML.toByteArray())))
        val context = context(http, XiyuwxStubWeb(XiyuwxFakePage(emptyMap())))
        val book = XiyuwxAdapter().parseBook("https://xiyuwx.com/book/123/", context)
        assertEquals(SourceId.XIYUWX, book.source)
        assertEquals("123", book.id)
        assertEquals(2, book.chapters.size)
    }

    @Test
    fun parseBookFallsBackToBrowserOnVerification() = runTest {
        val challenge = "<html><head><title>Just a moment...</title></head><body></body></html>"
        val url = "https://xiyuwx.com/book/123/"
        val http = XiyuwxStubHttp(mapOf(url to (200 to challenge.toByteArray())))
        val page = XiyuwxFakePage(mapOf(url to BOOK_HTML))
        val context = context(http, XiyuwxStubWeb(page))
        val book = XiyuwxAdapter().parseBook(url, context)
        assertEquals("Test Book", book.title)
        assertEquals(url, page.loadedUrls.firstOrNull())
    }

    @Test
    fun parseBookThrowsWhenNothingWorks() = runTest {
        val context = context(XiyuwxStubHttp(emptyMap()), XiyuwxStubWeb(XiyuwxFakePage(emptyMap())))
        try {
            XiyuwxAdapter().parseBook("https://xiyuwx.com/book/123/", context)
            fail("expected an error")
        } catch (e: SourceException) {
            // expected
        }
    }

    @Test
    fun multiPageChapterOverHttp() = runTest {
        val http = XiyuwxStubHttp(
            mapOf(
                "https://xiyuwx.com/book/123/5.html" to (200 to PLAIN_CHAPTER.toByteArray()),
                "https://xiyuwx.com/book/123/5_2.html" to (200 to SECOND_PAGE.toByteArray()),
            ),
        )
        val context = context(http, XiyuwxStubWeb(XiyuwxFakePage(emptyMap())))
        val result = XiyuwxAdapter().parseChapter(ref("https://xiyuwx.com/book/123/5.html"), book(), context)
        assertTrue("expected content", result is ChapterResult.Content)
        assertEquals("<p>Line one</p>\n<p>Line &amp; two</p>\n<p>Line three</p>", (result as ChapterResult.Content).content.html)
    }

    @Test
    fun chapterFallsBackToBrowserWhenHttpFails() = runTest {
        val url = "https://xiyuwx.com/book/123/8.html"
        val http = XiyuwxStubHttp(mapOf(url to (403 to ByteArray(0))))
        val page = XiyuwxFakePage(mapOf(url to SHUFFLED_CHAPTER))
        val context = context(http, XiyuwxStubWeb(page))
        val result = XiyuwxAdapter().parseChapter(ref(url), book(), context)
        assertTrue("expected content", result is ChapterResult.Content)
        assertEquals("<p>First</p>\n<p>Second</p>\n<p>Third</p>", (result as ChapterResult.Content).content.html)
    }

    @Test
    fun paidChapterIsSkippedWhenRequested() = runTest {
        val http = XiyuwxStubHttp(emptyMap())
        val context = context(http, XiyuwxStubWeb(XiyuwxFakePage(emptyMap())), skipPaid = true)
        val result = XiyuwxAdapter().parseChapter(ref("https://xiyuwx.com/book/123/2.html", paid = true), book(), context)
        assertTrue("expected refused", result is ChapterResult.Refused)
        assertEquals(0, http.requestCount)
    }

    @Test
    fun unreadablePaidChapterIsRefusedAndFreeOneFails() = runTest {
        val context = context(XiyuwxStubHttp(emptyMap()), XiyuwxStubWeb(XiyuwxFakePage(emptyMap())))
        val paid = XiyuwxAdapter().parseChapter(ref("https://xiyuwx.com/book/123/2.html", paid = true), book(), context)
        assertTrue("expected refused", paid is ChapterResult.Refused)
        val free = XiyuwxAdapter().parseChapter(ref("https://xiyuwx.com/book/123/3.html"), book(), context)
        assertTrue("expected failed", free is ChapterResult.Failed)
    }
}

// Stubs

private class XiyuwxStubHttp(private val routes: Map<String, Pair<Int, ByteArray>>) : HttpFetching {
    private val counter = AtomicInteger()

    val requestCount: Int get() = counter.get()

    override suspend fun send(request: HttpRequest): HttpResponse {
        counter.incrementAndGet()
        val route = routes[request.url] ?: return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
        return HttpResponse(request.url, route.first, emptyMap(), route.second)
    }
}

private class XiyuwxFakePage(private val pages: Map<String, String>) : WebPageDriving {
    @Volatile private var current: String? = null
    private val loaded: MutableList<String> = Collections.synchronizedList(mutableListOf())

    val loadedUrls: List<String> get() = synchronized(loaded) { loaded.toList() }

    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
        current = url
        loaded.add(url)
    }

    override suspend fun evaluateJson(functionBody: String): String = "null"
    override suspend fun addInitScript(source: String) {}
    override suspend fun currentUrl(): String? = current
    override suspend fun html(): String = current?.let { pages[it] } ?: "<html></html>"
    override suspend fun close() {}
}

private class XiyuwxStubWeb(private val page: XiyuwxFakePage) : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = page
    override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) {}
}
