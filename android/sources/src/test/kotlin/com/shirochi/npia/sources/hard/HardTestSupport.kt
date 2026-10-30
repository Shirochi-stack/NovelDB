package com.shirochi.npia.sources.hard

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SharedCookieStore
import com.shirochi.npia.core.SharedOverrides
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebLoadTimeoutException
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.sources.http.CollectingLogger
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import java.util.Collections

/**
 * A web view page that answers calls to `hard-sources.js` entry points with canned JSON. `respond` gets
 * the entry point and its arguments, for example `qdn.pageState()` or `rbooks.discover({...})`, and returns
 * the JSON text (null means JSON null). A `tasks.start` call runs `respond("<path>(<args>)")` and the matching
 * `tasks.poll` hands the answer back.
 */
internal class ScriptedPage(
    val name: String = "page",
    val respond: suspend ScriptedPage.(call: String) -> String?,
) : WebPageDriving {
    val loads: MutableList<String> = Collections.synchronizedList(mutableListOf())
    val calls: MutableList<String> = Collections.synchronizedList(mutableListOf())
    val initScripts: MutableList<String> = Collections.synchronizedList(mutableListOf())
    var userAgent: String? = null
    var viewport: Pair<Int, Int>? = null
    var closed = false
    var url: String? = null

    /** URLs that fail to load, mapped to what to throw. */
    val loadFailures = mutableMapOf<String, () -> Throwable>()
    /** What the page shows after loading `url`; the default is the URL itself. */
    var redirect: (String) -> String = { it }

    private var ticket = 0
    private val finished = mutableMapOf<String, String?>()

    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
        loads += url
        this.url = redirect(url)
        loadFailures[url]?.let { throw it() }
    }

    override suspend fun evaluateJson(functionBody: String): String {
        if (functionBody.contains("window.__npiaHard && window.__npiaHard.version")) return "true"
        if (functionBody.startsWith("(0, eval)(")) return "true"
        if (functionBody.contains("document.title")) return Json.encodeToString(String.serializer(), titleText)
        val tasks = Regex("window\\.__npiaHard\\.tasks\\.(start|poll)\\((.*)\\);\\s*$", RegexOption.DOT_MATCHES_ALL).find(functionBody)
        if (tasks != null) {
            val (kind, argument) = tasks.destructured
            if (kind == "start") {
                val path = Regex("^\"([^\"]+)\", (.*)$", RegexOption.DOT_MATCHES_ALL).find(argument)!!
                val call = "${path.groupValues[1]}(${path.groupValues[2].removePrefix("[").removeSuffix("]")})"
                calls += call
                val id = "t${++ticket}"
                finished[id] = respond(call)
                return "{\"ticket\":\"$id\"}"
            }
            val id = argument.trim('"')
            val json = finished.remove(id) ?: "null"
            return "{\"state\":\"done\",\"json\":${Json.encodeToString(String.serializer(), json)}}"
        }
        val call = Regex("window\\.__npiaHard\\.(.+?);\\s*$", RegexOption.DOT_MATCHES_ALL).find(functionBody)?.groupValues?.get(1)
            ?: return "null"
        calls += call
        return respond(call) ?: "null"
    }

    var titleText: String = ""

    override suspend fun addInitScript(source: String) {
        initScripts += source
    }

    override suspend fun currentUrl(): String? = url

    override suspend fun html(): String = ""

    override suspend fun emulateDesktop(userAgent: String?, width: Int, height: Int) {
        this.userAgent = userAgent
        viewport = width to height
    }

    override suspend fun close() {
        closed = true
    }
}

/** Hands out pages from `make`, remembering them so tests can look at every page a job opened. */
internal class ScriptedWeb(private val make: (index: Int) -> ScriptedPage) : WebEngineProviding {
    val pages: MutableList<ScriptedPage> = Collections.synchronizedList(mutableListOf())

    override suspend fun newPage(profile: String): WebPageDriving = make(pages.size).also { pages += it }

    var logins = 0
    var loginFailure: Throwable? = null

    override suspend fun presentLogin(url: String, profile: String) {
        logins += 1
        loginFailure?.let { throw it }
    }
}

internal class FakeCookies(initial: Map<String, String> = emptyMap()) : SharedCookieStore {
    val values = initial.toMutableMap()
    val removed = mutableListOf<String>()

    override fun cookies(url: String): Map<String, String> = values.toMap()

    override fun setCookie(url: String, name: String, value: String, domain: String?) {
        values[name] = value
    }

    override fun remove(url: String, name: String) {
        values.remove(name)
        removed += name
    }
}

/** Never reached: pages do the work. Records requests so tests can see image downloads. */
internal class RecordingHttp(private val reply: (HttpRequest) -> HttpResponse = { HttpResponse(it.url, 404, emptyMap(), ByteArray(0)) }) : HttpFetching {
    val requests: MutableList<HttpRequest> = Collections.synchronizedList(mutableListOf())

    override suspend fun send(request: HttpRequest): HttpResponse {
        requests += request
        return reply(request)
    }
}

internal fun context(
    web: WebEngineProviding,
    secrets: SecretStore = InMemorySecretStore(),
    settings: AppSettings = AppSettings(),
    http: HttpFetching = RecordingHttp(),
    logger: CollectingLogger = CollectingLogger(),
    source: SourceId? = null,
    onChapter: (com.shirochi.npia.core.ChapterResult) -> Unit = {},
): SourceContext {
    val effective = if (source == null) settings else settings.copy(
        // No pause between chapters in tests.
        overrides = settings.overrides + (source.raw to (settings.overrides[source.raw] ?: SharedOverrides()).copy(intervalMin = 0.0, intervalMax = 0.0)),
    )
    return SourceContext(http, web, secrets, effective, logger, onChapter = onChapter)
}

/** A load that times out, as a slow site's does. */
internal fun timeout(url: String): () -> Throwable = { WebLoadTimeoutException(url) }

internal fun failure(message: String): () -> Throwable = { WebEngineException(message) }
