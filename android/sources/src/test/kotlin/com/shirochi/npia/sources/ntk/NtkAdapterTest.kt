package com.shirochi.npia.sources.ntk

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.hard.RecordingHttp
import com.shirochi.npia.sources.hard.ScriptedPage
import com.shirochi.npia.sources.hard.ScriptedWeb
import com.shirochi.npia.sources.hard.context
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class NtkAdapterTest {
    private val adapter = NtkAdapter()

    @Test
    fun recognisesNtkHostsAndPaths() {
        assertTrue(adapter.matches("https://ntk01.com/novel/12345"))
        assertTrue(adapter.matches("https://newtoki468.com/webtoon/999/1000"))
        assertTrue(adapter.matches("https://sbxh9.com/novel/35110"))
        assertTrue(adapter.matches("https://www.sbxh12.org/novel/1/2"))
        assertFalse("another site", adapter.matches("https://example.com/novel/1"))
        assertFalse("wrong kind", adapter.matches("https://ntk01.com/comic/1"))
        assertFalse("host needs a number", adapter.matches("https://ntk.com/novel/1"))
        assertFalse("not a work path", adapter.matches("https://ntk01.com/"))
    }

    @Test
    fun recognisesMirrorSourceEpisodeIds() {
        assertTrue(adapter.matches("https://sbxh9.com/webtoon/56566288/goodtoon-74517682585840"))
        assertTrue(adapter.matches("https://sbxh9.com/webtoon/56566288/kp-56566288-67553760"))
        assertTrue(adapter.matches("https://sbxh9.com/webtoon/56566288/tkor5-1182039/"))
        assertEquals("https://sbxh9.com/webtoon/56566288", adapter.canonicalUrl("https://sbxh9.com/webtoon/56566288/goodtoon-74517682585840"))
    }

    @Test
    fun ownsTheRotatingMirrorHosts() {
        assertTrue(adapter.ownsSite("ntk01.com"))
        assertTrue(adapter.ownsSite("newtoki468.com"))
        assertTrue(adapter.ownsSite("sbxh12.org"))
        assertTrue(adapter.ownsSite("www.newtoki1.com"))
        assertFalse(adapter.ownsSite("example.com"))
        assertFalse(adapter.ownsSite("ntk.com"))
    }

    @Test
    fun aChapterUrlCanonicalisesToItsBookIndex() {
        assertEquals("https://sbxh9.com/novel/35110", adapter.canonicalUrl("https://sbxh9.com/novel/35110/812345"))
        assertEquals("https://newtoki468.com/webtoon/999", adapter.canonicalUrl("https://newtoki468.com/webtoon/999/1000"))
        // A book-index URL is already canonical.
        assertEquals("https://sbxh9.com/novel/35110", adapter.canonicalUrl("https://sbxh9.com/novel/35110"))
        // A URL this adapter does not own is left as-is.
        assertEquals("https://example.com/novel/1", adapter.canonicalUrl("https://example.com/novel/1"))
    }

    // Webtoon pages

    private val webp = "RIFF\u0000\u0000\u0000\u0000WEBPVP8 ".toByteArray(Charsets.ISO_8859_1)

    private val pages = """
        {"ok":true,"chapterName":"One","contentText":"","contentHtml":"<div class=\"ntk-content\"><div class=\"ntk-webtoon-page\"><img src=\"images/0001_0001.webp\" alt=\"page 1\"/></div>\n<div class=\"ntk-webtoon-page\"><img src=\"images/0001_0002.webp\" alt=\"page 2\"/></div></div>","images":[
          {"url":"https://dead.example/a.js","name":"images/0001_0001.webp","candidates":["https://dead.example/a.js","https://live.example/a.js"]},
          {"url":"https://dead.example/b.js","name":"images/0001_0002.webp","candidates":["https://dead.example/b.js","https://gone.example/b.js"]}
        ]}
    """.trimIndent()

    private fun book() = BookInfo(source = SourceId.NTK, id = "5", url = "https://sbxh9.com/webtoon/5", title = "T")

    private fun chapter() = ChapterRef(id = "kp-5-6", index = 1, title = "One", url = "https://sbxh9.com/webtoon/5/kp-5-6")

    private fun web(reply: String) = ScriptedWeb { index ->
        ScriptedPage("page$index") { call ->
            when {
                call.startsWith("ntk.chapterState") -> """{"ready":true}"""
                call.startsWith("ntk.webtoon") -> reply
                else -> null
            }
        }
    }

    private fun served(request: HttpRequest, status: Int, body: ByteArray) = HttpResponse(request.url, status, emptyMap(), body)

    @Test
    fun aWebtoonPageComesFromTheFirstMirrorServingItAndDeadPagesAreLeftOut() = runTest {
        val http = RecordingHttp { request ->
            if (request.url.startsWith("https://live.example/")) served(request, 200, webp) else served(request, 522, ByteArray(0))
        }
        val scripted = web(pages)
        val result = NtkAdapter().parseChapter(chapter(), book(), context(scripted, http = http, source = SourceId.NTK))

        val content = (result as ChapterResult.Content).content
        assertEquals(listOf("images/0001_0001.webp"), content.images.map { it.filename })
        assertArrayEquals(webp, content.images[0].data)
        assertEquals("https://dead.example/a.js", content.images[0].url)
        assertTrue(content.html.contains("images/0001_0001.webp"))
        assertFalse("the page no mirror served is gone from the chapter", content.html.contains("images/0001_0002.webp"))
        assertTrue("the chapter number reaches the page", scripted.pages[0].calls.any { it.contains("\"index\":1") })
        // One try per host: no address is asked for twice.
        assertEquals(http.requests.map { it.url }.distinct().size, http.requests.size)
    }

    @Test
    fun aMirrorAnsweringWithAnErrorPageDoesNotCountAsServingThePage() = runTest {
        val http = RecordingHttp { request ->
            if (request.url.startsWith("https://live.example/")) served(request, 200, webp) else served(request, 200, "<html>nope</html>".toByteArray())
        }
        val result = NtkAdapter().parseChapter(chapter(), book(), context(web(pages), http = http, source = SourceId.NTK))
        assertEquals(1, (result as ChapterResult.Content).content.images.size)
        assertArrayEquals(webp, result.content.images[0].data)
    }

    @Test
    fun aMirrorThatHangsLosesItsTurn() = runTest {
        val http = object : com.shirochi.npia.core.HttpFetching {
            override suspend fun send(request: HttpRequest): HttpResponse {
                if (!request.url.startsWith("https://live.example/")) delay(60_000)
                return served(request, 200, webp)
            }
        }
        val fast = NtkAdapter(NtkTiming(mirrorTimeoutSeconds = 1))
        val result = fast.parseChapter(chapter(), book(), context(web(pages), http = http, source = SourceId.NTK))
        assertEquals(listOf("images/0001_0001.webp"), (result as ChapterResult.Content).content.images.map { it.filename })
    }

    @Test
    fun aChapterNoMirrorServesFails() = runTest {
        val http = RecordingHttp { served(it, 523, ByteArray(0)) }
        val result = NtkAdapter().parseChapter(chapter(), book(), context(web(pages), http = http, source = SourceId.NTK))
        assertTrue((result as ChapterResult.Failed).message.contains("no mirror"))
    }

    @Test
    fun savingOnlyImageUrlsDownloadsNothing() = runTest {
        val http = RecordingHttp()
        val settings = AppSettings().let { it.copy(images = it.images.copy(saveImageUrlsOnly = true)) }
        val result = NtkAdapter().parseChapter(chapter(), book(), context(web(pages), http = http, settings = settings, source = SourceId.NTK))
        assertEquals(2, (result as ChapterResult.Content).content.images.size)
        assertTrue(http.requests.isEmpty())
    }

    @Test
    fun mirrorsThatFailedGoLast() {
        val mirrors = NtkMirrors()
        val urls = listOf("https://a.example/1.js", "https://b.example/1.js", "https://c.example/1.js")
        assertEquals(urls, mirrors.ordered(urls))
        mirrors.failed("https://a.example/9.js")
        assertEquals(listOf("https://b.example/1.js", "https://c.example/1.js", "https://a.example/1.js"), mirrors.ordered(urls))
    }

    @Test
    fun removingAPageLeavesTheOthers() {
        val html = "<div class=\"ntk-webtoon-page\"><img src=\"images/0001_0001.webp\" alt=\"page 1\"/></div>\n" +
            "<div class=\"ntk-webtoon-page\"><img src=\"images/0001_0002.webp\" alt=\"page 2\"/></div>"
        assertEquals(
            "<div class=\"ntk-webtoon-page\"><img src=\"images/0001_0002.webp\" alt=\"page 2\"/></div>",
            NtkSite.withoutPages(html, listOf("images/0001_0001.webp")),
        )
    }
}
