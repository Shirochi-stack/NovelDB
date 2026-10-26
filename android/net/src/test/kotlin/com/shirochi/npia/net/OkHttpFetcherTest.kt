package com.shirochi.npia.net

import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

class OkHttpFetcherTest {
    private lateinit var server: MockWebServer

    @Before
    fun setUp() {
        server = MockWebServer().apply { start() }
    }

    @After
    fun tearDown() {
        server.shutdown()
    }

    @Test
    fun retriesServerErrorsThenReturnsBody() = runTest {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setBody("hello").addHeader("X-Test", "1"))
        val fetcher = OkHttpFetcher(maxRetries = 2)

        val response = fetcher.get(server.url("/a").toString(), mapOf("Referer" to "https://example.com/"))

        assertEquals(200, response.status)
        assertEquals("hello", response.text)
        assertEquals("1", response.headers["x-test"])
        assertEquals(2, server.requestCount)
        server.takeRequest()
        assertEquals("https://example.com/", server.takeRequest().getHeader("Referer"))
    }

    @Test
    fun returnsErrorStatusAfterRetriesRunOut() = runTest {
        repeat(2) { server.enqueue(MockResponse().setResponseCode(500)) }
        val response = OkHttpFetcher(maxRetries = 1).get(server.url("/b").toString())
        assertEquals(500, response.status)
    }

    @Test
    fun postsBody() = runTest {
        server.enqueue(MockResponse().setBody("ok"))
        OkHttpFetcher().post(server.url("/c").toString(), "x=1".toByteArray())
        val request = server.takeRequest()
        assertEquals("POST", request.method)
        assertEquals("x=1", request.body.readUtf8())
    }

    @Test
    fun explicitCookieHeaderIsMergedWithJarCookies() = runTest {
        server.enqueue(MockResponse().setBody("ok"))
        val jar = object : okhttp3.CookieJar {
            override fun saveFromResponse(url: okhttp3.HttpUrl, cookies: List<okhttp3.Cookie>) {}
            override fun loadForRequest(url: okhttp3.HttpUrl) = listOf(
                okhttp3.Cookie.Builder().name("sid").value("jar").hostOnlyDomain(url.host).build(),
                okhttp3.Cookie.Builder().name("token").value("old").hostOnlyDomain(url.host).build(),
            )
        }
        OkHttpFetcher(cookieJar = jar).get(server.url("/d").toString(), mapOf("Cookie" to "token=new; extra=1"))
        assertEquals("token=new; extra=1; sid=jar", server.takeRequest().getHeader("Cookie"))
    }

    @Test
    fun mergeCookieHeadersKeepsExplicitValues() {
        assertEquals("a=1", OkHttpFetcher.mergeCookieHeaders(null, "a=1"))
        assertEquals("a=1; b=2", OkHttpFetcher.mergeCookieHeaders("a=0; b=2", "a=1"))
    }
}
