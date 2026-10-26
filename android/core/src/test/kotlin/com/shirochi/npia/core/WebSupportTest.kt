package com.shirochi.npia.core

import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.delay
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.util.concurrent.atomic.AtomicInteger

private class ScriptedPage(private val answer: (String) -> String = { "null" }) : WebPageDriving {
    val initScripts = mutableListOf<String>()
    var closed = false

    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {}
    override suspend fun evaluateJson(functionBody: String): String = answer(functionBody)
    override suspend fun addInitScript(source: String) {
        initScripts += source
    }
    override suspend fun currentUrl(): String? = null
    override suspend fun html(): String = ""
    override suspend fun close() {
        closed = true
    }
}

private class CountingEngine : WebEngineProviding {
    val created = AtomicInteger()
    val pages = mutableListOf<ScriptedPage>()

    override suspend fun newPage(profile: String): WebPageDriving {
        created.incrementAndGet()
        return ScriptedPage().also { synchronized(pages) { pages += it } }
    }

    override suspend fun presentLogin(url: String, profile: String) = throw WebEngineException("no")
}

class NetworkCaptureTest {
    @Test
    fun initScriptEmbedsPatternsAndStore() {
        val script = NetworkCapture.initScript(listOf("/api/", "it's"), maxBody = 10)
        assertTrue(script.contains("""const patterns = ["/api/","it's"];"""))
        assertTrue(script.contains("window.__npiaCaptured"))
        assertTrue(script.contains(".slice(0, 10)"))
        assertTrue(script.contains("XMLHttpRequest.prototype.send"))
    }

    @Test
    fun startCaptureInstallsInitScript() = runTest {
        val page = ScriptedPage()
        page.startCapture(listOf("chapter"))
        assertTrue(page.initScripts.single().contains("\"chapter\""))
    }

    @Test
    fun capturedResponsesDecodeAndWaitPicksNewestMatch() = runTest {
        val page = ScriptedPage {
            """[{"url":"https://x/api/a","status":200,"body":"one"},
                {"url":"https://x/other","method":"POST","status":201,"body":"two"},
                {"url":"https://x/api/b","status":200,"body":"{\"n\":3}"}]"""
        }
        val all = page.capturedResponses()
        assertEquals(3, all.size)
        assertEquals("GET", all[0].method)
        assertEquals("POST", all[1].method)
        val match = page.waitForResponse("/api/", timeoutSeconds = 1.0)
        assertEquals("https://x/api/b", match?.url)
        assertEquals(mapOf("n" to 3), match!!.json<Map<String, Int>>())
    }

    @Test
    fun waitForResponseTimesOut() = runTest {
        val page = ScriptedPage { "[]" }
        assertNull(page.waitForResponse("/api/", timeoutSeconds = 0.0))
    }

    @Test
    fun waitForPollsUntilTrue() = runTest {
        var calls = 0
        val page = ScriptedPage { body ->
            assertTrue(body.contains("!!(window.ready)"))
            calls++
            if (calls >= 3) "true" else "false"
        }
        assertTrue(page.waitFor("window.ready", timeoutSeconds = 30.0, intervalSeconds = 0.0))
        assertEquals(3, calls)
    }

    @Test
    fun waitForTreatsScriptErrorsAsFalse() = runTest {
        val page = ScriptedPage { throw WebEngineException("boom") }
        assertFalse(page.waitFor("x", timeoutSeconds = 0.0))
    }

    @Test
    fun evaluateDecodesJson() = runTest {
        val page = ScriptedPage { """{"title":"T","count":2}""" }
        val value: Map<String, kotlinx.serialization.json.JsonElement> = page.evaluate("return 1;")
        assertEquals("\"T\"", value["title"].toString())
    }

    @Test
    fun withPageAlwaysCloses() = runTest {
        val engine = CountingEngine()
        assertEquals(5, engine.withPage("external") { 5 })
        try {
            engine.withPage("external") { throw IllegalStateException("x") }
            fail("expected failure")
        } catch (e: IllegalStateException) {
            // expected
        }
        assertEquals(2, engine.pages.size)
        assertTrue(engine.pages.all { it.closed })
    }
}

class WebPagePoolTest {
    @Test
    fun poolHonoursSizeAndReusesPages() = runTest {
        val engine = CountingEngine()
        val prepared = AtomicInteger()
        val pool = WebPagePool(engine, WebProfile.EXTERNAL, size = 2) { prepared.incrementAndGet() }
        val active = AtomicInteger()
        val peak = AtomicInteger()

        (1..8).map {
            async {
                pool.withPage {
                    val now = active.incrementAndGet()
                    peak.accumulateAndGet(now) { a, b -> maxOf(a, b) }
                    delay(10)
                    active.decrementAndGet()
                }
            }
        }.awaitAll()

        assertEquals(2, engine.created.get())
        assertEquals(2, pool.pageCount())
        assertEquals(2, prepared.get())
        assertTrue(peak.get() <= 2)
        assertTrue(engine.pages.none { it.closed })

        pool.closeAll()
        assertTrue(engine.pages.all { it.closed })
    }

    @Test
    fun sizeIsAtLeastOne() = runTest {
        val engine = CountingEngine()
        val pool = WebPagePool(engine, WebProfile.EXTERNAL, size = 0)
        pool.withPage { }
        pool.withPage { }
        assertEquals(1, engine.created.get())
    }

    @Test
    fun releaseAfterCloseClosesThePage() = runTest {
        val engine = CountingEngine()
        val pool = WebPagePool(engine, WebProfile.NPIA, size = 1)
        val page = pool.acquire()
        pool.closeAll()
        pool.release(page)
        assertTrue((page as ScriptedPage).closed)
        try {
            pool.acquire()
            fail("closed pool must refuse")
        } catch (e: kotlinx.coroutines.CancellationException) {
            // expected
        }
    }

    @Test
    fun failedPageCreationFreesTheSlot() = runTest {
        var fail = true
        val engine = object : WebEngineProviding {
            override suspend fun newPage(profile: String): WebPageDriving {
                if (fail) {
                    fail = false
                    throw WebEngineException("no web view")
                }
                return ScriptedPage()
            }
            override suspend fun presentLogin(url: String, profile: String) = Unit
        }
        val pool = WebPagePool(engine, WebProfile.EXTERNAL, size = 1)
        try {
            pool.acquire()
            fail("expected failure")
        } catch (e: WebEngineException) {
            // expected
        }
        assertEquals(0, pool.pageCount())
        pool.withPage { }
        assertEquals(1, pool.pageCount())
    }
}
