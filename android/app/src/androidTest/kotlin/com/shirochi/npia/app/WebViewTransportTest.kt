package com.shirochi.npia.app

import android.app.Application
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.webengine.AndroidWebEngine
import kotlinx.coroutines.delay
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.util.concurrent.atomic.AtomicInteger

/**
 * `exposeTransport` on a real web view: the page's `window.__npia_gm_xmlhttp_request` reaches the
 * app and gets its answer back, other frames do not get the function, and a request that does not
 * carry the page's token is ignored. Needs no network.
 */
@RunWith(AndroidJUnit4::class)
class WebViewTransportTest {
    private val blank = "data:text/html,%3Chtml%3E%3Cbody%3Ehello%3C%2Fbody%3E%3C%2Fhtml%3E"

    @Test
    fun aPageRequestGoesToTheAppAndTheAnswerComesBack() = runBlocking {
        val app = ApplicationProvider.getApplicationContext<Application>()
        val page = AndroidWebEngine(app).newPage("transport-test")
        try {
            val calls = AtomicInteger()
            val seen = mutableListOf<String>()
            page.exposeTransport { request ->
                calls.incrementAndGet()
                seen += request
                """{"status":200,"responseHeaders":"x-a: 1\r\n","finalUrl":"https://x.test/","responseText":"café \"quoted\""}"""
            }
            page.load(blank, WebLoadState.LOAD, 30)

            val reply = page.evaluateJson(
                "return JSON.stringify(await window.__npia_gm_xmlhttp_request({url: 'https://x.test/a', method: 'GET', headers: {a: 'b'}, timeout: 5000}));",
            )
            assertTrue(reply, reply.contains("\\\"status\\\":200"))
            assertTrue(reply, reply.contains("x-a: 1"))
            assertEquals(1, calls.get())
            assertTrue(seen[0], seen[0].contains("\"url\":\"https://x.test/a\""))

            // Several requests at once come back to the right callers.
            val many = page.evaluateJson(
                "const r = await Promise.all([1, 2, 3].map((n) => window.__npia_gm_xmlhttp_request({n}))); return r.map((x) => x.status);",
            )
            assertEquals("[200,200,200]", many)
            assertEquals(4, calls.get())

            // A frame of another document gets no transport.
            val frame = page.evaluateJson(
                """
                const f = document.createElement('iframe');
                f.srcdoc = '<html><body>frame</body></html>';
                document.body.appendChild(f);
                await new Promise((resolve) => { f.onload = resolve; });
                return typeof f.contentWindow.__npia_gm_xmlhttp_request;
                """.trimIndent(),
            )
            assertEquals("\"undefined\"", frame)

            // Without the page's token the bridge ignores the call.
            page.evaluateJson("window.__npiaBridge.request('wrong-token', '99', '{}'); return true;")
            delay(500)
            assertEquals(4, calls.get())
        } finally {
            page.close()
        }
    }

    @Test
    fun theTransportIsThereAgainAfterANavigation() = runBlocking {
        val app = ApplicationProvider.getApplicationContext<Application>()
        val page = AndroidWebEngine(app).newPage("transport-test")
        try {
            page.exposeTransport { """{"status":204}""" }
            page.load(blank, WebLoadState.LOAD, 30)
            page.load("$blank#second", WebLoadState.LOAD, 30)
            page.load("data:text/html,%3Chtml%3E%3Cbody%3Eother%3C%2Fbody%3E%3C%2Fhtml%3E", WebLoadState.LOAD, 30)
            assertEquals("\"function\"", page.evaluateJson("return typeof window.__npia_gm_xmlhttp_request;"))
            assertEquals("204", page.evaluateJson("return (await window.__npia_gm_xmlhttp_request({})).status;"))
        } finally {
            page.close()
        }
    }
}
