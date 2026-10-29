package com.shirochi.npia.sources.sfc

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.RoutedHttp
import com.shirochi.npia.sources.http.content
import com.shirochi.npia.sources.http.isRefused
import com.shirochi.npia.sources.http.makeContext
import com.shirochi.npia.sources.http.toJson
import com.shirochi.npia.sources.hard.FakeCookies
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SfcAdapterTest {
    private val host = SfcAdapter.BOOK_HOST

    @Test
    fun matches() {
        val adapter = SfcAdapter()
        assertTrue(adapter.matches("https://$host/Novel/12345/"))
        assertTrue(adapter.matches("https://$host/Novel/12345/MainIndex/"))
        assertFalse(adapter.matches("https://$host/List/"))
        assertFalse(adapter.matches("https://example.com/Novel/12345/"))
        assertEquals("9012", SfcAdapter.chapterId("https://$host/Novel/12345/678/9012/"))
    }

    @Test
    fun signatureMatchesDesktop() {
        // Reference values from the desktop `_sfc_sign`.
        assertEquals("2D57B27662C6BA05B90E81A2AB992378", SfcAppApi.sign("0F6B2C1E-3A4D-4E5F-8A9B-112233445566", 1761456000000))
        assertEquals("3BEFB11AE80A54E17E2B8E514CDE4606", SfcAppApi.sign("ABCDEF01-2345-4678-9ABC-DEF012345678", 1700000000123))
    }

    @Test
    fun headers() {
        val nonce = "0F6B2C1E-3A4D-4E5F-8A9B-112233445566"
        val headers = SfcAppApi.headers(nonce, 1761456000000, "POST")
        assertEquals("application/json; charset=UTF-8", headers["content-type"])
        assertEquals(
            "nonce=$nonce&timestamp=1761456000000&devicetoken=${SfcAppApi.DEVICE_TOKEN}&sign=2D57B27662C6BA05B90E81A2AB992378",
            headers["sfsecurity"],
        )
        assertNull(SfcAppApi.headers(nonce, 5)["content-type"])
        assertEquals(32, SfcAppApi.sign("N", 5).length)
    }

    @Test
    fun characterTableDecodes() {
        assertEquals(3751L, SfcCharTable.obfuscated.codePoints().count())
        assertEquals(3751L, SfcCharTable.replacement.codePoints().count())
        val input = String(charArrayOf(Char(0x5ba6), Char(0x7f3a), 'x'))
        assertEquals(String(charArrayOf(Char(0x554a), Char(0x963f), 'x')), SfcAppApi.decode(input))
    }

    @Test
    fun contentWithImages() {
        val ref = ChapterRef("1", 2, "t")
        val body = SfcAppApi.content("a<b\n\n[img=1,2]https://p.example.com/x/pic.jpg[/img]tail", ref)
        assertEquals("<div><p>a&lt;b</p><p><br/></p><p><img src=\"images/0002_01.jpg\" alt=\"\" />tail</p></div>", body.html)
        assertEquals(listOf("https://p.example.com/x/pic.jpg"), body.images.map { it.url })
    }

    @Test
    fun cookieHandling() {
        val secrets = InMemorySecretStore()
        assertFalse(SfcAdapter.saveAppCookie(".SFCommunity=a", secrets))
        assertFalse(SfcAdapter.hasAppSession(secrets))
        assertTrue(SfcAdapter.saveAppCookie(" .SFCommunity=a;session_APP=b ", secrets))
        assertEquals(".SFCommunity=a; session_APP=b", SfcAppApi.savedCookie(secrets))
        val header = ".SFCommunity=AAA; path=/; HttpOnly, session_APP=BBB; expires=Wed, 01 Jan 2031 00:00:00 GMT; path=/"
        assertEquals(".SFCommunity=AAA; session_APP=BBB", SfcAppApi.sessionCookie(header))
    }

    @Test
    fun loginRetriesRejectedNonceAndStoresCookie() = runTest {
        val http = SequenceHttp(
            mutableListOf(
                mapOf("status" to mapOf("httpCode" to 417)) to emptyMap(),
                mapOf("status" to mapOf("httpCode" to 200)) to mapOf("set-cookie" to ".SFCommunity=C1; path=/, session_APP=S1; path=/"),
            ),
        )
        val secrets = InMemorySecretStore()
        assertTrue(SfcAdapter.login("user", "pw", http, secrets))
        assertEquals(".SFCommunity=C1; session_APP=S1", secrets.get(SfcAppApi.cookieKey))
        assertEquals(2, http.requests.size)
        assertEquals("POST", http.requests[0].method)
        assertNotEquals(http.requests[0].headers["sfsecurity"], http.requests[1].headers["sfsecurity"])
        val body = HttpSourceSupport.json.parseToJsonElement(http.requests[1].body!!.decodeToString()) as JsonObject
        assertEquals("user", HttpSourceSupport.string(body["username"]))
    }

    private val index = """
        <html><body><h1 class="story-title">测试小说</h1>
        <div class="story-catalog"><div class="catalog-title"><h3>【测试小说】第一卷</h3></div>
        <div class="catalog-list"><ul>
        <li><a href="/Novel/12345/600/7001/" title="第一章 开端">第一章 开端</a></li>
        <li><a href="/Novel/12345/600/7002/" title="第二章 付费"><span class="icn_vip"></span>第二章 付费</a></li>
        </ul></div></div></body></html>
    """.trimIndent()

    private val info = """
        <html><body><span class="author-name">作者甲</span>
        <div id="hasTicket"><div class="pic"><img src="//img.example.com/cover/12345.jpg"></div></div>
        <p class="introduce">简介第一行<br>简介第二行</p>
        <ul class="tag-list"><li class="tag"><a href="#"><span class="icn">x</span>恋爱(12)</a></li><li class="tag"><a href="#">校园</a></li></ul>
        </body></html>
    """.trimIndent()

    @Test
    fun parseBook() = runTest {
        val http = RoutedHttp()
        http.on("/Novel/12345/MainIndex/", index)
        http.on("/Novel/12345/", info)
        val book = SfcAdapter().parseBook("https://$host/Novel/12345/", makeContext(http))
        assertEquals("测试小说", book.title)
        assertEquals("作者甲", book.author)
        assertEquals("简介第一行\n简介第二行", book.synopsis)
        assertEquals(listOf("恋爱", "校园"), book.tags)
        assertEquals("https://img.example.com/cover/12345.jpg", book.coverUrl)
        assertEquals(listOf("第一章 开端", "第二章 付费"), book.chapters.map { it.title })
        assertEquals(listOf(false, true), book.chapters.map { it.isPaid })
        assertEquals(listOf("7001", "7002"), book.chapters.map { it.id })
    }

    private fun book() = BookInfo(SourceId.SFC, "12345", "https://$host/Novel/12345/", "测试小说")

    @Test
    fun freeChapterFromWebPage() = runTest {
        val http = RoutedHttp()
        http.on("/Novel/12345/600/7001/", "<h1 class=\"article-title\">第一章 开端</h1><div class=\"article-content\"><p>正文</p><p><img src=\"/pic/1.png\"></p></div>")
        val ref = ChapterRef("7001", 1, "第一章", "https://$host/Novel/12345/600/7001/")
        val body = content(SfcAdapter().parseChapter(ref, book(), makeContext(http)))
        assertNotNull(body)
        assertEquals("第一章 开端", body!!.ref.title)
        assertEquals(listOf("https://$host/pic/1.png"), body.images.map { it.url })
        assertTrue(http.requested(SfcAppApi.HOST).isEmpty())
    }

    @Test
    fun websitePagesGetTheLongTimeout() = runTest {
        val http = RoutedHttp()
        http.on("/Novel/12345/600/7001/", "<div class=\"article-content\"><p>正文</p></div>")
        val ref = ChapterRef("7001", 1, "第一章", "https://$host/Novel/12345/600/7001/")
        assertNotNull(content(SfcAdapter().parseChapter(ref, book(), makeContext(http))))
        assertEquals(SfcAdapter.SITE_TIMEOUT_SECONDS, http.requested("/Novel/12345/600/7001/").single().timeoutSeconds)
    }

    @Test
    fun aPlaceholderVipImageIsAskedForAgain() = runTest {
        val routed = RoutedHttp()
        routed.on("/vip/c/7002/", "<div class=\"article-content\"><img id=\"vipImage\" src=\"/ajax/ashx/common.ashx?op=getChapPic&cid=7002\"></div>")
        val ref = ChapterRef("7002", 2, "第二章", "https://$host/vip/c/7002/", isPaid = true)
        val twice = PlaceholderFirst(routed, placeholders = 2)
        val body = content(SfcAdapter().parseChapter(ref, book(), makeContext(twice)))
        assertEquals(100, body!!.images.single().data!!.size)
        assertEquals(3, twice.imageRequests)

        val always = PlaceholderFirst(routed, placeholders = 99)
        assertTrue(isRefused(SfcAdapter().parseChapter(ref, book(), makeContext(always))))
        assertEquals(1 + SfcAdapter.PLACEHOLDER_RETRIES, always.imageRequests)
    }

    @Test
    fun vipChapterWithoutSessionIsRefused() = runTest {
        val http = RoutedHttp()
        http.on("/Novel/12345/600/7002/", "<div class=\"article-content\"><p>预览</p></div><div class=\"pay-section\">购买</div>")
        val ref = ChapterRef("7002", 2, "第二章", "https://$host/Novel/12345/600/7002/", isPaid = true)
        assertTrue(isRefused(SfcAdapter().parseChapter(ref, book(), makeContext(http))))

        http.on(
            "/Novel/12345/600/7002/",
            "<div class=\"user-bar\"><div class=\"top-link\"><span class=\"normal-link\">您好，SF游客</span></div></div><div class=\"article-content\"><p>预览</p></div>",
        )
        assertTrue(isRefused(SfcAdapter().parseChapter(ref, book(), makeContext(http))))
    }

    @Test
    fun vipChapterThroughAppApi() = runTest {
        val http = RoutedHttp()
        val obfuscated = Char(0x5ba6)
        http.onJson(
            "/Chaps/7002",
            mapOf("status" to mapOf("httpCode" to 200), "data" to mapOf("title" to "第二章 付费", "expand" to mapOf("content" to "${obfuscated}ab\ncd"))),
        )
        val secrets = InMemorySecretStore(mapOf(SfcAppApi.cookieKey to ".SFCommunity=a; session_APP=b"))
        val ref = ChapterRef("7002", 2, "第二章", "https://$host/Novel/12345/600/7002/", isPaid = true)
        val body = content(SfcAdapter().parseChapter(ref, book(), makeContext(http, secrets)))
        assertNotNull(body)
        assertEquals("<div><p>${Char(0x554a)}ab</p><p>cd</p></div>", body!!.html)
        assertEquals("第二章 付费", body.ref.title)
        assertEquals(".SFCommunity=a; session_APP=b", http.requested("/Chaps/7002").first().headers["Cookie"])

        http.onJson("/Chaps/7002", mapOf("status" to mapOf("httpCode" to 403)))
        http.on("/Novel/12345/600/7002/", "<div class=\"pay-section\">购买</div>")
        assertTrue(isRefused(SfcAdapter().parseChapter(ref, book(), makeContext(http, secrets))))
    }

    @Test
    fun websiteSignInTicketIsOfferedToTheAppApi() = runTest {
        val http = RoutedHttp()
        http.onJson("/Chaps/7002", mapOf("status" to mapOf("httpCode" to 200), "data" to mapOf("title" to "第二章", "content" to "正文")))
        val adapter = SfcAdapter { FakeCookies(mapOf(".SFCommunity" to "ticket")) }
        val ref = ChapterRef("7002", 2, "第二章", "https://$host/Novel/12345/600/7002/", isPaid = true)
        assertNotNull(content(adapter.parseChapter(ref, book(), makeContext(http))))
        assertEquals(".SFCommunity=ticket", http.requested("/Chaps/7002").first().headers["Cookie"])
        // An app session wins over the website ticket.
        val secrets = InMemorySecretStore(mapOf(SfcAppApi.cookieKey to ".SFCommunity=a; session_APP=b"))
        assertNotNull(content(adapter.parseChapter(ref, book(), makeContext(http, secrets))))
        assertEquals(".SFCommunity=a; session_APP=b", http.requested("/Chaps/7002").last().headers["Cookie"])
    }

    @Test
    fun aTicketTheAppApiTurnsDownFallsBackToTheWebsite() = runTest {
        val http = RoutedHttp()
        http.onJson("/Chaps/7002", mapOf("status" to mapOf("httpCode" to 401)))
        http.onJson("/Chaps/7003", mapOf("status" to mapOf("httpCode" to 401)))
        http.on("/Novel/12345/600/7002/", "<div class=\"pay-section\">购买</div>")
        http.on("/Novel/12345/600/7003/", "<div class=\"article-content\"><p>免费</p></div>")
        val adapter = SfcAdapter { FakeCookies(mapOf(".SFCommunity" to "ticket")) }
        val locked = adapter.parseChapter(ChapterRef("7002", 2, "第二章", "https://$host/Novel/12345/600/7002/", isPaid = true), book(), makeContext(http))
        assertTrue(isRefused(locked))
        assertTrue((locked as com.shirochi.npia.core.ChapterResult.Refused).reason.contains("not bought"))
        // The turned-down ticket is not offered again; the next chapter comes from the website.
        assertNotNull(content(adapter.parseChapter(ChapterRef("7003", 3, "第三章", "https://$host/Novel/12345/600/7003/"), book(), makeContext(http))))
        assertTrue(http.requested("/Chaps/7003").isEmpty())
    }
}

/** Serves the placeholder for the first `placeholders` VIP image requests, then a real image. */
private class PlaceholderFirst(private val routed: RoutedHttp, private var placeholders: Int) : HttpFetching {
    var imageRequests = 0

    override suspend fun send(request: HttpRequest): HttpResponse {
        if ("common.ashx" !in request.url) return routed.send(request)
        imageRequests++
        val size = if (placeholders-- > 0) SfcAdapter.placeholderImageSizes.first() else 100
        return HttpResponse(request.url, 200, emptyMap(), ByteArray(size))
    }
}

/** Returns canned JSON replies in order. */
private class SequenceHttp(private val replies: MutableList<Pair<Map<String, Any>, Map<String, String>>>) : HttpFetching {
    val requests = mutableListOf<HttpRequest>()

    override suspend fun send(request: HttpRequest): HttpResponse {
        requests += request
        val (json, headers) = if (replies.isEmpty()) emptyMap<String, Any>() to emptyMap() else replies.removeAt(0)
        return HttpResponse(request.url, 200, headers, toJson(json).toString().encodeToByteArray())
    }
}
