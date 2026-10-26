package com.shirochi.npia.pipeline

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.ImageProcessing
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.PassthroughImages
import com.shirochi.npia.core.ProcessedImage
import com.shirochi.npia.core.SourceId
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

/** PNG signature plus filler; enough for extension sniffing. */
private val PNG = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A) + ByteArray(32) { 7 }

/** Stands in for the Android processor: every PNG becomes a (smaller) WebP. */
private object PngToWebp : ImageProcessing {
    override fun process(data: ByteArray, isCover: Boolean): ProcessedImage =
        if (data.size >= 4 && data[1] == 0x50.toByte()) ProcessedImage(data.copyOf(8), "webp") else PassthroughImages.process(data)
}

/** Serves fixed bytes per URL and records how many requests overlap. */
private class StubHttp(private val responses: Map<String, Pair<Int, ByteArray>>) : HttpFetching {
    private val active = AtomicInteger()
    val maxActive = AtomicInteger()
    val requests = java.util.Collections.synchronizedList(mutableListOf<String>())

    override suspend fun send(request: HttpRequest): HttpResponse {
        val now = active.incrementAndGet()
        maxActive.accumulateAndGet(now) { a, b -> maxOf(a, b) }
        requests += request.url
        delay(20)
        active.decrementAndGet()
        val (status, body) = responses[request.url] ?: (404 to ByteArray(0))
        return HttpResponse(request.url, status, emptyMap(), body)
    }
}

class ImageStageTest {
    private fun url(name: String) = "https://img.example.com/$name"

    private fun chapter(index: Int, images: List<ImageRef>): ChapterContent {
        val html = images.joinToString("") { "<p><img src=\"../${it.filename}\" alt=\"\"/></p>" }
        return ChapterContent(ChapterRef("$index", index, "Ch $index"), html, images)
    }

    private fun stage(http: HttpFetching, settings: AppSettings = AppSettings(), processor: ImageProcessing = PngToWebp) =
        ImageStage(http, settings, SourceId.READER, processor)

    @Test
    fun renamesToWebpAndRewritesHtml() = runTest {
        val http = StubHttp(mapOf(url("a") to (200 to PNG), url("b") to (200 to PNG)))
        val chapters = listOf(
            chapter(1, listOf(ImageRef(url("a"), "images/0001_01.png"))),
            chapter(2, listOf(ImageRef(url("b"), "images/0002_01.png"), ImageRef(url("a"), "images/0001_01.png"))),
        )
        val output = stage(http).run(chapters)

        assertEquals(setOf("images/0001_01.webp", "images/0002_01.webp"), output.images.keys)
        assertEquals(0, output.failed)
        assertEquals(listOf("images/0001_01.webp"), output.chapters[0].images.map { it.filename })
        assertTrue(output.chapters[0].html.contains("src=\"../images/0001_01.webp\""))
        assertTrue(output.chapters[1].html.contains("../images/0002_01.webp"))
        assertTrue(output.chapters[1].html.contains("../images/0001_01.webp"))
        assertFalse(output.chapters[1].html.contains(".png"))
        // The shared image is downloaded once.
        assertEquals(1, http.requests.count { it == url("a") })
    }

    @Test
    fun keepsNamesWhenExtensionMatches() = runTest {
        val http = StubHttp(mapOf(url("a") to (200 to PNG)))
        val output = stage(http, processor = PassthroughImages).run(listOf(chapter(1, listOf(ImageRef(url("a"), "images/1.png")))))
        assertEquals(setOf("images/1.png"), output.images.keys)
        assertTrue(output.chapters[0].html.contains("../images/1.png"))
    }

    @Test
    fun extensionlessNamesGainExtension() = runTest {
        val http = StubHttp(mapOf(url("a") to (200 to PNG)))
        val output = stage(http, processor = PassthroughImages).run(listOf(chapter(1, listOf(ImageRef(url("a"), "images/1")))))
        assertEquals(setOf("images/1.png"), output.images.keys)
        assertTrue(output.chapters[0].html.contains("../images/1.png"))
    }

    @Test
    fun renameCollisionGetsSuffix() {
        val processed = mapOf("a.png" to ProcessedImage(ByteArray(0), "webp"))
        assertEquals(mapOf("a.png" to "a_2.webp"), ImageStage.renames(listOf("a.png", "a.webp"), processed))
    }

    @Test
    fun jpegAliasIsNotRenamed() {
        val processed = mapOf("a.jpeg" to ProcessedImage(ByteArray(0), "jpg"))
        assertTrue(ImageStage.renames(listOf("a.jpeg"), processed).isEmpty())
    }

    @Test
    fun failedDownloadsAreDroppedAndCounted() = runTest {
        val http = StubHttp(mapOf(url("ok") to (200 to PNG)))
        val output = stage(http).run(
            listOf(chapter(1, listOf(ImageRef(url("ok"), "1.png"), ImageRef(url("missing"), "2.png")))),
        )
        assertEquals(1, output.failed)
        assertEquals(listOf("1.webp"), output.chapters[0].images.map { it.filename })
    }

    @Test
    fun inlineBytesSkipDownload() = runTest {
        val http = StubHttp(emptyMap())
        val output = stage(http).run(listOf(chapter(1, listOf(ImageRef(url("x"), "1.png", PNG)))))
        assertTrue(http.requests.isEmpty())
        assertNotNull(output.images["1.webp"])
        assertNull(output.chapters[0].images[0].data)
    }

    @Test
    fun concurrencyIsBoundedByImageWorkers() = runTest {
        val refs = (0 until 12).map { ImageRef(url("$it"), "$it.png") }
        val http = StubHttp(refs.associate { it.url to (200 to PNG) })
        val settings = AppSettings().let { it.copy(shared = it.shared.copy(imageWorkers = 3)) }
        val progress = mutableListOf<Pair<Int, Int>>()
        val output = stage(http, settings).run(listOf(chapter(1, refs))) { done, total -> progress += done to total }
        assertEquals(12, output.images.size)
        assertTrue(http.maxActive.get() <= 3)
        assertTrue(http.maxActive.get() > 1)
        assertEquals(12 to 12, progress.last())
    }

    @Test
    fun saveImageUrlsOnlyPointsAtRemote() = runTest {
        val http = StubHttp(emptyMap())
        val settings = AppSettings().let { it.copy(images = it.images.copy(saveImageUrlsOnly = true)) }
        val output = stage(http, settings).run(listOf(chapter(1, listOf(ImageRef(url("a"), "images/1.png")))))
        assertTrue(http.requests.isEmpty())
        assertTrue(output.images.isEmpty())
        assertTrue(output.chapters[0].images.isEmpty())
        assertTrue(output.chapters[0].html.contains("src=\"https://img.example.com/a\""))
    }

    @Test
    fun recompressQualityFollowsDesktopHeuristic() {
        // sqrt(25/100) halves the quality.
        assertEquals(45, ImageStage.nextRecompressQuality(90, 100, 25))
        // Close to the target still steps down by 10.
        assertEquals(80, ImageStage.nextRecompressQuality(90, 101, 100))
        assertEquals(10, ImageStage.nextRecompressQuality(15, 1000, 1))
        assertNull(ImageStage.nextRecompressQuality(10, 1000, 1))
    }

    @Test
    fun rewriteHandlesQuotesAndSuffixes() {
        val html = "<img src='images/a.png'/><a href=\"../images/a.png\">x</a><image xlink:href=\"a.png\"/><img src=\"b.png\"/>"
        val out = ImageReferences.rewrite(html, mapOf("images/a.png" to "images/a.webp"))
        assertEquals("<img src='images/a.webp'/><a href=\"../images/a.webp\">x</a><image xlink:href=\"a.png\"/><img src=\"b.png\"/>", out)
        assertEquals("dir.v1/name.webp", ImageReferences.renamed("dir.v1/name", "webp"))
        assertEquals("dir/name.webp", ImageReferences.renamed("dir/name.png", "webp"))
        assertEquals(".hidden.webp", ImageReferences.renamed(".hidden", "webp"))
    }
}
