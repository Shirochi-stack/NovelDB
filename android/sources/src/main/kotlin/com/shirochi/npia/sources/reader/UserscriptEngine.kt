package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebLoadTimeoutException
import com.shirochi.npia.core.WebPageDriving
import java.net.URI
import java.util.Base64
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.delay
import kotlinx.coroutines.withContext
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.contentOrNull
import kotlinx.serialization.json.put

/**
 * A userscript as an engine (a `.user.js` added in Settings > Site engines; the desktop's
 * noveldb/core/engines/userscript_engine.py has the idea in full).
 *
 * A link is one chapter. It opens in a web view page of its own, never shown, with the script running in it
 * from the start of the page (`userscript-host.js` gives it its GM_ functions, reads the page to its end,
 * presses the script's own buttons and keeps the files it saves). The images are taken from the page a few
 * megabytes at a time: a web view answers a script within a minute, and a page can take longer to read.
 */
internal object UserscriptEngine {
    private const val HOST = "/com/shirochi/npia/sources/reader/userscript/userscript-host.js"
    private const val LOAD_SECONDS = 30L
    private const val READ_SECONDS = 600
    private const val TAKE_BYTES = 3_000_000

    private val hostScript: String? by lazy {
        UserscriptEngine::class.java.getResourceAsStream(HOST)?.use { it.readBytes().decodeToString() }
    }

    /** The one-chapter book of `url`: the page's title, from a plain request (the script runs for the chapter). */
    suspend fun openBook(script: CustomEngines.Info, url: String, context: SourceContext): ReaderBookPayload {
        val title = runCatching {
            val html = context.http.get(url).body.decodeToString()
            Regex("(?is)<title[^>]*>(.*?)</title>").find(html)?.groupValues?.get(1)
                ?.let { decodeEntities(it).replace(Regex("\\s+"), " ").trim() }
        }.getOrNull().orEmpty().ifEmpty { runCatching { URI(url).host }.getOrNull() ?: "Page" }
        context.logger.log(LogLevel.INFO, "[Reader] ${script.label} (userscript)")
        val json = buildJsonObject {
            put("bookUrl", url)
            put("pageUrl", url)
            put("bookname", title)
            put("author", "")
            put("chapters", JsonArray(listOf(buildJsonObject {
                put("url", url)
                put("name", title)
            })))
        }
        return ReaderBookPayload.decode(json.toString())
    }

    /** The chapter's images: what the script saved from the page, in the order it numbered them. */
    suspend fun chapter(script: CustomEngines.Info, info: ReaderBookSession.ChapterInfo, context: SourceContext): List<RuleBlock> {
        val host = hostScript ?: throw SourceException.Parse("Userscript engines are missing from this build.")
        val meta = buildJsonObject { put("name", script.label) }
        val page = context.web.newPage(ReaderWebFallback.PROFILE)
        try {
            // The script's requests to other sites go through the app (the page may not make them).
            page.exposeTransport { request -> WebToEpubTransport.handle(request, context.http, context.web) }
            page.addInitScript(host + "\n;window.__NDB_USERSCRIPT.start(" + ReaderRuntime.jsLiteral(script.source) + ", $meta);")
            try {
                page.load(info.url, WebLoadState.DOM_CONTENT_LOADED, LOAD_SECONDS)
            } catch (e: WebLoadTimeoutException) {
                context.logger.log(LogLevel.INFO, "The page took over $LOAD_SECONDS s to load; reading what is there.")
            }
            return read(page, script, info.url, context)
        } finally {
            withContext(NonCancellable) { runCatching { page.close() } }
        }
    }

    private suspend fun read(page: WebPageDriving, script: CustomEngines.Info, url: String, context: SourceContext): List<RuleBlock> {
        val started = page.evaluateJson("return !!(window.__NDB_USERSCRIPT && window.__NDB_USERSCRIPT.begin({}));")
        if (started.trim() != "true") throw SourceException.Parse("${script.label}: the script did not start on $url.")
        val blocks = ArrayList<RuleBlock>()
        var done: JsonObject? = null
        var waited = 0
        while (done == null && waited < READ_SECONDS) {
            val reply = try {
                Json.parseToJsonElement(page.evaluateJson("return window.__NDB_USERSCRIPT.take($TAKE_BYTES);")) as? JsonObject
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null  // the page was busy; it is asked again
            }
            val files = (reply?.get("files") as? JsonArray).orEmpty()
            for (item in files) {
                val file = item as? JsonObject ?: continue
                val bytes = runCatching { Base64.getDecoder().decode((file["base64"] as? JsonPrimitive)?.contentOrNull.orEmpty()) }.getOrNull()
                    ?: continue
                if (bytes.isEmpty()) continue
                val extension = EXTENSIONS[(file["mime"] as? JsonPrimitive)?.contentOrNull] ?: "png"
                // An address of its own per image: the pipeline names the file from it and never fetches it (the bytes are here).
                blocks += RuleBlock.Image(String.format(java.util.Locale.ROOT, "https://userscript.invalid/%04d.%s", blocks.size + 1, extension), bytes)
            }
            done = reply?.get("done") as? JsonObject
            if (done == null && files.isEmpty()) {
                delay(1000)
                waited++
            }
        }
        val failed = (done?.get("failed") as? JsonPrimitive)?.contentOrNull.orEmpty()
        if (failed.isNotEmpty()) context.logger.log(LogLevel.WARNING, "[Reader] ${script.label}: the script stopped: $failed")
        if (blocks.isEmpty()) {
            throw SourceException.Parse(
                if (done == null) "${script.label}: the page was still being read after ${READ_SECONDS / 60} minutes."
                else "${script.label}: the script saved no images from this page."
            )
        }
        val pressed = (done?.get("pressed") as? JsonArray).orEmpty().mapNotNull { (it as? JsonPrimitive)?.contentOrNull }
        context.logger.log(
            LogLevel.INFO,
            "[Reader] ${script.label}: ${blocks.size} image(s)" + if (pressed.isEmpty()) "." else " (pressed ${pressed.joinToString(", ")}).",
        )
        return blocks
    }

    private val EXTENSIONS = mapOf(
        "image/png" to "png", "image/jpeg" to "jpg", "image/gif" to "gif", "image/webp" to "webp",
        "image/avif" to "avif", "image/bmp" to "bmp",
    )

    private fun decodeEntities(text: String): String = text
        .replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#39;", "'")
        .replace("&nbsp;", " ")
}
