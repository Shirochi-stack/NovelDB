package com.shirochi.npia.sources.kobo

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class KoboAdapterTests {
    // URL matching

    @Test
    fun matchesKoboProductPages() {
        assertTrue(KoboParsing.matches("https://www.kobo.com/us/en/ebook/some-book"))
        assertTrue(KoboParsing.matches("https://kobo.com/ebook/some-book/"))
        assertTrue(KoboParsing.matches("http://www.kobo.com/jp/ja/ebook/abc123"))
        assertTrue(KoboAdapter().matches("https://www.kobo.com/us/en/ebook/some-book"))
    }

    @Test
    fun matchesRakutenBooks() {
        assertTrue(KoboParsing.matches("https://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef/"))
        assertTrue(KoboParsing.matches("https://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef"))
    }

    @Test
    fun rejectsOtherUrls() {
        assertFalse(KoboParsing.matches("https://www.kobo.com/us/en/audiobook/some-book"))
        assertFalse(KoboParsing.matches("https://www.kobo.com/us/en/ebook/a/b"))
        assertFalse(KoboParsing.matches("https://www.kobo.com/"))
        assertFalse(KoboParsing.matches("https://books.rakuten.co.jp/rk/xyz/"))
        assertFalse(KoboParsing.matches("http://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef/"))
        assertFalse(KoboParsing.matches("https://example.com/ebook/some-book"))
        assertFalse(KoboParsing.matches("https://readnow.kobo.com/ebook/some-book"))
    }

    // Owned read URL

    @Test
    fun ownedReadUrlRequiresLibraryAndReaderHost() {
        fun page(config: String): String {
            val escaped = config.replace("\"", "&quot;")
            return "<div data-kobo-gizmo-config=\"$escaped\"></div>"
        }
        assertEquals(
            "https://readnow.kobo.com/book/1",
            KoboParsing.ownedReadUrl(page("{\"isInLibrary\":true,\"readNowUrl\":\"https://readnow.kobo.com/book/1\"}")),
        )
        assertEquals("", KoboParsing.ownedReadUrl(page("{\"isInLibrary\":false,\"readNowUrl\":\"https://readnow.kobo.com/book/1\"}")))
        assertEquals("", KoboParsing.ownedReadUrl(page("{\"isInLibrary\":true,\"readNowUrl\":\"https://evil.example/book/1\"}")))
        assertEquals("", KoboParsing.ownedReadUrl(page("{\"isInLibrary\":true,\"readNowUrl\":\"http://readnow.kobo.com/book/1\"}")))
        assertEquals("", KoboParsing.ownedReadUrl(page("not json")))
        assertEquals("", KoboParsing.ownedReadUrl("<p>nothing</p>"))
    }

    // Book

    @Test
    fun makeBookOwned() {
        val meta = KoboMeta(
            title = "A Book",
            author = "An Author",
            cover = "https://cdn.example/cover.jpg",
            description = "About it",
            language = "ja-JP",
            readUrl = "https://readnow.kobo.com/book/1",
        )
        val url = "https://www.kobo.com/us/en/ebook/a-book"
        val book = KoboParsing.makeBook(url, meta)
        assertEquals(SourceId.KOBO, book.source)
        assertEquals("a-book", book.id)
        assertEquals("A Book", book.title)
        assertEquals("An Author", book.author)
        assertEquals("ja", book.language)
        assertEquals(1, book.chapters.size)
        assertFalse(book.chapters[0].isPaid)
        assertEquals("https://readnow.kobo.com/book/1", book.chapters[0].url)
    }

    @Test
    fun makeBookNotOwnedMarksChapterPaid() {
        val url = "https://www.kobo.com/us/en/ebook/a-book"
        val book = KoboParsing.makeBook(url, KoboMeta())
        assertEquals("Kobo ebook", book.title)
        assertEquals("en", book.language)
        assertTrue(book.chapters[0].isPaid)
        assertEquals(url, book.chapters[0].url)
    }

    @Test
    fun metaDecodingToleratesMissingAndWrongTypes() {
        val meta = KoboMeta.decode("{\"title\":\"T\",\"cover\":{\"x\":1},\"readUrl\":null}")
        assertEquals("T", meta.title)
        assertEquals("", meta.cover)
        assertEquals("", meta.readUrl)
    }

    // Path and order helpers

    @Test
    fun resolvePath() {
        assertEquals("OEBPS/Text/ch1.xhtml", KoboParsing.resolvePath("ch1.xhtml#a", "OEBPS/Text/toc.xhtml"))
        assertEquals("OEBPS/Images/x.xhtml", KoboParsing.resolvePath("../Images/x.xhtml", "OEBPS/Text/toc.xhtml"))
        assertEquals("OEBPS/Text/c.xhtml", KoboParsing.resolvePath("OEBPS/Text/c.xhtml", "OPS/t.xhtml"))
        assertEquals("OEBPS/Text/a b.xhtml", KoboParsing.resolvePath("a%20b.xhtml", "OEBPS/Text/toc.xhtml"))
        assertEquals("", KoboParsing.resolvePath("https://x.example/a.xhtml"))
        assertEquals("", KoboParsing.resolvePath("#frag"))
    }

    @Test
    fun anchorIsStable() {
        val first = KoboParsing.anchor("OEBPS/Text/ch1.xhtml")
        assertEquals(first, KoboParsing.anchor("OEBPS/Text/ch1.xhtml"))
        assertTrue(first.startsWith("kobo-"))
        assertEquals(17, first.length)
        assertNotEquals(first, KoboParsing.anchor("OEBPS/Text/ch2.xhtml"))
    }

    @Test
    fun orderedPathsMergesOverlappingWindows() {
        val order = KoboParsing.orderedPaths(listOf(listOf("a", "b", "c"), listOf("b", "c", "d"), listOf("d", "e")))
        assertEquals(listOf("a", "b", "c", "d", "e"), order)
    }

    @Test
    fun orderedPathsRejectsCycles() {
        assertThrows { KoboParsing.orderedPaths(listOf(listOf("a", "b"), listOf("b", "a"))) }
    }

    @Test
    fun fontEncodedDetection() {
        val garbled = "\u1F70".repeat(20) + "abc"
        assertTrue(KoboParsing.isFontEncoded(garbled, listOf("Chapter 1")))
        assertFalse(KoboParsing.isFontEncoded(garbled, listOf("Prologue")))
        assertFalse(KoboParsing.isFontEncoded("plain text", listOf("Chapter 1")))
    }

    @Test
    fun dataUrlHelpers() {
        assertEquals("png", KoboParsing.imageExtension("data:image/png;base64,AAAA"))
        assertEquals("svg", KoboParsing.imageExtension("data:image/svg+xml;base64,AAAA"))
        assertEquals("jpg", KoboParsing.imageExtension(""))
        assertArrayEquals(byteArrayOf(1, 2, 3), KoboParsing.imageBytes("data:image/png;base64,AQID"))
        assertNull(KoboParsing.imageBytes("blob:abc"))
    }

    // Chapter assembly

    private fun makeRef() = ChapterRef("kobo-1", 1, "A Book", url = "https://readnow.kobo.com/book/1", isPaid = false)

    @Test
    fun buildContentCleansAndLinksSections() {
        val one = KoboSnapshotSection(
            path = "OEBPS/Text/c1.xhtml",
            html = "<p onclick=\"x()\">One</p><script>bad()</script><a href=\"c2.xhtml#t\">next</a><img src=\"blob:abc\">",
            images = listOf(KoboSnapshotImage("blob:abc", "data:image/png;base64,AQID")),
        )
        val two = KoboSnapshotSection(path = "OEBPS/Text/c2.xhtml", html = "<p>Two</p>", images = emptyList())
        val entries = listOf(KoboEntry("One", "OEBPS/Text/c1.xhtml"), KoboEntry("Two", "OEBPS/Text/c2.xhtml"))
        val content = KoboParsing.buildContent(
            makeRef(), entries,
            mapOf(one.path to one, two.path to two),
            listOf(listOf(one.path, two.path)), horizontalLayout = true,
        )
        assertFalse(content.html.contains("onclick"))
        assertFalse(content.html.contains("bad()"))
        assertTrue(content.html.contains("href=\"#${KoboParsing.anchor(two.path)}\""))
        assertTrue(content.html.contains("writing-mode:horizontal-tb"))
        assertTrue(content.html.contains("id=\"${KoboParsing.anchor(one.path)}\""))
        assertEquals(1, content.images.size)
        assertEquals("images/0001_001.png", content.images[0].filename)
        assertArrayEquals(byteArrayOf(1, 2, 3), content.images[0].data)
        assertTrue(content.html.contains("src=\"images/0001_001.png\""))
        // Section order follows the spine.
        assertTrue(content.html.indexOf("One") < content.html.indexOf("Two"))
    }

    @Test
    fun buildContentHonoursVerticalLayoutSetting() {
        val section = KoboSnapshotSection(path = "OEBPS/Text/c1.xhtml", html = "<p>One</p>", images = emptyList())
        val content = KoboParsing.buildContent(
            makeRef(), listOf(KoboEntry("One", section.path)),
            mapOf(section.path to section), listOf(listOf(section.path)), horizontalLayout = false,
        )
        assertFalse(content.html.contains("writing-mode"))
    }

    @Test
    fun buildContentFailsOnMissingSectionOrBlobImage() {
        val section = KoboSnapshotSection(
            path = "OEBPS/Text/c1.xhtml",
            html = "<p>One</p>",
            images = listOf(KoboSnapshotImage("blob:xyz", null)),
        )
        assertThrows {
            KoboParsing.buildContent(
                makeRef(), listOf(KoboEntry("One", section.path), KoboEntry("Two", "OEBPS/Text/c2.xhtml")),
                mapOf(section.path to section), listOf(listOf(section.path)), horizontalLayout = true,
            )
        }
        assertThrows {
            KoboParsing.buildContent(
                makeRef(), listOf(KoboEntry("One", section.path)),
                mapOf(section.path to section), listOf(listOf(section.path)), horizontalLayout = true,
            )
        }
    }

    // Refusals (no network, no web page)

    private fun makeContext() = SourceContext(KoboStubHttp, KoboStubEngine, InMemorySecretStore(), AppSettings())

    @Test
    fun rakutenBookIsListedButChapterRefused() = runTest {
        val url = "https://books.rakuten.co.jp/rk/0123456789abcdef0123456789abcdef/"
        val book = KoboAdapter().parseBook(url, makeContext())
        assertEquals(1, book.chapters.size)
        val result = KoboAdapter().parseChapter(book.chapters[0], book, makeContext())
        val refused = result as? ChapterResult.Refused ?: return@runTest fail("expected refused")
        assertTrue(refused.reason.isNotEmpty())
    }

    @Test
    fun unownedBookChapterIsRefusedWithoutWebPage() = runTest {
        val url = "https://www.kobo.com/us/en/ebook/a-book"
        val book = KoboParsing.makeBook(url, KoboMeta())
        // KoboStubEngine throws if a page is requested, so reaching Refused proves none was opened.
        val result = KoboAdapter().parseChapter(book.chapters[0], book, makeContext())
        assertTrue("expected refused", result is ChapterResult.Refused)
    }

    @Test
    fun foreignReaderUrlIsRefused() = runTest {
        val ref = ChapterRef("kobo-1", 1, "x", url = "https://evil.example/read", isPaid = false)
        val book = BookInfo(SourceId.KOBO, "x", "https://www.kobo.com/ebook/x", "x", chapters = listOf(ref))
        val result = KoboAdapter().parseChapter(ref, book, makeContext())
        assertTrue("expected refused", result is ChapterResult.Refused)
    }

    private fun assertThrows(block: () -> Unit) {
        try {
            block()
        } catch (e: Exception) {
            return
        }
        fail("expected an exception")
    }
}

// Stubs

private object KoboStubHttp : HttpFetching {
    override suspend fun send(request: HttpRequest) = HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
}

private object KoboStubEngine : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = throw WebEngineException("unavailable")
    override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) {}
}
