package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.LocalResponse
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.sources.engines.RuleEngine
import com.shirochi.npia.sources.engines.RuleEngines
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.util.Base64

/**
 * lightnovel-crawler, FanFicFare and gallery-dl: the desktop's own Python engine modules, run by
 * Pyodide in a hidden web view page (scripts/python_host; the page and Pyodide are built into this
 * module's resources by scripts/fetch_python_host.py).
 *
 * The page is served from an address of its own ([PREFIX], answered by `serveLocal`): its files from
 * the resources, `bundles/<engine>/` from the engine store (the bundle in use, downloaded update or
 * shipped copy), and `transport?r=` for the engines' requests. The engines' calls block (they are
 * Python), so the page asks synchronously and this answers through the engines' transport
 * (`WebToEpubTransport`), with the web view's cookies. The page is opened once and kept for a few
 * minutes after its last use.
 */
internal object PythonEngines {
    const val PREFIX = "https://appassets.androidplatform.net/noveldb-python/"
    private const val RESOURCES = "/com/shirochi/npia/sources/python/"
    private const val IDLE_MS = 5 * 60_000L
    private const val START_SECONDS = 90L

    private val mutex = Mutex()
    private var page: WebPageDriving? = null
    private var closer: Job? = null
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)

    /** The context of the latest call: its HTTP client and web engine answer the page's requests. */
    @Volatile private var context: SourceContext? = null

    /**
     * Starts the engines in the background (a job whose engine list reaches one): their ~15 s start
     * overlaps the engines tried before them. Not at app launch: the running engines take a lot of memory.
     */
    fun warm(context: SourceContext) {
        if (!available) return
        scope.launch {
            runCatching { page(context) }
            closeLater()
        }
    }

    fun name(engine: SiteEngine): String? = when (engine) {
        SiteEngine.LNCRAWL -> "lncrawl"
        SiteEngine.FANFICFARE -> "fanficfare"
        SiteEngine.GALLERYDL -> "gallerydl"
        SiteEngine.MANGAPEAK -> "mangapeak"
        SiteEngine.TAKO -> "tako"
        SiteEngine.CUSTOM -> "custom"
        else -> null
    }

    fun isPython(engine: SiteEngine): Boolean = name(engine) != null

    internal fun listsBundled(engine: SiteEngine, url: String): Boolean {
        val uri = runCatching { java.net.URI(url) }.getOrNull() ?: return false
        if (uri.scheme !in listOf("http", "https")) return false
        val host = uri.host?.lowercase()?.removePrefix("www.") ?: return false
        val path = uri.path.orEmpty()
        return when (engine) {
            SiteEngine.MANGAPEAK -> host == "\u0072\u006f\u006c\u0069\u0061\u0073\u0063\u0061\u006e.com" &&
                (path.startsWith("/manga/") || path.startsWith("/read/"))
            SiteEngine.TAKO -> when (host) {
                "mangadex.org" -> path.startsWith("/title/") || path.startsWith("/chapter/")
                "mangahere.cc", "mangahere.com", "newm.mangahere.cc" -> path.startsWith("/manga/")
                else -> false
            }
            else -> false
        }
    }

    /** This build has the Python engines' page (a build made without scripts/fetch_python_host.py has none). */
    val available: Boolean by lazy { PythonEngines::class.java.getResource(RESOURCES + "python-host.html") != null }

    /** The book at `url` read by `engine`; a site the engine doesn't list is refused before anything starts. */
    suspend fun openBook(engine: SiteEngine, url: String, context: SourceContext): ReaderBookPayload {
        val name = name(engine) ?: throw EngineMissException("${engine.label} are not a Python engine.")
        val version = if (engine == SiteEngine.CUSTOM) {
            // The user's engines: each one decides in the page whether it reads the link.
            val added = CustomEngines.list()
            if (added.isEmpty()) throw EngineMissException("No custom engines are added.")
            added.joinToString(", ") { it.label }
        } else if (engine == SiteEngine.MANGAPEAK || engine == SiteEngine.TAKO) {
            if (!listsBundled(engine, url)) throw EngineMissException(engine.label + " have no source for this site.")
            "built in"
        } else {
            val rule = RuleEngine.of(engine)
            val bundle = rule?.let { RuleEngines.store.current(it) }
                ?: throw EngineMissException("The ${engine.label} are missing from this build.")
            if (!bundle.knows(url)) throw EngineMissException("The ${engine.label} have no source for this site.")
            bundle.label
        }
        val payload = ReaderBookPayload.decode(call(context, "parseBook", name, url))
        payload.error?.let { error ->
            if (payload.noParser == true) throw EngineMissException("${engine.label}: $error")
            throw SourceException.Parse("${engine.label}: $error")
        }
        context.logger.log(LogLevel.INFO, "[Reader] ${engine.label} ($version), ${payload.chapters.size} chapters")
        return payload
    }

    /** One chapter's text and image blocks. */
    suspend fun chapter(engine: SiteEngine, info: ReaderBookSession.ChapterInfo, bookUrl: String, context: SourceContext): List<RuleBlock> {
        val name = name(engine) ?: throw SourceException.Parse("${engine.label} are not a Python engine.")
        val item = buildJsonObject {
            put("url", info.url)
            put("name", info.name)
            put("bookUrl", bookUrl)
        }.toString()
        val result = Json.parseToJsonElement(call(context, "parseChapter", name, item)) as? JsonObject
            ?: throw SourceException.Parse("Unexpected chapter data from the ${engine.label}.")
        result.string("error")?.let { throw SourceException.Parse("${engine.label}: $it") }
        return WebChapterBlocks.blocks(result, info.url, bookUrl)
    }

    private suspend fun call(context: SourceContext, function: String, name: String, argument: String): String {
        val page = page(context)
        try {
            return page.evaluateJson(
                "return JSON.parse(await window.NovelDBPython.$function(${ReaderRuntime.jsLiteral(name)}, ${ReaderRuntime.jsLiteral(argument)}));",
            )
        } finally {
            closeLater()
        }
    }

    private suspend fun page(context: SourceContext): WebPageDriving = mutex.withLock {
        this.context = context
        closer?.cancel()
        page?.let { return it }
        if (!available) throw EngineMissException("The Python engines are not in this build.")
        context.logger.log(LogLevel.INFO, "[Reader] Starting the Python engines…")
        val opened = context.web.newPage(ReaderWebFallback.PROFILE)
        try {
            opened.serveLocal(PREFIX, ::serve)
            opened.load(PREFIX + "python-host.html", WebLoadState.LOAD, START_SECONDS)
            val state = Json.parseToJsonElement(opened.evaluateJson("return await window.NovelDBPython.start();")) as? JsonObject
            if (state?.boolean("ready") != true) {
                throw SourceException.Parse("The Python engines did not start: ${state?.string("error") ?: "no answer"}")
            }
            // In a worker the Catalog page keeps working while they run; in the page it pauses with them.
            context.logger.log(LogLevel.INFO, if (state?.boolean("worker") == true) "[Reader] Python engines started (in the background)"
                else "[Reader] Python engines started (without a worker: the Catalog may pause while they work)")
        } catch (e: Throwable) {
            withContext(NonCancellable) { runCatching { opened.close() } }
            throw e
        }
        page = opened
        opened
    }

    private fun closeLater() {
        closer?.cancel()
        closer = scope.launch {
            delay(IDLE_MS)
            val open = mutex.withLock { page.also { page = null } }
            runCatching { open?.close() }
        }
    }

    /** The page's requests to its own address (on the web view's network thread). */
    private fun serve(path: String, query: String): LocalResponse? {
        if (path == "transport") {
            val raw = query.split('&').firstOrNull { it.startsWith("r=") }?.substring(2) ?: return null
            val json = runCatching { Base64.getUrlDecoder().decode(raw).decodeToString() }.getOrNull() ?: return null
            if (customEnginesAsked(json)) return LocalResponse("application/json", customEnginesReply().toByteArray())
            val reply = context?.let { runBlocking { WebToEpubTransport.handle(json, it.http, it.web) } }
                ?: """{"error":"the Python engines have no request context"}"""
            return LocalResponse("application/json", reply.toByteArray())
        }
        if (".." in path) return null
        if (path.startsWith("bundles/")) {
            val parts = path.split('/')
            val rule = RuleEngine.ALL.firstOrNull { it.engine.raw == parts.getOrNull(1) } ?: return null
            val bundle = RuleEngines.store.current(rule) ?: return null
            return if (parts.getOrNull(2) == "manifest.json") {
                LocalResponse("application/json", bundle.manifest.toString().toByteArray())
            } else {
                LocalResponse("application/zip", bundle.data())
            }
        }
        val bytes = PythonEngines::class.java.getResourceAsStream(RESOURCES + path)?.use { it.readBytes() } ?: return null
        return LocalResponse(mimeType(path), bytes)
    }

    /** The page asks for the custom engines (noveldb_mobile's `_custom_engines`), not a site. */
    private fun customEnginesAsked(json: String): Boolean =
        runCatching { (Json.parseToJsonElement(json) as? JsonObject)?.string("op") == "customEngines" }.getOrDefault(false)

    /** The custom engines with their source: the page runs them (and loads one again when its source changes). */
    private fun customEnginesReply(): String = buildJsonObject {
        put("engines", JsonArray(CustomEngines.list().map { engine ->
            buildJsonObject {
                put("id", engine.id)
                put("label", engine.label)
                put("sites", JsonArray(engine.sites.map { JsonPrimitive(it) }))
                put("source", engine.source)
            }
        }))
    }.toString()

    private fun mimeType(path: String): String = when (path.substringAfterLast('.', "").lowercase()) {
        "html" -> "text/html"
        "js" -> "text/javascript"
        "json" -> "application/json"
        "wasm" -> "application/wasm"
        "py" -> "text/plain"
        "zip", "whl" -> "application/zip"
        else -> "application/octet-stream"
    }
}
