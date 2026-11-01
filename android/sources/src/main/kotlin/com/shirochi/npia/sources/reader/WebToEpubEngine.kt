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

/** An engine has no rule or parser for the site (the desktop's `noParser`), as opposed to failing on it. */
internal class EngineMissException(message: String) : Exception(message)

/**
 * WebToEpub's site parsers (`webtoepub-lib.js`) in a web view page, through the bridge the desktop
 * built for them (`scripts/webtoepub/bridge.js`):
 * - `__W2E_parseBook()` returns the novel-downloader bridge's book shape, or `{error, noParser?}`;
 * - `__W2E_parseChapter({url, name})` returns `{chapterName, contentHtml, contentText, ...}` or `{error}`.
 *
 * The parsers need a real DOM, so unlike the novel-downloader rules they never run in QuickJS.
 * `contentHtml` keeps images as absolute `<img src>`, which become image blocks the pipeline's image
 * stage downloads. The page's `fetch` falls back to `window.__npia_gm_xmlhttp_request` (see
 * `WebToEpubTransport`) for sites the web view refuses to read.
 */
internal class WebToEpubEngine private constructor(
    private val page: WebPageDriving,
    private val pageUrl: String,
) {
    suspend fun chapter(info: ReaderBookSession.ChapterInfo): List<RuleBlock> {
        val item = buildJsonObject {
            put("url", info.url)
            put("name", info.name)
        }.toString()
        val body = "return JSON.parse(await window.__W2E_parseChapter(${ReaderRuntime.jsLiteral(item)}));"
        val result = Json.parseToJsonElement(page.evaluateJson(body)) as? JsonObject
            ?: throw SourceException.Parse("Unexpected chapter data from WebToEpub.")
        result.string("error")?.let { throw SourceException.Parse("WebToEpub: $it") }
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
         * Loads the WebToEpub bundle in `page` (already showing the book) and reads the book. A
         * downloaded update that does not become ready is set aside and the copy shipped with the app
         * is used. The page stays open for the caller to close or keep.
         */
        suspend fun openBook(page: WebPageDriving, context: SourceContext): Pair<ReaderBookPayload, WebToEpubEngine> {
            val bundle = loadBundle(page, context)
            val data = page.evaluateJson(
                "const data = JSON.parse(await window.__W2E_parseBook()); data.pageUrl = location.href; return data;",
            )
            val payload = ReaderBookPayload.decode(data)
            payload.error?.let { error ->
                if (payload.noParser == true) throw EngineMissException("WebToEpub: $error")
                throw SourceException.Parse("WebToEpub: $error")
            }
            context.logger.log(
                LogLevel.INFO,
                "[Reader] WebToEpub ${payload.parser.orEmpty()} (${payload.match.orEmpty()} match), parsers ${bundle.label}",
            )
            payload.warnings.orEmpty().forEach { context.logger.log(LogLevel.INFO, "[Reader] WebToEpub: $it") }
            return payload to WebToEpubEngine(page, page.currentUrl() ?: payload.pageUrl.orEmpty())
        }

        /** The bundle that became ready in the page: the one in use, else the shipped copy. */
        private suspend fun loadBundle(page: WebPageDriving, context: SourceContext): EngineBundle {
            val engine = RuleEngine.WEBTOEPUB
            val store = RuleEngines.store
            val bundle = store.current(engine) ?: throw SourceException.Parse("The ${engine.title} are missing from this build.")
            val candidates = listOfNotNull(bundle, store.shipped(engine).takeIf { bundle.updated })
            var failure = "did not load"
            for (candidate in candidates) {
                val error = inject(page, candidate)
                if (error == null) {
                    if (candidate.updated) context.logger.log(LogLevel.INFO, "[Reader] Using ${engine.title} ${candidate.label}.")
                    return candidate
                }
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

        /** Null when the bridge is ready in the page (`__W2E_READY` and the API number), otherwise why not. */
        private suspend fun inject(page: WebPageDriving, bundle: EngineBundle): String? {
            val api = bundle.engine.api
            val body = """
                if (!(window.__W2E_READY === true && window.__W2E_API === $api)) (0, eval)(${ReaderRuntime.jsLiteral(bundle.read())});
                return {
                  ready: window.__W2E_READY === true && window.__W2E_API === $api,
                  error: (window.__W2E_LOAD_ERROR || {}).message || null,
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
