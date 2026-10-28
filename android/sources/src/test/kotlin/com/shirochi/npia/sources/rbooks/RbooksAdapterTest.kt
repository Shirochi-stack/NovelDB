package com.shirochi.npia.sources.rbooks

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.ImageSettings
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.hard.FakeCookies
import com.shirochi.npia.sources.hard.RecordingHttp
import com.shirochi.npia.sources.hard.ScriptedPage
import com.shirochi.npia.sources.hard.ScriptedWeb
import com.shirochi.npia.sources.hard.context
import com.shirochi.npia.sources.hard.timeout
import com.shirochi.npia.sources.http.CollectingLogger
import com.shirochi.npia.sources.session.SiteSessions
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test

private const val ORIGIN = RbooksSite.ORIGIN
private const val API = RbooksSite.API_ORIGIN

private val FAST = RbooksTiming(
    loadTimeoutSeconds = 1, productPolls = 3, libraryPolls = 3, apiPolls = 3, discoverBudgetMillis = 100, discoverCalls = 6,
    apiRecoveries = 2, contentPolls = 8, stablePolls = 2, contentAttempts = 3, pollMillis = 10, maxPages = 2,
)

private fun productJson(links: List<String> = emptyList(), seriesId: String = "100", title: String = "A Series") =
    """{"title":"$title","author":"An Author","synopsis":"About it","cover":"https://img.example/c.jpg","publisher":"A Press","tags":["fantasy"],
        "links":[${links.joinToString(",") { "\"$it\"" }}],"titleById":{"100":"Opening"},"seriesId":"$seriesId","diagnostics":["Rendered episode links: ${links.size}"]}"""

private fun view(id: String) = "$ORIGIN/books/$id/view"

private fun discovery(ids: List<String>, done: Boolean = true, stalled: Boolean = false, error: String = "", state: String = """{"phase":"chain","ids":[${ids.joinToString(",") { "\"$it\"" }}],"total":${ids.size},"nextId":"x"}""") =
    """{"done":$done,"stalled":$stalled,"state":$state,"isSerial":true,"error":"$error","diagnostics":["Chained episode links: ${ids.size}"],
        "titleById":{${ids.joinToString(",") { "\"$it\":\"Episode title $it\"" }}},"links":[${ids.joinToString(",") { "\"${view(it)}\"" }}]}"""

private val VIEWER_READY = """{"complete":true,"found":true,"ready":true,"length":2400,"refused":false,"url":""}"""

private class Responses {
    var productReady = "true"
    var product = productJson()
    var libraryProbe = """{"product":"","unavailable":false,"adult":false,"title":"","viewer":false}"""
    var apiState = """{"json":true,"title":"","url":"$API/books/100","challenge":false}"""
    var discover: (String) -> String = { discovery(listOf("100", "101", "102")) }
    var contentState: (Int) -> String = { VIEWER_READY }
    var accessWall = """{"wall":false,"offViewer":false,"refused":false,"hits":0}"""
    var extract: (String) -> String = {
        """{"title":"Episode One","content":"<p>First.</p><p>Second.</p>","contentText":"First.\nSecond.","images":[]}"""
    }
    var ownedIds: () -> String = { """{"owned":["200"]}""" }
    var contentCalls = 0

    fun page(index: Int = 0) = ScriptedPage("page$index") { call ->
        when {
            call.startsWith("rbooks.productReady") -> productReady
            call.startsWith("rbooks.productInfo") -> product
            call.startsWith("rbooks.libraryProbe") -> libraryProbe
            call.startsWith("rbooks.apiState") -> apiState
            call.startsWith("rbooks.discover") -> discover(call)
            call.startsWith("rbooks.contentState") -> contentState(contentCalls++)
            call.startsWith("rbooks.accessWall") -> accessWall
            call.startsWith("rbooks.extract") -> extract(call)
            call.startsWith("rbooks.ownedIds") -> ownedIds()
            else -> null
        }
    }
}

private fun adapter(cookies: FakeCookies? = FakeCookies(), timing: RbooksTiming = FAST) = RbooksAdapter(timing) { cookies }

private fun web(responses: Responses, setup: (ScriptedPage) -> Unit = {}) = ScriptedWeb { index -> responses.page(index).also(setup) }

private fun book(vararg chapters: ChapterRef, id: String = "100", title: String = "A Series") =
    BookInfo(SourceId.RBOOKS, id, "$ORIGIN/books/$id", title, chapters = chapters.toList())

private val chapter1 = ChapterRef("101", 1, "Episode 1", view("101"))

private val signedIn = mapOf(RbooksSite.ACCESS_COOKIE to "at", RbooksSite.REFRESH_COOKIE to "rt")

class RbooksAdapterTest {
    @Before
    fun freshProcess() = SiteSessions.resetProcessState()

    // URLs

    @Test
    fun recognisesProductViewerAndLibraryUrls() {
        val a = RbooksAdapter()
        assertTrue(a.matches("$ORIGIN/books/6251000001"))
        assertTrue(a.matches("$ORIGIN/books/6251000001/"))
        assertTrue(a.matches("$ORIGIN/books/6251000001/view"))
        assertTrue(a.matches("https://www.${RbooksSite.DOMAIN}/books/12/view?from=library"))
        assertTrue(a.matches("$ORIGIN/library/books/8706175/"))
        assertFalse(a.matches("$ORIGIN/category/books/100"))
        assertFalse(a.matches("$ORIGIN/"))
        assertFalse(a.matches("$ORIGIN/books/"))
        assertFalse(a.matches("https://example.com/books/1234567890/view"))
        assertEquals("6251000001", RbooksSite.bookId("$ORIGIN/books/6251000001/view"))
        assertTrue(RbooksSite.isViewerUrl("$ORIGIN/books/1/view"))
        assertFalse(RbooksSite.isViewerUrl("$ORIGIN/books/1"))
        assertTrue(RbooksSite.isAccountGate("$ORIGIN/account/login?state=x"))
        assertTrue(RbooksSite.isAccountGate("$ORIGIN/account/verify-adult"))
        assertFalse(RbooksSite.isAccountGate("$ORIGIN/books/1"))
    }

    // Book

    @Test
    fun readsTheBookAndItsEpisodeListFromTheBookApi() = runTest {
        val responses = Responses().apply {
            product = productJson(links = listOf(view("100")))
            discover = { discovery(listOf("100", "101", "102")) }
        }
        val web = web(responses)
        val log = CollectingLogger()
        val book = adapter().parseBook("$ORIGIN/books/100", context(web, logger = log))

        assertEquals("A Series", book.title)
        assertEquals("An Author", book.author)
        assertEquals("ko", book.language)
        assertEquals("$ORIGIN/books/100", book.url)
        assertEquals(listOf("100", "101", "102"), book.chapters.map { it.id })
        assertEquals(listOf(view("100"), view("101"), view("102")), book.chapters.map { it.url })
        assertEquals(listOf(1, 2, 3), book.chapters.map { it.index })
        // Titles from the API replace the ones read off the page (as on the desktop).
        assertEquals(listOf("Episode title 100", "Episode title 101", "Episode title 102"), book.chapters.map { it.title })
        // A product page and an API page, each read as a desktop browser, both closed.
        val (product, api) = web.pages
        assertEquals(listOf("$ORIGIN/books/100"), product.loads)
        assertEquals(listOf("$API/books/100"), api.loads)
        assertEquals(RbooksSite.USER_AGENT, product.userAgent)
        assertTrue(web.pages.all { it.closed && it.initScripts.size == 1 })
        assertTrue(log.lines.any { it.contains("[rbooks] Book: A Series by An Author - 3 chapters") })
    }

    @Test
    fun theLongerOfTheRenderedListAndTheApiListIsKept() = runTest {
        val rendered = (1..5).map { view("${100 + it}") }
        val responses = Responses().apply {
            product = productJson(links = rendered)
            discover = { discovery(listOf("100", "101")) }
        }
        val book = adapter().parseBook("$ORIGIN/books/100", context(web(responses)))
        assertEquals(listOf("101", "102", "103", "104", "105"), book.chapters.map { it.id })
        // Titles from the API where it knew the episode, numbered ones elsewhere.
        assertEquals(listOf("Episode title 101", "Episode 2", "Episode 3", "Episode 4", "Episode 5"), book.chapters.map { it.title })
    }

    @Test
    fun episodesWithoutATitleAreNumbered() = runTest {
        val responses = Responses().apply {
            product = productJson(links = listOf(view("500"), view("501"), view("500"))).replace("\"100\":\"Opening\"", "")
            apiState = """{"json":true}"""
            discover = { discovery(emptyList(), done = true) }
        }
        val book = adapter().parseBook("$ORIGIN/books/500", context(web(responses)))
        assertEquals(listOf("500", "501"), book.chapters.map { it.id })
        assertEquals(listOf("Episode 1", "Episode 2"), book.chapters.map { it.title })
    }

    @Test
    fun theEpisodeApiIsWalkedInSlicesAndProgressIsLogged() = runTest {
        var calls = 0
        val responses = Responses().apply {
            discover = {
                calls += 1
                when (calls) {
                    1 -> discovery(listOf("100", "101"), done = false)
                    2 -> discovery(listOf("100", "101", "102"), done = false)
                    else -> discovery(listOf("100", "101", "102", "103"))
                }
            }
        }
        val log = CollectingLogger()
        val web = web(responses)
        val book = adapter().parseBook("$ORIGIN/books/100", context(web, logger = log))
        assertEquals(4, book.chapters.size)
        assertEquals(3, calls)
        assertEquals(2, log.lines.count { it.contains("Episode list:") })
        // The API page is opened once for the whole walk.
        assertEquals(1, web.pages.count { it.loads == listOf("$API/books/100") })
    }

    @Test
    fun aStalledApiIsReopenedWhereItStoppedThenGivenUpOnWithWhatWasFound() = runTest {
        var calls = 0
        val responses = Responses().apply {
            discover = { call ->
                calls += 1
                when {
                    call.contains("\"giveUp\":true") -> discovery(listOf("100", "101"))
                    else -> discovery(listOf("100", "101"), done = false, stalled = true, state = """{"ids":["100","101"],"total":9,"nextId":"205"}""")
                }
            }
        }
        val log = CollectingLogger()
        val web = web(responses)
        val book = adapter().parseBook("$ORIGIN/books/100", context(web, logger = log))
        assertEquals(listOf("100", "101"), book.chapters.map { it.id })
        // The API was reopened at the id it stopped at, twice, before giving up.
        val api = web.pages.filter { it.loads.firstOrNull()?.startsWith(API) == true }.single()
        assertEquals(listOf("$API/books/100", "$API/books/205", "$API/books/205"), api.loads)
        assertEquals(2, log.lines.count { it.contains("stopped answering") })
    }

    @Test
    fun aBookWithNoEpisodesIsAnError() = runTest {
        val empty = Responses().apply {
            product = productJson(seriesId = "")
        }
        try {
            adapter().parseBook("$ORIGIN/books/100", context(web(empty)))
            fail("expected a parse error")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message!!.contains("no webnovel episodes"))
        }
        val failed = Responses().apply {
            product = productJson()
            discover = { discovery(emptyList(), done = true, error = "book API did not return JSON") }
        }
        try {
            adapter().parseBook("$ORIGIN/books/100", context(web(failed)))
            fail("expected Blocked")
        } catch (e: SourceException.Blocked) {
            assertTrue(e.message!!.contains("book API did not return JSON"))
        }
    }

    @Test
    fun aSlowProductPageLoadIsOnlyAWarning() = runTest {
        val log = CollectingLogger()
        val web = web(Responses()) { it.loadFailures["$ORIGIN/books/100"] = timeout("product") }
        val book = adapter().parseBook("$ORIGIN/books/100", context(web, logger = log))
        assertEquals(3, book.chapters.size)
        assertTrue(log.lines.any { it.contains("Page load warning") })
    }

    // Sign-in

    @Test
    fun aLoginOrAdultCheckRedirectAsksForTheSignInBrowserOnceThenGivesUp() = runTest {
        val adapter = adapter()
        val web = web(Responses()) { page -> page.redirect = { if (it == "$ORIGIN/books/100") "$ORIGIN/account/login?state=x" else it } }
        try {
            adapter.parseBook("$ORIGIN/books/100", context(web))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            assertEquals(SourceId.RBOOKS, e.source)
        }
        try {
            adapter.parseBook("$ORIGIN/books/100", context(web))
            fail("expected Blocked")
        } catch (e: SourceException.Blocked) {
            assertTrue(e.message!!.contains("Settings > Enter App"))
        }
        assertTrue(web.pages.all { it.closed })
    }

    @Test
    fun aCloudflareCheckOnTheProductPageAsksForTheBrowser() = runTest {
        val responses = Responses().apply { productReady = "false" }
        val web = ScriptedWeb { index -> responses.page(index).also { it.titleText = "Just a moment..." } }
        try {
            adapter().parseBook("$ORIGIN/books/100", context(web))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // fine
        }
    }

    @Test
    fun aCloudflareCheckOnTheBookApiAsksForTheBrowser() = runTest {
        val responses = Responses().apply { apiState = """{"json":false,"title":"Just a moment...","url":"$API/books/100","challenge":true}""" }
        try {
            adapter().parseBook("$ORIGIN/books/100", context(web(responses)))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // fine
        }
    }

    @Test
    fun anApiThatNeverAnswersIsBlocked() = runTest {
        val responses = Responses().apply { apiState = """{"json":false,"title":"","url":"$API/books/100","challenge":false}""" }
        try {
            adapter().parseBook("$ORIGIN/books/100", context(web(responses)))
            fail("expected Blocked")
        } catch (e: SourceException.Blocked) {
            assertTrue(e.message!!.contains("book API"))
        }
    }

    // Library links

    @Test
    fun aLibraryLinkNeedsASignedInBrowser() = runTest {
        val adapter = adapter(FakeCookies())
        try {
            adapter.parseBook("$ORIGIN/library/books/8706175/", context(web(Responses())))
            fail("expected LoginRequired")
        } catch (e: SourceException.LoginRequired) {
            // fine
        }
    }

    @Test
    fun anOwnedBooksReaderIsOneChapter() = runTest {
        val responses = Responses().apply { libraryProbe = """{"product":"","unavailable":false,"adult":false,"title":"Owned Book","viewer":true}""" }
        val url = "$ORIGIN/library/books/8706175/"
        val book = adapter(FakeCookies(signedIn)).parseBook(url, context(web(responses)))
        assertEquals("Owned Book", book.title)
        assertEquals(1, book.chapters.size)
        assertEquals(url, book.chapters.single().url)
        assertEquals("8706175", book.id)
    }

    @Test
    fun aLibraryLinkThatPointsAtTheProductReadsTheProductBook() = runTest {
        val responses = Responses().apply {
            libraryProbe = """{"product":"$ORIGIN/books/6121000538","unavailable":true,"adult":true,"title":"x","viewer":false}"""
            product = productJson(seriesId = "6121000538")
            discover = { discovery(listOf("6121000538", "6121000539")) }
        }
        val web = web(responses)
        val log = CollectingLogger()
        val book = adapter(FakeCookies(signedIn)).parseBook("$ORIGIN/library/books/8706175/", context(web, logger = log))
        assertEquals("6121000538", book.id)
        assertEquals(2, book.chapters.size)
        assertEquals(listOf("$ORIGIN/library/books/8706175/", "$ORIGIN/books/6121000538"), web.pages.first().loads)
        assertTrue(log.lines.any { it.contains("adult (19+)") })
    }

    @Test
    fun aLibraryPageWithNothingReadableIsAnError() = runTest {
        try {
            adapter(FakeCookies(signedIn)).parseBook("$ORIGIN/library/books/8706175/", context(web(Responses())))
            fail("expected a parse error")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message!!.contains("no readable book"))
        }
    }

    @Test
    fun theLoginIsSavedAfterABookIsRead() = runTest {
        val secrets = InMemorySecretStore()
        adapter(FakeCookies(signedIn)).parseBook("$ORIGIN/books/100", context(web(Responses()), secrets))
        assertTrue(secrets.get(com.shirochi.npia.core.SecretKey.source(SourceId.RBOOKS, "sessionCookies"))!!.contains("at"))
    }

    // Chapters

    @Test
    fun readsAnEpisodeFromTheViewer() = runTest {
        val web = web(Responses())
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, source = SourceId.RBOOKS))
        val content = (result as ChapterResult.Content).content
        assertTrue(content.html.startsWith("<div class=\"rbooks-content\">"))
        assertTrue(content.html.contains("<p>First.</p>"))
        // The viewer's own title is used, the catalog's would otherwise be.
        assertEquals("Episode One", content.ref.title)
        assertEquals(listOf(view("101")), web.pages.first().loads)
        assertTrue(web.pages.all { it.closed })
    }

    @Test
    fun aSeriesTitleIsNeverUsedAsAnEpisodeTitle() = runTest {
        val responses = Responses().apply {
            extract = { """{"title":" a  series ","content":"<p>Text</p>","contentText":"Text","images":[]}""" }
        }
        val result = adapter().parseChapter(chapter1, book(chapter1, title = "A Series"), context(web(responses), source = SourceId.RBOOKS))
        assertEquals("Episode 1", (result as ChapterResult.Content).content.ref.title)
    }

    @Test
    fun theViewerHasToKeepTheSameLengthBeforeItCounts() = runTest {
        val responses = Responses().apply {
            contentState = { n ->
                val length = if (n < 3) 600 + n * 100 else 1200
                """{"complete":true,"found":true,"ready":true,"length":$length,"refused":false}"""
            }
        }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), source = SourceId.RBOOKS))
        assertTrue(result is ChapterResult.Content)
        // Growing for three polls, then the same length twice: five polls at least.
        assertTrue(responses.contentCalls >= 5)
    }

    @Test
    fun comicPagesAreDownloadedWithTheBookAsReferer() = runTest {
        val png = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 1, 2, 3)
        val http = RecordingHttp { request ->
            if (request.url.endsWith("broken.jpg")) HttpResponse(request.url, 403, emptyMap(), ByteArray(0))
            else HttpResponse(request.url, 200, emptyMap(), png)
        }
        val responses = Responses().apply {
            extract = {
                """{"title":"T","content":"<p><img src=\"images/0001_01.jpg\" /></p>","contentText":"x","images":[
                    {"url":"https://img.example/p/1.jpg","name":"images/0001_01.jpg"},
                    {"url":"https://img.example/p/broken.jpg","name":"images/0001_02.jpg"},
                    {"url":"data:image/png;base64,iVBORw0KGgo=","name":"images/0001_03.png"}]}"""
            }
        }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), http = http, source = SourceId.RBOOKS))
        val images = (result as ChapterResult.Content).content.images
        assertEquals(listOf("images/0001_01.jpg", "images/0001_02.jpg", "images/0001_03.png"), images.map { it.filename })
        assertArrayEquals(png, images[0].data)
        // A page that could not be fetched keeps its URL, so the image stage tries again.
        assertNull(images[1].data)
        assertEquals("https://img.example/p/broken.jpg", images[1].url)
        assertArrayEquals(byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A), images[2].data)
        val first = http.requests.first { it.url.endsWith("1.jpg") }
        assertEquals("$ORIGIN/books/100", first.headers["Referer"])
    }

    @Test
    fun savingImageUrlsOnlyDownloadsNothing() = runTest {
        val http = RecordingHttp()
        val responses = Responses().apply {
            extract = { """{"title":"T","content":"<p>x</p>","contentText":"x","images":[{"url":"https://img.example/p/1.jpg","name":"images/0001_01.jpg"}]}""" }
        }
        val settings = AppSettings(images = ImageSettings(saveImageUrlsOnly = true))
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), settings = settings, http = http, source = SourceId.RBOOKS))
        val image = (result as ChapterResult.Content).content.images.single()
        assertNull(image.data)
        assertTrue(http.requests.isEmpty())
    }

    @Test
    fun aRedirectToTheLoginPageMeansSignedOut() = runTest {
        val web = web(Responses()) { it.redirect = { _ -> "$ORIGIN/account/login?state=x" } }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, source = SourceId.RBOOKS))
        assertTrue((result as ChapterResult.Refused).reason.contains("Enter App"))
    }

    @Test
    fun aRedirectAwayFromTheViewerMeansNotBought() = runTest {
        val web = web(Responses()) { it.redirect = { _ -> "$ORIGIN/books/101" } }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, source = SourceId.RBOOKS))
        assertTrue((result as ChapterResult.Refused).reason.contains("not bought"))
    }

    @Test
    fun aTimedOutLoadIsFineWhenTheViewerItselfCommitted() = runTest {
        val web = web(Responses()) { it.loadFailures[view("101")] = timeout("viewer") }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, source = SourceId.RBOOKS))
        assertTrue(result is ChapterResult.Content)
        // ... but not when the page is somewhere else.
        val elsewhere = web(Responses()) {
            it.loadFailures[view("101")] = timeout("viewer")
            it.redirect = { _ -> "$ORIGIN/books/101" }
        }
        val failed = adapter().parseChapter(chapter1, book(chapter1), context(elsewhere, source = SourceId.RBOOKS))
        assertTrue((failed as ChapterResult.Failed).message.contains("timed out"))
    }

    @Test
    fun aPurchaseWallIsRefusedWithoutRetrying() = runTest {
        val responses = Responses().apply {
            contentState = { """{"complete":true,"found":false,"ready":false,"length":0,"refused":false}""" }
            accessWall = """{"wall":true,"offViewer":false,"refused":false,"hits":3}"""
        }
        val web = web(responses)
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, source = SourceId.RBOOKS))
        assertTrue((result as ChapterResult.Refused).reason.contains("not bought"))
        assertEquals(1, web.pages.first().loads.size)
    }

    @Test
    fun aViewerThatNeverDrawsIsReloadedThenFails() = runTest {
        val responses = Responses().apply {
            contentState = { """{"complete":true,"found":false,"ready":false,"length":0,"refused":false}""" }
        }
        val web = web(responses)
        val log = CollectingLogger()
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web, logger = log, source = SourceId.RBOOKS))
        assertTrue((result as ChapterResult.Failed).message.contains("did not finish"))
        // One load, then a reload before each of the other two attempts.
        assertEquals(3, web.pages.first().loads.size)
        assertEquals(3, log.lines.count { it.contains("Content validation failed") })
    }

    @Test
    fun anEmptyExtractionIsAFailureUnlessAWallExplainsIt() = runTest {
        val responses = Responses().apply { extract = { "null" } }
        val result = adapter().parseChapter(chapter1, book(chapter1), context(web(responses), source = SourceId.RBOOKS))
        assertTrue((result as ChapterResult.Failed).message.contains("no chapter content"))
    }

    // Titles the viewer refuses

    private fun refusing(owned: () -> String) = Responses().apply {
        contentState = { """{"complete":true,"found":false,"ready":false,"length":0,"refused":true}""" }
        accessWall = """{"wall":true,"offViewer":false,"refused":true,"hits":0}"""
        ownedIds = owned
    }

    @Test
    fun aRefusedTitleThatIsNotInTheLibraryHasNotBeenBought() = runTest {
        val web = web(refusing { """{"owned":["999"]}""" })
        val result = adapter(FakeCookies(signedIn)).parseChapter(chapter1, book(chapter1), context(web, source = SourceId.RBOOKS))
        assertTrue((result as ChapterResult.Refused).reason.contains("not in this account's library"))
        // The library was asked from its own page.
        assertTrue(web.pages.any { it.loads.contains("${RbooksSite.LIBRARY_ORIGIN}/") })
        assertTrue(web.pages.all { it.closed })
    }

    @Test
    fun aRefusedTitleTheAccountOwnsIsAppOnlyAndCannotBeExported() = runTest {
        val web = web(refusing { """{"owned":["101"]}""" })
        val result = adapter(FakeCookies(signedIn)).parseChapter(chapter1, book(chapter1), context(web, source = SourceId.RBOOKS))
        val reason = (result as ChapterResult.Refused).reason
        assertTrue(reason, reason.contains("owned") && reason.contains("cannot be exported on a phone"))
    }

    @Test
    fun anUnreachableLibraryIsALoginProblemWhenSignedOutAndRetryableOtherwise() = runTest {
        val signedOut = adapter(FakeCookies()).parseChapter(chapter1, book(chapter1), context(web(refusing { """{"owned":null}""" }), source = SourceId.RBOOKS))
        assertTrue((signedOut as ChapterResult.Refused).reason.contains("Enter App"))
        val unsure = adapter(FakeCookies(signedIn)).parseChapter(chapter1, book(chapter1), context(web(refusing { """{"owned":null}""" }), source = SourceId.RBOOKS))
        assertTrue((unsure as ChapterResult.Failed).message.contains("retry"))
    }

    @Test
    fun theLibraryIsAskedOnceForAWholeBatch() = runTest {
        var asked = 0
        val responses = refusing {
            asked += 1
            """{"owned":["102"]}"""
        }
        val refs = (1..3).map { ChapterRef("${100 + it}", it, "Episode $it", view("${100 + it}")) }
        val results = adapter(FakeCookies(signedIn)).parseChapters(refs, book(*refs.toTypedArray()), context(web(responses), source = SourceId.RBOOKS))
        assertEquals(3, results.count { it is ChapterResult.Refused })
        assertEquals(1, asked)
    }

    // Batches

    @Test
    fun aBatchRunsEveryEpisodeAndKeepsToTheMostPages() = runTest {
        val seen = mutableListOf<ChapterResult>()
        val web = web(Responses())
        val refs = (1..5).map { ChapterRef("${100 + it}", it, "Episode $it", view("${100 + it}")) }
        val results = adapter().parseChapters(refs, book(*refs.toTypedArray()), context(web, source = SourceId.RBOOKS, onChapter = { seen += it }))
        assertEquals(5, results.count { it is ChapterResult.Content })
        assertEquals(5, seen.size)
        assertTrue(web.pages.size <= FAST.maxPages)
        assertTrue(web.pages.all { it.closed })
    }

    @Test
    fun theLoginIsSavedAgainWhenABatchEnds() = runTest {
        val secrets = InMemorySecretStore()
        adapter(FakeCookies(signedIn)).parseChapters(listOf(chapter1), book(chapter1), context(web(Responses()), secrets, source = SourceId.RBOOKS))
        assertTrue(secrets.get(com.shirochi.npia.core.SecretKey.source(SourceId.RBOOKS, "sessionCookies"))!!.contains("rt"))
    }
}
