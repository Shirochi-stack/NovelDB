package com.shirochi.npia.pipeline

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.PassthroughImages
import com.shirochi.npia.core.SourceId
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.yield
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.util.Collections
import kotlin.coroutines.coroutineContext

class ImagePrefetchTest {
    @get:Rule
    val temp = TemporaryFolder()

    private val png = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A) + ByteArray(32) { 7 }

    private fun chapter(index: Int, vararg images: ImageRef) = ChapterContent(
        ChapterRef("$index", index, "Ch $index", url = "https://site.example/c$index"), "<p></p>", images.toList())

    private fun image(name: String, data: ByteArray? = null, url: String = "https://img.example/$name") =
        ImageRef(url, "images/$name", data)

    /** Answers by address; an address in `held` waits until its gate opens. */
    private class Site(private val files: Map<String, ByteArray>, private val gates: Map<String, CompletableDeferred<Unit>> = emptyMap()) : HttpFetching {
        val requests: MutableList<HttpRequest> = Collections.synchronizedList(mutableListOf())

        override suspend fun send(request: HttpRequest): HttpResponse {
            requests += request
            gates[request.url]?.await()
            val body = files[request.url] ?: return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
            return HttpResponse(request.url, 200, emptyMap(), body)
        }
    }

    private val noNetwork = object : HttpFetching {
        override suspend fun send(request: HttpRequest): HttpResponse = error("already downloaded: not asked for again")
    }

    @Test
    fun aChaptersImagesAreDownloadedAsSoonAsItIsReadAndTheImageStepTakesThem() = runTest {
        val store = ImageStore.create(temp.root)
        val site = Site(mapOf("https://img.example/a.png" to png, "https://img.example/b.png" to png + 1))
        val prefetch = ImagePrefetch(site, store, workers = 2, parent = coroutineContext)
        try {
            val first = chapter(1, image("a.png"), image("b.png"), image("held.png", byteArrayOf(5)), image("inline.png", url = "data:image/png;base64,AAAA"))
            // The pipeline hands a chapter over after `keep`: bytes a source brought are already in the store.
            val kept = (store.keep(ChapterResult.Content(first)) as ChapterResult.Content)
            prefetch.offer(kept)
            prefetch.offer(kept)  // a chapter offered again starts nothing twice
            prefetch.offer(ChapterResult.Refused(first.ref, "locked"))
            assertEquals(2, prefetch.started)
            prefetch.await("images/a.png")
            prefetch.await("images/b.png")
            prefetch.await("images/never-started.png")
            assertEquals(listOf("https://img.example/a.png", "https://img.example/b.png"), site.requests.map { it.url }.sorted())
            assertArrayEquals(png, store.held("images/a.png"))

            // The image step asks the site for nothing: every image is held.
            val output = ImageStage(noNetwork, AppSettings(), SourceId.READER, PassthroughImages, store = store, prefetch = prefetch).run(listOf(kept.content))
            assertEquals(1, output.failed)  // the inline address nobody can fetch here
            assertArrayEquals(png + 1, output.images["images/b.png"])
            assertArrayEquals(byteArrayOf(5), output.images["images/held.png"])
            assertNull(store.held("images/a.png"))  // the raw copy goes once it is encoded
        } finally {
            prefetch.close()
            store.close()
        }
    }

    @Test
    fun theImageStepWaitsForADownloadStillOnItsWayAndFetchesWhatFailedHere() = runTest {
        val store = ImageStore.create(temp.root)
        val gate = CompletableDeferred<Unit>()
        val early = Site(mapOf("https://img.example/slow.png" to png), gates = mapOf("https://img.example/slow.png" to gate))
        val prefetch = ImagePrefetch(early, store, workers = 4, parent = coroutineContext)
        try {
            val content = chapter(1, image("slow.png"), image("refused.png"))
            prefetch.offer(ChapterResult.Content(content))
            // The image step's own client: only for the image the early download could not get.
            val later = Site(mapOf("https://img.example/refused.png" to png + 2))
            val running = async { ImageStage(later, AppSettings(), SourceId.READER, PassthroughImages, store = store, prefetch = prefetch).run(listOf(content)) }
            yield()
            assertFalse("the step waits for the download on its way", running.isCompleted)
            gate.complete(Unit)
            val output = running.await()
            assertEquals(0, output.failed)
            assertArrayEquals(png, output.images["images/slow.png"])
            assertArrayEquals(png + 2, output.images["images/refused.png"])
            assertEquals(listOf("https://img.example/refused.png"), later.requests.map { it.url })
            assertEquals(1, early.requests.count { it.url.endsWith("slow.png") })
        } finally {
            prefetch.close()
            store.close()
        }
    }

    @Test
    fun aServerThatWantsAReferrerIsAskedAgainFromTheChaptersPageAndAStopEndsTheDownloads() = runTest {
        val store = ImageStore.create(temp.root)
        val gate = CompletableDeferred<Unit>()
        val site = object : HttpFetching {
            val requests: MutableList<HttpRequest> = Collections.synchronizedList(mutableListOf())
            override suspend fun send(request: HttpRequest): HttpResponse {
                requests += request
                if (request.url.endsWith("stuck.png")) gate.await()
                val allowed = request.headers["Referer"] == "https://site.example/c1"
                return HttpResponse(request.url, if (allowed) 200 else 403, emptyMap(), if (allowed) png else ByteArray(0))
            }
        }
        val prefetch = ImagePrefetch(site, store, workers = 4, parent = coroutineContext)
        try {
            prefetch.offer(ChapterResult.Content(chapter(1, image("a.png"), image("stuck.png"))))
            prefetch.await("images/a.png")
            assertArrayEquals(png, store.held("images/a.png"))
            assertEquals(2, site.requests.count { it.url.endsWith("a.png") })

            // Stop: the download on its way ends, nothing new starts, and waiting for it returns.
            prefetch.close()
            prefetch.await("images/stuck.png")
            assertTrue(store.held("images/stuck.png") == null)
            prefetch.offer(ChapterResult.Content(chapter(2, image("late.png"))))
            assertEquals(2, prefetch.started)
        } finally {
            prefetch.close()
            store.close()
        }
    }

    @Test
    fun anImageItsSourceDownloadedHasItsLineWhenItArrivesAndNotAgainAtTheImageStep() = runTest {
        val store = ImageStore.create(temp.root)
        val lines = Collections.synchronizedList(mutableListOf<String>())
        val logger = com.shirochi.npia.core.JobLogging { _, message -> lines += message }
        val prefetch = ImagePrefetch(noNetwork, store, workers = 2, parent = coroutineContext, logger = logger)
        try {
            // A source that fetches a chapter's pages itself (Bilibili Manga): it reports each page, then
            // hands the chapter over with the bytes.
            val content = chapter(3, image("p1.png", png), image("p2.png", png + 1))
            prefetch.arrived(content.ref, 0, "images/p1.png", png.size, 0.5)
            prefetch.arrived(content.ref, 1, "images/p2.png", png.size + 1, 0.0)
            assertEquals(listOf("  ✓ [3] Image #1 [Ch 3]: 40 B [80 B/s]", "  ✓ [3] Image #2 [Ch 3]: 41 B [40.0 KB/s]"), lines.toList())
            assertEquals(2, prefetch.started)
            val kept = store.keep(ChapterResult.Content(content)) as ChapterResult.Content
            prefetch.offer(kept)
            assertEquals(2, prefetch.started)  // held already: not asked for here

            val output = ImageStage(noNetwork, AppSettings(), SourceId.READER, PassthroughImages, logger, store, prefetch).run(listOf(kept.content))
            assertEquals(0, output.failed)
            assertArrayEquals(png + 1, output.images["images/p2.png"])
            assertEquals(lines.toString(), 2, lines.count { it.contains("✓") })
        } finally {
            prefetch.close()
            store.close()
        }
    }

    @Test
    fun withTheDeviceNearlyFullImagesWaitForTheImageStep() = runTest {
        val store = ImageStore.create(temp.root)
        val site = Site(mapOf("https://img.example/a.png" to png))
        val prefetch = ImagePrefetch(site, store, workers = 2, parent = coroutineContext, reserveBytes = Long.MAX_VALUE)
        try {
            prefetch.offer(ChapterResult.Content(chapter(1, image("a.png"))))
            assertEquals(0, prefetch.started)
            assertTrue(site.requests.isEmpty())
        } finally {
            prefetch.close()
            store.close()
        }
    }
}
