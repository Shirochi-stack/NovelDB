package com.shirochi.npia.sources.qdn

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SharedOverrides
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.hard.FakeCookies
import com.shirochi.npia.sources.hard.ScriptedPage
import com.shirochi.npia.sources.hard.ScriptedWeb
import com.shirochi.npia.sources.hard.context
import com.shirochi.npia.sources.hard.failure
import com.shirochi.npia.sources.hard.timeout
import com.shirochi.npia.sources.http.CollectingLogger
import com.shirochi.npia.sources.session.SiteSessions
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

private const val ORIGIN = QdnSite.ORIGIN

private val FAST = QdnTiming(
    minGapMillis = 0, firewallCooldownSeconds = listOf(1, 2), loadTimeoutSeconds = 1, bookPolls = 3, chapterPolls = 3,
    pollMillis = 10, decryptPolls = 4, decryptPollMillis = 10, extractPolls = 3, extractPollMillis = 10, decodePolls = 3,
    decodePollMillis = 10, referenceSlices = 3, referenceSliceMillis = 10, maxPages = 2,
)

private fun chapterJson(n: Int, vip: Boolean) =
    """{"url":"$ORIGIN/chapter/77/${1000 + n}/","name":"Chapter $n","isVip":$vip}"""

private fun bookJson(chapters: List<String>, total: Int = 0, catalogUrl: String = "") =
    """{"title":"A Book","author":"An Author","coverUrl":"https://img.example/c.jpg","description":"About it","tags":["x","y"],
        "chapters":[${chapters.joinToString(",")}],"bookUrl":"$ORIGIN/book/77/","totalChapters":$total,"catalogUrl":"$catalogUrl"}"""

private const val READY = """{"state":"ready","url":"$ORIGIN/book/77/","title":"A Book","text":"","catalogCount":3}"""

/** Responds to the qdn entry points; each test overrides what it cares about. */
private class Responses {
    var pageState = READY
    var book = bookJson(listOf(chapterJson(1, false), chapterJson(2, false), chapterJson(3, true)))
    var userInfo = """{"live":null}"""
    var catalogState = """{"state":"ready","count":0}"""
    var catalog = """{"chapters":[]}"""
    var chapterState: (String) -> String = { """{"ready":true,"blocked":false,"verification":false,"encrypted":false}""" }
    var encryptionState: () -> String = { """{"encrypted":false,"rendered":false,"failed":false,"blocked":false,"words":0}""" }
    var read: (String) -> String = { """{"lines":["First paragraph.","Second paragraph."]}""" }
    var extract: () -> String? = { """{"paragraphs":[]}""" }
    var decoderStatus: (String) -> String? = { """{"done":true,"built":9800,"total":9800}""" }
    var decode: (String) -> String = { """{"lines":["decoded one","decoded two"],"stats":{"decoded":10,"verifiedGlyphs":4,"shapeMatchedGlyphs":6}}""" }
    var knownFonts = "[]"

    fun page(index: Int = 0): ScriptedPage = ScriptedPage("page$index") { call ->
        when {
            call.startsWith("qdn.pageState") -> pageState
            call.startsWith("qdn.bookInfo") -> book
            call.startsWith("qdn.userInfo") -> userInfo
            call.startsWith("qdn.catalogState") -> catalogState
            call.startsWith("qdn.catalogInfo") -> catalog
            call.startsWith("qdn.chapterState") -> chapterState(url.orEmpty())
            call.startsWith("qdn.encryptionState") -> encryptionState()
            call.startsWith("qdn.readChapter") -> read(url.orEmpty())
            call.startsWith("qdn.extract") -> extract()
            call.startsWith("qdn.decoder.putResource") -> """{"complete":true}"""
            call.startsWith("qdn.decoder.prepare") -> decoderStatus(call)
            call.startsWith("qdn.decoder.knownFonts") -> knownFonts
            call.startsWith("qdn.decoder.decodeJson") -> decode(call)
            else -> null
        }
    }
}

/** Paid chapters skipped: the book is read without asking for a sign-in. */
private val skipPaid = AppSettings(shared = AppSettings().shared.copy(skipPaid = true))

private fun adapter(cookies: FakeCookies? = FakeCookies(), timing: QdnTiming = FAST) = QdnAdapter(timing) { cookies }

private fun web(responses: Responses) = ScriptedWeb { index -> responses.page(index) }

private fun book(vararg chapters: ChapterRef) = BookInfo(SourceId.QDN, "77", "$ORIGIN/book/77/", "A Book", chapters = chapters.toList())

private val chapter1 = ChapterRef("1001", 1, "Chapter 1", "$ORIGIN/chapter/77/1001/")

class QdnAdapterTest {
    @Before
    fun freshProcess() = SiteSessions.resetProcessState()

    // URLs

    @Test
    fun recognisesBookAndChapterUrlsOfTheSiteAndItsSubdomains() {
        val a = QdnAdapter()
        assertTrue(a.matches("$ORIGIN/book/1049836819/"))
        assertTrue(a.matches("https://m.${QdnSite.DOMAIN}/book/1"))
        assertTrue(a.matches("$ORIGIN/chapter/1049836819/12345/"))
        assertTrue(a.matches("https://${QdnSite.DOMAIN}/book/5?x=1"))
        assertFalse(a.matches("$ORIGIN/rank/"))
        assertFalse(a.matches("https://example.com/book/12"))
        assertFalse(a.matches("https://not${QdnSite.DOMAIN}/book/12"))
    }

    @Test
    fun theMobileHostIsReadLikeTheDesktopOne() {
        val a = QdnAdapter()
        val mobile = "https://m.${QdnSite.DOMAIN}"
        val desktop = "https://www.${QdnSite.DOMAIN}"
        // Book pages and the chapter pages under them match on both hosts, with or without tracking parameters.
        for (host in listOf(mobile, desktop)) {
            assertTrue(host, a.matches("$host/book/1010868264/"))
            assertTrue(host, a.matches("$host/book/1010868264"))
            assertTrue(host, a.matches("$host/book/1010868264/?source=m_jump"))
            assertTrue(host, a.matches("$host/chapter/1010868264/402733549/"))
        }
        // Everything else on the site does not: home, rankings, search, listings, a book path without an id.
        for (host in listOf(mobile, desktop, "https://${QdnSite.DOMAIN}")) {
            for (path in listOf("", "/", "/rank/yuepiao/", "/search?kw=abc", "/all/", "/finish/", "/book/", "/book/abc/", "/author/1/", "/bookstore/12/")) {
                assertFalse("$host$path", a.matches("$host$path"))
            }
        }
    }

    @Test
    fun everyBookOrChapterLinkIsKeptUnderTheDesktopBookAddress() {
        val a = QdnAdapter()
        val book = "https://www.${QdnSite.DOMAIN}/book/1010868264/"
        for (link in listOf(
            "https://m.${QdnSite.DOMAIN}/book/1010868264/",
            "https://m.${QdnSite.DOMAIN}/book/1010868264/?source=m_jump#intro",
            "https://m.${QdnSite.DOMAIN}/chapter/1010868264/402733549/",
            "https://www.${QdnSite.DOMAIN}/book/1010868264",
            "https://www.${QdnSite.DOMAIN}/chapter/1010868264/402733549/",
            "https://${QdnSite.DOMAIN}/book/1010868264/catalog/",
        )) {
            assertEquals(link, book, a.canonicalUrl(link))
        }
        // Links the adapter does not own are left alone.
        assertEquals("https://m.${QdnSite.DOMAIN}/rank/", a.canonicalUrl("https://m.${QdnSite.DOMAIN}/rank/"))
        assertEquals("https://example.com/book/1", a.canonicalUrl("https://example.com/book/1"))
    }

    @Test
    fun aMobileBookLinkIsParsedFromTheDesktopBookPage() = runTest {
        val web = web(Responses())
        val book = adapter().parseBook("https://m.${QdnSite.DOMAIN}/book/77/?source=m_jump", context(web, settings = skipPaid))
        assertEquals("$ORIGIN/book/77/", book.url)
        assertEquals(listOf("$ORIGIN/book/77/"), web.pages.single().loads)
    }

    @Test
    fun aLoginMadeOnTheMobileHostCountsAndIsSavedLikeAnyOther() = runTest {
        // The phone site's login cookies are on the parent domain, so the store answers for either host.
        val cookies = FakeCookies(mapOf("ywkey" to "m1", "ywguid" to "m2"))
        val secrets = InMemorySecretStore()
        val book = adapter(cookies).parseBook("https://m.${QdnSite.DOMAIN}/book/77/", context(web(Responses()), secrets))
        assertEquals(3, book.chapters.size)
        assertTrue(secrets.get(SecretKey.source(SourceId.QDN, "sessionCookies"))!!.contains("m1"))
        assertTrue(QdnSite.session.cookieUrls.any { it.contains("m.${QdnSite.DOMAIN}") })
    }

    @Test
    fun readsTheBookAndChapterIdsOutOfTheUrl() {
        assertEquals("1049836819", QdnSite.bookId("$ORIGIN/chapter/1049836819/12345/"))
        assertEquals("12345", QdnSite.chapterId("$ORIGIN/chapter/1049836819/12345/"))
        assertNull(QdnSite.chapterId("$ORIGIN/book/1049836819/"))
        assertNull(QdnSite.bookId("https://example.com/book/1"))
    }

    // Book

    @Test
    fun readsTheBookFromTheDesktopReaderPage() = runTest {
        val responses = Responses()
        val web = web(responses)
        val log = CollectingLogger()
        val book = adapter().parseBook("$ORIGIN/chapter/77/1001/", context(web, logger = log, settings = skipPaid))

        assertEquals("A Book", book.title)
        assertEquals("An Author", book.author)
        assertEquals("zh", book.language)
        assertEquals("$ORIGIN/book/77/", book.url)
        assertEquals(listOf("x", "y"), book.tags)
        assertEquals(listOf("1001", "1002", "1003"), book.chapters.map { it.id })
        assertEquals(listOf(1, 2, 3), book.chapters.map { it.index })
        assertEquals(listOf(false, false, true), book.chapters.map { it.isPaid })
        val page = web.pages.single()
        // A chapter URL is turned into the book page, read as a desktop browser with the shared script.
        assertEquals(listOf("$ORIGIN/book/77/"), page.loads)
        assertEquals(QdnSite.USER_AGENT, page.userAgent)
        assertEquals(1280 to 800, page.viewport)
        assertEquals(1, page.initScripts.size)
        assertTrue(page.initScripts.single().contains("__npiaHard"))
        assertTrue(page.closed)
        assertTrue(log.lines.any { it.contains("[qdn] Book: A Book") })
    }

    @Test
    fun theBookPageHasToRenderEventually() = runTest {
        val responses = Responses().apply { pageState = """{"state":"waiting","url":"$ORIGIN/book/77/","title":"Loading","text":"nothing yet"}""" }
        try {
            adapter().parseBook("$ORIGIN/book/77/", context(web(responses)))
            fail("expected a parse error")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message!!.contains("did not render"))
        }
    }

    @Test
    fun aCaptchaAsksForTheSignInBrowserOnceThenGivesUp() = runTest {
        val responses = Responses().apply { pageState = """{"state":"verification","url":"$ORIGIN/book/77/"}""" }
        val adapter = adapter()
        val web = web(responses)
        try {
            adapter.parseBook("$ORIGIN/book/77/", context(web))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            assertEquals(SourceId.QDN, e.source)
        }
        // After the user was shown the page, the same book does not ask again.
        try {
            adapter.parseBook("$ORIGIN/book/77/", context(web))
            fail("expected Blocked")
        } catch (e: SourceException.Blocked) {
            assertTrue(e.message!!.contains("verification"))
        }
        // A later download asks afresh.
        try {
            adapter.parseBook("$ORIGIN/book/77/", context(web))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // fine
        }
        assertTrue(web.pages.all { it.closed })
    }

    @Test
    fun aFirewallBlockIsNotSomethingTheUserCanFixInTheBrowser() = runTest {
        val responses = Responses().apply { pageState = """{"state":"blocked","url":"$ORIGIN/book/77/"}""" }
        try {
            adapter().parseBook("$ORIGIN/book/77/", context(web(responses)))
            fail("expected Blocked")
        } catch (e: SourceException.Blocked) {
            assertTrue(e.message!!.contains("firewall"))
        }
    }

    @Test
    fun aSlowPageLoadIsOnlyAWarning() = runTest {
        val responses = Responses()
        val web = ScriptedWeb { index -> responses.page(index).also { it.loadFailures["$ORIGIN/book/77/"] = timeout("book") } }
        val log = CollectingLogger()
        val book = adapter().parseBook("$ORIGIN/book/77/", context(web, logger = log, settings = skipPaid))
        assertEquals(3, book.chapters.size)
        assertTrue(log.lines.any { it.contains("Page load warning") })
    }

    @Test
    fun aLongBookIsReadFromItsFullCatalogPage() = runTest {
        val all = (1..6).map { """{"url":"$ORIGIN/chapter/77/${2000 + it}/","name":"Full $it","isVip":${it > 4}}""" }
        val responses = Responses().apply {
            book = bookJson(listOf(chapterJson(1, false), chapterJson(2, false)), total = 6, catalogUrl = "$ORIGIN/book/77/catalog/")
            catalog = """{"chapters":[${all.joinToString(",")}]}"""
        }
        val web = web(responses)
        val log = CollectingLogger()
        val book = adapter().parseBook("$ORIGIN/book/77/", context(web, logger = log, settings = skipPaid))
        assertEquals(6, book.chapters.size)
        assertEquals("Full 1", book.chapters[0].title)
        assertEquals(listOf(false, false, false, false, true, true), book.chapters.map { it.isPaid })
        assertEquals(listOf("$ORIGIN/book/77/", "$ORIGIN/book/77/catalog/"), web.pages.single().loads)
        assertTrue(log.lines.any { it.contains("full catalog: 6 chapters") })
    }

    @Test
    fun aCatalogPageThatCannotBeReadKeepsTheBookPageList() = runTest {
        val responses = Responses().apply {
            book = bookJson(listOf(chapterJson(1, false), chapterJson(2, false)), total = 6, catalogUrl = "$ORIGIN/book/77/catalog/")
            catalogState = """{"state":"waiting","count":0}"""
        }
        val log = CollectingLogger()
        val book = adapter().parseBook("$ORIGIN/book/77/", context(web(responses), logger = log))
        assertEquals(2, book.chapters.size)
        assertTrue(log.lines.any { it.contains("6 chapters but only 2") })
    }

    @Test
    fun aCaptchaOnTheCatalogPageAsksForTheBrowserToo() = runTest {
        val responses = Responses().apply {
            book = bookJson(listOf(chapterJson(1, false)), total = 6, catalogUrl = "$ORIGIN/book/77/catalog/")
            catalogState = """{"state":"verification","count":0}"""
        }
        try {
            adapter().parseBook("$ORIGIN/book/77/", context(web(responses)))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // fine
        }
    }

    @Test
    fun aBookWithoutChaptersOrTitleIsAParseError() = runTest {
        val empty = Responses().apply { book = bookJson(emptyList()) }
        try {
            adapter().parseBook("$ORIGIN/book/77/", context(web(empty)))
            fail("expected a parse error")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message!!.contains("no chapters"))
        }
        val untitled = Responses().apply { book = bookJson(listOf(chapterJson(1, false))).replace("\"A Book\"", "\"\"") }
        try {
            adapter().parseBook("$ORIGIN/book/77/", context(web(untitled)))
            fail("expected a parse error")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message!!.contains("title"))
        }
    }

    @Test
    fun notAQdnUrlIsRejected() = runTest {
        try {
            adapter().parseBook("https://example.com/book/1", context(web(Responses())))
            fail("expected UnsupportedUrl")
        } catch (e: SourceException.UnsupportedUrl) {
            // fine
        }
    }

    // Sign-in prompt for bought chapters

    @Test
    fun boughtChaptersWithoutASessionOpenTheSignInBrowserOnceThenGoOn() = runTest {
        val adapter = adapter()
        val web = web(Responses())
        try {
            adapter.parseBook("$ORIGIN/book/77/", context(web))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            assertEquals(SourceId.QDN, e.source)
        }
        // The pipeline starts over after the sign-in browser closes: now the book is read, signed in or not.
        val book = adapter.parseBook("$ORIGIN/book/77/", context(web))
        assertEquals(3, book.chapters.size)
        // A new download asks again.
        try {
            adapter.parseBook("$ORIGIN/book/77/", context(web))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // fine
        }
    }

    @Test
    fun noPromptWhenTheRangeHasOnlyFreeChapters() = runTest {
        val settings = AppSettings(shared = AppSettings().shared.copy(rangeFrom = 1, rangeTo = 2))
        val book = adapter().parseBook("$ORIGIN/book/77/", context(web(Responses()), settings = settings))
        assertEquals(3, book.chapters.size)
    }

    @Test
    fun noPromptWhenPaidChaptersAreSkipped() = runTest {
        val settings = AppSettings(shared = AppSettings().shared.copy(skipPaid = true))
        val book = adapter().parseBook("$ORIGIN/book/77/", context(web(Responses()), settings = settings))
        assertEquals(3, book.chapters.size)
    }

    @Test
    fun noPromptWhenTheBrowserIsSignedInAndTheLoginIsSaved() = runTest {
        val cookies = FakeCookies(mapOf("ywkey" to "k1", "ywguid" to "g1"))
        val secrets = InMemorySecretStore()
        val book = adapter(cookies).parseBook("$ORIGIN/book/77/", context(web(Responses()), secrets))
        assertEquals(3, book.chapters.size)
        val saved = secrets.get(SecretKey.source(SourceId.QDN, "sessionCookies"))
        assertNotNull(saved)
        assertTrue(saved!!.contains("k1") && saved.contains("g1"))
    }

    @Test
    fun halfALoginIsNotASession() = runTest {
        val cookies = FakeCookies(mapOf("ywguid" to "g1"))
        try {
            adapter(cookies).parseBook("$ORIGIN/book/77/", context(web(Responses())))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // fine
        }
    }

    @Test
    fun anExpiredRememberedLoginIsRemovedSoTheSiteCanRenewIt() = runTest {
        val cookies = FakeCookies(mapOf("ywkey" to "old", "ywguid" to "old"))
        val responses = Responses().apply { userInfo = """{"live":false}""" }
        val log = CollectingLogger()
        try {
            adapter(cookies).parseBook("$ORIGIN/book/77/", context(web(responses), logger = log))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // the session is gone, and a VIP chapter is in range
        }
        assertEquals(setOf("ywkey", "ywguid", "ywopenid"), cookies.removed.toSet())
        assertTrue(cookies.values.isEmpty())
        assertTrue(log.lines.any { it.contains("has expired") })
    }

    @Test
    fun aSavedLoginComesBackAfterTheCookieStoreLostIt() = runTest {
        val secrets = InMemorySecretStore()
        // First run: signed in, so the cookies are saved.
        adapter(FakeCookies(mapOf("ywkey" to "k1", "ywguid" to "g1"))).parseBook("$ORIGIN/book/77/", context(web(Responses()), secrets))
        // The app restarted and the web view dropped its session cookies.
        SiteSessions.resetProcessState()
        val cookies = FakeCookies()
        val book = adapter(cookies).parseBook("$ORIGIN/book/77/", context(web(Responses()), secrets))
        assertEquals(3, book.chapters.size)
        assertEquals(mapOf("ywkey" to "k1", "ywguid" to "g1"), cookies.values)
    }

    // Chapters

    @Test
    fun readsAFreeChapterAsParagraphs() = runTest {
        val web = web(Responses())
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, source = SourceId.QDN))
        val content = (result as ChapterResult.Content).content
        assertEquals(chapter1, content.ref)
        assertTrue(content.html.contains("<p>First paragraph.</p>"))
        assertTrue(content.html.contains("class=\"qdn-content\""))
        assertEquals(listOf("$ORIGIN/chapter/77/1001/"), web.pages.first().loads)
        assertTrue(web.pages.all { it.closed })
    }

    @Test
    fun anUnboughtChapterIsRefusedWithTheReason() = runTest {
        for ((reason, expected) in listOf("login" to "not signed in", "purchase" to "not bought")) {
            val responses = Responses().apply { read = { """{"error":"locked","reason":"$reason","chars":137,"expected":3094}""" } }
            val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), source = SourceId.QDN))
            val refused = result as ChapterResult.Refused
            assertTrue(refused.reason, refused.reason.contains(expected))
            assertTrue(refused.reason, refused.reason.contains("137/3094"))
        }
    }

    @Test
    fun aVerificationPageIsAFailureTheUserCanRetry() = runTest {
        val responses = Responses().apply { read = { """{"error":"verification"}""" } }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), source = SourceId.QDN))
        assertTrue((result as ChapterResult.Failed).message.contains("verification"))
    }

    @Test
    fun anEmptyChapterAndUnknownErrorsFail() = runTest {
        val empty = Responses().apply { read = { """{"lines":[]}""" } }
        assertTrue(adapter().parseChapter(chapter1, book(chapter1), context(web(empty), source = SourceId.QDN)) is ChapterResult.Failed)
        val odd = Responses().apply { read = { """{"error":"missing content"}""" } }
        val failed = adapter().parseChapter(chapter1, book(chapter1), context(web(odd), source = SourceId.QDN)) as ChapterResult.Failed
        assertEquals("missing content", failed.message)
    }

    @Test
    fun theFirewallIsWaitedOutBeforeAChapterIsRetried() = runTest {
        var loads = 0
        val responses = Responses().apply {
            chapterState = {
                loads += 1
                if (loads <= 2) """{"ready":false,"blocked":true,"verification":false,"encrypted":false}"""
                else """{"ready":true,"blocked":false,"verification":false,"encrypted":false}"""
            }
        }
        val web = web(responses)
        val log = CollectingLogger()
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, logger = log, source = SourceId.QDN))
        assertTrue(result is ChapterResult.Content)
        assertEquals(3, web.pages.first().loads.size)
        assertEquals(2, log.lines.count { it.contains("firewall is blocking") })
    }

    @Test
    fun aFirewallThatNeverLiftsFailsTheChapterAfterTheCooldowns() = runTest {
        val responses = Responses().apply { chapterState = { """{"ready":false,"blocked":true,"verification":false,"encrypted":false}""" } }
        val web = web(responses)
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, source = SourceId.QDN))
        assertTrue((result as ChapterResult.Failed).message.contains("firewall"))
        // The first try plus one per cooldown.
        assertEquals(3, web.pages.first().loads.size)
    }

    @Test
    fun aChapterThatNeverGetsReadyTimesOut() = runTest {
        val responses = Responses().apply { chapterState = { """{"ready":false,"blocked":false,"verification":false,"encrypted":false}""" } }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), source = SourceId.QDN))
        assertTrue((result as ChapterResult.Failed).message.contains("timed out"))
    }

    // Bought chapters

    private fun encrypted(words: Int = 0, rendered: () -> Boolean = { true }, failed: Boolean = false, blocked: Boolean = false) = Responses().apply {
        chapterState = { """{"ready":true,"blocked":false,"verification":false,"encrypted":true}""" }
        encryptionState = {
            """{"encrypted":true,"rendered":${rendered()},"failed":$failed,"blocked":$blocked,"words":$words}"""
        }
    }

    @Test
    fun aBoughtChapterIsExtractedOnItsPageAndDecodedOnTheDecoderPage() = runTest {
        var polls = 0
        val responses = encrypted(words = 20, rendered = { ++polls > 2 }).apply { knownFonts = """["abc"]""" }
        val web = web(responses)
        val log = CollectingLogger()
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, logger = log, source = SourceId.QDN))
        val content = (result as ChapterResult.Content).content
        assertTrue(content.html.contains("<p>decoded one</p>"))
        val (chapterPage, decoderPage) = web.pages
        assertTrue(chapterPage.calls.any { it.startsWith("qdn.extract") && it.contains("\"known\":[\"abc\"]") })
        assertEquals(listOf("about:blank"), decoderPage.loads)
        // The resources (the font in chunks), then the reference, then the decode.
        assertTrue(decoderPage.calls.count { it.startsWith("qdn.decoder.putResource") } >= 3)
        assertTrue(decoderPage.calls.any { it.startsWith("qdn.decoder.prepare") })
        assertTrue(decoderPage.calls.any { it.startsWith("qdn.decoder.decodeJson") })
        assertTrue(web.pages.all { it.closed })
        assertTrue(log.lines.any { it.contains("Decoded font-encrypted chapter: 2 paragraphs, 10 glyphs") })
    }

    @Test
    fun theDecoderIsBuiltOnceForAWholeDownload() = runTest {
        val responses = encrypted()
        val web = web(responses)
        val refs = (1..4).map { ChapterRef("${1000 + it}", it, "Chapter $it", "$ORIGIN/chapter/77/${1000 + it}/") }
        val results = adapter().parseChapters(refs, book(*refs.toTypedArray()), context(web, source = SourceId.QDN))
        assertEquals(4, results.count { it is ChapterResult.Content })
        val decoderPages = web.pages.filter { it.loads == listOf("about:blank") }
        assertEquals(1, decoderPages.size)
        assertEquals(1, decoderPages.single().calls.count { it.startsWith("qdn.decoder.prepare") })
        assertEquals(4, decoderPages.single().calls.count { it.startsWith("qdn.decoder.decodeJson") })
    }

    @Test
    fun aReaderThatCannotDecryptFailsTheChapter() = runTest {
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(encrypted(failed = true)), source = SourceId.QDN))
        assertTrue((result as ChapterResult.Failed).message.contains("could not decrypt"))
    }

    @Test
    fun aFirewallDuringTheDecryptionFailsTheChapter() = runTest {
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(encrypted(blocked = true)), source = SourceId.QDN))
        assertTrue((result as ChapterResult.Failed).message.contains("firewall"))
    }

    @Test
    fun aDecryptionThatNeverFinishesTimesOut() = runTest {
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(encrypted(rendered = { false })), source = SourceId.QDN))
        assertTrue((result as ChapterResult.Failed).message.contains("timed out decrypting"))
    }

    @Test
    fun anUnstableCaptureIsReadAgainAndTheBetterOneKept() = runTest {
        var decodes = 0
        val responses = encrypted().apply {
            decode = {
                decodes += 1
                if (decodes == 1) {
                    """{"lines":["bad"],"stats":{"decoded":1,"unstableParagraphs":[0,1]}}"""
                } else {
                    """{"lines":["good one","good two"],"stats":{"decoded":2}}"""
                }
            }
        }
        val log = CollectingLogger()
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), logger = log, source = SourceId.QDN))
        val content = (result as ChapterResult.Content).content
        assertEquals(2, decodes)
        assertTrue(content.html.contains("good one"))
        assertFalse(content.html.contains("bad"))
        assertTrue(log.lines.any { it.contains("retrying once") })
    }

    @Test
    fun aWorseSecondCaptureIsNotKept() = runTest {
        var decodes = 0
        val responses = encrypted().apply {
            decode = {
                decodes += 1
                if (decodes == 1) {
                    """{"lines":["first","capture"],"stats":{"decoded":2,"missingFonts":[{"family":"f"}]}}"""
                } else {
                    """{"lines":["x"],"stats":{"decoded":1,"unstableParagraphs":[0],"missingFonts":[{"family":"f"}]}}"""
                }
            }
        }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), source = SourceId.QDN))
        assertTrue((result as ChapterResult.Content).content.html.contains("first"))
    }

    @Test
    fun warningsAreLoggedButTheBestTextIsStillSaved() = runTest {
        val responses = encrypted(words = 5000).apply {
            decode = { """{"lines":["short"],"stats":{"decoded":3,"lowConfidence":2,"suspiciousPassthrough":1}}""" }
        }
        val log = CollectingLogger()
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), logger = log, source = SourceId.QDN))
        assertTrue(result is ChapterResult.Content)
        val warning = log.lines.single { it.contains("Saving best available text") }
        assertTrue(warning.contains("uncertain_glyphs=2") && warning.contains("suspicious_passthrough=1") && warning.contains("incomplete=1"))
    }

    @Test
    fun aDecoderThatFailsFailsTheChapterNotTheDownload() = runTest {
        val responses = encrypted().apply { decoderStatus = { throw com.shirochi.npia.core.WebEngineException("script error") } }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), source = SourceId.QDN))
        assertTrue((result as ChapterResult.Failed).message.contains("glyph decoder"))
    }

    // A batch

    @Test
    fun aBatchRunsEveryChapterAndReportsEachOne() = runTest {
        val seen = mutableListOf<ChapterResult>()
        val responses = Responses().apply {
            read = { if (it.contains("1002")) """{"error":"locked","reason":"purchase","chars":10,"expected":900}""" else """{"lines":["Text $it"]}""" }
        }
        val web = web(responses)
        val refs = (1..3).map { ChapterRef("${1000 + it}", it, "Chapter $it", "$ORIGIN/chapter/77/${1000 + it}/") }
        val results = adapter().parseChapters(refs, book(*refs.toTypedArray()), context(web, source = SourceId.QDN, onChapter = { seen += it }))
        assertEquals(listOf(true, false, true), results.map { it is ChapterResult.Content })
        assertTrue(results[1] is ChapterResult.Refused)
        assertEquals(3, seen.size)
        // At most `maxPages` reader pages, all closed at the end.
        assertTrue(web.pages.size <= FAST.maxPages)
        assertTrue(web.pages.all { it.closed })
    }

    @Test
    fun aPageThatFailsToLoadFailsOnlyItsChapter() = runTest {
        val responses = Responses()
        val web = ScriptedWeb { index -> responses.page(index).also { it.loadFailures["$ORIGIN/chapter/77/1002/"] = failure("net::ERR_FAILED") } }
        val refs = (1..2).map { ChapterRef("${1000 + it}", it, "Chapter $it", "$ORIGIN/chapter/77/${1000 + it}/") }
        val results = adapter().parseChapters(refs, book(*refs.toTypedArray()), context(web, source = SourceId.QDN))
        assertTrue(results[0] is ChapterResult.Content)
        assertTrue((results[1] as ChapterResult.Failed).message.contains("ERR_FAILED"))
    }

    @Test
    fun theLoginIsSavedAgainWhenABatchEnds() = runTest {
        val cookies = FakeCookies(mapOf("ywkey" to "k2", "ywguid" to "g2"))
        val secrets = InMemorySecretStore()
        adapter(cookies).parseChapters(listOf(chapter1), book(chapter1), context(web(Responses()), secrets, source = SourceId.QDN))
        assertTrue(secrets.get(SecretKey.source(SourceId.QDN, "sessionCookies"))!!.contains("k2"))
    }

    @Test
    fun pacingKeepsLoadsApart() = runTest {
        var now = 0L
        val pacer = QdnPacer(2_500) { now }
        pacer.pace()
        val before = testScheduler.currentTime
        now = 1_000
        pacer.pace()
        // The second load waited for the rest of the gap.
        assertEquals(1_500, testScheduler.currentTime - before)
    }

    @Test
    fun chapterWorkersAreCappedWhateverThreadsSays() = runTest {
        val settings = AppSettings(shared = AppSettings().shared.copy(threads = 16))
        val web = web(Responses())
        val refs = (1..6).map { ChapterRef("${1000 + it}", it, "Chapter $it", "$ORIGIN/chapter/77/${1000 + it}/") }
        adapter().parseChapters(refs, book(*refs.toTypedArray()), context(web, settings = settings, source = SourceId.QDN))
        assertTrue(web.pages.size <= FAST.maxPages)
    }

    @Test
    fun overridesForQdnApplyToItsRun() = runTest {
        val settings = AppSettings(overrides = mapOf("qdn" to SharedOverrides(threads = 1)))
        val web = web(Responses())
        val refs = (1..3).map { ChapterRef("${1000 + it}", it, "Chapter $it", "$ORIGIN/chapter/77/${1000 + it}/") }
        adapter().parseChapters(refs, book(*refs.toTypedArray()), context(web, settings = settings))
        assertEquals(1, web.pages.size)
    }
}
