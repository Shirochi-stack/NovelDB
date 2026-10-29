package com.shirochi.npia.net

import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import kotlinx.coroutines.test.runTest
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.SocketPolicy
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
    fun logsRetriesAndFailedRequestsWithoutTheQuery() = runTest {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setResponseCode(429))
        server.enqueue(MockResponse().setResponseCode(404))
        val lines = mutableListOf<Pair<LogLevel, String>>()
        val fetcher = OkHttpFetcher(maxRetries = 3, logger = JobLogging { level, message -> lines += level to message })

        assertEquals(404, fetcher.get(server.url("/novel/1?token=secret").toString()).status)

        val host = "${server.hostName}/novel/1"
        assertEquals(LogLevel.WARNING to true, lines[0].first to lines[0].second.startsWith("HTTP 503 from $host; retry 1 of 3 in "))
        assertEquals(LogLevel.WARNING to true, lines[1].first to lines[1].second.startsWith("HTTP 429 from $host; retry 2 of 3 in "))
        // A plain not-found is a detail, not a warning, and says how many retries came before it.
        assertEquals(LogLevel.DEBUG to "HTTP 404 from $host after 2 retries", lines[2])
        assertEquals(3, lines.size)
        assertEquals(false, lines.any { it.second.contains("secret") || it.second.contains("token") })
    }

    @Test
    fun logsNetworkErrorsAndGivingUp() = runTest {
        repeat(2) { server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AT_START)) }
        val lines = mutableListOf<Pair<LogLevel, String>>()
        val fetcher = OkHttpFetcher(maxRetries = 1, logger = JobLogging { level, message -> lines += level to message })

        val failure = runCatching { fetcher.get(server.url("/gone").toString()) }.exceptionOrNull()

        assertEquals(true, failure is java.io.IOException)
        assertEquals(2, lines.size)
        assertEquals(true, lines.all { it.first == LogLevel.WARNING })
        assertEquals(true, lines[0].second.contains("/gone; retry 1 of 1 in "))
        assertEquals(true, lines[1].second.endsWith("/gone; giving up after 1 retries"))
    }

    @Test
    fun staysQuietWithoutALogger() = runTest {
        server.enqueue(MockResponse().setResponseCode(503))
        server.enqueue(MockResponse().setBody("ok"))
        assertEquals("ok", OkHttpFetcher(maxRetries = 1).get(server.url("/q").toString()).text)
    }

    @Test
    fun aRefusedPlainHttpAddressIsAskedForOverHttps() = runTest {
        val seen = mutableListOf<String>()
        val client = okhttp3.OkHttpClient.Builder().addInterceptor { chain ->
            val request = chain.request()
            seen += request.url.toString()
            if (request.url.scheme == "http") throw java.net.UnknownServiceException("CLEARTEXT communication not permitted by network security policy")
            okhttp3.Response.Builder().request(request).protocol(okhttp3.Protocol.HTTP_1_1).code(200).message("OK")
                .body(okhttp3.ResponseBody.Companion.run { "cover".toResponseBody() }).build()
        }.build()
        val lines = mutableListOf<String>()
        val fetcher = OkHttpFetcher(client = client, maxRetries = 10, logger = JobLogging { _, message -> lines += message })
        val response = fetcher.get("http://rs.example.test/cover.jpg?x=1")
        assertEquals("cover", response.text)
        assertEquals(listOf("http://rs.example.test/cover.jpg?x=1", "https://rs.example.test/cover.jpg?x=1"), seen)
        assertEquals(emptyList<String>(), lines) // no retries with pauses
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
