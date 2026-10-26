package com.shirochi.npia.sources.globalnpia

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

private class GlobalNpiaStubHttp(
    /** First route whose needle is contained in the URL wins. */
    private val routes: List<Triple<String, Int, String>>,
) : HttpFetching {
    override suspend fun send(request: HttpRequest): HttpResponse {
        for ((needle, status, body) in routes) {
            if (request.url.contains(needle)) return HttpResponse(request.url, status, emptyMap(), body.toByteArray())
        }
        return HttpResponse(request.url, 404, emptyMap(), "{}".toByteArray())
    }
}

private object GlobalNpiaStubWeb : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = throw WebEngineException("unavailable")
    override suspend fun presentLogin(url: String, profile: String) = throw WebEngineException("unavailable")
}

class GlobalNpiaAdapterTests {
    private val jwt = "eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxIn0.c2ln"
    private val base: String get() = GlobalNpiaParsing.BASE

    private fun context(routes: List<Triple<String, Int, String>>): SourceContext =
        SourceContext(GlobalNpiaStubHttp(routes), GlobalNpiaStubWeb, InMemorySecretStore(), AppSettings())

    private fun ref(no: String = "501", index: Int = 1): ChapterRef =
        ChapterRef(id = no, index = index, title = "Episode $index", url = "$base/viewer/$no")

    private fun book(): BookInfo =
        BookInfo(source = SourceId.GLOBAL_NPIA, id = "77", url = "$base/novel/77", title = "T", language = "en")

    private fun json(text: String) = Json.parseToJsonElement(text)

    // matching

    @Test
    fun testMatches() {
        val adapter = GlobalNpiaAdapter()
        assertTrue(adapter.matches("$base/novel/123"))
        assertTrue(adapter.matches("$base/novel/123/?x=1"))
        assertTrue(adapter.matches("$base/novel/123?x=1"))
        assertFalse(adapter.matches("$base/novel/abc"))
        assertFalse(adapter.matches("$base/viewer/123"))
        assertFalse(adapter.matches("https://example.com/novel/123"))
        assertFalse(adapter.matches("https://api-global.example.com/novel/1"))
        val main = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/123"
        assertFalse(adapter.matches(main))
    }

    @Test
    fun testIdExtraction() {
        assertEquals("42", GlobalNpiaParsing.novelId("$base/novel/42/x"))
        assertEquals("9001", GlobalNpiaParsing.episodeId("$base/viewer/9001"))
        assertEquals("", GlobalNpiaParsing.episodeId("$base/novel/9"))
    }

    // helpers

    @Test
    fun testAdAndAccessDetection() {
        val ad = json("""{"code":"0010","result":{"name":"NOVEL_ERROR","message":"Basic advertisement required"}}""")
        assertTrue(GlobalNpiaParsing.adRequired(403, ad))
        assertFalse(GlobalNpiaParsing.adRequired(200, ad))
        val denied = json("""{"errmsg":"Please purchase this episode"}""")
        assertTrue(GlobalNpiaParsing.accessDenied(400, denied))
        assertFalse(GlobalNpiaParsing.accessDenied(500, JsonObject(mapOf("error" to JsonPrimitive("boom")))))
    }

    @Test
    fun testSavedLoginCookies() {
        assertFalse(GlobalNpiaParsing.hasSavedLogin(mapOf("USERKEY" to "u")))
        assertTrue(GlobalNpiaParsing.hasSavedLogin(mapOf("USERKEY" to "u", "TKEY" to "t")))
        assertTrue(GlobalNpiaParsing.hasSavedLogin(mapOf("USERKEY" to "u", "LOGINKEY" to "k", "ISLOGIN" to "1")))
        assertFalse(GlobalNpiaParsing.hasSavedLogin(mapOf("USERKEY" to "u", "LOGINKEY" to "k", "ISLOGIN" to "0")))
    }

    @Test
    fun testLoginToken() {
        val payload = json("""{"statusCode":200,"result":{"LOGINAT":"a%2Bb"}}""")
        assertEquals("a+b", GlobalNpiaParsing.loginToken(200, payload))
        assertNull(GlobalNpiaParsing.loginToken(401, payload))
    }

    @Test
    fun testTicketTokenOrder() {
        assertTrue(GlobalNpiaParsing.looksLikeJwt(jwt))
        assertFalse(GlobalNpiaParsing.looksLikeJwt("abc"))
        val plain = json("{\"result\":{\"_t\":\"plain\",\"t\":\"$jwt\"}}")
        assertEquals(jwt, GlobalNpiaParsing.extractTicketToken(plain).token)
        val nested = json("{\"result\":{\"x\":{\"token\":\"$jwt\"}}}")
        assertEquals(jwt, GlobalNpiaParsing.extractTicketToken(nested).token)
        val direct = json("{\"result\":{\"url\":\"${GlobalNpiaParsing.API}/v1/novel/episode/content?_t=$jwt\"}}")
        val found = GlobalNpiaParsing.extractTicketToken(direct)
        assertEquals(jwt, found.token)
        assertTrue(found.directUrl.contains("/v1/novel/episode/content"))
        assertEquals("", GlobalNpiaParsing.extractTicketToken(JsonObject(mapOf("result" to JsonObject(emptyMap())))).token)
    }

    @Test
    fun testRawContentOrdering() {
        val payload = json("""{"result":{"data":{"epi_content2":"<p>C</p>","epi_content":"<p>A</p>","epi_content1":"<p>B</p>","other":"x"}}}""")
        assertEquals("<p>A</p><p>B</p><p>C</p>", GlobalNpiaParsing.rawContent(payload))
        val fallback = json("""{"result":{"content":" <p>Z</p> "}}""")
        assertEquals("<p>Z</p>", GlobalNpiaParsing.rawContent(fallback))
    }

    @Test
    fun testSrcsetAndImageExtension() {
        assertEquals("b.jpg", GlobalNpiaParsing.bestSrcset("a.jpg 1x, b.jpg 2x"))
        assertEquals("b.jpg", GlobalNpiaParsing.bestSrcset("a.jpg 320w, b.jpg 800w, c.jpg 480w"))
        assertEquals("png", GlobalNpiaParsing.imageExtension("https://x.test/p/a.PNG"))
        assertEquals("jpg", GlobalNpiaParsing.imageExtension("https://x.test/p/a"))
        assertEquals("webp", GlobalNpiaParsing.imageExtension("data:image/webp;base64,AAAA"))
    }

    // book

    private val novelJson = """
    {"statusCode":200,"result":{"novel":{"novel_name":" Test Novel ","novel_story":"Story","flag_complete":"1","novel_full_img":"//img.test/c.jpg","tag_list":[{"tag_name":"fantasy"}]},"info":{"epi_cnt":3},"writer_list":[{"writer_name":"Writer"}],"cate_list":[{"cate_name":"Action"}]}}
    """.trimIndent()
    private val listJson = """
    {"result":{"list":[{"episode_no":303,"epi_num":3,"epi_title":"Third"},{"episode_no":101,"epi_num":1,"epi_title":"First"},{"episode_no":202,"epi_num":2,"epi_title":" "},{"episode_no":101,"epi_num":1,"epi_title":"Dup"}]}}
    """.trimIndent()

    @Test
    fun testMakeBookFromFixtures() {
        val novel: JsonObject = json(novelJson).jsonObject
        val list: JsonArray = json(listJson).jsonObject["result"]!!.jsonObject["list"]!!.jsonArray
        val book = GlobalNpiaParsing.makeBook("77", novel, list)
        assertEquals("Test Novel", book.title)
        assertEquals("Writer", book.author)
        assertEquals("Completed", book.status)
        assertEquals("en", book.language)
        assertEquals("https://img.test/c.jpg", book.coverUrl)
        assertEquals(listOf("Action", "fantasy"), book.tags)
        assertEquals(listOf("101", "202", "303"), book.chapters.map { it.id })
        assertEquals(listOf(1, 2, 3), book.chapters.map { it.index })
        assertEquals("Episode 2", book.chapters[1].title)
        assertEquals("$base/viewer/101", book.chapters[0].url)
        assertTrue(book.chapters.all { !it.isPaid })
    }

    @Test
    fun testParseBookThroughStub() = runTest {
        val ctx = context(
            listOf(
                Triple("/v1/novel/episode/list", 200, listJson),
                Triple("/v1/novel", 200, novelJson),
            ),
        )
        val book = GlobalNpiaAdapter().parseBook("$base/novel/77", ctx)
        assertEquals("77", book.id)
        assertEquals(3, book.chapters.size)
    }

    @Test
    fun testParseBookMetadataFailureThrows() = runTest {
        val ctx = context(listOf(Triple("/v1/novel", 404, "{\"message\":\"nope\"}")))
        try {
            GlobalNpiaAdapter().parseBook("$base/novel/77", ctx)
            fail("expected a throw")
        } catch (e: SourceException) {
            // expected
        }
    }

    // chapter HTML

    @Test
    fun testProcessChapterHtml() {
        val raw = """
        <p>Hello<br>World&nbsp;!</p><script>evil()</script>
        <picture><source srcset="/a/small.jpg 100w, /a/big.webp 900w"><img src="/a/fallback.png"></picture>
        <img data-src="https://cdn.test/x/lazy.png#frag" src="data:image/gif;base64,R0lGOD">
        <div style="background:url('/bg/pic.jpg')">t</div><iframe src="x"></iframe>
        """.trimIndent()
        val page = "$base/viewer/501"
        val processed = GlobalNpiaParsing.processChapterHtml(raw, page, 7)!!
        assertFalse(processed.html.contains("script"))
        assertFalse(processed.html.contains("iframe"))
        assertFalse(processed.html.contains("<picture"))
        assertFalse(processed.html.contains("&nbsp;"))
        assertTrue(processed.html.contains("<br/>"))
        assertTrue(processed.html.startsWith("<div class=\"global-npia-content\">"))
        assertEquals(
            listOf("$base/a/big.webp", "https://cdn.test/x/lazy.png", "$base/bg/pic.jpg"),
            processed.assets.map { it.url },
        )
        assertEquals(listOf("images/0007_01.webp", "images/0007_02.png", "images/0007_03.jpg"), processed.assets.map { it.filename })
        assertTrue(processed.html.contains("src=\"images/0007_01.webp\""))
        assertTrue(processed.html.contains("images/0007_03.jpg"))
    }

    @Test
    fun testEmptyChapterHtmlIsNull() {
        assertNull(GlobalNpiaParsing.processChapterHtml("<script>x</script>", "$base/viewer/1", 1))
    }

    // chapter fetch

    @Test
    fun testParseChapterReturnsContent() = runTest {
        val ticket = "{\"result\":{\"_t\":\"$jwt\"}}"
        val content = "{\"result\":{\"data\":{\"epi_content\":\"<p>Hello</p>\"}}}"
        val ctx = context(
            listOf(
                Triple("/v1/novel/episode/content", 200, content),
                Triple("/v1/novel/episode", 200, ticket),
            ),
        )
        val result = GlobalNpiaAdapter().parseChapter(ref(), book(), ctx)
        if (result !is ChapterResult.Content) {
            fail("expected content, got $result")
            return@runTest
        }
        assertTrue(result.content.html.contains("<p>Hello</p>"))
        assertTrue(result.content.images.isEmpty())
    }

    @Test
    fun testParseChapterAdGateIsRefused() = runTest {
        val body = """{"code":"0010","result":{"name":"NOVEL_ERROR","message":"basic advertisement"}}"""
        val ctx = context(listOf(Triple("/v1/novel/episode", 403, body)))
        val result = GlobalNpiaAdapter().parseChapter(ref(), book(), ctx)
        if (result !is ChapterResult.Refused) {
            fail("expected refused, got $result")
            return@runTest
        }
        assertEquals("needs an ad ticket; not supported yet", result.reason)
    }

    @Test
    fun testParseChapterLoginRequiredIsRefused() = runTest {
        val ctx = context(listOf(Triple("/v1/novel/episode", 401, "{\"message\":\"Not logged in\"}")))
        val result = GlobalNpiaAdapter().parseChapter(ref(), book(), ctx)
        assertTrue("expected refused, got $result", result is ChapterResult.Refused)
    }

    @Test
    fun testParseChapterServerErrorFails() = runTest {
        val ctx = context(listOf(Triple("/v1/novel/episode", 400, "{\"message\":\"weird\"}")))
        val result = GlobalNpiaAdapter().parseChapter(ref(), book(), ctx)
        assertTrue("expected failed, got $result", result is ChapterResult.Failed)
    }
}
