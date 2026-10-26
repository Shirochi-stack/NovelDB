package com.shirochi.npia.sources.reader

import com.dokar.quickjs.QuickJs
import com.dokar.quickjs.binding.define
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpStatusException
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineDispatcher
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExecutorCoroutineDispatcher
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.asCoroutineDispatcher
import kotlinx.coroutines.cancel
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.doubleOrNull
import kotlinx.serialization.json.intOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import kotlinx.serialization.json.put
import java.net.URI
import java.nio.charset.Charset
import java.security.MessageDigest
import java.util.Base64
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.locks.ReentrantLock
import kotlin.concurrent.withLock

/**
 * One QuickJS context running the desktop rules bundle.
 *
 * Mirrors the desktop's injection order (`gm_stubs.js`, `rules-lib.js`, `bridge.js`) with
 * `reader_env.js`, `reader_dom.js` and `reader_web.js` standing in for the browser page, and
 * `reader_host.js` exposing the calls Kotlin makes. Rule modules capture the page document when
 * they first load, so use one runtime per book.
 *
 * QuickJS (through quickjs-kt, which bundles native libraries for the JVM and every Android ABI)
 * runs on one private thread with a large stack. Requests go through `HttpFetching`.
 */
internal class ReaderRuntime private constructor(
    private val http: HttpFetching,
    private val logger: JobLogging,
    private val verbose: Boolean,
) {
    class ScriptError(message: String) : Exception(message) {
        companion object {
            fun loadFailed(message: String) = ScriptError("The reader rules failed to load: $message")
            fun timedOut(call: String) = ScriptError("The reader rules did not finish $call in time.")
            val closed get() = ScriptError("The reader runtime was closed.")
        }
    }

    private val executor = Executors.newSingleThreadExecutor { runnable ->
        Thread(null, runnable, "npia-reader-js", THREAD_STACK).apply { isDaemon = true }
    }
    private val dispatcher: ExecutorCoroutineDispatcher = executor.asCoroutineDispatcher()
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private lateinit var js: QuickJs
    private val calls = ConcurrentHashMap<Int, CompletableDeferred<String>>()
    private val requests = ConcurrentHashMap<Int, Job>()
    private val cookies = ReaderCookies()

    @Volatile
    private var closed = false

    /** Cancels in-flight requests and fails pending calls. The runtime is unusable afterwards. */
    fun close() {
        if (closed) return
        closed = true
        scope.cancel()
        requests.clear()
        val pending = calls.values.toList()
        calls.clear()
        for (deferred in pending) deferred.completeExceptionally(ScriptError.closed)
        executor.execute {
            if (::js.isInitialized) QuickJsInstances.release(js)
            executor.shutdown()
        }
    }

    // Calls

    /** Calls `__npia_host[name](...args)` and returns its result (JSON text unless it returned a string). */
    suspend fun call(name: String, arguments: List<Any?>, timeoutSeconds: Long = 300): String {
        if (closed) throw ScriptError.closed
        val token = tokens.incrementAndGet()
        val deferred = CompletableDeferred<String>()
        calls[token] = deferred
        try {
            val argsJson = toJson(arguments).toString()
            onThread {
                eval("__npia_call($token, ${jsLiteral(name)}, ${jsLiteral(argsJson)})", "call.js")
            }
            return withTimeoutOrNull(timeoutSeconds * 1000) { deferred.await() }
                ?: throw ScriptError.timedOut(name)
        } finally {
            calls.remove(token)
        }
    }

    /** Like `call`, but blocks the caller. Only for calls that settle without network or timers. */
    fun callSync(name: String, arguments: List<Any?>, timeoutSeconds: Long = 60): String =
        runBlocking { call(name, arguments, timeoutSeconds) }

    /** Evaluates a script on the runtime thread and returns `String(result)` (tests and diagnostics). */
    fun evaluate(script: String): String? = runBlocking {
        onThread { eval("String((0, eval)(${jsLiteral(script)}))", "evaluate.js") as? String }
    }

    private suspend fun <T> onThread(block: suspend () -> T): T {
        if (closed) throw ScriptError.closed
        return withContext(dispatcher) {
            if (closed) throw ScriptError.closed
            block()
        }
    }

    /**
     * Runtime thread only. QuickJS reports uncaught errors and unhandled promise rejections as
     * exceptions here; like the JavaScriptCore handler on iOS, they are logged, and calls settle
     * through `__npia_native.settle`.
     */
    private suspend fun eval(code: String, filename: String): Any? = try {
        QuickJsInstances.lock.withLock { js.evaluate<Any?>(code, filename) }
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        lastException = e.message ?: e.toString()
        logger.log(if (loading) LogLevel.WARNING else LogLevel.DEBUG, "[Reader] ${lastException?.take(300)}")
        null
    }

    private var lastException: String? = null
    private var loading = true

    /** Calls a global JavaScript function with JSON-encodable arguments, from any thread. */
    private fun invoke(function: String, vararg arguments: Any?) {
        if (closed) return
        scope.launch(dispatcher) {
            if (closed) return@launch
            val args = arguments.joinToString(", ") { toJson(it).toString().let(::escapeLineSeparators) }
            eval("$function($args)", "invoke.js")
        }
    }

    // Setup

    private suspend fun setUp() {
        js = QuickJsInstances.create(dispatcher)
        QuickJsInstances.lock.withLock {
            js.maxStackSize = JS_STACK
            defineNative()
        }
        val failures = mutableListOf<String>()
        for (script in ReaderScripts.load()) {
            lastException = null
            eval(script.source, script.name)
            lastException?.let { failures += "${script.name}: $it" }
        }
        // rules-lib.js finishes initialising in promise jobs after its script returns.
        val ready = eval("window.__ND_READY === true && window.__ND_BRIDGE_READY === true", "ready.js")
        loading = false
        if (ready != true) {
            throw ScriptError.loadFailed(failures.firstOrNull() ?: "rules-lib.js did not initialise")
        }
    }

    private fun defineNative() {
        js.define("__npia_native") {
            function<Any?>("log") { args ->
                val level = when (args.getOrNull(0) as? String) {
                    "debug" -> LogLevel.DEBUG
                    "warning" -> LogLevel.WARNING
                    "error" -> LogLevel.ERROR
                    else -> LogLevel.INFO
                }
                if (level != LogLevel.DEBUG || verbose) logger.log(level, "[Reader] ${args.getOrNull(1)}")
                null
            }
            function<Any?>("timer") { args ->
                val id = (args.getOrNull(0) as? Number)?.toInt()
                val milliseconds = (args.getOrNull(1) as? Number)?.toDouble() ?: 0.0
                if (id != null && !closed) {
                    scope.launch(dispatcher) {
                        delay(milliseconds.toLong().coerceAtLeast(0))
                        if (!closed) eval("__npia_fireTimer($id)", "timer.js")
                    }
                }
                null
            }
            function<Any?>("decode") { args ->
                val bytes = runCatching { Base64.getDecoder().decode(args.getOrNull(0) as? String ?: "") }.getOrNull()
                if (bytes == null) "" else ReaderText.decode(bytes, args.getOrNull(1) as? String ?: "")
            }
            function<Any?>("digest") { args ->
                val bytes = runCatching { Base64.getDecoder().decode(args.getOrNull(1) as? String ?: "") }.getOrNull()
                bytes?.let { ReaderText.digest(args.getOrNull(0) as? String ?: "", it) }
                    ?.let { Base64.getEncoder().encodeToString(it) }
            }
            function<Any?>("cookies") { args -> cookies.header(args.getOrNull(0) as? String ?: "") }
            function<Any?>("setCookie") { args ->
                cookies.set(args.getOrNull(0) as? String ?: "", args.getOrNull(1) as? String ?: "")
                null
            }
            function<Any?>("fetch") { args ->
                (args.getOrNull(0) as? String)?.let(::startRequest)
                null
            }
            function<Any?>("settle") { args ->
                val token = (args.getOrNull(0) as? Number)?.toInt()
                val value = args.getOrNull(2)?.toString() ?: ""
                val deferred = token?.let { calls[it] }
                if (deferred != null) {
                    if (args.getOrNull(1) == true) deferred.complete(value)
                    else deferred.completeExceptionally(ScriptError(value))
                }
                null
            }
        }
    }

    // Requests

    /** Runtime thread: called from JavaScript. */
    private fun startRequest(json: String) {
        val spec = runCatching { Json.parseToJsonElement(json).jsonObject }.getOrNull() ?: return
        val id = spec["id"]?.jsonPrimitive?.intOrNull ?: return
        val url = spec["url"]?.jsonPrimitive?.contentOrNull ?: ""
        if (!(url.startsWith("http://", true) || url.startsWith("https://", true))) {
            invoke("__npia_fetchDone", id, encode(buildJsonObject { put("error", "invalid URL $url") }))
            return
        }
        val headers = LinkedHashMap<String, String>()
        (spec["headers"] as? JsonObject)?.forEach { (key, value) ->
            (value as? JsonPrimitive)?.contentOrNull?.let { headers[key] = it }
        }
        if (headers.keys.none { it.equals("cookie", true) }) {
            cookies.header(url).takeIf { it.isNotEmpty() }?.let { headers["cookie"] = it }
        }
        val body = (spec["body"] as? JsonPrimitive)?.contentOrNull
            ?.let { runCatching { Base64.getDecoder().decode(it) }.getOrNull() }
        val timeout = (spec["timeout"] as? JsonPrimitive)?.doubleOrNull ?: 30000.0
        val request = HttpRequest(
            url = url,
            method = (spec["method"] as? JsonPrimitive)?.contentOrNull ?: "GET",
            headers = headers,
            body = body,
            timeoutSeconds = (timeout / 1000).toLong().coerceAtLeast(1),
        )
        if (verbose) logger.log(LogLevel.DEBUG, "[Reader] ${request.method} $url")
        val job = scope.launch {
            val payload = try {
                val response = http.send(request)
                buildJsonObject {
                    put("status", response.status)
                    put("url", response.url)
                    put("headers", JsonObject(response.headers.mapKeys { it.key.lowercase() }.mapValues { JsonPrimitive(it.value) }))
                    put("body", Base64.getEncoder().encodeToString(response.body))
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: HttpStatusException) {
                buildJsonObject {
                    put("status", e.status)
                    put("url", e.url)
                    put("headers", JsonObject(emptyMap()))
                    put("body", "")
                }
            } catch (e: Exception) {
                buildJsonObject { put("error", e.message ?: e.toString()) }
            }
            requests.remove(id)
            invoke("__npia_fetchDone", id, encode(payload))
        }
        requests[id] = job
    }

    companion object {
        private const val THREAD_STACK = 32L shl 20
        private const val JS_STACK = 16L shl 20
        private val tokens = AtomicInteger()

        /** Loads the scripts on a fresh context; throws `ScriptError` when they fail. */
        suspend fun create(http: HttpFetching, logger: JobLogging = NullLogger, verbose: Boolean = false): ReaderRuntime {
            val runtime = ReaderRuntime(http, logger, verbose)
            try {
                withContext(runtime.dispatcher) { runtime.setUp() }
            } catch (e: Throwable) {
                runtime.close()
                if (e is ScriptError || e is CancellationException) throw e
                throw ScriptError.loadFailed(e.message ?: e.toString())
            }
            return runtime
        }

        fun createBlocking(http: HttpFetching, logger: JobLogging = NullLogger): ReaderRuntime =
            runBlocking { create(http, logger) }

        private fun encode(payload: JsonObject) = payload.toString()

        /** A JavaScript string literal for any text. */
        fun jsLiteral(value: String): String = escapeLineSeparators(JsonPrimitive(value).toString())

        // U+2028/U+2029 are line terminators in older JavaScript string literals.
        private fun escapeLineSeparators(json: String) =
            json.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")

        fun toJson(value: Any?): JsonElement = when (value) {
            null -> JsonNull
            is JsonElement -> value
            is String -> JsonPrimitive(value)
            is Boolean -> JsonPrimitive(value)
            is Number -> JsonPrimitive(value)
            is Map<*, *> -> JsonObject(value.entries.associate { (k, v) -> k.toString() to toJson(v) })
            is Iterable<*> -> JsonArray(value.map(::toJson))
            is Array<*> -> JsonArray(value.map(::toJson))
            else -> JsonPrimitive(value.toString())
        }
    }
}

/**
 * Every QuickJS instance in the process, behind one lock.
 *
 * quickjs-kt 1.0.0-alpha13 keeps the JavaVM pointer and its JNI class and method caches in
 * process-wide statics and clears them whenever any instance is released, which leaves the other
 * live instances unable to call back into Kotlin. (Fixed upstream in 1.0.x, which needs Kotlin
 * 2.3.) So all native calls are serialized here, and after a release that leaves instances alive,
 * a fresh idle instance restores the VM pointer; the class caches refill on demand.
 */
internal object QuickJsInstances {
    val lock = ReentrantLock()
    private var live = 0
    private var keeper: QuickJs? = null

    fun create(dispatcher: CoroutineDispatcher): QuickJs = lock.withLock {
        QuickJs.create(dispatcher).also { live++ }
    }

    fun release(js: QuickJs) = lock.withLock {
        if (js.isClosed) return@withLock
        runCatching { js.close() }
        live--
        keeper?.let { runCatching { it.close() } }
        keeper = null
        if (live > 0) keeper = runCatching { QuickJs.create(Dispatchers.Default) }.getOrNull()
    }
}

/** The bundled scripts, read once. */
internal object ReaderScripts {
    class Script(val name: String, val source: String)

    /** Injection order; see `reader_env.js`. */
    val order = listOf(
        "reader_env.js", "reader_dom.js", "reader_web.js",
        "gm_stubs.js", "reader_gm_patch.js", "rules-lib.js", "bridge.js",
        "reader_host.js",
    )

    private val cached: Result<List<Script>> by lazy {
        runCatching {
            order.map { name ->
                val stream = ReaderScripts::class.java.getResourceAsStream(name)
                    ?: throw ReaderRuntime.ScriptError.loadFailed("missing resource $name")
                Script(name, stream.use { it.readBytes().decodeToString() })
            }
        }
    }

    fun load(): List<Script> = cached.getOrThrow()
}

/** Charset decoding and digests for the JavaScript side. */
internal object ReaderText {
    fun charset(label: String): Charset? {
        val name = label.trim().lowercase()
        // Decode with the supersets browsers use for these labels.
        val candidates = when (name) {
            "", "utf-8", "utf8", "unicode-1-1-utf-8" -> listOf("UTF-8")
            "gbk", "gb2312", "gb_2312-80", "x-gbk", "gb18030", "chinese", "csgb2312", "iso-ir-58" -> listOf("GB18030", "GBK")
            "big5", "big5-hkscs", "x-x-big5", "cn-big5" -> listOf("Big5-HKSCS", "Big5")
            "euc-kr", "ks_c_5601-1987", "korean", "cp949", "windows-949", "ksc5601" -> listOf("x-windows-949", "MS949", "EUC-KR")
            "shift_jis", "shift-jis", "sjis", "ms_kanji", "windows-31j", "cp932", "x-sjis" -> listOf("windows-31j", "MS932", "Shift_JIS")
            "iso-8859-1", "latin1", "us-ascii", "ascii", "windows-1252", "cp1252" -> listOf("windows-1252", "ISO-8859-1")
            else -> listOf(name)
        }
        for (candidate in candidates) {
            val charset = runCatching { Charset.forName(candidate) }.getOrNull()
            if (charset != null) return charset
        }
        return null
    }

    /** Lossy, like a browser: malformed bytes become U+FFFD. */
    fun decode(data: ByteArray, label: String): String = String(data, charset(label) ?: Charsets.UTF_8)

    fun digest(algorithm: String, data: ByteArray): ByteArray? {
        val name = when (algorithm.uppercase().replace("-", "")) {
            "SHA1" -> "SHA-1"
            "SHA256" -> "SHA-256"
            "SHA384" -> "SHA-384"
            "SHA512" -> "SHA-512"
            else -> return null
        }
        return MessageDigest.getInstance(name).digest(data)
    }
}

/**
 * Cookies the rules set through `document.cookie`, kept for the runtime's lifetime and sent with
 * the rules' own requests. On iOS this was the shared cookie storage; here sign-in cookies stay
 * with `HttpFetching`, whose cookie jar adds them to every request.
 */
internal class ReaderCookies {
    private class Cookie(val name: String, val value: String, val domain: String, val path: String, val hostOnly: Boolean)

    private val cookies = mutableListOf<Cookie>()

    @Synchronized
    fun header(url: String): String {
        val uri = runCatching { URI(url) }.getOrNull() ?: return ""
        val host = uri.host?.lowercase() ?: return ""
        val path = uri.rawPath?.ifEmpty { "/" } ?: "/"
        return cookies
            .filter { c ->
                (if (c.hostOnly) host == c.domain else host == c.domain || host.endsWith(".${c.domain}")) &&
                    path.startsWith(c.path)
            }
            .joinToString("; ") { "${it.name}=${it.value}" }
    }

    @Synchronized
    fun set(url: String, header: String) {
        val uri = runCatching { URI(url) }.getOrNull() ?: return
        val host = uri.host?.lowercase() ?: return
        val parts = header.split(";").map { it.trim() }
        val pair = parts.firstOrNull()?.takeIf { it.contains("=") } ?: return
        val name = pair.substringBefore("=").trim()
        if (name.isEmpty()) return
        val value = pair.substringAfter("=").trim()
        var domain = host
        var hostOnly = true
        var path = "/"
        var expired = false
        for (attribute in parts.drop(1)) {
            val key = attribute.substringBefore("=").trim().lowercase()
            val attrValue = attribute.substringAfter("=", "").trim()
            when (key) {
                "domain" -> attrValue.trimStart('.').lowercase().takeIf { it.isNotEmpty() }?.let {
                    if (host == it || host.endsWith(".$it")) {
                        domain = it
                        hostOnly = false
                    } else {
                        return
                    }
                }
                "path" -> if (attrValue.startsWith("/")) path = attrValue
                "max-age" -> if ((attrValue.toLongOrNull() ?: 1) <= 0) expired = true
                "expires" -> if (isPast(attrValue)) expired = true
            }
        }
        cookies.removeAll { it.name == name && it.domain == domain && it.path == path }
        if (!expired) cookies += Cookie(name, value, domain, path, hostOnly)
    }

    private fun isPast(value: String): Boolean = runCatching {
        java.time.ZonedDateTime.parse(value, java.time.format.DateTimeFormatter.RFC_1123_DATE_TIME)
            .toInstant().isBefore(java.time.Instant.now())
    }.getOrDefault(false)
}
