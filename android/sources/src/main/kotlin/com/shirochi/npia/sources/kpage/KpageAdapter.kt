package com.shirochi.npia.sources.kpage

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebProfile
import com.shirochi.npia.core.evaluate
import com.shirochi.npia.core.waitFor
import com.shirochi.npia.core.withPage
import com.shirochi.npia.sources.websupport.WebRegex
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.delay
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import kotlinx.serialization.builtins.serializer
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.net.URI

/** Hosts and endpoints for the kpage source (escaped so the provider name stays out of the repo). */
internal object KpageHosts {
    const val SITE_HOST = "page.\u006b\u0061\u006b\u0061\u006f.com"
    const val API_HOST = "bff-page.\u006b\u0061\u006b\u0061\u006f.com"
    const val SITE = "https://$SITE_HOST"
    const val API = "https://$API_HOST"

    const val USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

    fun apiHeaders(): Map<String, String> = mapOf(
        "User-Agent" to USER_AGENT,
        "Referer" to "$SITE/",
        "Origin" to SITE,
        "Accept" to "application/json, text/plain, */*",
    )
}

private class KpageBookPage(val html: String, val tags: List<String>)

private class KpageApiResult(val status: Int, val body: ByteArray)

/**
 * kpage: metadata from the rendered content page (web engine), episode list and viewer data from
 * the site's JSON gateway (plain HTTP, browser fallback), text chunks as JSON paragraph lists, and
 * image-only episodes as image pages. Paid episodes the account cannot open are refused; nothing
 * is ever purchased or rented.
 */
class KpageAdapter : SourceAdapter {
    override val id = SourceId.KPAGE
    override val tier = SourceTier.LIGHT_BROWSER

    override fun matches(url: String): Boolean {
        val uri = runCatching { URI(url.trim()) }.getOrNull() ?: return false
        val scheme = uri.scheme?.lowercase() ?: return false
        if (scheme != "http" && scheme != "https") return false
        if ((uri.host ?: "").lowercase() != KpageHosts.SITE_HOST) return false
        return WebRegex.matches("^/content/\\d+", uri.path ?: "")
    }

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val seriesId = KpageParsing.seriesId(url) ?: throw SourceException.UnsupportedUrl(url)
        context.logger.log(LogLevel.INFO, "[kpage] Navigating to: $url")

        val aboutUrl: String? = runCatching {
            val uri = URI(url)
            URI(uri.scheme, uri.authority, uri.path, "tab_type=about", null).toString()
        }.getOrNull()

        val loaded: KpageBookPage = context.web.withPage(WebProfile.EXTERNAL) { page ->
            page.load(url, WebLoadState.DOM_CONTENT_LOADED, 30)
            // The page is a SPA; wait for the title block to render.
            try {
                page.waitFor(
                    "document.querySelector('h2') && document.querySelector('meta[property=\"og:title\"]')",
                    timeoutSeconds = 12.0,
                )
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                // Keep going with whatever rendered.
            }
            delay(1000)
            val html = page.html()

            var tags: List<String> = emptyList()
            if (aboutUrl != null) {
                tags = try {
                    page.load(aboutUrl, WebLoadState.DOM_CONTENT_LOADED, 15)
                    delay(1500)
                    page.evaluate<List<String>>(TAG_SCRIPT)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    emptyList()
                }
            }
            KpageBookPage(html, tags)
        }

        val meta = KpageParsing.parseMetadata(loaded.html)
        if (meta.title.isEmpty()) throw SourceException.Parse("Could not extract the title")
        context.logger.log(LogLevel.INFO, "[kpage] Title: ${meta.title}, author: ${meta.author}")

        // Episode list: one request returns every episode.
        val listUrl = "${KpageHosts.API}/api/gateway/api/v2/content/product/list?series_id=$seriesId" +
            "&cursor_index=0&cursor_direction=ANCHOR&window_size=10000&sort_opt=asc"
        val listing = fetchApi(listUrl, url, context)
        val episodes = KpageParsing.parseEpisodes(listing.body)
        if (episodes.isEmpty()) {
            context.logger.log(LogLevel.WARNING, "[kpage] No episodes found (HTTP ${listing.status}).")
        }

        val chapters = episodes.mapIndexed { offset, episode ->
            val viewer = "${KpageHosts.SITE}/content/$seriesId/viewer/${episode.productId}"
            val title = if (episode.fullName.isEmpty()) episode.shortName else episode.fullName
            ChapterRef(
                id = episode.productId.toString(), index = offset + 1, title = title,
                url = viewer, isPaid = !episode.isAccessible,
            )
        }
        val rentedOrBought = episodes.count { it.isAccessible && !it.isFree }
        if (rentedOrBought > 0) {
            context.logger.log(LogLevel.INFO, "[kpage] Detected $rentedOrBought rented/purchased episode(s) as accessible.")
        }

        var coverText = meta.cover
        if (coverText.startsWith("//")) coverText = "https:$coverText"
        return BookInfo(
            source = id, id = seriesId, url = url, title = meta.title,
            author = meta.author.ifEmpty { null },
            synopsis = meta.description.ifEmpty { null },
            coverUrl = coverText.ifEmpty { null },
            tags = loaded.tags, language = "ko", chapters = chapters,
        )
    }

    // Chapters

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult =
        readChapter(ref, context, 0)

    /** Concurrent like the default, plus the optional cross-chapter duplicate image removal. */
    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> {
        val limit = context.shared(id).threads.coerceAtLeast(1)
        val options = context.settings.sources.kpage
        val leading = if (options.dedupeImages) options.dedupeLeadingImages.coerceIn(1, 10) else 0

        val ordered: List<ChapterResult> = coroutineScope {
            val permits = Semaphore(limit)
            refs.map { ref ->
                async {
                    permits.withPermit {
                        val result = try {
                            readChapter(ref, context, leading)
                        } catch (e: CancellationException) {
                            throw e
                        } catch (e: Exception) {
                            ChapterResult.Failed(ref, e.message ?: e.toString())
                        }
                        context.onChapter(result)
                        result
                    }
                }
            }.awaitAll()
        }
        if (leading <= 0) return ordered

        val contents = ordered.mapNotNull { (it as? ChapterResult.Content)?.content }
        val deduped = KpageParsing.removeDuplicateLeadingImages(contents, leading)
        var cursor = 0
        return ordered.map { result ->
            if (result is ChapterResult.Content && cursor < deduped.size) {
                ChapterResult.Content(deduped[cursor++])
            } else {
                result
            }
        }
    }

    private suspend fun readChapter(ref: ChapterRef, context: SourceContext, leadingImageBytes: Int): ChapterResult {
        if (context.shared(id).skipPaid && ref.isPaid) {
            return ChapterResult.Refused(ref, "Paid episode skipped (skip paid is on).")
        }
        val urlText = ref.url ?: return ChapterResult.Failed(ref, "Invalid viewer URL.")
        val ids = KpageParsing.viewerIds(urlText) ?: return ChapterResult.Failed(ref, "Invalid viewer URL.")
        val (series, product) = ids
        val viewerUrl = "${KpageHosts.API}/api/gateway/api/v1/viewer/data?series_id=$series&product_id=$product"
        val contentPage = "${KpageHosts.SITE}/content/$series"

        val response = fetchApi(viewerUrl, contentPage, context)
        return when (val outcome = KpageParsing.parseViewer(response.body, response.status)) {
            is KpageViewerOutcome.Locked ->
                ChapterResult.Refused(ref, "Not purchased or rentable with this account (${outcome.reason}). Nothing was bought.")

            is KpageViewerOutcome.Unavailable -> {
                if (response.status == 401 || response.status == 403) {
                    ChapterResult.Refused(ref, "Access denied (HTTP ${response.status}). Sign in with an account that owns this episode.")
                } else {
                    ChapterResult.Failed(ref, "Viewer data unavailable: ${outcome.reason} (HTTP ${response.status}).")
                }
            }

            is KpageViewerOutcome.Images -> {
                val built = KpageParsing.buildImageChapter(outcome.files, ref.title, ref.index, contentPage)
                    ?: return ChapterResult.Failed(ref, "No images in this episode.")
                val images = built.images.toMutableList()
                if (leadingImageBytes > 0) {
                    for (position in 0 until minOf(leadingImageBytes, images.size)) {
                        val bytes = fetchImageBytes(images[position].url, context)
                        if (bytes != null) images[position] = images[position].copy(data = bytes)
                    }
                }
                ChapterResult.Content(ChapterContent(ref, built.html, images))
            }

            is KpageViewerOutcome.Text -> {
                val chunks = mutableListOf<ByteArray>()
                for (secure in outcome.secureUrls) {
                    val chunkUrl = outcome.baseUrl + secure
                    try {
                        val chunk = context.http.get(
                            chunkUrl,
                            mapOf("User-Agent" to KpageHosts.USER_AGENT, "Referer" to "${KpageHosts.SITE}/"),
                        )
                        val contentType = chunk.headers.entries.firstOrNull { it.key.lowercase() == "content-type" }?.value ?: ""
                        if (chunk.status !in 200..299 || !contentType.lowercase().contains("json")) continue
                        chunks += chunk.body
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        continue
                    }
                }
                val options = context.settings.sources.kpage
                val paragraphs = KpageParsing.extractParagraphs(chunks, options.skipLastPage, options.keepFiller)
                if (paragraphs.isEmpty()) {
                    ChapterResult.Failed(ref, "No readable text in this episode.")
                } else {
                    val output = KpageParsing.buildOutput(paragraphs)
                    val titled = ref.copy(title = KpageParsing.headingTitle(paragraphs, ref.title))
                    ChapterResult.Content(ChapterContent(titled, output.html))
                }
            }
        }
    }

    private suspend fun fetchImageBytes(url: String, context: SourceContext): ByteArray? = try {
        val response = context.http.get(url, mapOf("User-Agent" to KpageHosts.USER_AGENT, "Referer" to "${KpageHosts.SITE}/"))
        if (response.status in 200..299 && response.body.isNotEmpty()) response.body else null
    } catch (e: CancellationException) {
        throw e
    } catch (e: Exception) {
        null
    }

    // Gateway requests

    /**
     * GET a gateway JSON endpoint with plain HTTP; when that is refused or not JSON (the gateway
     * can reject requests without a page origin), repeat the request from inside the content page.
     */
    private suspend fun fetchApi(url: String, contentPage: String, context: SourceContext): KpageApiResult {
        var status = 0
        var body = ByteArray(0)
        try {
            val response = context.http.get(url, KpageHosts.apiHeaders())
            status = response.status
            body = response.body
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            status = 0
        }
        val needsFallback = status == 0 || status == 401 || status == 403 || !looksLikeJson(body)
        if (needsFallback) {
            val browser = try {
                browserFetch(url, contentPage, context)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                null
            }
            if (browser != null) {
                val bytes = browser.second.toByteArray(Charsets.UTF_8)
                if (looksLikeJson(bytes)) return KpageApiResult(browser.first, bytes)
            }
        }
        return KpageApiResult(status, body)
    }

    private suspend fun browserFetch(url: String, contentPage: String, context: SourceContext): Pair<Int, String> {
        val literal = jsLiteral(url)
        val script = """
            const response = await fetch($literal, { credentials: 'include' });
            const text = await response.text();
            return { status: response.status, text: text };
        """.trimIndent()
        return context.web.withPage(WebProfile.EXTERNAL) { page ->
            page.load(contentPage, WebLoadState.DOM_CONTENT_LOADED, 30)
            val raw = page.evaluateJson(script)
            val obj = Json.parseToJsonElement(raw) as? JsonObject ?: throw SourceException.Parse("Unexpected browser fetch result")
            val status = KpageJson.int(obj["status"])
            val text = (obj["text"] as? JsonPrimitive)?.content ?: throw SourceException.Parse("Unexpected browser fetch result")
            Pair(status, text)
        }
    }

    companion object {
        private val TAG_SCRIPT = """
            const tags = [];
            const nodes = document.querySelectorAll('a, span, div');
            for (let i = 0; i < nodes.length; i++) {
              const t = (nodes[i].innerText || '').trim();
              if (t.startsWith('#') && t.length > 1 && t.length < 30) {
                const tag = t.substring(1);
                if (tags.indexOf(tag) === -1) tags.push(tag);
              }
            }
            return tags;
        """.trimIndent()

        internal fun looksLikeJson(data: ByteArray): Boolean {
            val text = String(data, 0, minOf(data.size, 64), Charsets.UTF_8)
            val first = text.trim().firstOrNull()
            return first == '{' || first == '['
        }

        internal fun jsLiteral(text: String): String = Json.encodeToString(String.serializer(), text)
    }
}
