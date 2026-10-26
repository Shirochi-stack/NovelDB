package com.shirochi.npia.sources.globalnpia

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SharedCookieStore
import com.shirochi.npia.core.SharedCookies
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.delay
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.net.URI
import java.net.URLEncoder
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlin.math.max
import kotlin.math.min
import kotlin.math.pow
import kotlin.random.Random

/** Login token state shared by the calls of one adapter instance. */
internal class GlobalNpiaAuth {
    @Volatile
    var loginAt: String = ""
        private set

    @Volatile
    var attempted: Boolean = false
        private set

    fun store(value: String) {
        loginAt = value
    }

    fun markAttempted() {
        attempted = true
    }
}

/**
 * global_npia: HTTP JSON API (book, episode list, ticket, content) with the web-view login cookies.
 *
 * Ad-gated episodes (the desktop earns a free ticket with a browser "ad worker") are not supported
 * on mobile yet and come back as `Refused`.
 */
class GlobalNpiaAdapter(
    private val cookieStore: () -> SharedCookieStore? = { SharedCookies.store },
) : SourceAdapter {
    override val id = SourceId.GLOBAL_NPIA
    override val tier = SourceTier.LIGHT_BROWSER

    private val auth = GlobalNpiaAuth()

    /** Base cookies sent by hand when no shared cookie store is wired (tests, JVM tools). */
    private val fallbackCookies = ConcurrentHashMap<String, String>()

    override fun matches(url: String): Boolean = GlobalNpiaParsing.matches(url)

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val novelId = GlobalNpiaParsing.novelId(url)
        if (novelId.isEmpty()) throw SourceException.UnsupportedUrl(url)
        ensureBaseCookies()
        if (savedLogin()) {
            if (refreshLogin(context, expected = null)) {
                context.logger.log(LogLevel.INFO, "[global_npia] using the saved authenticated session")
            } else {
                context.logger.log(LogLevel.INFO, "[global_npia] saved login could not be used; continuing anonymously")
            }
        } else {
            context.logger.log(LogLevel.INFO, "[global_npia] no saved login; using anonymous access")
        }

        val novelResponse = requestJson(
            GlobalNpiaParsing.API + "/v1/novel", listOf("novel_no" to novelId),
            loginAt = auth.loginAt, maxRetries = 3, context = context,
        )
        val novelPayload = GlobalNpiaParsing.dict(novelResponse.payload)
        val result = GlobalNpiaParsing.dict(novelPayload?.get("result"))
        if (novelResponse.status != 200 || novelPayload == null || result == null || GlobalNpiaParsing.dict(result["novel"]) == null) {
            val message = GlobalNpiaParsing.payloadMessage(novelResponse.payload)
            throw SourceException.Parse(
                "metadata request failed (HTTP ${novelResponse.status})" + (if (message.isEmpty()) "" else ": $message"),
            )
        }
        val novel = GlobalNpiaParsing.dict(result["novel"]) ?: JsonObject(emptyMap())
        val info = GlobalNpiaParsing.dict(result["info"]) ?: JsonObject(emptyMap())
        var countValue = GlobalNpiaParsing.safeInt(info["epi_cnt"], fallback = 0)
        if (countValue == 0) countValue = GlobalNpiaParsing.safeInt(novel["count_epi"], fallback = 0)
        val rows = max(1, if (countValue == 0) 1000 else countValue)

        val listResponse = requestJson(
            GlobalNpiaParsing.API + "/v1/novel/episode/list",
            listOf("novel_no" to novelId, "rows" to rows.toString(), "sort" to "ASC"),
            loginAt = auth.loginAt, maxRetries = 3, context = context,
        )
        val listPayload = GlobalNpiaParsing.dict(listResponse.payload)
        val listResult = GlobalNpiaParsing.dict(listPayload?.get("result"))
        val episodes = listResult?.get("list") as? JsonArray
        if (listResponse.status != 200 || episodes == null) {
            val message = GlobalNpiaParsing.payloadMessage(listResponse.payload)
            throw SourceException.Parse(
                "episode list failed (HTTP ${listResponse.status})" + (if (message.isEmpty()) "" else ": $message"),
            )
        }
        val book = GlobalNpiaParsing.makeBook(novelId, novelPayload, episodes)
        context.logger.log(LogLevel.INFO, "[global_npia] ${book.title}: ${book.chapters.size} chapters")
        return book
    }

    // Chapter

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        var episodeNo = ""
        ref.url?.let { episodeNo = GlobalNpiaParsing.episodeId(it) }
        if (episodeNo.isEmpty()) episodeNo = ref.id
        if (episodeNo.isEmpty() || episodeNo.toLongOrNull() == null) {
            return ChapterResult.Failed(ref, "invalid chapter URL")
        }
        val chapterUrl: String = ref.url ?: "${GlobalNpiaParsing.BASE}/viewer/$episodeNo"
        val loggedIn = savedLogin()
        val deniedReason = if (loggedIn) LOGIN_REFUSAL else "login required"

        if (auth.loginAt.isEmpty()) {
            val cached = context.secrets.get(TOKEN_KEY)
            if (!cached.isNullOrEmpty()) auth.store(cached)
        }
        if (auth.loginAt.isEmpty() && loggedIn && !auth.attempted) {
            refreshLogin(context, expected = null)
        }

        var authRefreshed = false
        repeat(3) {
            val loginAt = auth.loginAt
            val ticketResponse = requestJson(
                GlobalNpiaParsing.API + "/v1/novel/episode", listOf("episode_no" to episodeNo),
                loginAt = loginAt, maxRetries = 4, context = context,
            )
            val status = ticketResponse.status
            val ticket = ticketResponse.payload
            if (GlobalNpiaParsing.authExpired(status, ticket) && !authRefreshed) {
                authRefreshed = true
                if (refreshLogin(context, expected = loginAt)) return@repeat
            }
            if (GlobalNpiaParsing.adRequired(status, ticket)) {
                return ChapterResult.Refused(ref, AD_REFUSAL)
            }
            if (status >= 400 || status == 0) {
                if (GlobalNpiaParsing.accessDenied(status, ticket)) return ChapterResult.Refused(ref, deniedReason)
                val message = GlobalNpiaParsing.payloadMessage(ticket)
                return ChapterResult.Failed(ref, "ticket failed (HTTP $status)" + (if (message.isEmpty()) "" else ": $message"))
            }
            val extracted = GlobalNpiaParsing.extractTicketToken(ticket)
            if (extracted.token.isEmpty() && extracted.directUrl.isEmpty()) {
                if (GlobalNpiaParsing.accessDenied(status, ticket)) return ChapterResult.Refused(ref, deniedReason)
                return ChapterResult.Failed(ref, "ticket contained no content token")
            }
            val contentUrl = extracted.directUrl.ifEmpty { GlobalNpiaParsing.API + "/v1/novel/episode/content" }
            val query: List<Pair<String, String>> =
                if (extracted.directUrl.isEmpty()) listOf("_t" to extracted.token) else emptyList()
            val content = requestJson(contentUrl, query, loginAt = "", maxRetries = 3, context = context)
            if (content.status >= 400 || content.status == 0) {
                if (GlobalNpiaParsing.accessDenied(content.status, content.payload)) {
                    return ChapterResult.Refused(ref, deniedReason)
                }
                val message = GlobalNpiaParsing.payloadMessage(content.payload)
                return ChapterResult.Failed(
                    ref, "content failed (HTTP ${content.status})" + (if (message.isEmpty()) "" else ": $message"),
                )
            }
            val raw = GlobalNpiaParsing.rawContent(content.payload)
            if (raw.isEmpty()) return ChapterResult.Failed(ref, "empty content")
            val cookies = GlobalNpiaParsing.signedImageCookies(ticket)
            return buildResult(raw, ref, chapterUrl, cookies, context)
        }
        return ChapterResult.Failed(ref, "could not obtain an episode ticket")
    }

    private suspend fun buildResult(
        raw: String,
        ref: ChapterRef,
        chapterUrl: String,
        cookies: Map<String, String>,
        context: SourceContext,
    ): ChapterResult {
        val processed: GlobalNpiaParsing.ProcessedChapter? = try {
            GlobalNpiaParsing.processChapterHtml(raw, chapterUrl, ref.index)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return ChapterResult.Failed(ref, "could not parse chapter HTML: ${e.message}")
        }
        val chapter = processed ?: return ChapterResult.Failed(ref, "empty content")

        val images = mutableListOf<ImageRef>()
        for (asset in chapter.assets) {
            var data: ByteArray? = null
            if (asset.url.startsWith("data:")) {
                data = GlobalNpiaParsing.dataUriBytes(asset.url)
            } else if (cookies.isNotEmpty() && hostOf(asset.url) == GlobalNpiaParsing.IMAGE_HOST) {
                val cookieHeader = GlobalNpiaParsing.imageCookieNames
                    .mapNotNull { name -> cookies[name]?.let { "$name=$it" } }
                    .joinToString("; ")
                val headers = mapOf("Cookie" to cookieHeader, "Referer" to GlobalNpiaParsing.BASE + "/")
                val response = try {
                    context.http.get(asset.url, headers)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    null
                }
                if (response != null && response.status in 200..299 && response.body.isNotEmpty()) data = response.body
            }
            images.add(ImageRef(asset.url, asset.filename, data))
        }
        return ChapterResult.Content(ChapterContent(ref, chapter.html, images))
    }

    private fun hostOf(url: String): String = runCatching { URI(url).host }.getOrNull()?.lowercase() ?: ""

    // Auth

    private fun cookieValues(): Map<String, String> {
        val store = cookieStore() ?: return HashMap(fallbackCookies)
        val values = mutableMapOf<String, String>()
        for (origin in listOf(GlobalNpiaParsing.BASE, GlobalNpiaParsing.API)) values.putAll(store.cookies(origin))
        return values
    }

    private fun savedLogin(): Boolean = GlobalNpiaParsing.hasSavedLogin(cookieValues())

    /** The API wants a USERKEY and last_login cookie even for anonymous access. */
    private fun ensureBaseCookies() {
        val existing = cookieValues()
        val store = cookieStore()
        fun add(name: String, value: String) {
            if (store != null) {
                store.setCookie(GlobalNpiaParsing.BASE, name, value, GlobalNpiaParsing.COOKIE_DOMAIN)
            } else {
                fallbackCookies[name] = value
            }
        }
        if (existing["USERKEY"] == null) add("USERKEY", UUID.randomUUID().toString().replace("-", "").lowercase())
        if (existing["last_login"] == null) add("last_login", "basic")
    }

    /** Exchanges the web-view login cookies for a fresh LOGINAT token (desktop `_global_npia_refresh_login`). */
    private suspend fun refreshLogin(context: SourceContext, expected: String?): Boolean {
        if (expected != null && auth.loginAt.isNotEmpty() && auth.loginAt != expected) return true
        if (expected == null && auth.attempted && auth.loginAt.isEmpty()) return false
        auth.markAttempted()
        val response = try {
            requestJson(GlobalNpiaParsing.API + "/v1/login/refresh", emptyList(), loginAt = "", maxRetries = 1, context = context)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            return false
        }
        val token = GlobalNpiaParsing.loginToken(response.status, response.payload) ?: return false
        auth.store(token)
        context.secrets.set(TOKEN_KEY, token)
        return true
    }

    // HTTP

    internal class JsonReply(val status: Int, val payload: JsonElement?)

    private fun encode(value: String): String = URLEncoder.encode(value, "UTF-8").replace("+", "%20")

    private suspend fun requestJson(
        urlString: String,
        query: List<Pair<String, String>>,
        loginAt: String,
        maxRetries: Int,
        context: SourceContext,
    ): JsonReply {
        var url = urlString
        if (query.isNotEmpty()) {
            val joined = query.joinToString("&") { (name, value) -> encode(name) + "=" + encode(value) }
            url += (if (url.contains('?')) "&" else "?") + joined
        }
        val headers = mutableMapOf(
            "Accept" to "application/json, text/plain, */*",
            "Accept-Language" to "en-US,en;q=0.9",
            "Origin" to GlobalNpiaParsing.BASE,
            "Referer" to GlobalNpiaParsing.BASE + "/",
            "User-Agent" to USER_AGENT,
            "X-Requested-With" to "XMLHttpRequest",
        )
        if (loginAt.isNotEmpty()) headers["login-at"] = GlobalNpiaParsing.percentDecode(loginAt) ?: loginAt
        if (cookieStore() == null && fallbackCookies.isNotEmpty()) {
            headers["Cookie"] = fallbackCookies.entries.sortedBy { it.key }.joinToString("; ") { "${it.key}=${it.value}" }
        }

        var lastStatus = 0
        var lastPayload: JsonElement? = null
        val attempts = max(1, maxRetries)
        for (attempt in 1..attempts) {
            try {
                val response = context.http.send(HttpRequest(url, headers = headers, timeoutSeconds = 90))
                lastStatus = response.status
                val text = response.text
                lastPayload = try {
                    Json.parseToJsonElement(text)
                } catch (e: Exception) {
                    JsonObject(mapOf("error" to JsonPrimitive(text.take(300))))
                }
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                lastStatus = 0
                lastPayload = JsonObject(mapOf("error" to JsonPrimitive(e.message ?: e.toString())))
            }
            val retryable = (lastStatus == 429 || lastStatus >= 500 || lastStatus == 0) &&
                !GlobalNpiaParsing.adRequired(lastStatus, lastPayload)
            if (!retryable || attempt >= attempts) break
            val base = if (lastStatus == 429) max(5.0, 1.25.pow(attempt + 2)) else min(3.0, 1.25.pow(attempt))
            val wait = base + Random.nextDouble(0.2, 0.8)
            delay((wait * 1000).toLong())
        }
        return JsonReply(lastStatus, lastPayload)
    }

    companion object {
        const val AD_REFUSAL: String = "needs an ad ticket; not supported yet"
        const val LOGIN_REFUSAL: String = "login required or not available to this account"
        private val TOKEN_KEY = SecretKey.source(SourceId.GLOBAL_NPIA, "token")
        private const val USER_AGENT: String =
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    }
}
