package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebLoadTimeoutException
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.sources.SourceCatalog
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class SiteEngineChainTest {
    private val all = listOf(SiteEngine.NATIVE, SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB)
    private val url = "https://site.test/book"

    private fun book(source: SourceId, chapters: Int, title: String = "Book") = BookInfo(
        source = source, id = url, url = url, title = title,
        chapters = (1..chapters).map { ChapterRef("$it", it, "C$it") },
    )

    /** A built-in scraper whose book comes from `result`. */
    private class Native(val result: () -> BookInfo, override val id: SourceId = SourceId.KPAGE) : SourceAdapter {
        val calls = mutableListOf<String>()
        override val tier = SourceTier.HTTP

        override fun matches(url: String) = true

        override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
            calls += "native"
            return result()
        }

        override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
            ChapterResult.Content(ChapterContent(ref, "<p>native ${ref.index}</p>"))
    }

    /** Script engines that answer from `results`: a book, or an exception to throw. */
    private class Scripts(val results: Map<SiteEngine, () -> BookInfo>, val loadsPage: Boolean = false) : ScriptEngineOpener {
        val calls = mutableListOf<SiteEngine>()

        override suspend fun open(engine: SiteEngine, url: String, context: SourceContext, page: SharedBookPage): BookInfo {
            calls += engine
            if (loadsPage) page.page()
            val result = results[engine] ?: throw EngineMissException("no rule")
            return result()
        }
    }

    private class Log : JobLogging {
        val lines = mutableListOf<String>()
        override fun log(level: LogLevel, message: String) {
            lines += message
        }
    }

    private object NoHttp : HttpFetching {
        override suspend fun send(request: HttpRequest) = HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
    }

    /** A web engine that counts the pages it opens; each page's first `timeouts` loads time out. */
    private class CountingWeb(val timeouts: Int = 0) : WebEngineProviding {
        val pages = mutableListOf<CountingPage>()

        override suspend fun newPage(profile: String): WebPageDriving = CountingPage(timeouts).also { pages += it }

        override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) = throw WebEngineException("no login")
    }

    private class CountingPage(private var timeouts: Int = 0) : WebPageDriving {
        var loads = mutableListOf<String>()
        var closed = false
        var transport = false

        override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
            loads += url
            if (timeouts > 0) {
                timeouts--
                throw WebLoadTimeoutException(url)
            }
        }

        override suspend fun evaluateJson(functionBody: String) = "null"
        override suspend fun addInitScript(source: String) {}
        override suspend fun currentUrl(): String? = null
        override suspend fun html() = ""
        override suspend fun close() {
            closed = true
        }

        override suspend fun exposeTransport(handler: suspend (String) -> String) {
            transport = true
        }
    }

    // Page loads

    @Test
    fun aPageThatTimesOutIsLoadedOnceMore() = runTest {
        val web = CountingWeb(timeouts = 1)
        val log = Log()
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to { book(SourceId.READER, 1) }), loadsPage = true)
        val result = chain(null, scripts, listOf(SiteEngine.WEBTOEPUB)).parseBook(url, context(log, web))
        assertEquals(1, result.chapters.size)
        assertEquals(listOf(url, url), web.pages.single().loads)
        assertTrue(log.lines.toString(), log.lines.any { "trying once more" in it })
    }

    @Test
    fun aPageThatTimesOutTwiceFailsWithoutAThirdTry() = runTest {
        val web = CountingWeb(timeouts = 2)
        val scripts = Scripts(
            mapOf(SiteEngine.NOVEL_DOWNLOADER to { book(SourceId.READER, 1) }, SiteEngine.WEBTOEPUB to { book(SourceId.READER, 1) }),
            loadsPage = true,
        )
        val error = runCatching { chain(null, scripts, listOf(SiteEngine.WEBTOEPUB, SiteEngine.NOVEL_DOWNLOADER)).parseBook(url, context(web = web)) }
            .exceptionOrNull()
        assertTrue("$error", error?.message.orEmpty().contains("Timed out loading"))
        // The failure is remembered: the second engine does not wait for the same page again.
        assertEquals(listOf(url, url), web.pages.single().loads)
        assertTrue(web.pages.single().closed)
    }

    private fun context(log: Log = Log(), web: WebEngineProviding = CountingWeb()) =
        SourceContext(NoHttp, web, InMemorySecretStore(), AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0)), log)

    private fun chain(native: SourceAdapter?, scripts: ScriptEngineOpener, steps: List<SiteEngine> = all) =
        SiteEngineChain(native, steps.filter { it != SiteEngine.NATIVE || native != null }, scripts)

    private val readerBook = { book(SourceId.READER, 3) }

    // Order and fallback

    @Test
    fun theBuiltInScraperWinsWithoutTouchingTheScriptEngines() = runTest {
        val native = Native({ book(SourceId.KPAGE, 2) })
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to readerBook))
        val result = chain(native, scripts).parseBook(url, context())
        assertEquals(SourceId.KPAGE, result.source)
        assertEquals(emptyList<SiteEngine>(), scripts.calls)
    }

    @Test
    fun anErrorOrNoChaptersTriesTheNextEngineInOrder() = runTest {
        val native = Native({ book(SourceId.KPAGE, 0) })
        val scripts = Scripts(
            mapOf(
                SiteEngine.NOVEL_DOWNLOADER to { throw SourceException.Parse("rules broke") },
                SiteEngine.WEBTOEPUB to readerBook,
            ),
        )
        val log = Log()
        val result = chain(native, scripts).parseBook(url, context(log))
        assertEquals(SourceId.READER, result.source)
        assertEquals(listOf("native"), native.calls)
        assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), scripts.calls)
        assertTrue(log.lines.toString(), "Built-in scrapers found no chapters; trying novel-downloader rules next." in log.lines)
        assertTrue(log.lines.toString(), "novel-downloader rules found no chapters; trying WebToEpub parsers next." in log.lines)
    }

    @Test
    fun theUsersOrderDecidesWhoGoesFirst() = runTest {
        val native = Native({ book(SourceId.KPAGE, 2) })
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to readerBook))
        val order = listOf(SiteEngine.WEBTOEPUB, SiteEngine.NATIVE)
        val result = chain(native, scripts, order).parseBook(url, context())
        assertEquals(SourceId.READER, result.source)
        assertEquals(emptyList<String>(), native.calls)

        // The first one failing hands over to the built-in scraper.
        val failing = Scripts(mapOf(SiteEngine.WEBTOEPUB to { book(SourceId.READER, 0) }))
        val second = chain(native, failing, order).parseBook(url, context())
        assertEquals(SourceId.KPAGE, second.source)
        assertEquals(listOf("native"), native.calls)
    }

    @Test
    fun withoutABuiltInScraperEveryScriptEngineIsTried() = runTest {
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to readerBook))
        val result = chain(null, scripts).parseBook(url, context())
        assertEquals(SourceId.READER, result.source)
        assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), scripts.calls)
    }

    @Test
    fun anEmptyBookFromTheBuiltInScraperIsKeptWhenNothingElseWorks() = runTest {
        val empty = book(SourceId.KPAGE, 0, "Empty")
        val native = Native({ empty })
        val scripts = Scripts(mapOf(SiteEngine.NOVEL_DOWNLOADER to { throw SourceException.Parse("x") }))
        assertSame(empty, chain(native, scripts).parseBook(url, context()))
    }

    @Test
    fun aSignInRequestFromTheBuiltInScraperStopsTheFallback() = runTest {
        val native = Native({ throw SourceException.LoginRequired(SourceId.KPAGE) })
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to readerBook))
        try {
            chain(native, scripts).parseBook(url, context())
            fail("should throw")
        } catch (e: SourceException.LoginRequired) {
            assertEquals(emptyList<SiteEngine>(), scripts.calls)
        }
    }

    @Test
    fun otherBuiltInFailuresFallBackAndAreReportedWhenNothingElseWorks() = runTest {
        val native = Native({ throw SourceException.Blocked("HTTP 403") })
        val scripts = Scripts(emptyMap())
        // Both script engines say they have nothing; the built-in scraper's failure is the telling one.
        try {
            chain(native, scripts).parseBook(url, context())
            fail("should throw")
        } catch (e: SourceException.Blocked) {
            assertTrue(e.message, e.message!!.contains("HTTP 403"))
        }
        assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), scripts.calls)
    }

    @Test
    fun cancellationIsNotAFailure() = runTest {
        val scripts = Scripts(mapOf(SiteEngine.NOVEL_DOWNLOADER to { throw CancellationException("stop") }, SiteEngine.WEBTOEPUB to readerBook))
        try {
            chain(null, scripts).parseBook(url, context())
            fail("should throw")
        } catch (e: CancellationException) {
            assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER), scripts.calls)
        }
    }

    @Test
    fun whenEveryEngineHasNoRuleTheLinkIsUnsupported() = runTest {
        try {
            chain(null, Scripts(emptyMap())).parseBook(url, context())
            fail("should throw")
        } catch (e: SourceException.UnsupportedUrl) {
            assertEquals(url, e.url)
        }
    }

    @Test
    fun aRealFailureOutranksAnEngineWithNoRule() = runTest {
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to { throw SourceException.Parse("The chapter list is empty") }))
        try {
            chain(null, scripts).parseBook(url, context())
            fail("should throw")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message, e.message!!.contains("The chapter list is empty"))
        }
    }

    @Test
    fun anEngineThatCannotRunIsReportedAsAFailureNotASilentSkip() = runTest {
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to { throw WebEngineException("No web engine in this context") }))
        try {
            chain(null, scripts, listOf(SiteEngine.WEBTOEPUB)).parseBook(url, context())
            fail("should throw")
        } catch (e: SourceException) {
            assertTrue(e.message, e.message!!.contains("No web engine"))
        }
    }

    // One page for both script engines

    @Test
    fun theScriptEnginesShareOneLoadedPage() = runTest {
        val web = CountingWeb()
        val scripts = Scripts(mapOf(SiteEngine.WEBTOEPUB to { book(SourceId.READER, 1) }), loadsPage = true)
        chain(null, scripts).parseBook(url, context(web = web))
        assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), scripts.calls)
        assertEquals(1, web.pages.size)
        assertEquals(listOf(url), web.pages[0].loads)
        assertTrue("the transport is installed before the page loads", web.pages[0].transport)
        assertTrue("the page is closed when no engine kept it", web.pages[0].closed)
    }

    @Test
    fun theEngineThatReadTheBookKeepsThePage() = runTest {
        val web = CountingWeb()
        val scripts = object : ScriptEngineOpener {
            override suspend fun open(engine: SiteEngine, url: String, context: SourceContext, page: SharedBookPage): BookInfo {
                page.page()
                page.adopt()
                return book(SourceId.READER, 2)
            }
        }
        SiteEngineChain(null, listOf(SiteEngine.WEBTOEPUB), scripts).parseBook(url, context(web = web))
        assertEquals(1, web.pages.size)
        assertTrue(!web.pages[0].closed)
    }

    @Test
    fun aFailedPageLoadIsNotRetriedByTheNextEngine() = runTest {
        var opens = 0
        val web = object : WebEngineProviding {
            override suspend fun newPage(profile: String): WebPageDriving {
                opens++
                throw WebEngineException("No web engine in this context")
            }

            override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) = Unit
        }
        val scripts = Scripts(emptyMap())
        val shared = SharedBookPage(url, context(web = web))
        repeat(2) {
            try {
                shared.page()
                fail("should throw")
            } catch (e: WebEngineException) {
                assertTrue(e.message!!.contains("No web engine"))
            }
        }
        assertEquals(1, opens)
        assertNull(scripts.results[SiteEngine.WEBTOEPUB])
    }

    // Who reads the chapters

    @Test
    fun chaptersAreReadByTheEngineThatReadTheBook() = runTest {
        val native = Native({ book(SourceId.KPAGE, 1) })
        val engines = chain(native, Scripts(emptyMap()))
        assertSame(native, engines.owner(book(SourceId.KPAGE, 1)))
        assertTrue(engines.owner(book(SourceId.READER, 1)) is ReaderAdapter)
        val ref = ChapterRef("1", 1, "C1")
        val result = engines.parseChapter(ref, book(SourceId.KPAGE, 1), context())
        assertTrue((result as ChapterResult.Content).content.html.contains("native 1"))
        // A chain's identity is the built-in scraper's, so its settings apply until the book says otherwise.
        assertEquals(SourceId.KPAGE, engines.id)
        assertEquals(SourceId.READER, chain(null, Scripts(emptyMap())).id)
    }

    // Choosing the engines for a URL

    private fun steps(url: String, engines: EngineSettings = EngineSettings()): List<SiteEngine>? =
        (SourceCatalog.resolve(url, engines) as? SiteEngineChain)?.steps

    // A site with a built-in scraper that both script engines list (the escapes spell a provider name).
    private val both = "https://b.\u0066\u0061\u006c\u006f\u006f.com/novel/1"

    // A site with a built-in scraper that only WebToEpub lists.
    private val parsersOnly = "https://fenrirealm.com/series/my-book"

    // A site with a built-in scraper that no engine lists.
    private val builtInOnly = "https://www.kobo.com/us/en/ebook/some-book"

    @Test
    fun aBuiltInScraperIsFollowedOnlyByTheEnginesThatListTheSite() {
        assertEquals(SourceId.FLOO, SourceCatalog.resolve(both)!!.id)
        assertEquals(listOf(SiteEngine.NATIVE, SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), steps(both))
        assertEquals(listOf(SiteEngine.NATIVE, SiteEngine.WEBTOEPUB), steps(parsersOnly))
        val reordered = EngineSettings(order = listOf("webtoepub", "novel_downloader", "native"))
        assertEquals(listOf(SiteEngine.WEBTOEPUB, SiteEngine.NOVEL_DOWNLOADER, SiteEngine.NATIVE), steps(both, reordered))
        assertEquals(listOf(SiteEngine.WEBTOEPUB, SiteEngine.NATIVE), steps(parsersOnly, reordered))
        assertEquals(listOf(SiteEngine.NATIVE, SiteEngine.WEBTOEPUB), steps(both, EngineSettings(disabled = listOf("novel_downloader"))))
    }

    @Test
    fun aBuiltInScraperAloneIsReturnedAsItIs() {
        // No engine lists the site: a clear failure (sign-in, DRM) is not followed by browser attempts that cannot work.
        val plain = SourceCatalog.resolve(builtInOnly)!!
        assertEquals(SourceId.KOBO, plain.id)
        assertTrue(plain !is SiteEngineChain)
        // The same when the engines that list the site are switched off.
        val onlyNative = EngineSettings(disabled = listOf("novel_downloader", "webtoepub"))
        assertTrue(SourceCatalog.resolve(both, onlyNative) !is SiteEngineChain)
        assertEquals(SourceId.FLOO, SourceCatalog.resolve(both, onlyNative)!!.id)
    }

    @Test
    fun withTheBuiltInScrapersOffEveryScriptEngineIsTried() {
        val noNative = EngineSettings(disabled = listOf("native"))
        // Even a site no engine lists is tried: there is no scraper to fall back to.
        assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), steps(builtInOnly, noNative))
        assertEquals(SourceId.READER, SourceCatalog.resolve(both, noNative)!!.id)
    }

    @Test
    fun aSiteWithNoBuiltInScraperTriesEveryEnabledScriptEngine() {
        val novelfull = "https://novelfull.com/the-legendary-mechanic.html"
        assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), steps(novelfull))
        val order = EngineSettings(order = listOf("webtoepub", "native", "novel_downloader"))
        assertEquals(listOf(SiteEngine.WEBTOEPUB, SiteEngine.NOVEL_DOWNLOADER), steps(novelfull, order))
        // Sites neither bundle lists are still tried, since WebToEpub also recognises some pages.
        assertEquals(listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB), steps("https://unlisted.example/book/1"))
        assertEquals(listOf(SiteEngine.WEBTOEPUB), steps(novelfull, EngineSettings(disabled = listOf("native", "novel_downloader"))))
    }

    @Test
    fun nothingIsResolvedWhenNoEngineCanTryTheUrl() {
        assertNull(SourceCatalog.resolve("ftp://site.test/book"))
        assertNull(SourceCatalog.resolve("https://unlisted.example/book", EngineSettings(disabled = listOf("novel_downloader", "webtoepub"))))
        assertNotNull(SourceCatalog.resolve(both, EngineSettings(disabled = listOf("novel_downloader", "webtoepub"))))
    }

    @Test
    fun siteListsDecideWhatTheAppCallsSupported() {
        assertEquals(SourceId.READER, SourceCatalog.adapterFor("https://novelfull.com/the-legendary-mechanic.html")?.id)
        assertEquals(SourceId.READER, SourceCatalog.adapterFor("https://www.novelfull.com/x.html")?.id)
        assertNull(SourceCatalog.adapterFor("https://unlisted.example/book"))
        assertNull(SourceCatalog.adapterFor("ftp://novelfull.com/x.html"))
    }

    @Test
    fun theReaderAdapterNeedsAScriptEngine() = runTest {
        val noScripts = AppSettings(engines = EngineSettings(disabled = listOf("novel_downloader", "webtoepub")))
        val withNone = SourceContext(NoHttp, CountingWeb(), InMemorySecretStore(), noScripts)
        // Two engines off is allowed while the built-in scrapers stay on, but the reader has nothing to run.
        try {
            ReaderAdapter().parseBook("https://novelfull.com/x.html", withNone)
            fail("should throw")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message, e.message!!.contains("switched off"))
        }
    }
}
