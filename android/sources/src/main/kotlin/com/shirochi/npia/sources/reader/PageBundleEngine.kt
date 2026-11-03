package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.sources.engines.EngineBundle
import com.shirochi.npia.sources.engines.RuleEngine
import com.shirochi.npia.sources.engines.RuleEngines
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put

/**
 * How the app speaks to one page-script engine's bundle (the desktop's `PageScript`): the flags it
 * sets when it is ready, and the functions that read a book and a chapter. Each returns the
 * novel-downloader bridge's shapes as JSON text, or `{error}`.
 */
internal class PageScript(
    val rule: RuleEngine,
    /** `window.<prefix>READY`, `<prefix>API` and `<prefix>LOAD_ERROR`, e.g. `__LNR_`. */
    val prefix: String,
    /** Reads the book shown in the page; the result is the bridge's book JSON text. */
    val parseBook: String,
    /** The chapter function, given the chapter as JSON text (`{url, name}`). */
    val parseChapter: String,
) {
    companion object {
        val LNREADER = PageScript(
            RuleEngine.LNREADER, "__LNR_",
            "await window.__LNR_parseBook(location.href)",
            "window.__LNR_parseChapter",
        )

        fun of(rule: RuleEngine): PageScript? = listOf(LNREADER).firstOrNull { it.rule === rule }
    }
}

/**
 * A page-script engine (the LNReader plugins) in the book's web view page. Its requests go through
 * the page's `fetch`, which falls back to `window.__npia_gm_xmlhttp_request` (`WebToEpubTransport`)
 * for sites the web view refuses to read, as WebToEpub's do. `contentHtml` keeps images as absolute
 * `<img src>`, which become image blocks the pipeline downloads.
 */
internal class PageBundleEngine private constructor(
    private val page: WebPageDriving,
    private val pageUrl: String,
    private val script: PageScript,
) {
    suspend fun chapter(info: ReaderBookSession.ChapterInfo): List<RuleBlock> {
        val item = buildJsonObject {
            put("url", info.url)
            put("name", info.name)
        }.toString()
        val body = "return JSON.parse(await ${script.parseChapter}(${ReaderRuntime.jsLiteral(item)}));"
        val result = Json.parseToJsonElement(page.evaluateJson(body)) as? JsonObject
            ?: throw SourceException.Parse("Unexpected chapter data from the ${script.rule.title}.")
        result.string("error")?.let { throw SourceException.Parse("${script.rule.title}: $it") }
        return WebChapterBlocks.blocks(result, info.url, pageUrl)
    }

    suspend fun close() {
        runCatching { page.close() }
    }

    fun closeLater() {
        closer.launch { close() }
    }

    companion object {
        private val closer = CoroutineScope(SupervisorJob() + Dispatchers.Default)

        /**
         * Loads `script`'s bundle in `page` (already showing the book) and reads the book. A site the
         * engine doesn't list is refused before anything loads; a downloaded update that does not
         * become ready is set aside and the copy shipped with the app is used.
         */
        suspend fun openBook(
            page: WebPageDriving,
            url: String,
            script: PageScript,
            context: SourceContext,
        ): Pair<ReaderBookPayload, PageBundleEngine> {
            val store = RuleEngines.store
            val bundle = store.current(script.rule)
                ?: throw SourceException.Parse("The ${script.rule.title} are missing from this build.")
            if (!bundle.knows(url)) throw EngineMissException("The ${script.rule.title} have no plugin for this site.")
            val loaded = load(page, script, context)
            val data = page.evaluateJson(
                "const data = JSON.parse(${script.parseBook}); data.pageUrl = data.pageUrl || location.href; return data;",
            )
            val payload = ReaderBookPayload.decode(data)
            payload.error?.let { error ->
                if (payload.noParser == true) throw EngineMissException("${script.rule.title}: $error")
                throw SourceException.Parse("${script.rule.title}: $error")
            }
            context.logger.log(LogLevel.INFO, "[Reader] ${script.rule.title} ${loaded.label}")
            payload.warnings.orEmpty().forEach { context.logger.log(LogLevel.INFO, "[Reader] ${script.rule.title}: $it") }
            return payload to PageBundleEngine(page, page.currentUrl() ?: payload.pageUrl.orEmpty(), script)
        }

        /** The bundle that became ready in the page: the one in use, else the shipped copy. */
        private suspend fun load(page: WebPageDriving, script: PageScript, context: SourceContext): EngineBundle {
            val engine = script.rule
            val store = RuleEngines.store
            val bundle = store.current(engine) ?: throw SourceException.Parse("The ${engine.title} are missing from this build.")
            val candidates = listOfNotNull(bundle, store.shipped(engine).takeIf { bundle.updated })
            var failure = "did not load"
            for (candidate in candidates) {
                val error = inject(page, script, candidate)
                if (error == null) return candidate
                failure = error
                if (candidate.updated) {
                    context.logger.log(
                        LogLevel.WARNING,
                        "[Reader] ${engine.title} update (${candidate.label}) failed to load ($error); using the built-in copy.",
                    )
                    store.reject(candidate, error)
                } else {
                    context.logger.log(LogLevel.WARNING, "[Reader] ${engine.title} failed to load: $error")
                }
            }
            throw SourceException.Parse("The ${engine.title} failed to load: $failure")
        }

        /** Null when the bundle is ready in the page (its READY flag and API number), otherwise why not. */
        private suspend fun inject(page: WebPageDriving, script: PageScript, bundle: EngineBundle): String? {
            val p = script.prefix
            val api = bundle.engine.api
            val body = """
                if (!(window.${p}READY === true && window.${p}API === $api)) (0, eval)(${ReaderRuntime.jsLiteral(bundle.read())});
                return {
                  ready: window.${p}READY === true && window.${p}API === $api,
                  error: (window.${p}LOAD_ERROR || {}).message || null,
                };
            """.trimIndent()
            return try {
                val state = Json.parseToJsonElement(page.evaluateJson(body)) as? JsonObject
                if (state?.boolean("ready") == true) null else state?.string("error") ?: "did not load"
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                e.message ?: e.toString()
            }
        }
    }
}
