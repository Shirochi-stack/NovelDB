package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.UnavailableWebEngine
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.sources.http.RoutedHttp
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.spec.IvParameterSpec
import javax.crypto.spec.SecretKeySpec

class BiliComicAdapterTest {
    private val jpeg = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 0xE0.toByte()) + ByteArray(40) { it.toByte() }
    private val png = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A) + ByteArray(40) { it.toByte() }
    private val host = "https://manga.hdslb.com/bfs/manga/"
    private val ref = ChapterRef(id = "https://manga.bilibili.com/mc29839/10", index = 1, title = "01 First",
        url = "https://manga.bilibili.com/mc29839/10")

    private fun chapter(vararg names: String) = ChapterResult.Content(ChapterContent(ref, "<p></p>",
        names.mapIndexed { index, name -> ImageRef("$host$name?token=abc&ts=6b02b691", "images/0001_%02d.jpg".format(index + 1)) }))

    @Test
    fun theSitesBookAndChapterPagesBelongToTheBuiltInScraper() {
        val book = "https://manga.bilibili.com/detail/mc29839"
        for (url in listOf(book, "https://manga.bilibili.com/mc29839", "https://manga.bilibili.com/mc29839/585738",
            "https://manga.bilibili.com/m/detail/mc29839?from=x")) {
            assertTrue(url, PythonEngines.biliComicMatches(url))
            assertEquals(url, SourceId.BILICOMIC, SourceCatalog.adapterFor(url)?.id)
        }
        for (url in listOf("https://manga.bilibili.com/", "https://manga.bilibili.com/ranking",
            "https://www.bilibili.com/video/mc29839", "https://manga.bilibili.com.evil.test/detail/mc1", "ftp://manga.bilibili.com/mc1")) {
            assertFalse(url, PythonEngines.biliComicMatches(url))
        }
        assertEquals(SourceId.BILICOMIC, SourceCatalog.resolve(book)?.id)
        assertTrue(SourceCatalog.downloadablePage(book))
        assertTrue(SourceCatalog.supportedSite("https://manga.bilibili.com/"))
        // With the built-in scrapers off the page is not offered as this scraper's.
        assertFalse(SourceId.BILICOMIC == SourceCatalog.resolve(book, EngineSettings(disabled = listOf("native")))?.id)
    }

    @Test
    fun aDownloadFetchesAChaptersPagesAsSoonAsItIsReadAndHandsThemOver() = runBlocking {
        val http = RoutedHttp()
        http.onData("a.jpg", jpeg)
        http.onData("b.jpg", png)
        val kept = ArrayList<ChapterResult>()
        val arrived = java.util.Collections.synchronizedList(ArrayList<String>())
        val context = SourceContext(http, UnavailableWebEngine, InMemorySecretStore(), AppSettings(), keep = { result ->
            kept += result
            // The pipeline's store: the bytes go to disk and leave the chapter.
            if (result is ChapterResult.Content) ChapterResult.Content(result.content.copy(images = result.content.images.map { it.copy(data = null) }))
            else result
        }, onImage = { chapter, position, total, filename, bytes, seconds ->
            assertTrue(seconds >= 0)
            assertEquals(2, total)
            arrived += "${chapter.index}/$position $filename $bytes"
        })
        val result = BiliComicAdapter().withPages(chapter("a.jpg", "b.jpg"), context)
        // Each page is reported as it arrives, so its log line is written while the chapters are read.
        assertEquals(listOf("1/0 images/0001_01.jpg ${jpeg.size}", "1/1 images/0001_02.jpg ${png.size}"), arrived.sorted())
        val held = (kept.single() as ChapterResult.Content).content.images
        assertArrayEquals(jpeg, held[0].data)
        assertArrayEquals(png, held[1].data)
        assertEquals(listOf("images/0001_01.jpg", "images/0001_02.jpg"), held.map { it.filename })
        assertTrue((result as ChapterResult.Content).content.images.all { it.data == null })
        assertEquals(2, http.requests.size)
        assertTrue(http.requests.all { it.headers["Referer"] == "https://manga.bilibili.com/" && "token=abc&ts=6b02b691" in it.url })
    }

    @Test
    fun aPageThatCannotBeFetchedFailsTheChapterSoItIsReadAgain() = runBlocking {
        val http = RoutedHttp()
        http.onData("a.jpg", jpeg)
        http.on("c.jpg", "<html>refused</html>")  // an answer that is not an image
        val kept = ArrayList<ChapterResult>()
        val context = SourceContext(http, UnavailableWebEngine, InMemorySecretStore(), AppSettings(), keep = { kept += it; it })
        val missing = BiliComicAdapter().withPages(chapter("a.jpg", "gone.jpg"), context)
        assertEquals("Page 2 of 2 could not be downloaded.", (missing as ChapterResult.Failed).message)
        val wrong = BiliComicAdapter().withPages(chapter("c.jpg"), context)
        assertEquals("Page 1 of 1 could not be downloaded.", (wrong as ChapterResult.Failed).message)
        assertTrue(kept.isEmpty())
        // A refused or failed chapter passes through untouched.
        val refused = ChapterResult.Refused(ref, "locked")
        assertTrue(BiliComicAdapter().withPages(refused, context) === refused)
    }

    @Test
    fun aProtectedPageIsDecoded() {
        val image = jpeg + ByteArray(25_600) { (it % 251).toByte() }  // longer than the encrypted start
        val key = ByteArray(16) { it.toByte() }
        val iv = ByteArray(16) { (it + 16).toByte() }
        // The first 20480 bytes are encrypted with one block of padding (20496 bytes); the rest is plain.
        val cipher = Cipher.getInstance("AES/CBC/NoPadding")
        cipher.init(Cipher.ENCRYPT_MODE, SecretKeySpec(key, "AES"), IvParameterSpec(iv))
        val body = cipher.doFinal(image.copyOfRange(0, 20480) + ByteArray(16) { 16 }) + image.copyOfRange(20480, image.size)
        val stored = byteArrayOf(1, (body.size ushr 24).toByte(), (body.size ushr 16).toByte(), (body.size ushr 8).toByte(), body.size.toByte()) + body + key
        val cpx = Base64.getEncoder().withoutPadding().encodeToString(ByteArray(60) + iv + ByteArray(8))
            .replace("+", "%2B").replace("/", "%2F")
        val url = "${host}a.jpg?token=t&ts=6b02b691&cpx=$cpx"
        assertArrayEquals(image, BiliComicPages.decode(stored, url))
        assertArrayEquals(jpeg, BiliComicPages.decode(jpeg, url))  // already an image
        assertNull(runCatching { BiliComicPages.decode(stored, "${host}a.jpg?token=t&ts=6b02b691") }.getOrNull())  // no key in the address
        assertNull(runCatching { BiliComicPages.decode(byteArrayOf(2) + stored.copyOfRange(1, stored.size), url) }.getOrNull())
    }
}
