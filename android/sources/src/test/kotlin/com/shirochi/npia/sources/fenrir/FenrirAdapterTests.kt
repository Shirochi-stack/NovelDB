package com.shirochi.npia.sources.fenrir

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
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.net.URI
import java.time.Instant

class FenrirAdapterTests {
    private val adapter = FenrirAdapter()

    private fun obj(json: String): JsonObject = Json.parseToJsonElement(json).jsonObject

    // URL matching

    @Test
    fun matchesSeriesAndChapterUrls() {
        assertTrue(adapter.matches("https://fenrirealm.com/series/my-book"))
        assertTrue(adapter.matches("https://www.fenrirealm.com/series/my-book/12"))
    }

    @Test
    fun rejectsOtherUrls() {
        assertFalse(adapter.matches("https://fenrirealm.com/"))
        assertFalse(adapter.matches("https://fenrirealm.com/series"))
        assertFalse(adapter.matches("https://fenrirealm.com/user/my-book"))
        assertFalse(adapter.matches("https://example.com/series/my-book"))
        assertFalse(adapter.matches("https://notfenrirealm.com/series/my-book"))
    }

    @Test
    fun urlParts() {
        val parts = FenrirParsing.urlParts("https://fenrirealm.com/series/book-a/3.1")
        assertEquals("book-a", parts.first)
        assertEquals("3.1", parts.second)
        assertEquals("", FenrirParsing.urlParts("https://fenrirealm.com/series/book-a").second)
    }

    // Paid detection

    @Test
    fun paidLocked() {
        assertTrue(FenrirParsing.paidLocked(obj("""{"locked":{"price":5}}""")))
        assertTrue(FenrirParsing.paidLocked(obj("""{"locked":{"price":"5"}}""")))
        assertFalse(FenrirParsing.paidLocked(obj("""{"locked":{"price":0}}""")))
        assertFalse(FenrirParsing.paidLocked(obj("""{"locked":{"price":5,"unlocked_at":"2025-01-01"}}""")))
        assertFalse(FenrirParsing.paidLocked(obj("""{"locked":{"price":5},"bought":{"bought_at":"2025-01-01"}}""")))
        assertFalse(FenrirParsing.paidLocked(obj("""{"locked":{"price":5},"bypass":true}""")))
        assertFalse(FenrirParsing.paidLocked(obj("{}")))
    }

    // Retry delay

    @Test
    fun retryDelaySeconds() {
        assertEquals(12.0, FenrirParsing.retryDelay("12"), 0.0)
        assertEquals(0.0, FenrirParsing.retryDelay(" 0 "), 0.0)
    }

    @Test
    fun retryDelayDefaults() {
        assertEquals(30.0, FenrirParsing.retryDelay(null), 0.0)
        assertEquals(30.0, FenrirParsing.retryDelay(""), 0.0)
        assertEquals(30.0, FenrirParsing.retryDelay("soon"), 0.0)
        assertEquals(30.0, FenrirParsing.retryDelay("-5"), 0.0)
    }

    @Test
    fun retryDelayHttpDate() {
        val now = Instant.ofEpochSecond(1_445_412_400) // Wed, 21 Oct 2015 07:26:40 GMT
        assertEquals(80.0, FenrirParsing.retryDelay("Wed, 21 Oct 2015 07:28:00 GMT", now), 0.5)
        assertEquals(0.0, FenrirParsing.retryDelay("Wed, 21 Oct 2015 07:00:00 GMT", now), 0.0)
    }

    // Book

    private val seriesJson = """
        {"title":"Test Saga","status":"ongoing","cover":"/covers/a.jpg",
         "description":"<p>First line</p><p>Second line</p>",
         "user":{"name":"Writer One","username":"writer1"},
         "genres":[{"name":"Fantasy"}],"tags":[{"name":"Magic"},{"name":"Fantasy"}]}
    """.trimIndent()

    private val chaptersJson = """
        {"data":[
          {"id":3,"uuid":"u3","number":3,"name":"Chapter 3","title":"Third","locked":{"price":4}},
          {"id":1,"uuid":"u1","number":1,"index":1,"name":"Chapter 1","title":"Start"},
          {"id":2,"uuid":"u2","number":2,"index":2,"name":"Chapter 2","title":"chapter 2","slug":"two"},
          {"id":9,"number":4,"part":1,"name":"","title":"Part"}
        ]}
    """.trimIndent()

    @Test
    fun makeBookFromFixture() {
        val info = obj(seriesJson)
        val rows = (obj(chaptersJson)["data"] as JsonArray).map { it.jsonObject }
        val book = FenrirParsing.makeBook(info, rows, "test-saga")
        assertNotNull(book)
        book!!

        assertEquals(SourceId.FENRIR, book.source)
        assertEquals("Test Saga", book.title)
        assertEquals("Writer One", book.author)
        assertEquals("First line\nSecond line", book.synopsis)
        assertEquals("https://fenrirealm.com/covers/a.jpg", book.coverUrl)
        assertEquals(listOf("Fantasy", "Magic"), book.tags)
        assertEquals("en", book.language)
        assertEquals(listOf(1, 2, 3, 4), book.chapters.map { it.index })
        // Rows with an index come first, then by number.
        assertEquals(listOf("Chapter 1 - Start", "Chapter 2", "Chapter 3 - Third", "Part"), book.chapters.map { it.title })
        assertEquals(listOf(false, false, true, false), book.chapters.map { it.isPaid })
        assertEquals("https://fenrirealm.com/series/test-saga/two", book.chapters[1].url)
        assertEquals("https://fenrirealm.com/series/test-saga/4.1", book.chapters[3].url)
        assertEquals("two", FenrirParsing.splitRefId(book.chapters[1].id).first)
        assertEquals("u2", FenrirParsing.splitRefId(book.chapters[1].id).second)
        assertEquals("9", FenrirParsing.splitRefId(book.chapters[3].id).second)
    }

    @Test
    fun makeBookWithoutChaptersIsNull() {
        assertNull(FenrirParsing.makeBook(obj("""{"title":"x"}"""), listOf(obj("""{"name":"no slug"}""")), "x"))
    }

    @Test
    fun parseBookOverStubHttp() = runTest {
        val http = FenrirStubHttp(
            mapOf(
                "/api/new/v2/series/test-saga/chapters" to FenrirStubHttp.CHAPTERS_JSON_TEXT,
                "/api/new/v2/series/test-saga" to FenrirStubHttp.SERIES_JSON_TEXT,
            ),
        )
        val context = SourceContext(http, FenrirStubEngine(FenrirFakePage()), InMemorySecretStore(), AppSettings())
        val book = adapter.parseBook("https://fenrirealm.com/series/test-saga", context)
        assertEquals(4, book.chapters.size)
        assertEquals("test-saga", book.id)
    }

    // Chapter content

    @Test
    fun cleanHtmlDropsHiddenBlocksAndInvisibleChars() {
        val source = """
            <style>.junk { display:none } .keep { color: red }</style>
            <p>Real${"\u200B"} text</p><p class="junk">poison</p><p aria-hidden="true">poison2</p>
            <p style="font-size:0">poison3</p><p class="keep">kept</p><p hidden>poison4</p>
        """.trimIndent()
        val blocks = FenrirParsing.htmlBlocks(FenrirParsing.cleanHtml(source), "https://fenrirealm.com/")
        assertEquals(listOf(FenrirBlock.Text("Real text"), FenrirBlock.Text("kept")), blocks)
    }

    @Test
    fun htmlBlocksKeepImagesAndLineBreaks() {
        val blocks = FenrirParsing.htmlBlocks("<div>a<br>b</div><img src=\"/x/p.png\"><p>c\nd</p>", "https://fenrirealm.com/")
        assertEquals(
            listOf(
                FenrirBlock.Text("a"), FenrirBlock.Text("b"), FenrirBlock.Image("https://fenrirealm.com/x/p.png"),
                FenrirBlock.Text("c"), FenrirBlock.Text("d"),
            ),
            blocks,
        )
    }

    @Test
    fun buildChapterFromDocument() {
        val ref = ChapterRef("1|u1", 7, "Chapter 7")
        val doc = """
            {"type":"doc","content":[
              {"type":"heading","content":[{"type":"text","text":"Chapter 7"}]},
              {"type":"paragraph","content":[{"type":"text","text":"Hello "},{"type":"text","text":"world"}]},
              {"type":"image","attrs":{"src":"/img/a.webp"}},
              {"type":"paragraph","content":[{"type":"text","text":"x"},{"type":"hardBreak"},{"type":"text","text":"y <z>"}]}
            ]}
        """.trimIndent()
        val payload = JsonObject(mapOf("content" to kotlinx.serialization.json.JsonPrimitive(doc)))
        val content = FenrirParsing.buildChapter(payload, ref)
        assertNotNull(content)
        content!!
        assertTrue(content.html.contains("<p>Hello world</p>"))
        assertFalse("title heading is dropped", content.html.contains(">Chapter 7<"))
        assertTrue(content.html.contains("<p>y &lt;z&gt;</p>"))
        assertEquals(listOf("images/0007_01.webp"), content.images.map { it.filename })
        assertTrue(content.html.contains("src=\"images/0007_01.webp\""))
    }

    @Test
    fun buildChapterPlainTextAndEmpty() {
        val ref = ChapterRef("1|u1", 1, "T")
        val content = FenrirParsing.buildChapter(obj("""{"content":"one\r\n\r\ntwo","content_format":"text"}"""), ref)
        assertEquals("<div class=\"fenrir-content\">\n<p>one</p>\n<p>two</p>\n</div>", content?.html)
        assertNull(FenrirParsing.buildChapter(obj("""{"content":"   ","content_format":"text"}"""), ref))
        assertNull(FenrirParsing.buildChapter(obj("""{"content":5}"""), ref))
    }

    // Batch over a fake page

    private fun makeContext(page: FenrirFakePage): SourceContext {
        val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0))
        return SourceContext(FenrirStubHttp(emptyMap()), FenrirStubEngine(page), InMemorySecretStore(), settings)
    }

    private fun makeBook(): BookInfo {
        val chapters = listOf(
            ChapterRef("one|u1", 1, "Chapter 1", url = "https://fenrirealm.com/series/s/one"),
            ChapterRef("two|u2", 2, "Chapter 2", url = "https://fenrirealm.com/series/s/two", isPaid = true),
            ChapterRef("three|u3", 3, "Chapter 3", url = "https://fenrirealm.com/series/s/three"),
        )
        return BookInfo(SourceId.FENRIR, "s", "https://fenrirealm.com/series/s", "S", chapters = chapters)
    }

    @Test
    fun batchReadsOwnedAndRefusesPaidAndLocked() = runTest {
        val replies = """
            [{"status":200,"payload":{"content_format":"text","content":"Hello there"}},
             {"status":200,"payload":{"content_format":"locked","content":""}}]
        """.trimIndent()
        val page = FenrirFakePage(tokenActive = true, sequence = listOf(replies))
        val book = makeBook()
        val results = adapter.parseChapters(book.chapters, book, makeContext(page))
        assertEquals(3, results.size)
        val first = results[0] as? ChapterResult.Content ?: return@runTest fail("chapter 1 should be content")
        assertTrue(first.content.html.contains("<p>Hello there</p>"))
        val paid = results[1] as? ChapterResult.Refused ?: return@runTest fail("paid chapter should be refused")
        assertFalse(paid.reason.isEmpty())
        assertTrue("locked chapter should be refused", results[2] is ChapterResult.Refused)
    }

    @Test
    fun batchRetriesRateLimit() = runTest {
        val page = FenrirFakePage(
            tokenActive = true,
            sequence = listOf(
                "[{\"status\":429,\"retryAfter\":\"0\"}]",
                "[{\"status\":200,\"payload\":{\"content_format\":\"text\",\"content\":\"After retry\"}}]",
            ),
        )
        val book = makeBook()
        val result = adapter.parseChapter(book.chapters[0], book, makeContext(page))
        val content = result as? ChapterResult.Content ?: return@runTest fail("expected content after retry")
        assertTrue(content.content.html.contains("After retry"))
    }
}

// Stubs

private class FenrirStubHttp(private val routes: Map<String, String>) : HttpFetching {
    companion object {
        val SERIES_JSON_TEXT = """
            {"title":"Test Saga","user":{"name":"Writer One"},"description":"<p>d</p>"}
        """.trimIndent()
        val CHAPTERS_JSON_TEXT = """
            [{"number":1,"index":1,"name":"Chapter 1"},{"number":2,"index":2,"name":"Chapter 2"},
             {"number":3,"index":3,"name":"Chapter 3","locked":{"price":2}},{"number":4,"index":4,"name":"Chapter 4"}]
        """.trimIndent()
    }

    override suspend fun send(request: HttpRequest): HttpResponse {
        // Longest matching route wins so the chapter list beats the series route.
        val path = URI(request.url).path ?: ""
        val key = routes.keys.filter { path.endsWith(it) }.maxByOrNull { it.length }
        val body = key?.let { routes[it] } ?: return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
        return HttpResponse(request.url, 200, emptyMap(), body.toByteArray())
    }
}

private class FenrirFakePage(
    private val tokenActive: Boolean = true,
    sequence: List<String> = listOf("[]"),
) : WebPageDriving {
    private val sequence = sequence.toMutableList()

    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {}

    override suspend fun evaluateJson(functionBody: String): String {
        if (functionBody.contains("__fn")) {
            synchronized(this) {
                return if (sequence.size > 1) sequence.removeAt(0) else (sequence.firstOrNull() ?: "[]")
            }
        }
        if (functionBody.contains("read-token")) return if (tokenActive) "true" else "false"
        return "[]"
    }

    override suspend fun addInitScript(source: String) {}
    override suspend fun currentUrl(): String? = null
    override suspend fun html(): String = ""
    override suspend fun close() {}
}

private class FenrirStubEngine(private val page: FenrirFakePage) : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = page
    override suspend fun presentLogin(url: String, profile: String) {}
}
