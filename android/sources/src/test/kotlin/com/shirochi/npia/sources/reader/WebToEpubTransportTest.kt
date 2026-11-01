package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.SharedCookieStore
import com.shirochi.npia.core.SharedCookies
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.IOException
import java.util.Base64

class WebToEpubTransportTest {
    private class Recording(val reply: (HttpRequest) -> HttpResponse) : HttpFetching {
        val requests = mutableListOf<HttpRequest>()

        override suspend fun send(request: HttpRequest): HttpResponse {
            requests += request
            return reply(request)
        }
    }

    private fun parse(json: String): JsonObject = Json.parseToJsonElement(json).jsonObject

    @Test
    fun aGetGoesThroughTheAppClientAndComesBackInTheBundlesShape() = runTest {
        val http = Recording { request ->
            HttpResponse(
                url = "https://novel.example/final",
                status = 200,
                headers = mapOf("content-type" to "text/html; charset=utf-8", "x-a" to "1"),
                body = "<p>héllo</p>".toByteArray(),
            )
        }
        val reply = parse(
            WebToEpubTransport.handle(
                """{"url":"https://novel.example/page","method":"get","headers":{"Accept":"text/html","Referer":"https://novel.example/"},
                   "responseType":"arraybuffer","timeout":45000}""",
                http,
            ),
        )

        val request = http.requests.single()
        assertEquals("https://novel.example/page", request.url)
        assertEquals("GET", request.method)
        assertEquals(mapOf("Accept" to "text/html", "Referer" to "https://novel.example/"), request.headers)
        assertNull(request.body)
        assertEquals(45L, request.timeoutSeconds)

        assertEquals(200, reply["status"]!!.jsonPrimitive.content.toInt())
        assertEquals("OK", reply["statusText"]!!.jsonPrimitive.content)
        assertEquals("https://novel.example/final", reply["finalUrl"]!!.jsonPrimitive.content)
        assertEquals("content-type: text/html; charset=utf-8\r\nx-a: 1\r\n", reply["responseHeaders"]!!.jsonPrimitive.content)
        assertEquals("<p>héllo</p>", String(Base64.getDecoder().decode(reply["responseBase64"]!!.jsonPrimitive.content)))
        assertNull("an arraybuffer reply carries no text", reply["responseText"])
    }

    @Test
    fun aPostSendsItsStringOrBinaryBody() = runTest {
        val http = Recording { HttpResponse(it.url, 201, emptyMap(), ByteArray(0)) }
        WebToEpubTransport.handle(
            """{"url":"https://novel.example/api","method":"POST","headers":{"Content-Type":"application/x-www-form-urlencoded"},"data":"a=1&b=2"}""",
            http,
        )
        val bytes = Base64.getEncoder().encodeToString(byteArrayOf(1, 2, 3, 0, -1))
        WebToEpubTransport.handle("""{"url":"https://novel.example/api","method":"post","dataBase64":"$bytes"}""", http)

        assertEquals("POST", http.requests[0].method)
        assertEquals("a=1&b=2", String(http.requests[0].body!!))
        assertEquals(listOf<Byte>(1, 2, 3, 0, -1), http.requests[1].body!!.toList())
        assertEquals(30L, http.requests[1].timeoutSeconds)
    }

    @Test
    fun httpErrorStatusesAreRepliesNotErrors() = runTest {
        val http = Recording { HttpResponse(it.url, 404, emptyMap(), "gone".toByteArray()) }
        val reply = parse(WebToEpubTransport.handle("""{"url":"https://novel.example/missing"}""", http))
        assertEquals(404, reply["status"]!!.jsonPrimitive.content.toInt())
        assertEquals("Not Found", reply["statusText"]!!.jsonPrimitive.content)
        assertNull(reply["error"])
    }

    @Test
    fun aCookieStringBecomesAHeaderUnlessOneIsGiven() = runTest {
        val http = Recording { HttpResponse(it.url, 200, emptyMap(), ByteArray(0)) }
        WebToEpubTransport.handle("""{"url":"https://novel.example/a","cookie":"sid=1"}""", http)
        WebToEpubTransport.handle("""{"url":"https://novel.example/b","cookie":"sid=1","headers":{"cookie":"own=2"}}""", http)
        assertEquals(mapOf("Cookie" to "sid=1"), http.requests[0].headers)
        assertEquals(mapOf("cookie" to "own=2"), http.requests[1].headers)
    }

    @Test
    fun aCookieOpReadsFromTheSharedCookieStore() = runTest {
        val previous = SharedCookies.store
        SharedCookies.store = object : SharedCookieStore {
            override fun cookies(url: String) =
                if (url.startsWith("https://sbxh9.com")) mapOf("cf_clearance" to "secret", "sid" to "1") else emptyMap()
            override fun setCookie(url: String, name: String, value: String, domain: String?) {}
        }
        try {
            val http = Recording { error("a cookie op must not touch the HTTP client") }
            val hit = parse(WebToEpubTransport.handle(
                """{"op":"cookie","action":"get","url":"https://sbxh9.com/novel/1","name":"cf_clearance"}""", http))
            val cookie = hit["cookie"]!!.jsonObject
            assertEquals("cf_clearance", cookie["name"]!!.jsonPrimitive.content)
            assertEquals("secret", cookie["value"]!!.jsonPrimitive.content)
            assertEquals("sbxh9.com", cookie["domain"]!!.jsonPrimitive.content)

            val miss = parse(WebToEpubTransport.handle(
                """{"op":"cookie","action":"get","url":"https://sbxh9.com/novel/1","name":"absent"}""", http))
            assertEquals(JsonNull, miss["cookie"])
            assertTrue(http.requests.isEmpty())
        } finally {
            SharedCookies.store = previous
        }
    }

    @Test
    fun onlyHttpUrlsAreSent() = runTest {
        val http = Recording { error("must not be called") }
        for (url in listOf("file:///data/data/app/files/settings.json", "content://x/y", "javascript:alert(1)", "ftp://x/y", "")) {
            val reply = parse(WebToEpubTransport.handle("""{"url":${JsonPrimitive(url)}}""", http))
            assertTrue(url, reply["error"]!!.jsonPrimitive.content.startsWith("invalid URL"))
        }
        assertTrue(http.requests.isEmpty())
        assertTrue(parse(WebToEpubTransport.handle("[]", http))["error"] != null)
        assertTrue(parse(WebToEpubTransport.handle("not json", http))["error"] != null)
    }

    @Test
    fun aNetworkFailureIsAnErrorReply() = runTest {
        val http = Recording { throw IOException("Unable to resolve host") }
        val reply = parse(WebToEpubTransport.handle("""{"url":"https://novel.example/a"}""", http))
        assertEquals("Unable to resolve host", reply["error"]!!.jsonPrimitive.content)
    }

    @Test
    fun cancellationPropagates() = runTest {
        val http = Recording { throw CancellationException("stop") }
        try {
            WebToEpubTransport.handle("""{"url":"https://novel.example/a"}""", http)
            fail("should throw")
        } catch (e: CancellationException) {
            assertEquals("stop", e.message)
        }
    }
}
