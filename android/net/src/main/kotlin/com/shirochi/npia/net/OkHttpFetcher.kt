package com.shirochi.npia.net

import com.shirochi.npia.core.DEFAULT_TIMEOUT_SECONDS
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import com.shirochi.npia.core.SharedSettings
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import okhttp3.Call
import okhttp3.Callback
import okhttp3.CookieJar
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response
import java.io.IOException
import java.net.UnknownServiceException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

/** Spaces requests out by a random delay in `[min, max]` seconds, like the desktop interval settings. */
class RequestPacer(min: Double, max: Double) {
    private val minDelay = min.coerceAtLeast(0.0)
    private val maxDelay = max.coerceAtLeast(minDelay)
    private val mutex = Mutex()
    private var nextAllowed = 0L

    suspend fun await() {
        val wait = mutex.withLock {
            val now = System.currentTimeMillis()
            val start = maxOf(now, nextAllowed)
            val gap = if (maxDelay > minDelay) Random.nextDouble(minDelay, maxDelay) else minDelay
            nextAllowed = start + (gap * 1000).toLong()
            start - now
        }
        if (wait > 0) delay(wait)
    }
}

/**
 * `HttpFetching` over OkHttp. The app passes a `CookieJar` backed by Android's `CookieManager`
 * so requests share login cookies with the WebView engine.
 */
class OkHttpFetcher(
    cookieJar: CookieJar = CookieJar.NO_COOKIES,
    private val pacer: RequestPacer? = null,
    private val maxRetries: Int = 3,
    private val userAgent: String = DEFAULT_USER_AGENT,
    client: OkHttpClient? = null,
    /** Where retries and failed requests are reported (host and path only, never the query). */
    private val logger: JobLogging = NullLogger,
    /** The app's request timeout: every request waits at least this long for the server. */
    private val minTimeoutSeconds: Long = DEFAULT_TIMEOUT_SECONDS.toLong(),
) : HttpFetching {
    /**
     * No request pacing: like the desktop scraper, the rate limit is a pause each chapter worker
     * takes after a chapter (`SharedSettings.pauseBetweenChapters`), so threads and images run
     * in parallel.
     */
    constructor(settings: SharedSettings, cookieJar: CookieJar = CookieJar.NO_COOKIES, logger: JobLogging = NullLogger) :
        this(cookieJar, null, settings.maxRetries, logger = logger, minTimeoutSeconds = settings.requestTimeoutSeconds.toLong())

    private val client = client ?: OkHttpClient.Builder()
        .cookieJar(cookieJar)
        .connectTimeout(20, TimeUnit.SECONDS)
        // OkHttp replaces a caller's `Cookie` header with the jar's cookies; keep both instead.
        .addInterceptor { chain ->
            val request = chain.request()
            val explicit = request.header("Cookie")
            if (explicit == null) {
                chain.proceed(request)
            } else {
                chain.proceed(request.newBuilder().tag(ExplicitCookie::class.java, ExplicitCookie(explicit)).build())
            }
        }
        .addNetworkInterceptor { chain ->
            val request = chain.request()
            val explicit = request.tag(ExplicitCookie::class.java)
            if (explicit == null) {
                chain.proceed(request)
            } else {
                chain.proceed(request.newBuilder().header("Cookie", mergeCookieHeaders(request.header("Cookie"), explicit.value)).build())
            }
        }
        .build()

    override suspend fun send(request: HttpRequest): HttpResponse {
        val builder = Request.Builder().url(request.url).header("User-Agent", userAgent)
        request.headers.forEach { (name, value) -> builder.header(name, value) }
        builder.method(request.method, request.body?.toRequestBody() ?: if (request.method == "GET" || request.method == "HEAD") null else ByteArray(0).toRequestBody())
        var okRequest = builder.build()
        // The timeout is how long the server may stay silent, as on iOS and the desktop (OkHttp's own
        // default is 10 s); a whole request, a large image on a slow line included, gets 10 minutes.
        val timeout = timeoutFor(request.timeoutSeconds, minTimeoutSeconds)
        val call = client.newBuilder()
            .readTimeout(timeout, TimeUnit.SECONDS)
            .writeTimeout(timeout, TimeUnit.SECONDS)
            .callTimeout(maxOf(timeout, MAX_CALL_SECONDS), TimeUnit.SECONDS)
            .build()

        val where = "${okRequest.url.host}${okRequest.url.encodedPath}"
        var attempt = 0
        while (true) {
            pacer?.await()
            var wait = 0L
            try {
                call.newCall(okRequest).await().use { response ->
                    if (isRetryable(response.code) && attempt < maxRetries) {
                        attempt++
                        wait = backoffMillis(attempt)
                        logger.log(LogLevel.WARNING, "HTTP ${response.code} from $where; retry $attempt of $maxRetries in ${seconds(wait)} s")
                    } else {
                        val body = withContext(Dispatchers.IO) { response.body?.bytes() ?: ByteArray(0) }
                        val headers = response.headers.toMultimap().mapKeys { it.key.lowercase() }.mapValues { it.value.joinToString(", ") }
                        if (response.code !in 200..299 && response.code !in request.quietStatuses) {
                            val level = if (isRetryable(response.code)) LogLevel.WARNING else LogLevel.DEBUG
                            val tried = if (attempt > 0) " after $attempt retries" else ""
                            logger.log(level, "HTTP ${response.code} from $where$tried")
                        }
                        return HttpResponse(response.request.url.toString(), response.code, headers, body)
                    }
                }
            } catch (e: IOException) {
                // Android allows the app no plain http (cleartext policy). Sites that still link http://
                // covers and images (sfc's, for one) serve them over https too, so ask again that way.
                if (e is UnknownServiceException && okRequest.url.scheme == "http") {
                    okRequest = okRequest.newBuilder().url(okRequest.url.newBuilder().scheme("https").build()).build()
                    continue
                }
                val reason = "${e.javaClass.simpleName}: ${e.message ?: "no details"}"
                // A request the system refuses outright fails the same way every time.
                if (attempt >= maxRetries || e is UnknownServiceException) {
                    logger.log(LogLevel.WARNING, "$reason for $where; giving up after $attempt retries")
                    throw e
                }
                attempt++
                wait = backoffMillis(attempt)
                logger.log(LogLevel.WARNING, "$reason for $where; retry $attempt of $maxRetries in ${seconds(wait)} s")
            }
            delay(wait)
        }
    }

    companion object {
        const val DEFAULT_USER_AGENT =
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"

        fun isRetryable(status: Int) = status == 429 || status in 500..599

        /** The longest a whole request may take, however steadily the server sends. */
        const val MAX_CALL_SECONDS = 600L

        /** What a request asks for or the app's request timeout, whichever is longer. */
        fun timeoutFor(requested: Long, minimum: Long): Long = maxOf(requested, minimum).coerceAtLeast(1)

        private fun seconds(millis: Long): String = String.format(java.util.Locale.US, "%.1f", millis / 1000.0)

        /** Joins jar cookies with an explicit `Cookie` header; explicit values win by name. */
        fun mergeCookieHeaders(jar: String?, explicit: String): String {
            fun pairs(header: String?) = header.orEmpty().split(';').map { it.trim() }.filter { it.isNotEmpty() }
            val explicitPairs = pairs(explicit)
            val explicitNames = explicitPairs.map { it.substringBefore('=').trim() }.toSet()
            val kept = pairs(jar).filter { it.substringBefore('=').trim() !in explicitNames }
            return (explicitPairs + kept).joinToString("; ")
        }

        fun backoffMillis(attempt: Int): Long =
            (min(30.0, 2.0.pow(attempt - 1)) * 1000).toLong() + Random.nextLong(0, 500)
    }
}

private class ExplicitCookie(val value: String)

private suspend fun Call.await(): Response = suspendCancellableCoroutine { continuation ->
    enqueue(object : Callback {
        override fun onResponse(call: Call, response: Response) = continuation.resume(response)
        override fun onFailure(call: Call, e: IOException) {
            if (!continuation.isCancelled) continuation.resumeWithException(e)
        }
    })
    continuation.invokeOnCancellation { runCatching { cancel() } }
}
