package com.shirochi.npia.webengine

import com.shirochi.npia.core.WebEngineException
import kotlinx.coroutines.async
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.yield
import okhttp3.Cookie
import okhttp3.HttpUrl.Companion.toHttpUrl
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test

class WebViewCookieJarTest {
    private class FakeBackend : CookieBackend {
        val stored = mutableListOf<Pair<String, String>>()
        var header: String? = null
        var flushes = 0

        override fun cookieHeader(url: String): String? = header
        override fun setCookie(url: String, setCookie: String) {
            stored += url to setCookie
        }
        override fun flush() {
            flushes++
        }
    }

    @Test
    fun responseCookiesAreWrittenAsSetCookieHeaders() {
        val backend = FakeBackend()
        val url = "https://example.com/login".toHttpUrl()
        val cookie = Cookie.Builder().name("sid").value("abc").domain("example.com").path("/").httpOnly().build()

        WebViewCookieJar(backend).saveFromResponse(url, listOf(cookie))

        assertEquals(1, backend.stored.size)
        assertEquals("https://example.com/login", backend.stored[0].first)
        assertTrue(backend.stored[0].second.startsWith("sid=abc"))
        assertTrue(backend.stored[0].second.contains("domain=example.com"))
        assertEquals(1, backend.flushes)
    }

    @Test
    fun emptyResponseDoesNotFlush() {
        val backend = FakeBackend()
        WebViewCookieJar(backend).saveFromResponse("https://example.com/".toHttpUrl(), emptyList())
        assertEquals(0, backend.flushes)
    }

    @Test
    fun requestCookiesComeFromTheSharedHeader() {
        val backend = FakeBackend().apply { header = "sid=abc; theme=dark ; =bad; novalue" }
        val cookies = WebViewCookieJar(backend).loadForRequest("https://example.com/api".toHttpUrl())
        assertEquals(listOf("sid" to "abc", "theme" to "dark"), cookies.map { it.name to it.value })
        assertTrue(cookies.all { it.domain == "example.com" && it.hostOnly })
    }

    @Test
    fun missingHeaderGivesNoCookies() {
        assertTrue(WebViewCookieJar.parseCookieHeader(null, "https://example.com/".toHttpUrl()).isEmpty())
        assertTrue(WebViewCookieJar.parseCookieHeader("  ", "https://example.com/".toHttpUrl()).isEmpty())
    }

    @Test
    fun sharedCookieStoreReadsAndWritesTheBackend() {
        val backend = FakeBackend().apply { header = "USERKEY=abc; last_login=basic" }
        val store = BackendSharedCookies(backend)
        assertEquals(mapOf("USERKEY" to "abc", "last_login" to "basic"), store.cookies("https://example.com/"))
        store.setCookie("https://example.com/", "a", "1", ".example.com")
        assertEquals("https://example.com/" to "a=1; path=/; domain=.example.com", backend.stored.single())
        assertEquals(1, backend.flushes)
    }

    @Test
    fun valuesWithEqualsSignsSurvive() {
        val cookies = WebViewCookieJar.parseCookieHeader("token=a=b==", "https://example.com/".toHttpUrl())
        assertEquals("a=b==", cookies.single().value)
    }
}

class ScriptBridgeTest {
    @Test
    fun wrapperQuotesTheIdAndDeliversThroughBridgeOrStore() {
        val script = ScriptBridge.wrap("id-'1\"", "return 1;")
        assertTrue(script.contains("\"id-'1\\\"\""))
        assertTrue(script.contains("window.__npiaBridge.resolve(id, ok, payload)"))
        assertTrue(script.contains("window.__npiaResults"))
        assertTrue(script.contains("(async () => { return 1; })()"))
    }

    @Test
    fun polledResultsDecode() {
        assertEquals("[1,2]", ScriptBridge.decodePolled("""{"ok":true,"payload":"[1,2]"}""")?.getOrThrow())
        val failure = ScriptBridge.decodePolled("""{"ok":false,"payload":"boom"}""")
        assertTrue(failure!!.exceptionOrNull() is WebEngineException)
        assertNull(ScriptBridge.decodePolled("null"))
        assertNull(ScriptBridge.decodePolled(null))
        assertNull(ScriptBridge.decodePolled("not json"))
    }

    @Test
    fun evaluateResultStringsDecode() {
        assertEquals("complete", ScriptBridge.decodeString("\"complete\""))
        assertEquals("<p>\"x\"</p>", ScriptBridge.decodeString("\"<p>\\\"x\\\"</p>\""))
        assertNull(ScriptBridge.decodeString("null"))
        assertNull(ScriptBridge.decodeString("{}"))
    }
}

class WebLoginTest {
    private class RecordingPresenter(override val canPresent: Boolean = true) : WebLoginPresenting {
        val presented = mutableListOf<WebLoginSession>()
        val dismissed = mutableListOf<WebLoginSession>()
        override fun present(session: WebLoginSession) {
            presented += session
        }
        override fun dismiss(session: WebLoginSession) {
            dismissed += session
        }
    }

    @Test
    fun loginWithoutPresenterIsUnavailable() = runTest {
        val presenter = RecordingPresenter(canPresent = false)
        try {
            WebLoginSession("https://example.com/login", "external").run(presenter)
            fail("expected failure")
        } catch (e: WebEngineException) {
            assertTrue(presenter.presented.isEmpty())
        }
    }

    @Test
    fun loginReturnsWhenUserFinishes() = runTest {
        val presenter = RecordingPresenter()
        val session = WebLoginSession("https://example.com/login", "external")
        val run = async { session.run(presenter) }
        yield()
        assertSame(session, presenter.presented.single())
        session.update("https://example.com/home", "Home", loading = false)
        assertEquals("https://example.com/home", session.currentUrl.value)
        assertEquals("Home", session.title.value)
        session.finish()
        run.await()
        assertSame(session, presenter.dismissed.single())
        assertTrue(session.isDone)
    }

    @Test
    fun loginCancelThrows() = runTest {
        val presenter = RecordingPresenter()
        val session = WebLoginSession("https://example.com/login", "external")
        val run = async { runCatching { session.run(presenter) } }
        yield()
        session.cancel()
        assertTrue(run.await().exceptionOrNull() is WebEngineException)
        assertSame(session, presenter.dismissed.single())
    }

    @Test
    fun coordinatorReplacesAndClearsSessions() {
        val coordinator = WebLoginCoordinator
        coordinator.isAttached = true
        try {
            val first = WebLoginSession("https://a.example/", "external")
            val second = WebLoginSession("https://b.example/", "external")
            coordinator.present(first)
            coordinator.present(second)
            assertTrue("the replaced session is cancelled", first.isDone)
            assertSame(second, coordinator.session.value)
            coordinator.dismiss(first)
            assertSame(second, coordinator.session.value)
            coordinator.dismiss(second)
            assertNull(coordinator.session.value)
            assertFalse(second.isDone)
        } finally {
            coordinator.isAttached = false
        }
    }
}

class TransportScriptTest {
    @Test
    fun scriptDefinesTheTransportInTheTopFrameOnlyAndCarriesTheToken() {
        val script = ScriptBridge.transportScript("tok123")
        assertTrue(script.contains("window.top !== window"))
        assertTrue(script.contains("window.${ScriptBridge.TRANSPORT_NAME} = function"))
        assertTrue(script.contains("__npiaBridge.request(token, id,"))
        // The token appears as a literal once, and the reply hook is named after it.
        assertEquals(1, Regex("\"tok123\"").findAll(script).count())
        assertTrue(script.contains("\"__npia_reply_tok123\""))
    }

    @Test
    fun replyScriptEscapesTheReplyAsAStringLiteral() {
        val script = ScriptBridge.replyScript("tok", "7", "{\"a\":\"line\nbreak \\\" quote\"}")
        assertTrue(script.startsWith("(function () { const f = window[\"__npia_reply_tok\"];"))
        assertTrue(script.contains("f(\"7\", \"{\\\"a\\\":\\\"line\\nbreak"))
        assertFalse(script.contains("\n"))
    }
}
