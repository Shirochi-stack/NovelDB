package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.bareHost
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonNull
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.net.URL

/**
 * Pure parsing for npia pages and payloads, ported from `downloader_core.py`, `gui.py`
 * (`extract_chapter_content_and_images`) and `external_scraper.py` (`_npia_parse_episode_html`),
 * following the iOS `NpiaParser`. No networking, so every function is fixture-testable.
 */
internal object NpiaParser {
    const val HOST = "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    const val IMAGE_HOST = "images.\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com"
    const val HOST_PATTERN = "\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061\\.com"
    val BASE = "https://$HOST/"

    private val NUL = Char(0)

    fun novelUrl(id: String) = "https://$HOST/novel/$id"
    fun viewerUrl(id: String) = "https://$HOST/viewer/$id"

    // URLs

    /** Novel id from a `/novel/<id>` URL on the main site (not the global one). */
    fun novelId(url: String): String? {
        val host = bareHost(url)
        if (!(host == HOST || (host.endsWith(".$HOST") && host != "global.$HOST"))) return null
        val path = runCatching { URL(url.trim()).path }.getOrNull() ?: return null
        return NpiaRx.first("""^/novel/(\d+)(?:/|${'$'})""", path)?.get(1)
    }

    // Book page

    data class Metadata(
        val title: String,
        val author: String,
        val coverUrl: String?,
        val tags: List<String>,
        val synopsis: String,
        val status: String?,
    )

    fun parseMetadata(text: String, novelId: String): Metadata {
        val title = NpiaRx.first("""productName = '(.+?)';""", text)?.let { NpiaEntities.decode(it[1]) }
            ?: NpiaRx.first("""<meta\s+property=["']og:title["']\s+content=["'](.+?)["']""", text, NpiaRx.I)
                ?.let { lastDashPart(NpiaEntities.decode(it[1])) }
            ?: NpiaRx.first("""<title>(.+?)</title>""", text, NpiaRx.I)?.let { lastDashPart(NpiaEntities.decode(it[1])) }
            ?: "Novel_$novelId"

        val author = NpiaRx.first("""<a class="writer-name"[^>]*>\s*(.+?)\s*</a>""", text)
            ?.let { NpiaEntities.decode(it[1]).trim() } ?: "Unknown Author"

        val tags = mutableListOf<String>()
        val seen = mutableSetOf<String>()
        for (match in NpiaRx.all("""<span class="tag".*?>(#.+?)</span>""", text)) {
            val raw = match[1]
            if (raw.isBlank()) continue
            val tag = NpiaEntities.decode(raw.trimStart('#').trim())
            if (seen.add(tag.lowercase())) tags.add(tag)
        }

        return Metadata(
            title = title.trim(),
            author = author,
            coverUrl = extractCoverUrl(text),
            tags = tags,
            synopsis = parseSynopsis(text),
            status = parseStatus(text),
        )
    }

    private fun lastDashPart(full: String): String {
        val parts = full.split(" - ")
        return if (parts.size > 1) parts.last().trim() else full
    }

    fun parseSynopsis(text: String): String {
        NpiaRx.first("""<div[^>]*class=["'][^"']*\bsynopsis\b[^"']*["'][^>]*>(.*?)</div>""", text, NpiaRx.IS)?.let { match ->
            var syn = NpiaRx.replace("""<\s*br\s*/?\s*>""", match[1], "\n", NpiaRx.I)
            syn = NpiaRx.replace("""</?[^>]+>""", syn, " ")
            syn = NpiaEntities.decode(syn)
            syn = NpiaRx.replace("""[ \t\f\x0B]+""", syn, " ")
            syn = NpiaRx.replace("""\n{3,}""", syn, "\n\n")
            syn = syn.trim()
            if (syn.isNotEmpty()) return syn
        }
        val meta = NpiaRx.first("""<meta[^>]*name=["']description["'][^>]*content=(["'])(.*?)\1""", text, NpiaRx.IS)
            ?.let { NpiaEntities.decode(it[2].trim()) } ?: ""
        val og = NpiaRx.first("""<meta[^>]*property=["']og:description["'][^>]*content=(["'])(.*?)\1""", text, NpiaRx.IS)
            ?.let { NpiaEntities.decode(it[2].trim()) } ?: ""
        return if (og.length > meta.length) og else meta
    }

    /**
     * `parse_npia_status`: the completion badge inside `.epnew-novel-info .in-badge`, or null
     * when the page has no badge block (login/error page or unknown layout).
     */
    fun parseStatus(text: String): String? {
        val info = NpiaRx.matches("""class=["'][^"']*\bepnew-novel-info\b""", text, NpiaRx.I).firstOrNull() ?: return null
        val after = text.substring(info.range.first)
        val badge = NpiaRx.first("""<(\w+)[^>]*class=["'][^"']*\bin-badge\b[^"']*["'][^>]*>(.*?)</\1>""", after, NpiaRx.IS) ?: return null
        return if (NpiaRx.contains("""class=["'][^"']*\bb_comp\b""", badge[2], NpiaRx.I)) "Completed" else "Ongoing"
    }

    // Cover

    fun normalizeImageboxUrl(raw: String): String? {
        var value = NpiaEntities.decode(raw).trim().trim('"', '\'')
        value = value.replace("\\/", "/")
        value = NpiaRx.replace("""[\s,;)]+${'$'}""", value, "")
        value = when {
            value.startsWith("//") -> "https:$value"
            value.startsWith("/imagebox/") -> "https://$IMAGE_HOST$value"
            value.startsWith("imagebox/") -> "https://$IMAGE_HOST/$value"
            else -> value
        }
        value = NpiaRx.replace("^https?://(?:image\\.$HOST_PATTERN|$HOST_PATTERN)/imagebox/", value, "https://$IMAGE_HOST/imagebox/", NpiaRx.I)
        if (!NpiaRx.contains("^https://images?\\.$HOST_PATTERN/imagebox/", value, NpiaRx.I)) return null
        return value
    }

    /** `_extract_cover_url`: scores every candidate and prefers original covers over previews. */
    fun extractCoverUrl(text: String): String? {
        class Candidate(var score: Int, val order: Int, val url: String)
        val candidates = mutableListOf<Candidate>()
        val seen = mutableMapOf<String, Candidate>()

        fun add(raw: String, bonus: Int) {
            val url = normalizeImageboxUrl(raw) ?: return
            val lower = url.lowercase()
            if (lower.contains("readycover")) return
            var score = bonus
            score += when {
                lower.contains("/imagebox/original/") -> 1000
                lower.contains("/imagebox/cover/") -> 500
                else -> 250
            }
            score += if (lower.contains("_q_") || lower.contains("_q_ori")) -80 else 40
            if (lower.endsWith(".wimg")) score -= 120
            if (lower.contains("_ori.file")) score += 30
            seen[url]?.let { existing ->
                if (score > existing.score) existing.score = score
                return
            }
            val candidate = Candidate(score, candidates.size, url)
            seen[url] = candidate
            candidates.add(candidate)
        }

        for (tag in NpiaRx.all("""<meta\b[^>]*>""", text, NpiaRx.IS).map { it[0] }) {
            if (!NpiaRx.contains("""\b(?:property|name)=['"](?:og:image|twitter:image|image)['"]""", tag, NpiaRx.I)) continue
            NpiaRx.first("""\bcontent=(['"])(.*?)\1""", tag, NpiaRx.IS)?.let { add(it[2], 220) }
        }
        for (tag in NpiaRx.all("""<img\b[^>]*>""", text, NpiaRx.IS).map { it[0] }) {
            if (!NpiaRx.contains("""\bclass=(['"])[^'"]*\bcover_img\b""", tag, NpiaRx.I)) continue
            NpiaRx.first("""\bsrc=(['"])(.*?)\1""", tag, NpiaRx.IS)?.let { add(it[2], 200) }
        }
        for (match in NpiaRx.matches("""\bepnew-cover-box\b""", text, NpiaRx.I)) {
            val start = match.range.first
            val chunk = text.substring(start, minOf(text.length, start + 1600))
            for (attr in NpiaRx.all("""\b(?:href|src)=(['"])(.*?)\1""", chunk, NpiaRx.IS)) add(attr[2], 120)
        }
        for (match in NpiaRx.all("""\bimageUrl\s*=\s*(['"])(.*?)\1""", text, NpiaRx.IS)) add(match[2], 160)

        val scan = text.replace("\\/", "/")
        val imagebox = "(?:https?:)?//(?:images?\\.$HOST_PATTERN|$HOST_PATTERN)/imagebox/(?:original|cover|[0-9a-f]{1,3})/[^\\s\"'<>\\\\)]+" +
            "|/imagebox/(?:original|cover|[0-9a-f]{1,3})/[^\\s\"'<>\\\\)]+"
        for (match in NpiaRx.all(imagebox, scan, NpiaRx.I)) add(match[0], 0)

        return candidates.sortedWith(compareByDescending<Candidate> { it.score }.thenBy { it.order }).firstOrNull()?.url
    }

    // Chapter list and notices

    data class EpisodeRow(val id: String, val title: String, val isPaid: Boolean)

    /** `_npia_parse_episode_html`: chapters from one `episode_list` page, with PLUS rows marked paid. */
    fun parseEpisodePage(source: String): List<EpisodeRow> {
        val result = mutableListOf<EpisodeRow>()
        val seen = mutableSetOf<String>()
        for (match in NpiaRx.matches("""id=["']bookmark_(\d+)["'][^>]*>\s*</i>(.*?)</b>""", source, NpiaRx.IS)) {
            val id = match.groupValues[1]
            if (!seen.add(id)) continue
            val rawTitle = NpiaRx.replace("""<[^>]+>""", match.groupValues[2], "")
            val title = NpiaEntities.decode(rawTitle).trim()

            val rowStart = source.lastIndexOf("<tr", match.range.first)
            val end = match.range.last + 1
            val rowEnd = source.indexOf("</tr>", end)
            val start = if (rowStart < 0) match.range.first else rowStart
            val stop = if (rowEnd < 0) end else rowEnd + 5
            val row = source.substring(start, stop)
            val isPlus = NpiaRx.contains("""(?:\bPLUS\b|플러스|b_plus|icon[-_]?plus)""", row, NpiaRx.I)
            result.add(EpisodeRow(id, title.ifEmpty { "Chapter ${result.size + 1}" }, isPlus))
        }
        return result
    }

    data class Notice(val id: String, val title: String)

    /** `parse_npia_notice_html`: author notices from the novel page, oldest first. */
    fun parseNotices(source: String): List<Notice> {
        val table = NpiaRx.first(
            """<table\b[^>]*\bclass=["'][^"']*\bnotice_table\b[^"']*["'][^>]*>.*?</table>""", source, NpiaRx.IS,
        )?.get(0) ?: return emptyList()
        var matches = NpiaRx.all("""location=['"]/viewer/(\d+)['"][^>]*><b>(.*?)</b>""", table, NpiaRx.S)
        if (matches.isEmpty()) {
            matches = NpiaRx.all("""href=['"]/viewer/(\d+)['"][^>]*><b>(.*?)</b>""", table, NpiaRx.S)
        }
        return matches.map { match ->
            val clean = NpiaRx.replace("""<.*?>""", match[2], "").trim()
            Notice(match[1], NpiaEntities.decode(if (clean.isEmpty()) "Notice" else "Notice: $clean"))
        }.reversed()
    }

    // Viewer payload

    sealed class Payload {
        data class Segments(val segments: List<JsonObject>) : Payload()

        /** The server rejected access (`잘못된 접근`, login or age verification). */
        data class Blocked(val reason: String) : Payload()

        /** An error page or malformed JSON; worth retrying. */
        data class Invalid(val reason: String) : Payload()
    }

    /** `validate_chapter_payload`: returns the `s` segment list or explains why it is unusable. */
    fun validatePayload(text: String): Payload {
        val stripped = text.trimStart()
        val probe = stripped.take(4096).lowercase()
        if (probe.startsWith("<!doctype html") || probe.startsWith("<html")) {
            return Payload.Invalid("HTTP 200 contained an HTML error page")
        }
        val root: JsonElement = try {
            Json.parseToJsonElement(stripped)
        } catch (e: Exception) {
            return Payload.Invalid("invalid viewer JSON")
        }
        val data = root as? JsonObject ?: return Payload.Invalid("viewer JSON root is not an object")

        fun message(fallback: String): String {
            for (key in listOf("errmsg", "message", "error")) {
                val value = data[key] ?: continue
                if (value is JsonNull) continue
                val string = if (value is JsonPrimitive && value.isString) value.content else value.toString()
                if (string.isNotEmpty()) return string
            }
            return fallback
        }

        if (isCode500(data["status"]) || isCode500(data["code"])) {
            val detail = message("server error")
            if (detail.contains("잘못된 접근")) return Payload.Blocked("viewer access restricted: $detail")
            return Payload.Invalid("viewer returned status/code 500: $detail")
        }
        val segments = data["s"] as? JsonArray
        if (segments == null) {
            // Desktop checks the whole body for these words; only the non-content fields are
            // checked here so a chapter that merely mentions logging in is not refused.
            val detail = message("missing chapter segment list ('s')")
            if (isAccessWall(detail)) return Payload.Blocked("login/age verification required: $detail")
            return Payload.Invalid("viewer returned no chapter content: $detail")
        }
        return Payload.Segments(segments.filterIsInstance<JsonObject>())
    }

    fun isAccessWall(text: String): Boolean =
        text.contains("본인인증") || text.contains("로그인") || text.lowercase().contains("authentication required")

    private fun isCode500(value: JsonElement?): Boolean {
        val primitive = value as? JsonPrimitive ?: return false
        if (primitive is JsonNull) return false
        return if (primitive.isString) primitive.content == "500" else primitive.content.toDoubleOrNull() == 500.0
    }

    data class RenderOptions(
        val stripLeadingSpaces: Boolean = false,
        val removeNewlines: Boolean = false,
        val saveImageUrlsOnly: Boolean = false,
        /** Prefix for image file names, e.g. `0012` or `n0001`. */
        val imagePrefix: String = "0001",
    )

    data class Rendered(val html: String, val images: List<ImageRef>)

    private fun segmentText(segment: JsonObject): String? {
        val value = segment["text"] as? JsonPrimitive ?: return null
        return if (value.isString) value.content else null
    }

    /**
     * `extract_chapter_content_and_images`: segments to an XHTML body fragment plus the images it
     * references. Images are not downloaded here; the pipeline fetches `ImageRef.url`.
     */
    fun renderSegments(segments: List<JsonObject>, options: RenderOptions): Rendered {
        val parts = mutableListOf<String>()
        val images = mutableListOf<ImageRef>()
        val imgPattern = """<img\b[^>]*\bsrc=(['"])(.*?)\1[^>]*>"""
        val zeroHeight = """<p\s+style=['"]height:\s*0px;[^>]*>.*?</p>"""

        for (segment in segments) {
            val raw = segmentText(segment)
            if (raw.isNullOrEmpty() || raw.contains("cover-wrapper")) continue
            var text: String = raw

            if (NpiaRx.contains(imgPattern, text, NpiaRx.IS)) {
                text = NpiaRx.replace(imgPattern, text, NpiaRx.IS) { groups ->
                    val src = groups[2]
                    if (unfetchableImageReason(src) != null) return@replace ""
                    val url = normalizeChapterImageUrl(src) ?: return@replace ""
                    if (options.saveImageUrlsOnly) {
                        return@replace """<img class="remote-image" alt="Image ${images.size + 1}" src="${NpiaEntities.escape(url)}"/>"""
                    }
                    val number = images.size + 1
                    val filename = "images/${options.imagePrefix}_${number.toString().padStart(2, '0')}.${imageExtension(url)}"
                    images.add(ImageRef(url, filename))
                    """<img alt="$number" src="$filename" width="100%"/>"""
                }
                text = NpiaRx.replace(zeroHeight, text, "", NpiaRx.IS)
                text = breaks(text, options.removeNewlines)
                parts.add("<p>$text</p>")
                continue
            }

            text = NpiaRx.replace(zeroHeight, text, "", NpiaRx.IS)
            // Keep inline formatting through tag stripping with placeholders.
            val preserved = mutableListOf<String>()
            var tags = "b|strong|i|em|u|s|strike|del|sub|sup|ruby|rb|rt|rp|rtc"
            if (!options.removeNewlines) tags += "|br"
            text = NpiaRx.replace("</?(?:$tags)\\b[^>]*>", text, NpiaRx.I) { groups ->
                preserved.add(groups[0])
                "${NUL}FMT${preserved.size - 1}$NUL"
            }
            // Only tags that start with an ASCII letter, so Korean text in angle brackets survives.
            text = NpiaRx.replace("""</?[a-zA-Z][^>]*>""", text, "")
            if (options.removeNewlines) {
                text = text.replace("\r", "").replace("\n", "")
                if (text.replace(NUL.toString(), "").isBlank()) continue
            } else {
                text = text.replace("\r\n", "\n").replace("\r", "\n")
                if (text.isEmpty() && preserved.isEmpty()) continue
            }
            text = NpiaEntities.decode(text)
            if (options.stripLeadingSpaces) {
                text = NpiaRx.replace("""^(?:[ \t\f\x0B\x{A0}]|&nbsp;)+""", text, "")
            }
            if (text.isEmpty()) continue
            var safe = NpiaEntities.escape(text)
            if (!options.removeNewlines) safe = safe.replace("\n", "<br/>")
            preserved.forEachIndexed { index, tag ->
                val restored = if (NpiaRx.contains("""^</?br\b""", tag, NpiaRx.I)) "<br/>" else tag
                safe = safe.replace("${NUL}FMT$index$NUL", restored)
            }
            parts.add("<p>$safe</p>")
        }

        if (parts.isEmpty()) return Rendered("<p>[No text segments found in chapter]</p>", images)
        return Rendered(parts.joinToString(""), images)
    }

    private fun breaks(text: String, remove: Boolean): String {
        if (remove) {
            return NpiaRx.replace("""<br\s*/?>""", text, "", NpiaRx.I).replace("\r", "").replace("\n", "")
        }
        return NpiaRx.replace("""<br\s*/?>""", text, "<br/>", NpiaRx.I)
            .replace("\r\n", "\n").replace("\r", "\n").replace("\n", "<br/>")
    }

    private val unfetchableSchemes = setOf("file", "data", "about", "blob", "javascript", "cid", "res", "chrome")

    private fun cleanImageValue(value: String): String =
        NpiaEntities.decode(value).trim().trim('"', '\'').replace("\\/", "/")

    fun unfetchableImageReason(value: String): String? {
        val raw = cleanImageValue(value)
        if (raw.isEmpty()) return "empty image URL"
        if (NpiaRx.contains("""^[A-Za-z]:[\\/]""", raw)) return "local file path"
        val scheme = NpiaRx.first("""^([A-Za-z][A-Za-z0-9+.-]*):""", raw)?.get(1)?.lowercase()
        if (scheme != null && scheme in unfetchableSchemes) return "non-web image source ($scheme:)"
        return null
    }

    fun normalizeChapterImageUrl(value: String): String? {
        var url = cleanImageValue(value)
        if (url.isEmpty()) return null
        url = when {
            url.startsWith("//") -> "https:$url"
            url.startsWith("/imagebox/") -> "https://$IMAGE_HOST$url"
            url.startsWith("imagebox/") -> "https://$IMAGE_HOST/$url"
            !NpiaRx.contains("""^[A-Za-z][A-Za-z0-9+.-]*:""", url) ->
                runCatching { URL(URL(BASE), url).toString() }.getOrNull() ?: return null
            else -> url
        }
        val parsed = runCatching { URL(url) }.getOrNull() ?: return null
        if (parsed.protocol.lowercase() !in setOf("http", "https") || parsed.host.isNullOrEmpty()) return null
        return url
    }

    /** Extension guessed from the URL; the pipeline rewrites it if recompression changes the format. */
    fun imageExtension(url: String): String {
        val path = runCatching { URL(url).path }.getOrNull() ?: return "jpg"
        val name = path.substringAfterLast('/')
        val ext = if (name.contains('.')) name.substringAfterLast('.').lowercase() else ""
        return if (ext in setOf("jpg", "jpeg", "png", "gif", "webp", "avif")) ext else "jpg"
    }
}
