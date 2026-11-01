package com.shirochi.npia.sources.floo

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
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.Collections
import java.util.concurrent.atomic.AtomicInteger

class FlooAdapterTests {
    companion object {
        // Hosts come from the parser's escaped constants so no provider name is spelled out here.
        private val bookHost = FlooParser.bookHost
        private val domain = FlooParser.domain

        private val bookHTML = """
            <html><body><div class="C-Fo-Zuo"><h1 id="novelName">Test Book</h1>
            <a class="rentouOne" href="/guru/1">Writer</a>
            <div class="T-L-T-C-Box1">A story.</div>
            <img class="imgcss" src="/c.jpg">
            <div class="T-R-T-B2-Box1"><a href="/t/1">Tag1</a><a href="/t/2">Tag2</a></div>
            <div class="DivTable"><table>
            <tr><td><a href="/724903_1.html">Chapter 1</a></td></tr>
            <tr class="vip"><td><a href="/vip/724903/2.html">Chapter 2</a></td></tr>
            <tr><td><a href="/724903.html">Work page</a></td></tr>
            <tr><td><a href="https://other.example.com/x.html">Other</a></td></tr>
            <tr><td><a href="/724903_1.html">Chapter 1</a></td></tr>
            <tr><td><a href="/724903_3.aspx">Chapter 3</a></td></tr>
            </table></div></div></body></html>
        """.trimIndent()

        private val freeChapter =
            "<html><body><div class=\"noveContent\">Line one<br>Line &amp; two<p><a href=\"https://pay.$domain/x\">recharge</a></p><script>var x = 1;</script></div></body></html>"

        private val loginWallChapter =
            "<html><body><div class=\"noveContent\">\u60a8\u8fd8\u6ca1\u6709\u767b\u5f55</div></body></html>"

        private val purchaseWallChapter =
            "<html><body><div class=\"noveContent\"><div class=\"c_c3\">\u8ba2\u9605\u672c\u7ae0</div></div></body></html>"

        private val imageChapterHTML =
            "<html><body><div class=\"noveContent\"><script>image_do3('1','0','724903','2','en1','p1','key','u1','123','32','000000','1','0','1')</script><div class=\"con_img\"></div></div></body></html>"

        private val imageURL =
            "https://read.$domain/Page4VipImage.aspx?num=1&o=0&id=724903&n=2&ct=1&en=en1&t=p1&font_size=32&font_color=000000&FontFamilyType=0&backgroundtype=1&u=u1&time=123&k=key"

        private val gifBytes = byteArrayOf(0x47, 0x49, 0x46, 0x38, 0x39, 0x61, 0x01, 0x00)

        private fun context(http: HttpFetching, web: WebEngineProviding, skipPaid: Boolean = false): SourceContext {
            val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, skipPaid = skipPaid))
            return SourceContext(http, web, InMemorySecretStore(), settings)
        }

        private fun ref(path: String, index: Int = 1, paid: Boolean = false): ChapterRef {
            val text = "https://$bookHost$path"
            return ChapterRef(id = text, index = index, title = "Chapter", url = text, isPaid = paid)
        }

        private fun book(): BookInfo = BookInfo(SourceId.FLOO, "724903", "https://$bookHost/724903.html", "Test Book")
    }

    // URL matching

    @Test
    fun matchesBookCatalogVipAndChapterLinks() {
        val cases = listOf(
            "https://$bookHost/724903.html" to "724903",
            "https://wap.$domain/724903_1.html" to "724903",
            "https://www.$domain/vip/724903/1658.html" to "724903",
            "https://$bookHost/html_724_724903/" to "724903",
            "https://$bookHost/book/55" to "55",
            "https://$bookHost/catalog?id=123" to "123",
        )
        for ((text, expected) in cases) {
            assertTrue(text, FlooAdapter().matches(text))
            assertEquals(text, expected, FlooParser.bookId(text))
        }
    }

    @Test
    fun rejectsOtherHostsAndPaths() {
        val rejected = listOf(
            "https://read.$domain/724903.html",
            "https://$domain/724903.html",
            "https://example.com/724903.html",
            "https://$bookHost/",
            "https://$bookHost/about",
            "https://$bookHost/x?id=0",
            "https://$bookHost.evil.com/724903.html",
        )
        for (text in rejected) {
            assertFalse(text, FlooAdapter().matches(text))
        }
    }

    // Helpers

    @Test
    fun fixTextRepairsMisreadGbk() {
        assertEquals("\u4f60\u597d", FlooParser.fixText("\u00c4\u00e3\u00ba\u00c3"))
        assertEquals("\u4f60\u597d", FlooParser.fixText("\u4f60\u597d"))
        assertEquals("plain", FlooParser.fixText("plain"))
    }

    @Test
    fun decodingsHandleGbkWithoutCharset() {
        val data = "<html><body><h1>\u4e2d\u6587</h1></body></html>".toByteArray(WebHtml.GB18030)
        assertTrue(FlooParser.decodings(data).first().contains("\u4e2d\u6587"))
        val bom = byteArrayOf(0xEF.toByte(), 0xBB.toByte(), 0xBF.toByte()) + "<p>hi</p>".toByteArray()
        assertEquals("<p>hi</p>", FlooParser.decodings(bom).first())
    }

    @Test
    fun challengeToken() {
        val challenge = "<script>document.cookie=\"C3VK=ab12CD\";location.reload()</script>".toByteArray()
        assertEquals("ab12CD", FlooParser.challengeToken(challenge))
        assertNull(FlooParser.challengeToken("<html>ok</html>".toByteArray()))
    }

    @Test
    fun imageUrlArguments() {
        val args = "'1','0','724903','2','en1','p1','key','u1','123','32','000000','1','0','1'"
        assertEquals(imageURL, FlooParser.imageUrl(args))
        val older = FlooParser.imageUrl("1,0,9,2,en,p,k,u,5,24,ff0000,0")
        assertTrue(older?.startsWith("https://read6.$domain/Page4VipImage.aspx?num=1") == true)
        assertTrue(older?.contains("&FontFamilyType=0&backgroundtype=1&") == true)
        assertNull(FlooParser.imageUrl("1,2,3"))
    }

    @Test
    fun imageNamingAndMagic() {
        assertEquals("images/0007_03.gif", FlooParser.imageFilename(7, 3, "gif"))
        assertEquals("images/12345_10.png", FlooParser.imageFilename(12345, 10, "png"))
        assertEquals("gif", FlooParser.imageExtension(gifBytes))
        assertEquals("png", FlooParser.imageExtension(byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D)))
        assertEquals("jpg", FlooParser.imageExtension(byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte())))
        assertNull(FlooParser.imageExtension("<html>login</html>".toByteArray()))
        assertEquals("<p><img src=\"images/0001_01.gif\" alt=\"\"/></p>", FlooParser.imageHtml(listOf("images/0001_01.gif")))
    }

    // Book

    @Test
    fun bookParsing() {
        val pageUrl = "https://$bookHost/724903.html"
        val book = FlooParser.book(listOf(bookHTML), pageUrl, "724903", pageUrl)
        assertNotNull(book)
        book!!
        assertEquals(SourceId.FLOO, book.source)
        assertEquals("Test Book", book.title)
        assertEquals("Writer", book.author)
        assertEquals("A story.", book.synopsis)
        assertEquals(listOf("Tag1", "Tag2"), book.tags)
        assertEquals("https://$bookHost/c.jpg", book.coverUrl)
        assertEquals(listOf(1, 2, 3), book.chapters.map { it.index })
        assertEquals(listOf("Chapter 1", "Chapter 2", "Chapter 3"), book.chapters.map { it.title })
        assertEquals(listOf(false, true, false), book.chapters.map { it.isPaid })
        assertEquals("https://$bookHost/vip/724903/2.html", book.chapters[1].url)
    }

    @Test
    fun bookParsingNeedsTitleAndCatalog() {
        val pageUrl = "https://$bookHost/724903.html"
        val empty = "<html><body><h1 id=\"novelName\">T</h1></body></html>"
        assertNull(FlooParser.book(listOf(empty), pageUrl, "724903", pageUrl))
    }

    // Chapter pages

    @Test
    fun freeChapterText() {
        val outcome = FlooParser.chapter(listOf(freeChapter), null)
        assertEquals(FlooChapterOutcome.Text(listOf("Line one", "Line & two")), outcome)
    }

    @Test
    fun loginAndPurchaseWalls() {
        assertEquals(FlooChapterOutcome.Locked("login"), FlooParser.chapter(listOf(loginWallChapter), null))
        assertEquals(FlooChapterOutcome.Locked("purchase"), FlooParser.chapter(listOf(purchaseWallChapter), null))
        val loginLink = "<html><body><div class=\"noveContent\"><a href=\"/regist/login.aspx\">x</a></div></body></html>"
        assertEquals(FlooChapterOutcome.Locked("login"), FlooParser.chapter(listOf(loginLink), null))
        val noContainer = "<html><body><p>\u8bf7\u767b\u5f55\u540e\u67e5\u770b</p></body></html>"
        assertEquals(FlooChapterOutcome.Locked("login"), FlooParser.chapter(listOf(noContainer), null))
        assertEquals(FlooChapterOutcome.None, FlooParser.chapter(listOf("<html><body><p>hello</p></body></html>"), null))
    }

    @Test
    fun vipImagesFromScriptCalls() {
        val outcome = FlooParser.chapter(listOf(imageChapterHTML), null)
        assertEquals(FlooChapterOutcome.Images(listOf(imageURL)), outcome)
    }

    @Test
    fun vipImagesFromRenderedStyle() {
        val html = "<html><body><div class=\"noveContent\"><div class=\"con_img\"><div style=\"background:url('/Page4VipImage.aspx?num=1')\"></div></div></div></body></html>"
        val pageUrl = "https://$bookHost/724903_2.html"
        assertEquals(
            FlooChapterOutcome.Images(listOf("https://$bookHost/Page4VipImage.aspx?num=1")),
            FlooParser.chapter(listOf(html), pageUrl),
        )
    }

    @Test
    fun emptySlotWithoutImagesIsPurchaseWall() {
        val html = "<html><body><div class=\"noveContent\"><div class=\"con_img\"></div></div></body></html>"
        assertEquals(FlooChapterOutcome.Locked("purchase"), FlooParser.chapter(listOf(html), null))
    }

    // Adapter flows

    @Test
    fun parseBookOverHttp() = runTest {
        val canonical = "https://$bookHost/724903.html"
        val http = FlooStubHttp(mapOf(canonical to (200 to bookHTML.toByteArray())))
        val context = context(http, FlooStubWeb(FlooFakePage(emptyMap())))
        val book = FlooAdapter().parseBook("https://wap.$domain/724903_1.html", context)
        assertEquals("724903", book.id)
        assertEquals(canonical, book.url)
        assertEquals(3, book.chapters.size)
    }

    @Test
    fun parseBookFallsBackToBrowser() = runTest {
        val canonical = "https://$bookHost/724903.html"
        val http = FlooStubHttp(mapOf(canonical to (200 to "<html></html>".toByteArray())))
        val page = FlooFakePage(mapOf(canonical to bookHTML))
        val context = context(http, FlooStubWeb(page))
        val book = FlooAdapter().parseBook(canonical, context)
        assertEquals("Test Book", book.title)
    }

    @Test
    fun parseBookRejectsForeignUrl() = runTest {
        val context = context(FlooStubHttp(emptyMap()), FlooStubWeb(FlooFakePage(emptyMap())))
        try {
            FlooAdapter().parseBook("https://example.com/1.html", context)
            fail("expected an error")
        } catch (e: SourceException) {
            // expected
        }
    }

    @Test
    fun freeChapterOverHttp() = runTest {
        val path = "/724903_1.html"
        val http = FlooStubHttp(mapOf("https://$bookHost$path" to (200 to freeChapter.toByteArray())))
        val context = context(http, FlooStubWeb(FlooFakePage(emptyMap())))
        val result = FlooAdapter().parseChapter(ref(path), book(), context)
        val content = (result as? ChapterResult.Content)?.content ?: return@runTest fail("expected content")
        assertEquals("<p>Line one</p>\n<p>Line &amp; two</p>", content.html)
        assertTrue(content.images.isEmpty())
    }

    @Test
    fun lockedFreeChapterIsRefusedWhenBrowserAlsoLocked() = runTest {
        val path = "/724903_4.html"
        val url = "https://$bookHost$path"
        val http = FlooStubHttp(mapOf(url to (200 to loginWallChapter.toByteArray())))
        val page = FlooFakePage(mapOf(url to loginWallChapter))
        val context = context(http, FlooStubWeb(page))
        val result = FlooAdapter().parseChapter(ref(path), book(), context)
        val refused = result as? ChapterResult.Refused ?: return@runTest fail("expected refused")
        assertTrue(refused.reason.contains("Sign in"))
    }

    @Test
    fun lockedOverHttpButReadableInSignedInBrowser() = runTest {
        val path = "/724903_5.html"
        val url = "https://$bookHost$path"
        val http = FlooStubHttp(mapOf(url to (200 to loginWallChapter.toByteArray())))
        val page = FlooFakePage(mapOf(url to freeChapter))
        val context = context(http, FlooStubWeb(page))
        val result = FlooAdapter().parseChapter(ref(path), book(), context)
        val content = (result as? ChapterResult.Content)?.content ?: return@runTest fail("expected content")
        assertTrue(content.html.contains("Line one"))
    }

    @Test
    fun paidChapterSkippedWhenRequested() = runTest {
        val http = FlooStubHttp(emptyMap())
        val page = FlooFakePage(emptyMap())
        val context = context(http, FlooStubWeb(page), skipPaid = true)
        val result = FlooAdapter().parseChapter(ref("/vip/724903/2.html", paid = true), book(), context)
        assertTrue(result is ChapterResult.Refused)
        assertEquals(0, http.requestCount)
        assertTrue(page.loadedUrls.isEmpty())
    }

    @Test
    fun paidChapterWithoutPurchaseIsRefused() = runTest {
        val path = "/vip/724903/2.html"
        val url = "https://$bookHost$path"
        val http = FlooStubHttp(emptyMap())
        val page = FlooFakePage(mapOf(url to purchaseWallChapter))
        val context = context(http, FlooStubWeb(page))
        val result = FlooAdapter().parseChapter(ref(path, paid = true), book(), context)
        assertTrue(result is ChapterResult.Refused)
        // Paid chapters are read in the web profile only, never as a guest over HTTP.
        assertEquals(0, http.requestCount)
    }

    @Test
    fun boughtVipChapterReturnsImages() = runTest {
        val path = "/vip/724903/2.html"
        val url = "https://$bookHost$path"
        val http = FlooStubHttp(mapOf(imageURL to (200 to gifBytes)))
        val page = FlooFakePage(mapOf(url to imageChapterHTML))
        val context = context(http, FlooStubWeb(page))
        val result = FlooAdapter().parseChapter(ref(path, index = 2, paid = true), book(), context)
        val content = (result as? ChapterResult.Content)?.content ?: return@runTest fail("expected content")
        assertEquals(1, content.images.size)
        assertEquals("images/0002_01.gif", content.images[0].filename)
        assertArrayEquals(gifBytes, content.images[0].data)
        assertEquals(imageURL, content.images[0].url)
        assertEquals("<p><img src=\"images/0002_01.gif\" alt=\"\"/></p>", content.html)
    }

    @Test
    fun vipImageRequestThatReturnsAPageIsRefused() = runTest {
        val path = "/vip/724903/2.html"
        val url = "https://$bookHost$path"
        val http = FlooStubHttp(mapOf(imageURL to (200 to "<html>please log in</html>".toByteArray())))
        val page = FlooFakePage(mapOf(url to imageChapterHTML))
        val context = context(http, FlooStubWeb(page))
        val result = FlooAdapter().parseChapter(ref(path, paid = true), book(), context)
        assertTrue(result is ChapterResult.Refused)
    }

    @Test
    fun vipImageForbiddenIsRefused() = runTest {
        val path = "/vip/724903/2.html"
        val url = "https://$bookHost$path"
        val http = FlooStubHttp(mapOf(imageURL to (403 to ByteArray(0))))
        val page = FlooFakePage(mapOf(url to imageChapterHTML))
        val context = context(http, FlooStubWeb(page))
        val result = FlooAdapter().parseChapter(ref(path, paid = true), book(), context)
        assertTrue(result is ChapterResult.Refused)
    }
}

// Stubs

private class FlooStubHttp(private val routes: Map<String, Pair<Int, ByteArray>>) : HttpFetching {
    private val counter = AtomicInteger()

    val requestCount: Int get() = counter.get()

    override suspend fun send(request: HttpRequest): HttpResponse {
        counter.incrementAndGet()
        val route = routes[request.url] ?: return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
        return HttpResponse(request.url, route.first, emptyMap(), route.second)
    }
}

private class FlooFakePage(private val pages: Map<String, String>) : WebPageDriving {
    @Volatile
    private var current: String? = null
    private val loaded: MutableList<String> = Collections.synchronizedList(mutableListOf())

    val loadedUrls: List<String> get() = loaded.toList()

    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
        current = url
        loaded.add(url)
    }

    /** `waitFor` conditions are immediately truthy so tests never wait for a selector. */
    override suspend fun evaluateJson(functionBody: String): String = "true"

    override suspend fun addInitScript(source: String) {}

    override suspend fun currentUrl(): String? = current

    override suspend fun html(): String = current?.let { pages[it] } ?: "<html></html>"

    override suspend fun close() {}
}

private class FlooStubWeb(private val page: FlooFakePage) : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = page

    override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) {}
}
