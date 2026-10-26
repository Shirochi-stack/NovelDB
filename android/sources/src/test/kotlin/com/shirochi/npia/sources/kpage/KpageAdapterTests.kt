package com.shirochi.npia.sources.kpage

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.KpageSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceOptions
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.Collections

private const val KPAGE_SITE = "page.\u006b\u0061\u006b\u0061\u006f.com"

// Stubs

private class KpageRoute(val contains: String, val status: Int, val contentType: String, val body: String)

private class KpageStubHttp(private val routes: List<KpageRoute> = emptyList()) : HttpFetching {
    val log: MutableList<String> = Collections.synchronizedList(mutableListOf())

    override suspend fun send(request: HttpRequest): HttpResponse {
        log.add(request.url)
        for (route in routes) {
            if (request.url.contains(route.contains)) {
                return HttpResponse(request.url, route.status, mapOf("content-type" to route.contentType), route.body.toByteArray())
            }
        }
        return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
    }
}

private class KpageFakePage(private val htmlText: String, private val tagsJson: String = "[]") : WebPageDriving {
    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {}

    override suspend fun evaluateJson(functionBody: String): String =
        if (functionBody.contains("startsWith('#')")) tagsJson else "true"

    override suspend fun addInitScript(source: String) {}
    override suspend fun currentUrl(): String? = null
    override suspend fun html(): String = htmlText
    override suspend fun close() {}
}

private class KpageStubWeb(private val page: KpageFakePage) : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = page
    override suspend fun presentLogin(url: String, profile: String) {}
}

// Fixtures

private const val KPAGE_BOOK_HTML = """
<html><head>
<meta property="og:title" content="Test Novel - &#xc6f9;&#xc18c;&#xc124; | kpage"/>
<meta property="og:image" content="https://img.example.test/cover.jpg"/>
<meta property="og:description" content="A short synopsis."/>
</head><body><div><h2>Test Novel</h2><span>Author Name</span></div></body></html>
"""

// JSON fixtures use JSON's own escapes for Korean text.
private const val KPAGE_EPISODES_JSON = """
{"result":{"list":[
 {"item":{"product_id":103,"title":"Test Novel 3\uD654","order_value":3,"is_free":false,"rental":{"owned":"yes_rented"}}},
 {"item":{"product_id":101,"title":"Test Novel 1\uD654","order_value":1,"is_free":true}},
 {"item":{"product_id":102,"title":"Test Novel 2\uD654","order_value":2,"is_free":false,"ticket_count":5,"price":100}},
 {"item":{"product_id":104,"title":"Test Novel 4\uD654","order_value":0,"is_free":false}}
]}}
"""

private const val KPAGE_VIEWER_TEXT = """
{"result":{"viewerData":{"atsServerUrl":"http://ats.example.test","contentsList":[{"secureUrl":"/chunk1"},{"secureUrl":"/chunk2"}]}}}
"""

private const val KPAGE_CHUNK1 = """
{"contentInfo":{"contentId":0,"paragraphList":[
 {"id":1,"type":"H1","text":"Heading"},
 {"id":2,"type":"P","text":"Hello &amp; <world>"},
 {"id":3,"type":"P","childParagraphList":[{"type":"SPAN","text":"Bold","style":{"bold":true}},{"type":"BR"}]}
]}}
"""

private const val KPAGE_CHUNK2 = """
{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","text":"Second page"}]}}
"""

private const val KPAGE_VIEWER_IMAGES = """
{"result":{"viewerData":{"imageDownloadData":{"files":[
 {"no":2,"secureUrl":"https://img.example.test/b?filename=b%20x.png","width":100,"height":200},
 {"no":1,"secureUrl":"https://img.example.test/a?filename=a.jpg"}
]}}}}
"""

private fun kpPara(plain: String, type: String = "P", chunk: Int = 0, html: String? = null) = KpageParagraph(
    plain = plain, html = html ?: plain, type = type, style = JsonObject(emptyMap()), attrs = JsonObject(emptyMap()),
    chunkIndex = chunk, contentId = 0, paragraphId = 0,
)

private fun kpContext(
    http: KpageStubHttp,
    web: KpageStubWeb = KpageStubWeb(KpageFakePage("")),
    settings: AppSettings = AppSettings(),
) = SourceContext(http, web, InMemorySecretStore(), settings)

private fun kpRef(product: String, index: Int, paid: Boolean = false) = ChapterRef(
    id = product, index = index, title = "Episode $index",
    url = "https://$KPAGE_SITE/content/42/viewer/$product", isPaid = paid,
)

private fun kpBook() = BookInfo(SourceId.KPAGE, "42", "https://$KPAGE_SITE/content/42", "Test Novel")

private fun utf8(text: String): ByteArray = text.toByteArray(Charsets.UTF_8)

class KpageAdapterTests {
    private val adapter = KpageAdapter()

    // URL matching

    @Test
    fun matchesContentUrls() {
        assertTrue(adapter.matches("https://$KPAGE_SITE/content/123"))
        assertTrue(adapter.matches("http://$KPAGE_SITE/content/123?tab_type=about"))
        assertTrue(adapter.matches("https://$KPAGE_SITE/content/123/viewer/456"))
    }

    @Test
    fun rejectsOtherUrls() {
        assertFalse(adapter.matches("https://$KPAGE_SITE/home"))
        assertFalse(adapter.matches("https://$KPAGE_SITE/content/abc"))
        assertFalse(adapter.matches("https://example.com/content/123"))
        assertFalse(adapter.matches("https://www.$KPAGE_SITE/content/123"))
        assertFalse(adapter.matches("https://example.com/novel/1"))
        assertFalse(adapter.matches("ftp://$KPAGE_SITE/content/123"))
    }

    @Test
    fun viewerIds() {
        val ids = KpageParsing.viewerIds("https://$KPAGE_SITE/content/12/viewer/345")
        assertEquals("12", ids?.first)
        assertEquals("345", ids?.second)
        assertNull(KpageParsing.viewerIds("https://$KPAGE_SITE/content/12"))
        assertEquals("12", KpageParsing.seriesId("https://$KPAGE_SITE/content/12?x=1"))
    }

    // Metadata and episodes

    @Test
    fun parseMetadata() {
        val meta = KpageParsing.parseMetadata(KPAGE_BOOK_HTML)
        assertEquals("Test Novel", meta.title)
        assertEquals("Author Name", meta.author)
        assertEquals("https://img.example.test/cover.jpg", meta.cover)
        assertEquals("A short synopsis.", meta.description)
    }

    @Test
    fun parseEpisodesSortsAndFlagsAccess() {
        val episodes = KpageParsing.parseEpisodes(utf8(KPAGE_EPISODES_JSON))
        assertEquals(listOf(101, 102, 103, 104), episodes.map { it.productId })
        assertEquals(listOf(true, false, true, false), episodes.map { it.isAccessible })
        assertEquals("1\uD654", episodes[0].shortName)
        assertTrue(episodes[0].isFree)
    }

    @Test
    fun hasUserAccessFlag() {
        fun check(json: String): Boolean = KpageParsing.hasUserAccessFlag(Json.parseToJsonElement(json))
        assertTrue(check("""{"a":{"purchased":true}}"""))
        assertTrue(check("""{"pass":1}"""))
        assertTrue(check("""{"rented":"yes_rented"}"""))
        assertFalse(check("""{"ticket_count":3}"""))
        assertFalse(check("""{"usable":"no"}"""))
        assertFalse(check("""{"x":{"rented":"expired"}}"""))
        assertFalse(check("""{"price_pass":true}"""))
        assertFalse(check("""{"rented":false}"""))
    }

    @Test
    fun sortKeyFallsBackToTitleNumberThenLast() {
        val numbered = KpageEpisode(5, 0, "", "Title 7\uD654", false, false, "")
        val unnumbered = KpageEpisode(1, 0, "", "Prologue", false, false, "")
        val ordered = KpageParsing.sortEpisodes(listOf(unnumbered, numbered))
        assertEquals(listOf(5, 1), ordered.map { it.productId })
        assertEquals(7L, KpageParsing.sortKey(numbered).first)
        assertEquals(12, KpageParsing.titleNumber("12\uD654"))
        assertEquals(0, KpageParsing.titleNumber("none"))
    }

    // Viewer data

    @Test
    fun viewerOutcomes() {
        assertEquals(
            KpageViewerOutcome.Text("https://ats.example.test", listOf("/chunk1", "/chunk2")),
            KpageParsing.parseViewer(utf8(KPAGE_VIEWER_TEXT), 200),
        )
        assertEquals(
            KpageViewerOutcome.Locked("not_purchased"),
            KpageParsing.parseViewer(utf8("""{"message":"not_purchased"}"""), 200),
        )
        assertEquals(KpageViewerOutcome.Locked("access denied"), KpageParsing.parseViewer(utf8("{}"), 403))
        assertEquals(KpageViewerOutcome.Unavailable("non-json response"), KpageParsing.parseViewer(utf8("<html>"), 200))
        assertEquals(
            KpageViewerOutcome.Unavailable("api returned no viewer resources (code 7)"),
            KpageParsing.parseViewer(utf8("""{"result_code":7}"""), 200),
        )
        val images = KpageParsing.parseViewer(utf8(KPAGE_VIEWER_IMAGES), 200)
        if (images is KpageViewerOutcome.Images) {
            assertEquals(2, images.files.size)
            assertEquals("b%20x.png", images.files[0].filename)
        } else {
            fail("expected images")
        }
    }

    // Paragraph extraction and output

    @Test
    fun extractAndBuildOutput() {
        val paragraphs = KpageParsing.extractParagraphs(
            listOf(utf8(KPAGE_CHUNK1), utf8(KPAGE_CHUNK2)), skipLastPage = false, keepFiller = false,
        )
        assertEquals(listOf("Heading", "Hello & <world>", "Bold", "Second page"), paragraphs.map { it.plain })
        val output = KpageParsing.buildOutput(paragraphs)
        val expected = listOf(
            "<h1 class=\"kpage-source-heading\">Heading</h1>",
            KpageParsing.PAGE_BREAK,
            "<p>Hello &amp; &lt;world&gt;</p>",
            "<p><b>Bold</b><br /></p>",
            KpageParsing.PAGE_BREAK,
            "<p>Second page</p>",
        ).joinToString("\n")
        assertEquals(expected, output.html)
        assertEquals("Heading\nHello & <world>\nBold\nSecond page", output.text)
        assertEquals("Heading", KpageParsing.headingTitle(paragraphs, "x"))
        assertEquals("fallback", KpageParsing.headingTitle(listOf(kpPara("body")), "fallback"))
    }

    @Test
    fun skipLastPageAndKeepFiller() {
        val chunks = listOf(utf8(KPAGE_CHUNK1), utf8(KPAGE_CHUNK2))
        val skipped = KpageParsing.extractParagraphs(chunks, skipLastPage = true, keepFiller = false)
        assertFalse(skipped.any { it.plain == "Second page" })
        val kept = KpageParsing.extractParagraphs(chunks, skipLastPage = true, keepFiller = true)
        assertTrue(kept.any { it.plain == "Second page" })
    }

    @Test
    fun colophonChunkIsDropped() {
        val colophon = """{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","text":"ISBN 978 \uBC1C\uD589\uC778 Someone \uBE14\uB85C\uADF8"}]}}"""
        val chunks = listOf(utf8(KPAGE_CHUNK1), utf8(colophon))
        val dropped = KpageParsing.extractParagraphs(chunks, skipLastPage = false, keepFiller = false)
        assertFalse(dropped.any { it.plain.contains("ISBN") })
        val kept = KpageParsing.extractParagraphs(chunks, skipLastPage = false, keepFiller = true)
        assertTrue(kept.any { it.plain.contains("ISBN") })
        assertFalse(KpageParsing.isColophonChunk("Just a story with an ISBN mention"))
    }

    @Test
    fun imageOnlyParagraphsAreDropped() {
        val chunk = """{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","childParagraphList":[{"type":"IMG","text":""}]},{"id":2,"type":"P","text":"Kept"}]}}"""
        val paragraphs = KpageParsing.extractParagraphs(listOf(utf8(chunk)), skipLastPage = false, keepFiller = false)
        assertEquals(listOf("Kept"), paragraphs.map { it.plain })
    }

    @Test
    fun styleIsRendered() {
        val chunk = """{"contentInfo":{"contentId":0,"paragraphList":[{"id":1,"type":"P","text":"X","style":{"textAlign":"CENTER","color":"ff0000","fontSize":14}}]}}"""
        val paragraphs = KpageParsing.extractParagraphs(listOf(utf8(chunk)), skipLastPage = false, keepFiller = false)
        val output = KpageParsing.buildOutput(paragraphs)
        assertTrue(output.html.contains("color:#ff0000"))
        assertTrue(output.html.contains("text-align:center"))
        assertTrue(output.html.contains("font-size:14px"))
    }

    @Test
    fun stripHeadings() {
        val stripped = KpageParsing.stripHeadings(
            listOf(kpPara("\uC81C12\uD654"), kpPara("&nbsp;"), kpPara("  "), kpPara("Text"), kpPara("\uC81C3\uD654")),
        )
        assertEquals(listOf("Text", "\uC81C3\uD654"), stripped.map { it.plain })
    }

    @Test
    fun blankBreakParagraphIsKept() {
        val output = KpageParsing.buildOutput(listOf(kpPara("", "P", html = "<br />"), kpPara("Text")))
        assertEquals("<p><br /></p>\n<p>Text</p>", output.html)
    }

    // Image chapters

    @Test
    fun buildImageChapter() {
        val outcome = KpageParsing.parseViewer(utf8(KPAGE_VIEWER_IMAGES), 200)
        if (outcome !is KpageViewerOutcome.Images) return fail("expected images")
        val built = KpageParsing.buildImageChapter(outcome.files, "Ep 5", 5)
        assertNotNull(built)
        built!!
        assertEquals(listOf("images/0005_01.jpg", "images/0005_02.png"), built.images.map { it.filename })
        assertEquals("https://img.example.test/a?filename=a.jpg", built.images[0].url)
        assertTrue(built.html.contains("src=\"images/0005_01.jpg\""))
        assertTrue(built.html.contains("width=\"100\" height=\"200\""))
        assertNull(KpageParsing.buildImageChapter(emptyList(), "x", 1))
    }

    @Test
    fun removeDuplicateLeadingImages() {
        fun chapter(index: Int, bytes: ByteArray): ChapterContent {
            val file = KpageImageFile(1, "https://img.example.test/$index", 0, 0, "p.jpg")
            val built = KpageParsing.buildImageChapter(listOf(file), "c", index)!!
            val images = built.images.toMutableList()
            images[0] = images[0].copy(data = bytes)
            return ChapterContent(ChapterRef("$index", index, "c"), built.html, images)
        }
        val cleaned = KpageParsing.removeDuplicateLeadingImages(
            listOf(chapter(1, byteArrayOf(1)), chapter(2, byteArrayOf(1)), chapter(3, byteArrayOf(2))), 2,
        )
        assertEquals(1, cleaned[0].images.size)
        assertEquals(0, cleaned[1].images.size)
        assertFalse(cleaned[1].html.contains("<img"))
        assertEquals(1, cleaned[2].images.size)
        assertTrue(cleaned[2].html.contains("<img"))
    }

    @Test
    fun jsLiteralEscapes() {
        assertEquals("\"a\\\"b\"", KpageAdapter.jsLiteral("a\"b"))
    }

    // Adapter flows

    @Test
    fun parseBookEndToEnd() = runTest {
        val http = KpageStubHttp(listOf(KpageRoute("/product/list", 200, "application/json", KPAGE_EPISODES_JSON)))
        val web = KpageStubWeb(KpageFakePage(KPAGE_BOOK_HTML, """["tagone","tagtwo"]"""))
        val book = adapter.parseBook("https://$KPAGE_SITE/content/42", kpContext(http, web))
        assertEquals(SourceId.KPAGE, book.source)
        assertEquals("42", book.id)
        assertEquals("Test Novel", book.title)
        assertEquals("Author Name", book.author)
        assertEquals(listOf("tagone", "tagtwo"), book.tags)
        assertEquals("https://img.example.test/cover.jpg", book.coverUrl)
        assertEquals(listOf(1, 2, 3, 4), book.chapters.map { it.index })
        assertEquals(listOf("101", "102", "103", "104"), book.chapters.map { it.id })
        assertEquals(listOf(false, true, false, true), book.chapters.map { it.isPaid })
        assertEquals("https://$KPAGE_SITE/content/42/viewer/101", book.chapters[0].url)
    }

    @Test
    fun parseTextChapter() = runTest {
        val http = KpageStubHttp(
            listOf(
                KpageRoute("/viewer/data", 200, "application/json", KPAGE_VIEWER_TEXT),
                KpageRoute("ats.example.test/chunk1", 200, "application/json", KPAGE_CHUNK1),
                KpageRoute("ats.example.test/chunk2", 200, "application/json", KPAGE_CHUNK2),
            ),
        )
        val result = adapter.parseChapter(kpRef("105", 5), kpBook(), kpContext(http))
        if (result !is ChapterResult.Content) return@runTest fail("expected content, got $result")
        assertEquals("Heading", result.content.ref.title)
        assertTrue(result.content.html.contains("<p>Hello &amp; &lt;world&gt;</p>"))
        assertTrue(result.content.images.isEmpty())
    }

    @Test
    fun parseImageChapterWithDedupeFetchesLeadingBytes() = runTest {
        val http = KpageStubHttp(
            listOf(
                KpageRoute("/viewer/data", 200, "application/json", KPAGE_VIEWER_IMAGES),
                KpageRoute("img.example.test", 200, "image/jpeg", "bytes"),
            ),
        )
        val settings = AppSettings(
            sources = SourceOptions(kpage = KpageSettings(dedupeImages = true, dedupeLeadingImages = 1)),
        )
        val results = adapter.parseChapters(listOf(kpRef("106", 6)), kpBook(), kpContext(http, settings = settings))
        val first = results[0]
        if (first !is ChapterResult.Content) return@runTest fail("expected content")
        assertEquals(2, first.content.images.size)
        assertNotNull(first.content.images[0].data)
        assertNull(first.content.images[1].data)
    }

    @Test
    fun lockedChapterIsRefused() = runTest {
        val http = KpageStubHttp(listOf(KpageRoute("/viewer/data", 200, "application/json", """{"message":"not_purchased"}""")))
        val result = adapter.parseChapter(kpRef("107", 7, paid = true), kpBook(), kpContext(http))
        if (result !is ChapterResult.Refused) return@runTest fail("expected refused, got $result")
        assertEquals("107", result.ref.id)
        assertTrue(result.reason.contains("not_purchased"))
    }

    @Test
    fun forbiddenStatusIsRefused() = runTest {
        val http = KpageStubHttp(listOf(KpageRoute("/viewer/data", 403, "application/json", "{}")))
        val result = adapter.parseChapter(kpRef("108", 8), kpBook(), kpContext(http))
        assertTrue("expected refused, got $result", result is ChapterResult.Refused)
    }

    @Test
    fun skipPaidDoesNotTouchTheNetwork() = runTest {
        val settings = AppSettings().let { it.copy(shared = it.shared.copy(skipPaid = true)) }
        val http = KpageStubHttp()
        val result = adapter.parseChapter(kpRef("109", 9, paid = true), kpBook(), kpContext(http, settings = settings))
        assertTrue("expected refused, got $result", result is ChapterResult.Refused)
        assertTrue(http.log.isEmpty())
    }

    @Test
    fun emptyTextIsFailedNotContent() = runTest {
        val http = KpageStubHttp(listOf(KpageRoute("/viewer/data", 200, "application/json", KPAGE_VIEWER_TEXT)))
        val result = adapter.parseChapter(kpRef("110", 10), kpBook(), kpContext(http))
        assertTrue("expected failed, got $result", result is ChapterResult.Failed)
    }
}
