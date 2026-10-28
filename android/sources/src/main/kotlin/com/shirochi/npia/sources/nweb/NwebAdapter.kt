package com.shirochi.npia.sources.nweb

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.ReaderBlock
import com.shirochi.npia.sources.http.ReaderBlocks
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.isActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import org.jsoup.Jsoup
import org.jsoup.nodes.Document
import java.net.URLDecoder

/**
 * Korean web-novel portal (webnovel / best / challenge tiers), plus nseries product pages
 * that link to a free nweb edition. Port of `_nweb_*` and `_nweb_series_*` in the desktop
 * `external_scraper.py`, and of the iOS `NwebAdapter`.
 *
 * Age-restricted works need the user signed in; the HTTP client shares cookies with the
 * web engine, so a sign-in in the `nweb` web profile carries over.
 */
class NwebAdapter : SourceAdapter {
    override val id = SourceId.NWEB
    override val tier = SourceTier.HTTP
    override val sites: Set<String> = setOf("novel.$DOMAIN", "m.novel.$DOMAIN", "series.$DOMAIN", "m.series.$DOMAIN")

    private val session = NwebSession()

    override fun matches(url: String): Boolean = Companion.matches(url)

    data class NovelParts(val tier: String, val novel: String, val volume: String)

    data class Row(val volume: Int, val name: String)

    class ListPage(val book: BookInfo, val total: Int, val rows: List<Row>)

    sealed class Episode {
        data class Blocks(val blocks: List<ReaderBlock>) : Episode()
        data object NeedsLogin : Episode()
    }

    companion object {
        const val DOMAIN = "\u006e\u0061\u0076\u0065\u0072.com"
        const val NOVEL_ORIGIN = "https://novel.$DOMAIN"
        val webProfile = SourceId.NWEB.raw

        val headers = mapOf(
            "User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT,
            "Accept" to "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language" to "ko-KR,ko;q=0.9,en;q=0.7",
        )

        // URLs

        /** `(tier, novelId, volumeNo)` for an nweb work or episode URL. */
        fun novelParts(url: String): NovelParts? {
            val host = HttpSourceSupport.host(url)
            if (host != "novel.$DOMAIN" && host != "m.novel.$DOMAIN") return null
            val tier = HttpSourceSupport.firstMatch("^/(webnovel|best|challenge)/(?:list|detail)/?$", HttpSourceSupport.path(url)) ?: return null
            val novel = HttpSourceSupport.query(url, "novelId")?.takeIf { HttpSourceSupport.isPositiveInteger(it) } ?: return null
            return NovelParts(tier, novel, HttpSourceSupport.query(url, "volumeNo") ?: "")
        }

        fun isSeries(url: String): Boolean {
            val host = HttpSourceSupport.host(url)
            if (host != "series.$DOMAIN" && host != "m.series.$DOMAIN") return false
            if (!Regex("^/novel/detail\\.(?:series|nhn)/?$").containsMatchIn(HttpSourceSupport.path(url))) return false
            return listOf("productNo", "originalProductId").any { HttpSourceSupport.isPositiveInteger(HttpSourceSupport.query(url, it)) }
        }

        fun matches(url: String): Boolean = novelParts(url) != null || isSeries(url)

        fun needsLogin(response: HttpResponse): Boolean = HttpSourceSupport.host(response.url).startsWith("nid.$DOMAIN")

        /** The nweb edition a series product page links to. */
        fun seriesWebEdition(page: String): String? {
            val pattern = "href=\"((?:https?:)?//novel\\.${Regex.escape(DOMAIN)}/(?:webnovel|best|challenge)/list\\?novelId=\\d+)\""
            var href = HttpSourceSupport.firstMatch(pattern, page) ?: return null
            if (href.startsWith("//")) href = "https:$href"
            if (href.startsWith("http://")) href = "https://" + href.drop(7)
            return href
        }

        fun listPage(html: String, tier: String, novel: String, listUrl: String): ListPage {
            val document = Jsoup.parse(html, listUrl)
            val info = document.selectFirst(".section_area_info")
            val titleNode = info?.selectFirst("h2.title")
            if (info == null || titleNode == null) {
                val message = document.selectFirst("#content")?.text().orEmpty()
                throw SourceException.Parse("work page not found or not public" + if (message.isEmpty()) "" else ": ${message.take(120)}")
            }
            val title = titleNode.text()
            val authors = info.select("a[href*=target=author]").map { it.text() }.filter { it.isNotEmpty() }
            val genre = info.selectFirst(".info_top .info_group .item")?.text().orEmpty()
            var synopsis = ""
            info.selectFirst("p.summary")?.let { summary ->
                summary.select("a, button").remove()
                synopsis = HttpSourceSupport.textLines(summary).joinToString("\n").replace(Regex("\n{3,}"), "\n\n")
            }
            var cover = document.selectFirst("meta[property=og:image]")?.attr("content").orEmpty()
            info.selectFirst(".thumbnail img[src]")?.let { cover = it.absUrl("src") }
            val tags = mutableListOf<String>()
            if (genre.isNotEmpty()) tags += genre
            for (link in document.select(".end_tag_area .tag_collection a.tag")) {
                val tag = link.text().trim { it == '#' || it.isWhitespace() }
                if (tag.isNotEmpty() && tag !in tags) tags += tag
            }
            var total = 0
            for (heading in document.select(".cont_sub .component_head h3.title")) {
                val count = HttpSourceSupport.firstMatch("작품\\s*회차\\s*\\(([\\d,]+)\\)", heading.text())
                if (count != null) {
                    total = count.replace(",", "").toIntOrNull() ?: 0
                    break
                }
            }
            val completed = info.selectFirst(".bullet_comp, .bullet_comp_ex") != null
            val book = BookInfo(
                source = SourceId.NWEB, id = novel, url = listUrl, title = title,
                author = authors.firstOrNull(), synopsis = synopsis.ifEmpty { null },
                coverUrl = cover.ifEmpty { null }, tags = tags, status = if (completed) "완결" else "연재", language = "ko",
            )
            return ListPage(book, total, rows(document, tier, novel))
        }

        fun rows(document: Document, tier: String, novel: String): List<Row> {
            val pattern = "/$tier/detail\\?novelId=$novel&(?:amp;)?volumeNo=(\\d+)"
            val rows = mutableListOf<Row>()
            for (link in document.select("li.volumeComment a.list_item[href]")) {
                val volume = HttpSourceSupport.firstMatch(pattern, link.attr("href"))?.toIntOrNull() ?: continue
                var name = ""
                link.selectFirst(".subj")?.let { subject ->
                    subject.select(".bullet_wrap").remove()
                    name = subject.text()
                }
                rows += Row(volume, name.replace(Regex("\\s+"), " ").trim())
            }
            return rows
        }

        fun episode(html: String, url: String): Episode? {
            val document = Jsoup.parse(html, url)
            val content = document.selectFirst(".detail_view_content") ?: return null
            return Episode.Blocks(ReaderBlocks.fromHtml(content.outerHtml(), url))
        }

        // Publisher logo banners

        /** The same upload is served with different `?type=` resize hints. */
        fun imageKey(url: String): String = HttpSourceSupport.host(url) + HttpSourceSupport.path(url)

        fun imageName(key: String): String =
            runCatching { URLDecoder.decode(key.substringAfterLast('/'), "UTF-8") }.getOrDefault(key.substringAfterLast('/')).lowercase()

        fun trailingImages(blocks: List<ReaderBlock>): List<String> =
            blocks.asReversed().takeWhile { it is ReaderBlock.Image }.map { (it as ReaderBlock.Image).url }.reversed()
    }

    private suspend fun fetch(url: String, referer: String? = null, http: HttpFetching): HttpResponse {
        val headers = if (referer != null) headers + ("Referer" to referer) else headers
        return HttpSourceSupport.get(url, headers, http)
    }

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        if (isSeries(url)) {
            val response = fetch(url, http = context.http)
            if (needsLogin(response)) throw SourceException.LoginRequired(id)
            val webUrl = seriesWebEdition(response.text)
                ?: throw SourceException.Blocked("nseries episodes are app-only (DRM) and this product has no nweb edition to download.")
            context.logger.log(LogLevel.INFO, "nseries episodes are app-only (DRM). Downloading the free nweb edition: $webUrl")
            return parseNovel(webUrl, context)
        }
        return parseNovel(url, context)
    }

    private suspend fun parseNovel(url: String, context: SourceContext): BookInfo {
        val parts = novelParts(url) ?: throw SourceException.UnsupportedUrl(url)
        val listUrl = "$NOVEL_ORIGIN/${parts.tier}/list?novelId=${parts.novel}"
        val response = fetch(listUrl, http = context.http)
        if (needsLogin(response)) throw SourceException.LoginRequired(id)
        val page = listPage(response.text, parts.tier, parts.novel, listUrl)
        val allRows = page.rows.toMutableList()

        val perPage = page.rows.size
        if (perPage > 0 && page.total > perPage) {
            val pageCount = (page.total + perPage - 1) / perPage
            context.logger.log(LogLevel.INFO, "Reading $pageCount episode list pages (${page.total} episodes)...")
            allRows += coroutineScope {
                (2..pageCount).map { number ->
                    async {
                        val html = fetch("$listUrl&page=$number", listUrl, context.http).text
                        rows(Jsoup.parse(html), parts.tier, parts.novel)
                    }
                }.awaitAll().flatten()
            }
        }

        val seen = mutableSetOf<Int>()
        val unique = allRows.filter { seen.add(it.volume) }.sortedBy { it.volume }
        if (unique.isEmpty()) throw SourceException.Parse("no readable episodes were listed")
        if (page.total > 0 && unique.size != page.total) {
            context.logger.log(LogLevel.WARNING, "The page reports ${page.total} episodes but ${unique.size} were listed.")
        }
        val chapters = unique.mapIndexed { offset, row ->
            val name = row.name.ifEmpty { "${row.volume}화" }
            val chapterUrl = "$NOVEL_ORIGIN/${parts.tier}/detail?novelId=${parts.novel}&volumeNo=${row.volume}"
            ChapterRef(row.volume.toString(), offset + 1, name, chapterUrl)
        }
        detectEndImages(chapters, listUrl, context.http, context.logger)
        return page.book.copy(chapters = chapters)
    }

    // Chapters

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val url = ref.url ?: return ChapterResult.Failed(ref, "missing episode URL")
        return when (val episode = episodeBlocks(url, book.url, context.http)) {
            null -> ChapterResult.Failed(ref, "episode is not readable")
            Episode.NeedsLogin -> ChapterResult.Refused(ref, "needs a signed-in, age-verified account")
            is Episode.Blocks -> {
                val kept = dropEndImages(episode.blocks, book.url, context.http)
                if (kept.isEmpty()) ChapterResult.Failed(ref, "empty episode")
                else ChapterResult.Content(ReaderBlocks.content(kept, ref, "nweb-novel-content"))
            }
        }
    }

    private suspend fun episodeBlocks(url: String, referer: String?, http: HttpFetching): Episode? {
        val response = fetch(url, referer, http)
        if (needsLogin(response)) return Episode.NeedsLogin
        return episode(response.text, url)
    }

    private suspend fun signature(url: String, referer: String?, http: HttpFetching): NwebImageSignature? {
        val key = imageKey(url)
        session.cachedSignature(key)?.let { return it.value }
        val response = try {
            HttpSourceSupport.get(
                url,
                mapOf("User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT, "Referer" to (referer ?: NOVEL_ORIGIN)),
                http,
            )
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            null
        }
        val signature = response?.let { NwebImageSignature.fromImageData(it.body) }
        session.storeSignature(key, signature)
        return signature
    }

    private class Banner(val sample: Int, val url: String, val signature: NwebImageSignature)

    /**
     * Finds publisher logo banners to drop from every episode. Some works close each episode
     * with the publisher's logo, often a fresh upload per episode. A banner-shaped image that
     * closes at least two sampled episodes with the same appearance is a logo.
     */
    private suspend fun detectEndImages(chapters: List<ChapterRef>, referer: String, http: HttpFetching, logger: JobLogging) {
        if (chapters.size < 2) return
        val count = chapters.size
        val picks = setOf(0, count / 4, count / 2, (3 * count) / 4, count - 1).sorted()
        val banners = mutableListOf<Banner>()
        for ((sample, index) in picks.withIndex()) {
            if (!currentCoroutineContext().isActive) return
            val url = chapters[index].url ?: continue
            val episode = try {
                episodeBlocks(url, referer, http)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            if (episode !is Episode.Blocks) continue
            for (image in trailingImages(episode.blocks)) {
                val signature = signature(image, referer, http)
                if (signature != null && signature.isBanner) banners += Banner(sample, image, signature)
            }
        }
        val keys = mutableSetOf<String>()
        val names = mutableSetOf<String>()
        val logos = mutableListOf<NwebImageSignature>()
        for (banner in banners) {
            val samples = banners.filter { banner.signature.matches(it.signature) }.map { it.sample }.toSet()
            if (samples.size < 2) continue
            val key = imageKey(banner.url)
            keys += key
            names += imageName(key)
            if (logos.none { banner.signature.matches(it) }) logos += banner.signature
        }
        session.setLogos(NwebSession.Logos(keys, names, logos))
        if (keys.isNotEmpty()) {
            logger.log(LogLevel.INFO, "Removing the publisher logo from every episode: ${names.sorted().joinToString(", ")}")
        }
    }

    private suspend fun isEndImage(url: String, trailing: Boolean, referer: String?, http: HttpFetching): Boolean {
        val key = imageKey(url)
        val logos = session.logos()
        if (key in logos.keys) return true
        // Only images named like a logo or closing the episode are downloaded for comparison.
        if (!trailing && imageName(key) !in logos.names) return false
        val signature = signature(url, referer, http)
        if (signature == null || !signature.isBanner) return false
        return logos.signatures.any { signature.matches(it) }
    }

    /** Removes every copy of a detected publisher logo from an episode. */
    private suspend fun dropEndImages(blocks: List<ReaderBlock>, referer: String?, http: HttpFetching): List<ReaderBlock> {
        val logos = session.logos()
        if (logos.keys.isEmpty() && logos.signatures.isEmpty()) return blocks
        val lastText = blocks.indexOfLast { it is ReaderBlock.Text }
        val kept = mutableListOf<ReaderBlock>()
        for ((index, block) in blocks.withIndex()) {
            if (block is ReaderBlock.Image && isEndImage(block.url, index > lastText, referer, http)) continue
            kept += block
        }
        // An episode that is nothing but the logo keeps it rather than becoming empty.
        return kept.ifEmpty { blocks }
    }
}

/** Per-job logo detection results and the image signature cache. */
internal class NwebSession {
    data class Logos(
        val keys: Set<String> = emptySet(),
        val names: Set<String> = emptySet(),
        val signatures: List<NwebImageSignature> = emptyList(),
    )

    /** A cached lookup; `value` is null when the image could not be read. */
    class Cached(val value: NwebImageSignature?)

    private val mutex = Mutex()
    private var logos = Logos()
    private val signatures = mutableMapOf<String, Cached>()

    suspend fun logos(): Logos = mutex.withLock { logos }

    suspend fun setLogos(value: Logos) = mutex.withLock { logos = value }

    suspend fun cachedSignature(key: String): Cached? = mutex.withLock { signatures[key] }

    suspend fun storeSignature(key: String, signature: NwebImageSignature?) = mutex.withLock { signatures[key] = Cached(signature) }
}
