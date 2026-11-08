package com.shirochi.npia.sources.mpia

import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.hard.ScriptedPage
import com.shirochi.npia.sources.hard.ScriptedWeb
import com.shirochi.npia.sources.hard.context
import com.shirochi.npia.sources.reader.BuiltInReader
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class MpiaAdapterTest {
    private val adapter = MpiaAdapter()

    // The host is read from the escaped constant, so the provider name stays out of this file too.
    private val host = MpiaSite.DOMAIN

    @Test
    fun matchesDetailViewerAndLegacyUrls() {
        assertTrue(adapter.matches("https://www.$host/novel/detail/12345"))
        assertTrue(adapter.matches("https://$host/novel/detail/12345/"))
        assertTrue(adapter.matches("https://www.$host/novel/viewer/12345/67890"))
        val mobileViewer = "https://m.$host/novel/viewer/12345/67890"
        assertTrue(adapter.matches(mobileViewer))
        assertEquals(mobileViewer, MpiaSite.mobileViewerUrl(MpiaSite.viewerUrl("12345", "67890")))
        assertTrue(adapter.matches("https://novel.$host/12345"))
        assertTrue(adapter.matches("https://novel.$host/12345/page/2"))
    }

    @Test
    fun rejectsOtherUrls() {
        assertFalse(adapter.matches("https://www.$host/"))
        assertFalse(adapter.matches("https://www.$host/novel/detail/"))
        assertFalse(adapter.matches("https://example.com/novel/detail/1"))
        assertFalse(adapter.matches("ftp://www.$host/novel/detail/1"))
        assertFalse(adapter.matches("not a url"))
    }

    @Test
    fun listsItsSiteAndOwnsItsSubdomains() {
        assertEquals(setOf(host), adapter.sites)
        assertTrue(adapter.ownsSite(host))
        assertTrue(adapter.ownsSite("www.$host"))
        assertTrue(adapter.ownsSite("novel.$host"))
        assertFalse(adapter.ownsSite("example.com"))
        assertEquals(SourceId.MPIA, adapter.id)
    }

    @Test
    fun canonicalUrlPointsAtTheBookDetailPage() {
        val detail = MpiaSite.bookUrl("12345")
        assertEquals(detail, adapter.canonicalUrl("https://www.$host/novel/viewer/12345/67890"))
        assertEquals(detail, adapter.canonicalUrl("https://novel.$host/12345/neSrl/67890"))
        assertEquals(detail, adapter.canonicalUrl("https://www.$host/novel/detail/12345"))
    }

    @Test
    fun catalogReaderOpensTheFirstChapterWhileTheSecondTocPageIsPending() = runBlocking {
        val secondStarted = CompletableDeferred<Unit>()
        val finishSecond = CompletableDeferred<Unit>()
        val url = MpiaSite.bookUrl("12345")
        fun listing(id: Int) =
            """{"ok":true,"total":2,"list":[{"id":"$id","order":$id,"title":"Episode $id","url":"${MpiaSite.viewerUrl("12345", id.toString())}"}]}"""
        val web = ScriptedWeb {
            ScriptedPage { call ->
                when {
                    call.startsWith("mpia.detail") -> """{"ok":true,"title":"Book"}"""
                    call.matches(Regex("mpia\\.chapters.*[, ]1\\)")) -> listing(1)
                    call.startsWith("mpia.chapters") -> {
                        secondStarted.complete(Unit)
                        finishSecond.await()
                        listing(2)
                    }
                    call == "mpia.readerState()" -> """{"ready":true}"""
                    call.startsWith("mpia.extract(") -> """{"contentHtml":"<p>Chapter one text</p>"}"""
                    else -> error("Unexpected call: $call")
                }
            }
        }
        val ctx = context(web, source = SourceId.MPIA)
        suspend fun read(op: String, number: Int) = BuiltInReader.handle(op, buildJsonObject {
            put("url", url)
            put("reader", "mpia-paged-fixture")
            put(if (op == "book") "page" else "index", number)
        }, ctx)
        assertTrue(read("book", 0).contains("\"more\":true"))
        val later = async { read("book", 1) }
        try {
            withTimeout(3_000) {
                secondStarted.await()
                assertTrue(read("chapter", 0).contains("Chapter one text"))
                assertFalse(later.isCompleted)
            }
        } finally {
            finishSecond.complete(Unit)
        }
        assertTrue(later.await().contains("Episode 2"))
        assertTrue(web.pages.all { it.closed })
    }
}
