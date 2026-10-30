package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.sources.engines.EngineStore
import com.shirochi.npia.sources.engines.RuleEngine
import com.shirochi.npia.sources.engines.RuleEngines
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * The Kotlin side of the WebToEpub engine against a scripted page: the bridge's JavaScript needs a
 * real DOM, so these tests answer its entry points with the JSON the desktop bridge returns.
 */
class WebToEpubEngineTest {
    private val url = "https://novel.example/the-book.html"
    private val log = mutableListOf<String>()
    private val logger = JobLogging { _, message -> log += message }

    /** A page that answers the injected bundle and the bridge's entry points. */
    private class ScriptedPage(
        val book: String,
        val chapters: Map<String, String> = emptyMap(),
        /** Sources (as a substring of the injected script) that fail to become ready, with the error. */
        val broken: Map<String, String> = emptyMap(),
    ) : WebPageDriving {
        val injected = mutableListOf<String>()
        val loads = mutableListOf<String>()
        var closed = false

        override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
            loads += url
        }

        override suspend fun evaluateJson(functionBody: String): String {
            if (functionBody.contains("window.__W2E_READY")) {
                injected += functionBody
                val failure = broken.entries.firstOrNull { functionBody.contains(it.key) }
                return if (failure == null) """{"ready":true,"error":null}""" else """{"ready":false,"error":"${failure.value}"}"""
            }
            if (functionBody.contains("__W2E_parseBook")) return book
            if (functionBody.contains("__W2E_parseChapter")) {
                val target = chapters.keys.firstOrNull { functionBody.contains(it) }
                return chapters[target] ?: """{"error":"no chapter reply"}"""
            }
            error("unexpected script: ${functionBody.take(80)}")
        }

        override suspend fun addInitScript(source: String) {}
        override suspend fun currentUrl(): String = loads.lastOrNull() ?: "about:blank"
        override suspend fun html() = ""
        override suspend fun close() {
            closed = true
        }
    }

    private fun context(page: ScriptedPage): SourceContext {
        val web = object : WebEngineProviding {
            override suspend fun newPage(profile: String): WebPageDriving = page
            override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) = Unit
        }
        return SourceContext(
            FixtureHttp(emptyMap()), web, InMemorySecretStore(),
            AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0)), logger,
        )
    }

    private val bookJson = """
        {"api":1,"engine":"webtoepub","parser":"NovelFullParser","match":"site","ruleName":"WebToEpub NovelFullParser",
         "bookUrl":"https://novel.example/the-book.html","bookname":"The Book","author":"A. Writer",
         "introduction":"About the book.","introductionHTML":"","coverUrl":"https://novel.example/cover.jpg",
         "tags":["Action","Sci-fi"],"language":"en","chapterCount":3,
         "chapters":[
           {"url":"https://novel.example/the-book/chapter-1.html","name":"Chapter 1","number":1,"sectionName":null,"isVIP":false,"isPaid":false},
           {"url":"https://novel.example/the-book/chapter-2.html","name":"Chapter 2: Two","number":2,"sectionName":"Arc","isVIP":false,"isPaid":false},
           {"url":"https://novel.example/the-book/locked.html","name":"Locked","number":3,"sectionName":"Arc","isVIP":false,"isPaid":true}],
         "warnings":["The chapter list is paged."],"pageUrl":"https://novel.example/the-book.html"}
    """.trimIndent().replace("\n", "")

    private val chapterOne = """
        {"chapterName":"Chapter 1","contentHtml":"<p>Hello there.</p><p><img src=\"https://img.example/a.png\"></p><p>Second paragraph.</p>",
         "contentText":"Hello there.\nSecond paragraph.","imageCount":0,"images":[]}
    """.trimIndent().replace("\n", "")

    @After
    fun restoreStore() {
        RuleEngines.configure(null)
    }

    // The book

    @Test
    fun readsTheBookInTheBridgesShape() = runTest {
        val page = ScriptedPage(bookJson)
        val session = ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
        val book = session.book

        assertEquals(SourceId.READER, book.source)
        assertEquals("https://novel.example/the-book.html", book.url)
        assertEquals("The Book", book.title)
        assertEquals("A. Writer", book.author)
        assertEquals("About the book.", book.synopsis)
        assertEquals("https://novel.example/cover.jpg", book.coverUrl)
        assertEquals(listOf("Action", "Sci-fi"), book.tags)
        assertEquals("en", book.language)
        assertEquals(listOf("Chapter 1", "Chapter 2: Two", "Locked"), book.chapters.map { it.title })
        assertEquals(listOf(1, 2, 3), book.chapters.map { it.index })
        assertEquals("https://novel.example/the-book/chapter-2.html", book.chapters[1].url)
        // WebToEpub's isPaid marks a chapter it cannot include.
        assertEquals(listOf(false, false, true), book.chapters.map { it.isPaid })
        assertEquals(listOf(url), page.loads)
        assertTrue("the page stays open for the chapters", !page.closed)
        session.close()
    }

    @Test
    fun injectsTheShippedBundleAndLogsWhatItFound() = runTest {
        val page = ScriptedPage(bookJson)
        val context = context(page)
        ReaderBookSession.open(url, context, SiteEngine.WEBTOEPUB).close()
        assertEquals(1, page.injected.size)
        val script = page.injected[0]
        assertTrue(script.contains("window.__W2E_API === 1"))
        assertTrue("the whole bundle is evaluated", script.contains("__W2E_parseChapterBatch"))
        assertTrue(log.toString(), log.any { it.startsWith("[Reader] WebToEpub NovelFullParser (site match), parsers 2026-10-27") })
        assertTrue(log.toString(), log.contains("[Reader] WebToEpub: The chapter list is paged."))
    }

    @Test
    fun aChapterBecomesTextAndImageBlocks() = runTest {
        val page = ScriptedPage(bookJson, mapOf("https://novel.example/the-book/chapter-1.html" to chapterOne))
        val session = ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
        val result = session.chapter(session.book.chapters[0])
        if (result !is ChapterResult.Content) fail("chapter should parse: $result")
        result as ChapterResult.Content
        assertEquals(
            """
            <div class="reader-content">
            <p>Hello there.</p>
            <p class="img"><img src="images/0001_01.png" alt=""/></p>
            <p>Second paragraph.</p>
            </div>
            """.trimIndent(),
            result.content.html,
        )
        // The absolute URL goes to the pipeline's image stage, like novel-downloader's images.
        assertEquals(listOf(ImageRef("https://img.example/a.png", "images/0001_01.png")), result.content.images)
        session.close()
    }

    @Test
    fun aChapterFallsBackToItsPlainTextLines() = runTest {
        val plain = """{"chapterName":"One","contentHtml":"","contentText":"First line\n\nSecond line","imageCount":0,"images":[]}"""
        val page = ScriptedPage(bookJson, mapOf("https://novel.example/the-book/chapter-2.html" to plain))
        val session = ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
        val result = session.chapter(session.book.chapters[1]) as ChapterResult.Content
        assertEquals("<div class=\"reader-content\">\n<p>First line</p>\n<p>Second line</p>\n</div>", result.content.html)
    }

    @Test
    fun aChapterErrorFailsThatChapterAndALockedOneIsRefused() = runTest {
        val page = ScriptedPage(bookJson)
        val session = ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
        try {
            session.chapter(session.book.chapters[0])
            fail("should throw")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message, e.message!!.contains("WebToEpub: no chapter reply"))
        }
        val locked = session.chapter(session.book.chapters[2])
        assertTrue(locked.toString(), locked is ChapterResult.Refused)
    }

    // Failures

    @Test
    fun noParserMeansTheEngineHasNothingForTheSite() = runTest {
        val page = ScriptedPage("""{"error":"WebToEpub has no parser for this site","noParser":true}""")
        try {
            ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
            fail("should throw")
        } catch (e: EngineMissException) {
            assertEquals("WebToEpub: WebToEpub has no parser for this site", e.message)
        }
    }

    @Test
    fun aParserErrorIsAParseFailure() = runTest {
        val page = ScriptedPage("""{"error":"Could not find the chapter list","stack":"at x"}""")
        try {
            ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
            fail("should throw")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message, e.message!!.contains("WebToEpub: Could not find the chapter list"))
        }
    }

    @Test
    fun aBookWithoutChaptersIsAFailure() = runTest {
        val page = ScriptedPage("""{"api":1,"bookname":"Empty","chapters":[]}""")
        try {
            ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
            fail("should throw")
        } catch (e: SourceException.Parse) {
            assertTrue(e.message, e.message!!.contains("found no chapters"))
        }
        assertTrue("a page no engine kept is closed", page.closed)
    }

    @Test
    fun aBundleThatDoesNotBecomeReadyIsReportedWithItsLoadError() = runTest {
        val page = ScriptedPage(bookJson, broken = mapOf("__W2E_parseChapterBatch" to "unexpected token"))
        try {
            ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
            fail("should throw")
        } catch (e: SourceException.Parse) {
            assertEquals("Could not read the page: The WebToEpub parsers failed to load: unexpected token", e.message)
        }
    }

    // Downloaded updates

    private fun installUpdate(marker: String): File {
        val root = Files.createTempDirectory("engines").toFile()
        val folder = File(root, "webtoepub").apply { mkdirs() }
        val data = "/* $marker */ window.__W2E_parseChapterBatch = 1;".toByteArray()
        File(folder, "webtoepub-lib.js").writeBytes(data)
        File(folder, "manifest.json").writeText(
            """{"engine":"webtoepub","api":1,"commit":"feedfacefeedface","date":"2026-10-30T00:00:00Z","version":"1.0.99.1",
               "shim":"s1","file":"webtoepub-lib.js","size":${data.size},"sha256":"${EngineStore.sha256(data)}","built":"2026-10-30T01:00:00Z"}""",
        )
        RuleEngines.configure(root)
        return root
    }

    @Test
    fun aValidDownloadedUpdateIsTheOneInjected() = runTest {
        val root = installUpdate("UPDATED-BUNDLE")
        try {
            val page = ScriptedPage(bookJson)
            ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB).close()
            assertEquals(1, page.injected.size)
            assertTrue(page.injected[0].contains("UPDATED-BUNDLE"))
            assertTrue(log.toString(), log.any { it.startsWith("[Reader] Using WebToEpub parsers 2026-10-30, feedfac, downloaded update") })
            assertTrue(File(root, "webtoepub/webtoepub-lib.js").isFile)
        } finally {
            root.deleteRecursively()
        }
    }

    @Test
    fun anUpdateThatFailsToLoadIsSetAsideForTheShippedCopy() = runTest {
        val root = installUpdate("BROKEN-BUNDLE")
        try {
            val page = ScriptedPage(bookJson, broken = mapOf("BROKEN-BUNDLE" to "boom"))
            val session = ReaderBookSession.open(url, context(page), SiteEngine.WEBTOEPUB)
            assertEquals("The Book", session.book.title)
            session.close()

            assertEquals(2, page.injected.size)
            assertTrue(page.injected[0].contains("BROKEN-BUNDLE"))
            assertFalse(page.injected[1].contains("BROKEN-BUNDLE"))
            assertTrue(page.injected[1].contains("__W2E_parseChapterBatch"))
            assertTrue(log.toString(), log.any { it.contains("update (2026-10-30, feedfac, downloaded update) failed to load (boom); using the built-in copy.") })
            // The download is deleted and its release is remembered as rejected.
            assertFalse(File(root, "webtoepub/webtoepub-lib.js").exists())
            assertNull(RuleEngines.store.downloaded(RuleEngine.WEBTOEPUB))
            assertEquals(setOf("feedfacefeedface-s1"), RuleEngines.store.rejectedReleases(RuleEngine.WEBTOEPUB))
            assertFalse(RuleEngines.store.current(RuleEngine.WEBTOEPUB)!!.updated)
        } finally {
            root.deleteRecursively()
        }
    }
}
