package com.shirochi.npia.app

import com.shirochi.npia.app.model.CartItem
import com.shirochi.npia.app.model.LinkCart
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.NpiaSettings
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.UnavailableWebEngine
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.flow.first
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.net.URL
import java.net.URLDecoder
import java.util.Collections

/** Plain JVM tests for the tag search job that runs behind the Tag search screen. */
class TagSearchManagerTest {
    private class FakeHttp(private val handler: suspend (HttpRequest) -> String) : HttpFetching {
        val requests: MutableList<HttpRequest> = Collections.synchronizedList(mutableListOf())

        override suspend fun send(request: HttpRequest): HttpResponse {
            requests += request
            return HttpResponse(request.url, 200, emptyMap(), handler(request).encodeToByteArray())
        }
    }

    private fun params(request: HttpRequest): Map<String, String> =
        (URL(request.url).query ?: "").split("&").filter { it.isNotEmpty() }.associate { pair ->
            val parts = pair.split("=", limit = 2)
            URLDecoder.decode(parts[0], "UTF-8") to URLDecoder.decode(parts.getOrElse(1) { "" }, "UTF-8")
        }

    private fun settings(npia: NpiaSettings) = AppSettings(shared = SharedSettings(threads = 1, intervalMin = 0.0, intervalMax = 0.0), npia = npia)

    private fun manager(http: HttpFetching, settings: AppSettings): TagSearchManager {
        val secrets = InMemorySecretStore()
        return TagSearchManager(
            scope = CoroutineScope(Dispatchers.Default),
            settings = { settings },
            contextFor = { logger -> SourceContext(http, UnavailableWebEngine, secrets, settings, logger) },
        )
    }

    private fun awaitEnd(manager: TagSearchManager) = runBlocking { withTimeout(30_000) { manager.running.first { !it } } }

    private fun idsReply(vararg ids: Int) = """{"status":200,"list":[${ids.joinToString(",") { """{"novel_no":$it}""" }}]}"""

    @Test
    fun selectedAndCustomTagsEachBecomeAGroupAndAreAnded() {
        val http = FakeHttp { request ->
            when (params(request)["search_val"]) {
                "TS" -> idsReply(1, 2, 3, 4)
                "백합" -> idsReply(4, 5)
                else -> idsReply()
            }
        }
        val search = manager(http, settings(NpiaSettings(tagAgeFilter = "", tagSelectedTags = listOf("TS"), tagCustomTags = "백합")))

        assertNull(search.retrieve())
        awaitEnd(search)

        assertEquals("Found 1 novel(s).", search.status.value)
        val result = search.result.value!!
        assertEquals(TagSearchManager.Kind.TAGS, result.kind)
        assertEquals(listOf("4"), result.ids)
        assertEquals("tag_TS+Yuri.txt", result.fileName)
        assertEquals("4\n", result.fileText)
        val lines = search.log.value.lines
        assertTrue(lines.toString(), lines.contains("Searching with 2 group(s), 1 thread(s):"))
        assertTrue(lines.toString(), lines.contains("Tag search complete: 1 novel(s) found."))
        // The sign-in step reports through the same log.
        assertTrue(lines.toString(), lines.contains("npia: no saved login; only free chapters can be read."))
        assertEquals(search.log.value.lines.size, search.log.value.total)
    }

    @Test
    fun queryBuilderGroupsWinOverTheSelection() {
        val http = FakeHttp { idsReply(1) }
        val npia = NpiaSettings(tagSelectedTags = listOf("TS"), tagQueryGroups = listOf(listOf("백합", "GL")))
        val search = manager(http, settings(npia))
        assertNull(search.retrieve())
        awaitEnd(search)
        assertEquals(listOf("백합", "GL"), http.requests.map { params(it)["search_val"] })
        assertEquals("tag_Yuri_OR_GL.txt", search.result.value!!.fileName)
    }

    @Test
    fun theAdultTagIsLeftOutWhenTheAgeFilterIsSet() {
        val http = FakeHttp { idsReply(1) }
        val npia = NpiaSettings(tagAgeFilter = "15", tagSelectedTags = listOf("TS", "19금"))
        val search = manager(http, settings(npia))
        assertNull(search.retrieve())
        awaitEnd(search)
        assertEquals(listOf("TS"), http.requests.map { params(it)["search_val"] })
        assertEquals("15", params(http.requests.first())["novel_age"])
    }

    @Test
    fun nothingToSearchGivesAMessageAndStartsNothing() {
        val http = FakeHttp { error("no request expected") }
        val search = manager(http, settings(NpiaSettings(tagSelectedTags = emptyList(), tagCustomTags = " , ")))
        assertEquals("Add at least one group to the query builder, or select tags directly.", search.retrieve())
        assertFalse(search.running.value)
        assertTrue(http.requests.isEmpty())
        assertNull(search.result.value)
    }

    @Test
    fun top100ReportsEachAudienceAndCanBeStopped() {
        val http = FakeHttp { request ->
            // Every ranking after the first hangs, so a stop always finds the search waiting.
            if (!request.url.endsWith("/today/view/adult/plus")) delay(60_000)
            """<a href="/novel/2">x</a><a href="/novel/7">y</a><a href="/novel/2">z</a>"""
        }
        val search = manager(http, settings(NpiaSettings(tagAgeFilter = "19")))
        search.retrieveTop100()
        runBlocking { withTimeout(30_000) { search.log.first { "  daily adult: 2 novels" in it.lines } } }
        search.stop()
        awaitEnd(search)

        assertEquals("Top 100: 2 unique novels\n  adult: daily: 2\n(stopped)", search.status.value)
        val result = search.result.value!!
        assertEquals(TagSearchManager.Kind.TOP100, result.kind)
        assertEquals(listOf("2", "7"), result.ids)
        assertEquals("top100_rankings.txt", result.fileName)
        assertEquals("# Daily Adult Top 100\n2\n7\n\n", result.fileText)
        assertEquals("  Stopped by user.", search.log.value.lines.last())
        assertTrue(search.log.value.lines.contains("  Stop requested..."))
    }

    @Test
    fun aFailureShowsAsTheStatusAndInTheLog() {
        val http = FakeHttp { "this is not json" }
        val search = manager(http, settings(NpiaSettings(tagSelectedTags = listOf("TS"))))
        assertNull(search.retrieve())
        awaitEnd(search)
        assertTrue(search.status.value, search.status.value.startsWith("Error: invalid JSON response"))
        assertTrue(search.log.value.lines.last().startsWith("Tag retrieval failed: invalid JSON response"))
        assertNull(search.result.value)
        assertFalse(search.running.value)
    }

    @Test
    fun theLogKeepsTheNewestLines() {
        val search = manager(FakeHttp { "" }, settings(NpiaSettings()))
        repeat(TagSearchManager.MAX_LOG_LINES + 5) { search.append("line $it") }
        val log = search.log.value
        assertEquals(TagSearchManager.MAX_LOG_LINES, log.lines.size)
        assertEquals("line 5", log.lines.first())
        assertEquals("line ${TagSearchManager.MAX_LOG_LINES + 4}", log.lines.last())
        assertEquals(TagSearchManager.MAX_LOG_LINES + 5, log.total)
        search.clearLog()
        assertTrue(search.log.value.lines.isEmpty())
    }

    @Test
    fun addingManyNovelsToTheCartSavesOnceAndSkipsRepeats() {
        val file = File.createTempFile("cart", ".json").apply { deleteOnExit() }
        val cart = LinkCart(file)
        cart.add(CartItem("https://example.com/novel/1", "npia 1", "npia"))
        cart.addAll(listOf(1, 2, 3, 2).map { CartItem("https://example.com/novel/$it", "npia $it", "npia") })
        assertEquals(listOf("1", "2", "3"), cart.items.value.map { it.url.substringAfterLast('/') })
        assertEquals("npia 2", cart.items.value[1].title)
        // Persisted.
        assertEquals(3, LinkCart(file).items.value.size)
        assertNotNull(cart.items.value.firstOrNull { it.source == "npia" })
    }
}
