package com.shirochi.npia.net

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
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
) : HttpFetching {
    constructor(settings: SharedSettings, cookieJar: CookieJar = CookieJar.NO_COOKIES) :
        this(cookieJar, RequestPacer(settings.intervalMin, settings.intervalMax), settings.maxRetries)

    private val client = client ?: OkHttpClient.Builder()
        .cookieJar(cookieJar)
        .connectTimeout(20, TimeUnit.SECONDS)
        .build()

    override suspend fun send(request: HttpRequest): HttpResponse {
        val builder = Request.Builder().url(request.url).header("User-Agent", userAgent)
        request.headers.forEach { (name, value) -> builder.header(name, value) }
        builder.method(request.method, request.body?.toRequestBody() ?: if (request.method == "GET" || request.method == "HEAD") null else ByteArray(0).toRequestBody())
        val okRequest = builder.build()
        val call = client.newBuilder().callTimeout(request.timeoutSeconds, TimeUnit.SECONDS).build()

        var attempt = 0
        while (true) {
            pacer?.await()
            try {
                call.newCall(okRequest).await().use { response ->
                    if (isRetryable(response.code) && attempt < maxRetries) {
                        attempt++
                    } else {
                        val body = withContext(Dispatchers.IO) { response.body?.bytes() ?: ByteArray(0) }
                        val headers = response.headers.toMultimap().mapKeys { it.key.lowercase() }.mapValues { it.value.joinToString(", ") }
                        return HttpResponse(response.request.url.toString(), response.code, headers, body)
                    }
                }
            } catch (e: IOException) {
                if (attempt >= maxRetries) throw e
                attempt++
            }
            delay(backoffMillis(attempt))
        }
    }

    companion object {
        const val DEFAULT_USER_AGENT =
            "Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36"

        fun isRetryable(status: Int) = status == 429 || status in 500..599

        fun backoffMillis(attempt: Int): Long =
            (min(30.0, 2.0.pow(attempt - 1)) * 1000).toLong() + Random.nextLong(0, 500)
    }
}

private suspend fun Call.await(): Response = suspendCancellableCoroutine { continuation ->
    enqueue(object : Callback {
        override fun onResponse(call: Call, response: Response) = continuation.resume(response)
        override fun onFailure(call: Call, e: IOException) {
            if (!continuation.isCancelled) continuation.resumeWithException(e)
        }
    })
    continuation.invokeOnCancellation { runCatching { cancel() } }
}
