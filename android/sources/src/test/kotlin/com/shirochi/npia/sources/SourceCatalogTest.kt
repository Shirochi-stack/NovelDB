package com.shirochi.npia.sources

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.UnavailableWebEngine
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

class SourceCatalogTest {
    @Test
    fun everySourceHasExactlyOneAdapter() {
        val ids = SourceCatalog.adapters.map { it.id }
        assertEquals(SourceId.entries.toSet(), ids.toSet())
        assertEquals(SourceId.entries.size, ids.size)
        assertEquals(SourceId.READER, ids.last())
    }

    @Test
    fun unknownUrlHasNoAdapter() {
        assertNull(SourceCatalog.adapterFor("https://example.com/x"))
    }

    @Test
    fun defaultParseChaptersKeepsOrderLimitsConcurrencyAndCapturesFailures() = runTest {
        val reported = java.util.concurrent.atomic.AtomicInteger()
        val context = SourceContext(StubHttp, UnavailableWebEngine, InMemorySecretStore(), AppSettings(shared = SharedSettings(threads = 3))) {
            reported.incrementAndGet()
        }
        val refs = (1..10).map { ChapterRef("$it", it, "Chapter $it") }
        val book = BookInfo(SourceId.READER, "b", "https://example.com", "Book", chapters = refs)
        val adapter = FakeAdapter()

        val results = adapter.parseChapters(refs, book, context)

        assertEquals((1..10).toList(), results.map { it.ref.index })
        assertTrue(results[6] is ChapterResult.Failed)
        assertTrue("at most 3 at once", adapter.maxActive.get() <= 3)
        assertEquals("every chapter reports progress, failures included", 10, reported.get())
    }

    private class FakeAdapter : SourceAdapter {
        override val id = SourceId.READER
        override val tier = SourceTier.HTTP
        val active = AtomicInteger()
        val maxActive = AtomicInteger()

        override fun matches(url: String) = true

        override suspend fun parseBook(url: String, context: SourceContext): BookInfo = throw SourceException.NotImplemented(id)

        override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
            val now = active.incrementAndGet()
            maxActive.accumulateAndGet(now) { a, b -> maxOf(a, b) }
            delay((10 - ref.index) * 5L)
            active.decrementAndGet()
            if (ref.index == 7) throw SourceException.Parse("bad page")
            return ChapterResult.Content(ChapterContent(ref, "<p>${ref.index}</p>"))
        }
    }

    private object StubHttp : HttpFetching {
        override suspend fun send(request: HttpRequest) = HttpResponse(request.url, 200, emptyMap(), ByteArray(0))
    }
}
