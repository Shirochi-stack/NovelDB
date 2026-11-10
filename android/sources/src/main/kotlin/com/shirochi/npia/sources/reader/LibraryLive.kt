package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import kotlinx.serialization.json.*
import java.io.File
import java.net.URI
import java.nio.file.Files
import java.util.Base64
import java.util.UUID

/** One bounded history per Library file, with chapter bodies in temporary files. */
object LibraryLive {
    private val books = mutableMapOf<String, MutableList<Session>>()

    @Synchronized fun begin(file: File, referer: String = ""): Session = Session(referer).also { session ->
        val history = books.getOrPut(file.canonicalPath) { mutableListOf() }
        history += session
        if (history.size > 2) history.removeAt(0).close()
    }

    @Synchronized fun saved(file: File, spineCount: Int) { books[file.canonicalPath]?.lastOrNull()?.finish(saved = true, spineCount = spineCount) }

    @Synchronized fun handle(request: JsonObject): String {
        val path = runCatching { File(URI(request["url"]?.jsonPrimitive?.content.orEmpty())).canonicalPath }.getOrNull()
        val history = books[path].orEmpty()
        val key = request["key"]?.jsonPrimitive?.contentOrNull
        val session = request["session"]?.jsonPrimitive?.contentOrNull.orEmpty()
        val book = if (key == null) history.lastOrNull() else history.firstOrNull { it.id == session }
        return (book?.snapshot(session, request["after"]?.jsonPrimitive?.intOrNull ?: 0, key) ?: buildJsonObject {}).toString()
    }

    class Session(private val referer: String = "") {
        val id = UUID.randomUUID().toString()
        private val folder = Files.createTempDirectory("noveldb-live-").toFile()
        private var chapters = emptyList<ChapterRef>()
        private var indices = emptyMap<String, Int>()
        private val states = mutableMapOf<Int, Pair<String, Int>>()
        private var version = 0
        private var active = true
        private var saved = false
        private var savedOffset: Int? = null

        private fun refKey(ref: ChapterRef) = "${ref.isNotice}:${ref.id}"

        @Synchronized fun book(selected: List<ChapterRef>) {
            chapters = selected
            indices = selected.mapIndexed { index, ref -> refKey(ref) to index }.toMap()
            ++version
            selected.indices.forEach { states[it] = "pending" to version }
        }

        @Synchronized fun chapter(result: ChapterResult) {
            val index = indices[refKey(result.ref)] ?: return
            var state = "unavailable"
            if (result is ChapterResult.Content) {
                var html = result.content.html
                for (image in result.content.images) {
                    val replacement = image.data?.let {
                        val mime = java.net.URLConnection.guessContentTypeFromName(image.filename) ?: "image/jpeg"
                        "data:$mime;base64," + Base64.getEncoder().encodeToString(it)
                    } ?: image.url
                    html = html.replace("\"${image.filename}\"", "\"$replacement\"").replace("'${image.filename}'", "'$replacement'")
                }
                if (runCatching { File(folder, "$index.html").writeText(html) }.isSuccess) state = "ready"
            }
            states[index] = state to ++version
        }

        @Synchronized fun finish(saved: Boolean = false, spineCount: Int? = null) {
            active = false
            this.saved = this.saved || saved
            if (spineCount != null) savedOffset = (spineCount - chapters.size).coerceAtLeast(0)
            ++version
            states.keys.toList().forEach { if (states[it]?.first == "pending") states[it] = "unavailable" to version }
        }

        @Synchronized fun snapshot(session: String, after: Int, key: String?): JsonObject {
            if (key != null) {
                val index = key.toIntOrNull()
                val html = index?.takeIf { states[it]?.first == "ready" }?.let { runCatching { File(folder, "$it.html").readText() }.getOrNull() }
                return buildJsonObject {
                    if (html == null) put("error", "Chapter is not available") else {
                        put("html", html); put("name", chapters[index!!].title); put("imageReferer", referer)
                    }
                }
            }
            val full = session != id || after < 1
            return buildJsonObject {
                put("session", id); put("version", version); put("active", active); put("saved", saved)
                savedOffset?.let { put("savedOffset", it) }
                if (full && chapters.isNotEmpty()) putJsonArray("chapters") {
                    chapters.forEachIndexed { index, ref -> addJsonObject {
                        put("key", index.toString()); put("name", ref.title); put("identity", refKey(ref))
                    } }
                }
                putJsonArray("states") {
                    states.forEach { (index, state) -> if (full || state.second > after) addJsonObject {
                        put("key", index.toString()); put("state", state.first)
                    } }
                }
            }
        }

        @Synchronized fun close() { folder.deleteRecursively() }
    }
}
