package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.NpiaSettings
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.UnavailableWebEngine
import com.shirochi.npia.sources.SourceCatalog
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.URL
import java.net.URLDecoder

private const val HOST = NpiaParser.HOST
private const val IMAGE_HOST = NpiaParser.IMAGE_HOST

/** Hand-made, trimmed versions of the npia pages the desktop scraper reads. */
private object Fixture {
    val novelPage = """
        <html><head>
        <title>Site - Ignored - Fallback Title</title>
        <meta property="og:image" content="https://$IMAGE_HOST/imagebox/cover/ab/cover_q_1.jpg">
        <meta name="description" content="short">
        </head><body>
        <script>var productName = 'The Tower &amp; Me';</script>
        <div class="epnew-cover-box"><a href="//$IMAGE_HOST/imagebox/original/ab/real_ori.file">x</a></div>
        <img class="cover_img" src="//$IMAGE_HOST/imagebox/cover/readycover.jpg">
        <div class="epnew-novel-info"><p class="in-badge"><span class="b_plus">PLUS</span><span class="b_comp s_inv">완결</span></p></div>
        <a class="writer-name" href="/user/1">  Writer Kim  </a>
        <span class="tag">#Fantasy</span><span class="tag" data-x="1">#fantasy</span><span class="tag">#Regression</span>
        <div class="synopsis">Line one<br/>Line &lt;two&gt;</div>
        <table class="notice_table">
          <tr><td onclick="location='/viewer/902'"><b>Second notice</b></td></tr>
          <tr><td onclick="location='/viewer/901'"><b>First <i>notice</i></b></td></tr>
        </table>
        </body></html>
    """.trimIndent()

    val episodePage0 = """
        <table>
        <tr class="ep_style5"><td><i id="bookmark_101"></i>Chapter &amp; One</b></td></tr>
        <tr class="ep_style5"><td><span class="b_plus">PLUS</span><i id="bookmark_102"></i><span>Chapter</span> Two</b></td></tr>
        </table>
    """.trimIndent()

    val episodePage1 = """
        <tr><td><i id="bookmark_103"></i>Chapter Three</b></td></tr>
        <tr><td><i id="bookmark_101"></i>Duplicate</b></td></tr>
    """.trimIndent()

    fun viewer(vararg segments: String): String =
        "{\"s\":[" + segments.joinToString(",") { JsonObject(mapOf("text" to JsonPrimitive(it))).toString() } + "]}"
}

private fun segments(vararg texts: String) = texts.map { JsonObject(mapOf("text" to JsonPrimitive(it))) }

private fun path(request: HttpRequest) = URL(request.url).path

private fun form(request: HttpRequest): Map<String, String> {
    val text = request.body?.decodeToString() ?: ""
    if (text.isEmpty()) return emptyMap()
    return text.split("&").associate { pair ->
        val parts = pair.split("=", limit = 2)
        parts[0] to URLDecoder.decode(parts.getOrElse(1) { "" }, "UTF-8")
    }
}

/** Routes requests by path and records them. */
private class RoutingHttp(private val route: (HttpRequest, Int) -> Pair<Int, String>) : HttpFetching {
    val requests = mutableListOf<HttpRequest>()

    override suspend fun send(request: HttpRequest): HttpResponse {
        val count = synchronized(requests) {
            requests.count { it.url == request.url && it.body.contentEqualsNullable(request.body) }.also { requests.add(request) }
        }
        val (status, body) = route(request, count)
        return HttpResponse(request.url, status, emptyMap(), body.encodeToByteArray())
    }
}

private fun ByteArray?.contentEqualsNullable(other: ByteArray?) =
    if (this == null || other == null) this === other else contentEquals(other)

private class RecordingJar : NpiaCookieSetting {
    val keys = mutableListOf<String>()
    override fun setLoginKey(key: String) {
        keys.add(key)
    }
}

class NpiaAdapterTest {
    private fun context(
        http: HttpFetching,
        secrets: InMemorySecretStore = InMemorySecretStore(),
        npia: NpiaSettings = NpiaSettings(),
    ) = SourceContext(http, UnavailableWebEngine, secrets, AppSettings(shared = SharedSettings(maxRetries = 3), npia = npia))

    // URLs

    @Test
    fun matchesOnlyMainSiteNovelPages() {
        val adapter = NpiaAdapter()
        assertTrue(adapter.matches("https://$HOST/novel/346898"))
        assertTrue(adapter.matches("https://www.$HOST/novel/346898?x=1"))
        assertFalse(adapter.matches("https://global.$HOST/novel/346898"))
        assertFalse(adapter.matches("https://$HOST/search"))
        assertFalse(adapter.matches("https://example.com/novel/346898"))
        assertEquals(SourceId.NPIA, SourceCatalog.adapterFor("https://$HOST/novel/5")?.id)
    }

    // Parsing

    @Test
    fun metadata() {
        val meta = NpiaParser.parseMetadata(Fixture.novelPage, "1")
        assertEquals("The Tower & Me", meta.title)
        assertEquals("Writer Kim", meta.author)
        assertEquals(listOf("Fantasy", "Regression"), meta.tags)
        assertEquals("Line one\nLine <two>", meta.synopsis)
        assertEquals("Completed", meta.status)
        assertEquals("https://$IMAGE_HOST/imagebox/original/ab/real_ori.file", meta.coverUrl)
    }

    @Test
    fun metadataFallbacks() {
        val page = """
            <meta property="og:title" content="Site - Tagline - Real Title">
            <meta name="description" content="short"><meta property="og:description" content="a longer description">
            <div class="epnew-novel-info"><p class="in-badge"><span class="b_plus">PLUS</span></p></div>
        """.trimIndent()
        val meta = NpiaParser.parseMetadata(page, "9")
        assertEquals("Real Title", meta.title)
        assertEquals("Unknown Author", meta.author)
        assertEquals("a longer description", meta.synopsis)
        assertEquals("Ongoing", meta.status)
        assertNull(meta.coverUrl)
        assertNull(NpiaParser.parseStatus("<p>login please</p>"))
        assertEquals("Novel_9", NpiaParser.parseMetadata("", "9").title)
    }

    @Test
    fun episodePageMarksPlusRows() {
        val rows = NpiaParser.parseEpisodePage(Fixture.episodePage0)
        assertEquals(listOf("101", "102"), rows.map { it.id })
        assertEquals(listOf("Chapter & One", "Chapter Two"), rows.map { it.title })
        assertEquals(listOf(false, true), rows.map { it.isPaid })
    }

    @Test
    fun noticesAreOldestFirst() {
        val notices = NpiaParser.parseNotices(Fixture.novelPage)
        assertEquals(listOf("901", "902"), notices.map { it.id })
        assertEquals(listOf("Notice: First notice", "Notice: Second notice"), notices.map { it.title })
        assertTrue(NpiaParser.parseNotices("<p>none</p>").isEmpty())
    }

    @Test
    fun renderSegments() {
        val input = segments(
            "<div class=\"cover-wrapper\">skip</div>",
            "&nbsp;<b>Bold</b> &lt;주인공&gt; <주인공> <span>plain</span><br>next",
            "<p style='height: 0px;'>hidden</p>",
            "<img src=\"//$IMAGE_HOST/imagebox/a/b.png\"><img src=\"file:///C:/clip.png\">caption",
            "<img src=\"/imagebox/c/d\">",
            "",
        )
        var options = NpiaParser.RenderOptions(stripLeadingSpaces = true, imagePrefix = "0007")
        val rendered = NpiaParser.renderSegments(input, options)
        assertEquals(
            "<p><b>Bold</b> &lt;주인공&gt; &lt;주인공&gt; plain<br/>next</p>" +
                "<p><img alt=\"1\" src=\"images/0007_01.png\" width=\"100%\"/>caption</p>" +
                "<p><img alt=\"2\" src=\"images/0007_02.jpg\" width=\"100%\"/></p>",
            rendered.html,
        )
        assertEquals(listOf("images/0007_01.png", "images/0007_02.jpg"), rendered.images.map { it.filename })
        assertEquals(
            listOf("https://$IMAGE_HOST/imagebox/a/b.png", "https://$IMAGE_HOST/imagebox/c/d"),
            rendered.images.map { it.url },
        )

        options = options.copy(saveImageUrlsOnly = true)
        val remote = NpiaParser.renderSegments(segments("<img src=\"//$IMAGE_HOST/imagebox/a/b.png\">"), options)
        assertTrue(remote.images.isEmpty())
        assertTrue(remote.html.contains("src=\"https://$IMAGE_HOST/imagebox/a/b.png\""))

        options = options.copy(removeNewlines = true)
        assertEquals("<p>abc</p>", NpiaParser.renderSegments(segments("a<br>b\nc"), options).html)
        assertEquals("<p>[No text segments found in chapter]</p>", NpiaParser.renderSegments(emptyList(), options).html)
    }

    @Test
    fun validatePayload() {
        assertTrue(NpiaParser.validatePayload("<!DOCTYPE html><html></html>") is NpiaParser.Payload.Invalid)
        assertTrue(NpiaParser.validatePayload("not json") is NpiaParser.Payload.Invalid)
        assertTrue(NpiaParser.validatePayload("""{"status":500,"errmsg":"잘못된 접근입니다"}""") is NpiaParser.Payload.Blocked)
        assertTrue(NpiaParser.validatePayload("""{"code":"500","message":"oops"}""") is NpiaParser.Payload.Invalid)
        assertTrue(NpiaParser.validatePayload("""{"errmsg":"로그인 후 이용해주세요"}""") is NpiaParser.Payload.Blocked)
        // Chapter text that mentions logging in is still content.
        val content = NpiaParser.validatePayload(Fixture.viewer("로그인했다"))
        assertEquals(1, (content as NpiaParser.Payload.Segments).segments.size)
    }

    @Test
    fun formBodyEncodesLikeRequests() {
        assertEquals("a=x+y%26z&b=", NpiaAuth.formBody(listOf("a" to "x y&z", "b" to "")).decodeToString())
        assertTrue(Regex("^[0-9a-f]{32}_[0-9a-f]{32}$").matches(NpiaAuth.generateLoginKey()))
    }

    // Network flow

    @Test
    fun parseBookLogsInAndCollectsChapters() = runTest {
        val http = RoutingHttp { request, _ ->
            when (path(request)) {
                "/proc/login" -> 200 to "<script>alert('감사합니다')</script>"
                "/novel/123" -> 200 to Fixture.novelPage
                "/proc/episode_list" -> when (form(request)["page"]) {
                    "0" -> 200 to Fixture.episodePage0
                    "1" -> 200 to Fixture.episodePage1
                    else -> 200 to "<table></table>"
                }
                else -> 404 to ""
            }
        }
        val secrets = InMemorySecretStore(mapOf(SecretKey.NPIA_EMAIL to "user@example.com", SecretKey.NPIA_PASSWORD to "pa ss&word"))
        val jar = RecordingJar()
        val ctx = context(http, secrets, NpiaSettings(autoLogin = true))

        val book = NpiaAdapter(jar) { 0.0 }.parseBook("https://$HOST/novel/123", ctx)

        val login = http.requests.first()
        assertEquals("/proc/login", path(login))
        assertEquals(mapOf("redirectrurl" to "", "email" to "user@example.com", "wd" to "pa ss&word"), form(login))
        val key = secrets.get(SecretKey.NPIA_LOGIN_KEY)!!
        assertTrue(Regex("^[0-9a-f]{32}_[0-9a-f]{32}$").matches(key))
        assertEquals(listOf(key), jar.keys)
        assertTrue(http.requests.all { it.headers["Cookie"] == "LOGINKEY=$key" })

        assertEquals("123", book.id)
        assertEquals("The Tower & Me", book.title)
        assertEquals(listOf("101", "102", "103"), book.chapters.map { it.id })
        assertEquals(listOf(1, 2, 3), book.chapters.map { it.index })
        assertEquals(listOf(false, true, false), book.chapters.map { it.isPaid })
        assertEquals("https://$HOST/viewer/101", book.chapters[0].url)
        assertEquals(listOf("901", "902"), book.notices.map { it.id })
        assertTrue(book.notices.all { it.isNotice })
        val pages = http.requests.filter { path(it) == "/proc/episode_list" }.map { form(it)["page"] }
        assertEquals(listOf("0", "1", "2"), pages)
        assertEquals("DOWN", form(http.requests.first { path(it) == "/proc/episode_list" })["sort"])
    }

    @Test
    fun parseBookUsesSavedKeyWithoutAutoLoginAndSkipsNotices() = runTest {
        val http = RoutingHttp { request, _ ->
            when (path(request)) {
                "/novel/5" -> 200 to Fixture.novelPage
                "/proc/episode_list" -> 200 to (if (form(request)["page"] == "0") Fixture.episodePage0 else "")
                else -> 500 to ""
            }
        }
        val secrets = InMemorySecretStore(
            mapOf(SecretKey.NPIA_LOGIN_KEY to "saved_key", SecretKey.NPIA_EMAIL to "a", SecretKey.NPIA_PASSWORD to "b"),
        )
        val jar = RecordingJar()
        val ctx = context(http, secrets, NpiaSettings(includeNotices = false))
        val book = NpiaAdapter(jar).parseBook("https://$HOST/novel/5", ctx)
        assertEquals(listOf("saved_key"), jar.keys)
        assertFalse(http.requests.any { path(it) == "/proc/login" })
        assertTrue(book.notices.isEmpty())
        assertEquals(2, book.chapters.size)
    }

    @Test
    fun chapterListRequiringLoginThrows() = runTest {
        val http = RoutingHttp { request, _ ->
            if (path(request) == "/novel/5") 200 to Fixture.novelPage else 200 to "Authentication required"
        }
        try {
            NpiaAdapter(RecordingJar()).parseBook("https://$HOST/novel/5", context(http))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            assertEquals(SourceId.NPIA, e.source)
        }
    }

    @Test
    fun parseChapterResults() = runTest {
        val http = RoutingHttp { request, count ->
            when (request.url.substringAfterLast('/')) {
                "1" -> 200 to Fixture.viewer("<b>Hello</b>", "<img src=\"//$IMAGE_HOST/imagebox/x/y.gif\">")
                "2" -> 200 to "  "
                "3" -> 200 to """{"status":500,"errmsg":"잘못된 접근"}"""
                "4" -> if (count == 0) 502 to "" else 200 to Fixture.viewer("recovered")
                "5" -> 200 to "<html><body>error</body></html>"
                else -> 404 to ""
            }
        }
        val adapter = NpiaAdapter(RecordingJar()) { 0.0 }
        val ctx = context(http, InMemorySecretStore(mapOf(SecretKey.NPIA_LOGIN_KEY to "k")))
        val book = BookInfo(SourceId.NPIA, "b", "https://$HOST/novel/1", "B")
        fun ref(id: Int, paid: Boolean = false, notice: Boolean = false) =
            ChapterRef("$id", id, "C$id", isPaid = paid, isNotice = notice)

        val first = adapter.parseChapter(ref(1, notice = true), book, ctx) as ChapterResult.Content
        assertEquals(
            "<p><b>Hello</b></p><p><img alt=\"1\" src=\"images/n0001_01.gif\" width=\"100%\"/></p>",
            first.content.html,
        )
        assertEquals(listOf("images/n0001_01.gif"), first.content.images.map { it.filename })

        assertTrue("unpaid PLUS chapter", adapter.parseChapter(ref(2, paid = true), book, ctx) is ChapterResult.Refused)
        assertTrue("empty free chapter", adapter.parseChapter(ref(2), book, ctx) is ChapterResult.Failed)
        assertTrue("blocked chapter", adapter.parseChapter(ref(3), book, ctx) is ChapterResult.Refused)
        val recovered = adapter.parseChapter(ref(4), book, ctx) as ChapterResult.Content
        assertEquals("<p>recovered</p>", recovered.content.html)
        val failed = adapter.parseChapter(ref(5), book, ctx) as ChapterResult.Failed
        assertTrue(failed.message, failed.message.contains("3 attempts"))

        val viewerCalls = http.requests.filter { it.url.endsWith("/5") }
        assertEquals(3, viewerCalls.size)
        assertEquals("POST", viewerCalls.first().method)
        assertEquals("https://$HOST/proc/viewer_data/5", viewerCalls.first().url)
        assertEquals("LOGINKEY=k", viewerCalls.first().headers["Cookie"])
    }
}
