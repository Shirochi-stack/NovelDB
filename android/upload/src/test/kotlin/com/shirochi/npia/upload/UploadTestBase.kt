package com.shirochi.npia.upload

import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.UploadSettings
import okhttp3.Headers
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.OkHttpClient
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import org.junit.After
import org.junit.Before
import org.junit.Rule
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.concurrent.CopyOnWriteArrayList

/**
 * Every request goes to a local MockWebServer: an interceptor rewrites the real host URL and keeps
 * it in a header, so handlers and assertions see the URL the uploader asked for. No test touches a
 * live host.
 */
abstract class UploadTestBase {
    @get:Rule
    val tmp = TemporaryFolder()

    class Seen(val url: HttpUrl, val method: String, val headers: Headers, val body: ByteArray) {
        fun header(name: String): String? = headers[name]
        val text: String get() = body.toString(Charsets.UTF_8)
    }

    protected lateinit var server: MockWebServer
    protected val seen = CopyOnWriteArrayList<Seen>()
    protected val failures = CopyOnWriteArrayList<String>()

    @Volatile
    protected var handler: (Seen) -> MockResponse = { unexpected("no handler for ${it.method} ${it.url}") }

    protected val directory: File get() = tmp.root
    protected val fixedNow = 1_800_000_000_000L

    @Before
    fun startServer() {
        server = MockWebServer()
        server.dispatcher = object : Dispatcher() {
            override fun dispatch(request: RecordedRequest): MockResponse {
                val original = request.getHeader(ORIGINAL_URL)?.toHttpUrl() ?: return MockResponse().setResponseCode(400)
                val entry = Seen(original, request.method.orEmpty(), request.headers, request.body.readByteArray())
                seen += entry
                return try {
                    handler(entry)
                } catch (e: Throwable) {
                    failures += e.toString()
                    MockResponse().setResponseCode(599).setBody("test handler failed: $e")
                }
            }
        }
        server.start()
    }

    @After
    fun stopServer() {
        server.shutdown()
        if (failures.isNotEmpty()) throw AssertionError("handler failures: $failures")
    }

    /** Records an unexpected request; the test fails in tearDown. */
    protected fun unexpected(message: String): MockResponse {
        failures += message
        return MockResponse().setResponseCode(599).setBody(message)
    }

    protected val client: OkHttpClient by lazy {
        OkHttpClient.Builder()
            .retryOnConnectionFailure(false)
            .addInterceptor { chain ->
                val request = chain.request()
                val target = request.url.newBuilder().scheme("http").host(server.hostName).port(server.port).build()
                chain.proceed(request.newBuilder().url(target).header(ORIGINAL_URL, request.url.toString()).build())
            }
            .build()
    }

    protected fun makeFile(name: String = "book.epub", bytes: Int = 1_000): File =
        File(directory, name).apply { writeBytes(ByteArray(bytes) { (it % 251).toByte() }) }

    protected fun context(
        secrets: Map<SecretKey, String> = emptyMap(),
        settings: UploadSettings = UploadSettings(),
        google: GoogleOAuthConfig? = null,
    ) = UploadContext(
        secrets = InMemorySecretStore(secrets),
        settings = settings,
        client = client,
        googleOAuth = google,
        temporaryDirectory = directory,
        now = { fixedNow },
    )

    protected fun text(body: String, status: Int = 200) =
        MockResponse().setResponseCode(status).setHeader("Content-Type", "text/plain").setBody(body)

    protected fun json(body: String, status: Int = 200, headers: Map<String, String> = emptyMap()) =
        MockResponse().setResponseCode(status).setHeader("Content-Type", "application/json").setBody(body).apply {
            headers.forEach { (key, value) -> setHeader(key, value) }
        }

    protected fun reply(text: String, status: Int = 200, headers: Map<String, String> = emptyMap()) =
        HttpReply(status, headers, text.toByteArray(Charsets.UTF_8))

    companion object {
        const val ORIGINAL_URL = "X-Npia-Test-Original-Url"
    }
}

class ProgressBox {
    val all = CopyOnWriteArrayList<UploadProgress>()
    fun add(value: UploadProgress) {
        all += value
    }
    val last: UploadProgress? get() = all.lastOrNull()
}
