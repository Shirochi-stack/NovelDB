package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.JobLogging
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
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.contentOrNull

/**
 * Runs the rules in a real web view, exactly like the desktop's Playwright path: load the book
 * page, inject `gm_stubs.js`, `rules-lib.js` and `bridge.js`, then call `__ND_parseBook()` and
 * `__ND_parseChapter()`.
 *
 * Used when the script engine cannot read a book, for example sites that build their chapter
 * list with their own JavaScript. The page comes from `SharedBookPage`, which the WebToEpub
 * engine can reuse when the rules find nothing. Unverified until `:webengine` provides pages.
 */
internal class ReaderWebFallback private constructor(
    private val page: WebPageDriving,
    private val pageUrl: String,
) {
    suspend fun chapter(info: ReaderBookSession.ChapterInfo): List<RuleBlock> {
        val isPaid = info.isPaid?.toString() ?: "undefined"
        val body = "return JSON.parse(await window.__ND_parseChapter(" +
            "${ReaderRuntime.jsLiteral(info.url)}, ${ReaderRuntime.jsLiteral(info.name)}, ${info.isVIP}, $isPaid));"
        val result = Json.parseToJsonElement(page.evaluateJson(body)) as? JsonObject
            ?: throw SourceException.Parse("Unexpected chapter data from the web view.")
        result.string("error")?.let { throw SourceException.Parse(it) }
        return WebChapterBlocks.blocks(result, info.url, pageUrl)
    }

    suspend fun close() {
        runCatching { page.close() }
    }

    fun closeLater() {
        closer.launch { close() }
    }

    companion object {
        const val PROFILE = "reader"
        private val closer = CoroutineScope(SupervisorJob() + Dispatchers.Default)

        /**
         * Loads the rules bundle in `page` (already showing the book) and reads the book. A downloaded
         * rules update that does not become ready is set aside and the copy shipped with the app is used.
         * The page stays open for the caller to close or keep.
         */
        suspend fun openBook(page: WebPageDriving, requested: String, context: SourceContext): Pair<ReaderBookPayload, ReaderWebFallback> {
            val stubs = ReaderScripts.bundled("gm_stubs.js").source
            val bridge = ReaderScripts.bundled("bridge.js").source
            page.evaluateJson("if (!window.__ND_BRIDGE_READY) (0, eval)(${ReaderRuntime.jsLiteral(stubs)}); return true;")
            loadRules(page, context.logger)
            val body = """
                if (!window.__ND_BRIDGE_READY) (0, eval)(${ReaderRuntime.jsLiteral(bridge)});
                const data = JSON.parse(await window.__ND_parseBook());
                data.pageUrl = location.href;
                data.charset = document.characterSet;
                return data;
            """.trimIndent()
            val payload = ReaderBookPayload.decode(page.evaluateJson(body))
            val current = page.currentUrl() ?: requested
            return payload to ReaderWebFallback(page, current)
        }

        /** Evaluates the rules bundle in use, and the shipped copy when a downloaded update fails to initialise. */
        private suspend fun loadRules(page: WebPageDriving, logger: JobLogging) {
            val store = RuleEngines.store
            val bundle = store.current(RuleEngine.NOVEL_DOWNLOADER) ?: throw SourceException.Parse("rules-lib.js is missing from this build.")
            val candidates = listOfNotNull(bundle, store.shipped(RuleEngine.NOVEL_DOWNLOADER).takeIf { bundle.updated })
            var failure = "did not initialise"
            for (candidate in candidates) {
                val error = injectRules(page, candidate)
                if (error == null) return
                failure = error
                if (candidate.updated) {
                    logger.log(LogLevel.WARNING, "[Reader] ${candidate.engine.title} update (${candidate.label}) failed to load ($error); using the built-in copy.")
                    store.reject(candidate, error)
                } else {
                    logger.log(LogLevel.WARNING, "[Reader] ${candidate.engine.title} failed to load: $error")
                }
            }
            throw SourceException.Parse("The reader rules failed to load: $failure")
        }

        /** Null when `rules-lib.js` is ready in the page, otherwise why not. */
        private suspend fun injectRules(page: WebPageDriving, rules: EngineBundle): String? {
            val body = """
                if (!window.__ND_READY) (0, eval)(${ReaderRuntime.jsLiteral(rules.read())});
                for (let i = 0; i < 200 && !window.__ND_READY; i++) await new Promise((r) => setTimeout(r, 50));
                return window.__ND_READY === true;
            """.trimIndent()
            return try {
                if (page.evaluateJson(body) == "true") null else "rules-lib.js did not initialise"
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                e.message ?: e.toString()
            }
        }
    }
}

/** Turns an engine's chapter reply (`contentHtml`, `contentText`, `images`) into text and image blocks. */
internal object WebChapterBlocks {
    suspend fun blocks(result: JsonObject, chapterUrl: String, pageUrl: String): List<RuleBlock> {
        val attachments = HashMap<String, String>()
        for (image in result["images"] as? JsonArray ?: JsonArray(emptyList())) {
            val obj = image as? JsonObject ?: continue
            val name = obj.string("name")
            val url = obj.string("url")
            if (name != null && url != null) attachments[name] = url
        }
        val base = if (chapterUrl.isEmpty()) pageUrl else resolveUrl(pageUrl, chapterUrl)
        val blocks = ReaderRules.blocks(result.string("contentHtml") ?: "", base, attachments)
        val text = result.string("contentText")
        if (blocks.isEmpty() && text != null) {
            return text.lines()
                .map { it.replace(' ', ' ').trim() }
                .filter { it.isNotEmpty() }
                .map { RuleBlock.Text(it) }
        }
        return blocks
    }
}

internal fun JsonObject.string(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull

internal fun JsonObject.boolean(key: String): Boolean? = (this[key] as? JsonPrimitive)?.booleanOrNull
