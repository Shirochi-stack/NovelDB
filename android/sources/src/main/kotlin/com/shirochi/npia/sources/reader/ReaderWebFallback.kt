package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.contentOrNull

/**
 * Runs the rules in a real web view, exactly like the desktop's Playwright path: load the book
 * page, inject `gm_stubs.js`, `rules-lib.js` and `bridge.js`, then call `__ND_parseBook()` and
 * `__ND_parseChapter()`.
 *
 * Used when the script engine cannot read a book, for example sites that build their chapter
 * list with their own JavaScript. Unverified until `:webengine` provides pages.
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
        val attachments = HashMap<String, String>()
        for (image in result["images"] as? JsonArray ?: JsonArray(emptyList())) {
            val obj = image as? JsonObject ?: continue
            val name = obj.string("name")
            val url = obj.string("url")
            if (name != null && url != null) attachments[name] = url
        }
        val base = if (info.url.isEmpty()) pageUrl else resolveUrl(pageUrl, info.url)
        val blocks = ReaderRules.blocks(result.string("contentHtml") ?: "", base, attachments)
        val text = result.string("contentText")
        if (blocks.isEmpty() && text != null) {
            return text.lines()
                .map { it.replace('\u00a0', ' ').trim() }
                .filter { it.isNotEmpty() }
                .map { RuleBlock.Text(it) }
        }
        return blocks
    }

    suspend fun close() {
        runCatching { page.close() }
    }

    fun closeLater() {
        closer.launch { close() }
    }

    companion object {
        const val PROFILE = "reader"
        val injected = setOf("gm_stubs.js", "rules-lib.js", "bridge.js")
        private val closer = CoroutineScope(SupervisorJob() + Dispatchers.Default)

        suspend fun openBook(url: String, context: SourceContext): Pair<ReaderBookPayload, ReaderWebFallback> {
            val page = context.web.newPage(PROFILE)
            try {
                page.load(url, WebLoadState.DOM_CONTENT_LOADED, 30)
                val sources = ReaderScripts.load().filter { it.name in injected }
                    .joinToString(", ", "[", "]") { ReaderRuntime.jsLiteral(it.source) }
                val body = """
                    if (!window.__ND_BRIDGE_READY) {
                      const sources = $sources;
                      for (const source of sources) (0, eval)(source);
                    }
                    for (let i = 0; i < 200 && !window.__ND_READY; i++) await new Promise((r) => setTimeout(r, 50));
                    const data = JSON.parse(await window.__ND_parseBook());
                    data.pageUrl = location.href;
                    data.charset = document.characterSet;
                    return data;
                """.trimIndent()
                val payload = ReaderBookPayload.decode(page.evaluateJson(body))
                val current = page.currentUrl() ?: url
                return payload to ReaderWebFallback(page, current)
            } catch (e: Throwable) {
                runCatching { page.close() }
                throw e
            }
        }

        private fun JsonObject.string(key: String): String? = (this[key] as? JsonPrimitive)?.contentOrNull
    }
}
