package com.shirochi.npia.core

import kotlinx.serialization.json.Json

// HTTP

data class HttpRequest(
    val url: String,
    val method: String = "GET",
    val headers: Map<String, String> = emptyMap(),
    val body: ByteArray? = null,
    /** The least this request waits for the server; 0 leaves it to the app's request timeout, which also wins when longer. */
    val timeoutSeconds: Long = 0,
)

class HttpResponse(
    val url: String,
    val status: Int,
    /** Header names are lower-cased. */
    val headers: Map<String, String>,
    val body: ByteArray,
) {
    val text: String get() = body.decodeToString()

    inline fun <reified T> json(format: Json = Json { ignoreUnknownKeys = true }): T = format.decodeFromString(text)
}

class HttpStatusException(val status: Int, val url: String) : Exception("HTTP $status for $url")

/** Implemented by `:net` (OkHttp); tests use stubs. */
interface HttpFetching {
    suspend fun send(request: HttpRequest): HttpResponse

    suspend fun get(url: String, headers: Map<String, String> = emptyMap()): HttpResponse =
        send(HttpRequest(url, headers = headers))

    suspend fun post(url: String, body: ByteArray, headers: Map<String, String> = emptyMap()): HttpResponse =
        send(HttpRequest(url, method = "POST", headers = headers, body = body))
}

// Web engine

enum class WebLoadState { DOM_CONTENT_LOADED, LOAD }

/** One browser tab. Replaces a Playwright `Page` from the desktop scraper; `:webengine` uses Android WebView. */
interface WebPageDriving {
    /** Loads `url`, waiting at least `timeoutSeconds` (0: the app's page load wait, which also wins when longer). */
    suspend fun load(url: String, until: WebLoadState = WebLoadState.LOAD, timeoutSeconds: Long = 0)

    /** The app's page load wait: `waitFor` and `waitForResponse` on this page last at least this long. */
    val minimumWaitSeconds: Double get() = 0.0

    /** Runs an async JavaScript function body and returns its result as a JSON string. */
    suspend fun evaluateJson(functionBody: String): String

    /** Registers a script that runs at document start (fetch/XHR hooks). */
    suspend fun addInitScript(source: String)

    suspend fun currentUrl(): String?

    suspend fun html(): String

    /**
     * Defines `window.__npia_gm_xmlhttp_request(details)` in the page's top frame, now and on every
     * later navigation: an async function that sends the `details` object (as JSON) to `handler` in
     * the app and resolves to the JSON object it returns. Scripts use it for requests the page itself
     * may not make (CORS). The default leaves the function undefined.
     */
    suspend fun exposeTransport(handler: suspend (String) -> String) {}

    /**
     * Presents the page as a desktop browser: `userAgent` (null keeps the web view's own) and a layout
     * viewport of `width` x `height` CSS pixels. Call before `load`; it applies to every later load of
     * this page. Sites that serve a phone layout (or no reader at all) to a phone need it. The default
     * ignores the request.
     */
    suspend fun emulateDesktop(userAgent: String?, width: Int, height: Int) {}

    suspend fun close()
}

interface WebEngineProviding {
    /** Pages from the same profile share cookies with each other and with `HttpFetching`. */
    suspend fun newPage(profile: String): WebPageDriving

    /** Shows a visible web view so the user can sign in; returns when they finish. */
    suspend fun presentLogin(url: String, profile: String)
}

open class WebEngineException(message: String) : Exception(message)

/** A page that was not ready within its time limit; a second try often is (nothing was cached the first time). */
class WebLoadTimeoutException(val url: String) : WebEngineException("Timed out loading $url")

object UnavailableWebEngine : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = throw WebEngineException("No web engine in this context")
    override suspend fun presentLogin(url: String, profile: String) = throw WebEngineException("No web engine in this context")
}

// Images

class ProcessedImage(val data: ByteArray, val fileExtension: String)

/** Implemented by `:images` (Android Bitmap/ImageDecoder); the default passes bytes through. */
interface ImageProcessing {
    fun process(data: ByteArray, isCover: Boolean = false): ProcessedImage

    /**
     * Re-encodes an already processed image at a lower quality for `recompressTargetMb`, keeping
     * its container so file names stay valid. The default returns the bytes unchanged.
     */
    fun reencode(data: ByteArray, fileExtension: String, quality: Int): ByteArray = data
}

object PassthroughImages : ImageProcessing {
    override fun process(data: ByteArray, isCover: Boolean) = ProcessedImage(data, sniffImageExtension(data))
}

fun sniffImageExtension(data: ByteArray): String {
    fun at(i: Int) = if (i < data.size) data[i].toInt() and 0xFF else -1
    return when {
        at(0) == 0xFF && at(1) == 0xD8 && at(2) == 0xFF -> "jpg"
        at(0) == 0x89 && at(1) == 0x50 && at(2) == 0x4E && at(3) == 0x47 -> "png"
        at(0) == 0x47 && at(1) == 0x49 && at(2) == 0x46 && at(3) == 0x38 -> "gif"
        at(0) == 0x52 && at(1) == 0x49 && at(2) == 0x46 && at(3) == 0x46 &&
            at(8) == 0x57 && at(9) == 0x45 && at(10) == 0x42 && at(11) == 0x50 -> "webp"
        at(4) == 0x66 && at(5) == 0x74 && at(6) == 0x79 && at(7) == 0x70 -> "avif"
        else -> "bin"
    }
}

// Logging

enum class LogLevel { DEBUG, INFO, WARNING, ERROR }

fun interface JobLogging {
    fun log(level: LogLevel, message: String)
}

val NullLogger = JobLogging { _, _ -> }
