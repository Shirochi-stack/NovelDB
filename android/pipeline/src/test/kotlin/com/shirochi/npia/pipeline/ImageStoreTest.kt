package com.shirochi.npia.pipeline

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.PassthroughImages
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.UnavailableWebEngine
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

class ImageStoreTest {
    @get:Rule
    val temp = TemporaryFolder()

    private fun picture(index: Int, data: ByteArray) = ChapterContent(
        ChapterRef("$index", index, "Ch $index"),
        "<p><img src=\"../images/vip$index.gif\" alt=\"\"/></p>",
        listOf(ImageRef("https://img.example.com/$index", "images/vip$index.gif", data)),
    )

    @Test
    fun aChaptersImageBytesWaitOnDiskAndAreReadBackOneByOne() {
        val store = ImageStore.create(temp.root)
        val kept = store.keep(ChapterResult.Content(picture(1, byteArrayOf(1, 2, 3)))) as ChapterResult.Content
        assertNull(kept.content.images.single().data) // nothing left in memory
        assertArrayEquals(byteArrayOf(1, 2, 3), store.held("images/vip1.gif"))
        // A chapter without held bytes, and a refused one, pass through untouched.
        val plain = ChapterResult.Content(ChapterContent(ChapterRef("2", 2, "Ch 2"), "<p>text</p>"))
        assertTrue(store.keep(plain) === plain)

        store.put("encoded:images/vip1.gif", byteArrayOf(9))
        val view = store.view(mapOf("images/vip1.webp" to "encoded:images/vip1.gif"))
        assertEquals(setOf("images/vip1.webp"), view.keys)
        assertTrue(view.containsKey("images/vip1.webp"))
        assertArrayEquals(byteArrayOf(9), view["images/vip1.webp"])
        assertArrayEquals(byteArrayOf(9), view.entries.single().value)
        val smaller = store.transform(view, "pass1") { _, data -> data + 0 }
        assertArrayEquals(byteArrayOf(9, 0), smaller["images/vip1.webp"])

        store.close()
        assertTrue(temp.root.listFiles().orEmpty().isEmpty()) // the job's folder is gone
    }

    @Test
    fun theImageStageUsesHeldBytesAndKeepsEncodedOnesOnDisk() = runTest {
        val store = ImageStore.create(temp.root)
        val chapters = (1..3).map { i -> (store.keep(ChapterResult.Content(picture(i, ByteArray(64) { i.toByte() }))) as ChapterResult.Content).content }
        val noNetwork = object : HttpFetching {
            override suspend fun send(request: HttpRequest): HttpResponse = error("held images are not downloaded again")
        }
        val output = ImageStage(noNetwork, AppSettings(), SourceId.SFC, PassthroughImages, store = store).run(chapters)
        assertEquals(0, output.failed)
        assertEquals(3, output.images.size)
        assertArrayEquals(ByteArray(64) { 2 }, output.images["images/vip2.gif"])
        assertNull(store.held("images/vip2.gif")) // the raw copy goes once it is encoded
        store.close()
    }

    @Test
    fun theSharedChapterLoopHandsEachChapterToKeep() = runTest {
        val adapter = object : SourceAdapter {
            override val id = SourceId.SFC
            override val tier = SourceTier.HTTP
            override fun matches(url: String) = true
            override suspend fun parseBook(url: String, context: SourceContext) = error("unused")
            override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
                ChapterResult.Content(picture(ref.index, ByteArray(16)))
        }
        val store = ImageStore.create(temp.root)
        val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0))
        val context = SourceContext(NoHttp, UnavailableWebEngine, InMemorySecretStore(), settings, keep = store::keep)
        val book = BookInfo(SourceId.SFC, "1", "https://example.com/1", "Book")
        val results = adapter.parseChapters((1..5).map { ChapterRef("$it", it, "Ch $it") }, book, context)
        assertFalse(results.any { r -> (r as ChapterResult.Content).content.images.any { it.data != null } })
        assertArrayEquals(ByteArray(16), store.held("images/vip5.gif"))
        store.close()
    }

    private object NoHttp : HttpFetching {
        override suspend fun send(request: HttpRequest): HttpResponse = error("unused")
    }
}
