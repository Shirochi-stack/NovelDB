package com.shirochi.npia.sources.maru

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.ReaderBlock
import com.shirochi.npia.sources.http.ReaderBlocks
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject

/**
 * Numbered-domain novel site read through its public reader API.
 * Port of `_maru_*` in the desktop `external_scraper.py`, and of the iOS `MaruAdapter`.
 */
class MaruAdapter : SourceAdapter {
    override val id = SourceId.MARU
    override val tier = SourceTier.HTTP

    override fun matches(url: String): Boolean = parts(url) != null

    data class Parts(val work: String, val episode: String)

    companion object {
        /** The numbered site domain keeps moving; the reader API host does not. */
        const val API = "https://api.storagewebtoon.store/api/v1/reader/A2/"

        private val hostPattern = Regex("^(?:www\\.)?marumaru\\d*\\.[a-z]{2,}$", RegexOption.IGNORE_CASE)

        /** `(workId, episodeId)` for a novel or episode URL. */
        fun parts(url: String): Parts? {
            if (!hostPattern.matches(HttpSourceSupport.host(url))) return null
            val groups = HttpSourceSupport.allMatches("^/novel/(\\d+)(?:/(\\d+))?", HttpSourceSupport.path(url)).firstOrNull() ?: return null
            val work = groups.firstOrNull()?.takeIf { it.isNotEmpty() } ?: return null
            return Parts(work, groups.getOrElse(1) { "" })
        }

        fun episodeName(title: String, workTitle: String, number: String): String {
            var name = title.replace(Regex("\\s+"), " ").trim()
            val work = workTitle.trim()
            if (work.isNotEmpty() && name.startsWith("$work ")) name = name.drop(work.length).trim()
            return name.ifEmpty { "${number}화" }
        }

        private fun kind(part: JsonObject): String? = (part["kind"] as? kotlinx.serialization.json.JsonPrimitive)?.takeIf { it.isString }?.content

        suspend fun chapter(data: JsonObject, ref: ChapterRef, http: HttpFetching): ChapterResult {
            val item = HttpSourceSupport.obj(data["item"]) ?: JsonObject(emptyMap())
            val body = (HttpSourceSupport.array(item["body"]) ?: JsonArray(emptyList())).mapNotNull { it as? JsonObject }
            if (body.any { kind(it) == "paid" }) return ChapterResult.Refused(ref, "point-purchase episode")
            var blocks = mutableListOf<ReaderBlock>()
            for (part in body) {
                val kind = kind(part)
                val text = HttpSourceSupport.string(part["text"])
                if ((kind == "narration" || kind == "text") && text.isNotEmpty()) {
                    blocks += ReaderBlocks.fromPlainText(text)
                } else if (kind == "image") {
                    ReaderBlocks.resolve(HttpSourceSupport.string(part["url"]), null)?.let { blocks += ReaderBlock.Image(it) }
                }
            }
            val ready = HttpSourceSupport.bool(item["ready"]) != false
            val embed = HttpSourceSupport.string(item["embed_url"])
            if (blocks.isEmpty() && embed.isNotEmpty() && ready) {
                val embedUrl = ReaderBlocks.resolve(embed, API) ?: return ChapterResult.Failed(ref, "invalid embedded reader URL")
                val response = HttpSourceSupport.get(
                    embedUrl,
                    mapOf("User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT, "Accept" to "text/plain"),
                    http,
                )
                val text = response.text.removePrefix("\uFEFF")
                if (Regex("^\\s*<(?:!doctype|html|head|body)\\b", RegexOption.IGNORE_CASE).containsMatchIn(text)) {
                    return ChapterResult.Failed(ref, "embedded reader returned an HTML page")
                }
                blocks = ReaderBlocks.fromPlainText(text).toMutableList()
            }
            if (blocks.isEmpty()) {
                if (!ready || body.any { kind(it) == "processing" }) return ChapterResult.Refused(ref, "still processing on the site")
                return ChapterResult.Failed(ref, "empty episode")
            }
            return ChapterResult.Content(ReaderBlocks.content(blocks, ref, "maru-content"))
        }
    }

    private suspend fun apiGet(path: String, http: HttpFetching): JsonObject {
        // No Referer: the API refuses retired domains but accepts requests naming no site.
        val url = HttpSourceSupport.resolve(path, API)!!
        val response = HttpSourceSupport.get(
            url,
            mapOf("User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT, "Accept" to "application/json"),
            http,
        )
        val data = HttpSourceSupport.jsonObject(response.body)
        if (HttpSourceSupport.bool(data["ok"]) == false) {
            throw SourceException.Parse(HttpSourceSupport.string(data["message"]).ifEmpty { "bad reply" })
        }
        return data
    }

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val parts = parts(url) ?: throw SourceException.UnsupportedUrl(url)
        val data = apiGet("works/${parts.work}", context.http)
        val item = HttpSourceSupport.obj(data["item"]) ?: JsonObject(emptyMap())
        val rows = (HttpSourceSupport.array(data["episodes"]) ?: JsonArray(emptyList()))
            .mapNotNull { it as? JsonObject }
            .filter { it["id"] != null && it["id"] !is JsonNull }
            .sortedWith(compareBy({ HttpSourceSupport.double(it["number"]) }, { HttpSourceSupport.int(it["id"]) }))
        val title = HttpSourceSupport.string(item["title"]).trim()
        val workTitle = title.ifEmpty { "Maru ${parts.work}" }
        val origin = "${HttpSourceSupport.scheme(url) ?: "https"}://${HttpSourceSupport.host(url)}"
        val bookUrl = "$origin/novel/${parts.work}"

        val chapters = rows.mapIndexed { offset, row ->
            val episodeId = HttpSourceSupport.string(row["id"])
            val number = HttpSourceSupport.string(row["number"])
            val name = episodeName(HttpSourceSupport.string(row["title"]), workTitle, number)
            ChapterRef(episodeId, offset + 1, name, "$bookUrl/$episodeId")
        }
        if (chapters.isEmpty()) throw SourceException.Parse("no episodes found")

        val synopsis = HttpSourceSupport.string(data["synopsis"]).trim()
        val genres = HttpSourceSupport.string(item["genres"]).split(',', '/').map { it.trim() }.filter { it.isNotEmpty() }
        val author = HttpSourceSupport.string(item["author"]).trim()
        val status = HttpSourceSupport.string(item["status"])
        return BookInfo(
            source = SourceId.MARU, id = parts.work, url = bookUrl, title = workTitle,
            author = author.ifEmpty { null }, synopsis = synopsis.ifEmpty { null },
            coverUrl = HttpSourceSupport.string(item["cover_url"]).ifEmpty { null },
            tags = genres, status = status.ifEmpty { null }, language = "ko", chapters = chapters,
        )
    }

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val episode = ref.url?.let { parts(it)?.episode }?.ifEmpty { null } ?: ref.id
        val data = apiGet("episodes/$episode", context.http)
        return chapter(data, ref, context.http)
    }
}
