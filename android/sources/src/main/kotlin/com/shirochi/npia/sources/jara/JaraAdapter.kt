package com.shirochi.npia.sources.jara

import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpStatusException
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SourceAdapter
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.SourceTier
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.ReaderBlock
import com.shirochi.npia.sources.http.ReaderBlocks
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonElement
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.JsonPrimitive
import java.util.UUID
import kotlin.coroutines.coroutineContext

/**
 * Korean web-novel platform read through its public mobile-web JSON API.
 * Port of `_jara_*` in the desktop `external_scraper.py`, and of the iOS `JaraAdapter`.
 *
 * Chapter text is AES-CBC encrypted with a short-lived key from `chapter_valid`, and
 * chapter requests are paced (one every 5 s) to stay under the site's bot check.
 * Purchased and adult chapters need the site's login token: either saved in the
 * `SecretStore` under `jara.token`, or read from the `signedInfo` localStorage entry of
 * the `jara` web profile after the user signs in there.
 */
class JaraAdapter : SourceAdapter {
    override val id = SourceId.JARA
    override val tier = SourceTier.HTTP

    private val session = JaraSession()

    override fun matches(url: String): Boolean = bookCode(url) != null

    companion object {
        const val HOST = "\u006a\u006f\u0061\u0072\u0061.com"
        const val ORIGIN = "https://www.$HOST"
        const val API = "https://api.$HOST"
        const val HOME = "https://www.$HOST/"
        val tokenKey = SecretKey.source(SourceId.JARA, "token")
        val webProfile = SourceId.JARA.raw

        /** Public production config from the site's web bundle; re-read live when possible. */
        val defaultParams = mapOf(
            "api_key" to "mw_8ba234e7801ba288554ca07ae44c7",
            "ver" to "3.2.0",
            "device" to "mw",
            "devicetoken" to "mw",
        )
        const val MIN_REQUEST_INTERVAL = 5.0
        const val KEY_MAX_AGE = 20.0
        const val KEY_TTL = 31.5
        const val DECRYPT_RETRIES = 2

        val headers = mapOf(
            "User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT,
            "Accept" to "application/json",
            "Accept-Language" to "ko-KR,ko;q=0.9,en;q=0.7",
            "Origin" to ORIGIN,
            "Referer" to "$ORIGIN/",
        )

        fun bookCode(url: String): String? {
            val host = HttpSourceSupport.host(url)
            if (host !in setOf(HOST, "www.$HOST", "m.$HOST")) return null
            HttpSourceSupport.firstMatch("^/book/(\\d+)/?$", HttpSourceSupport.path(url))?.let { return it }
            for (key in listOf("bookCode", "book_code")) {
                val value = HttpSourceSupport.query(url, key)
                if (HttpSourceSupport.isPositiveInteger(value)) return value
            }
            return null
        }

        // Helpers

        fun isTrue(value: JsonElement?): Boolean = HttpSourceSupport.string(value).trim().uppercase() in setOf("TRUE", "Y", "1")

        fun isLoginError(payload: JsonObject): Boolean =
            HttpSourceSupport.int(payload["error_code"]) == 9200 || HttpSourceSupport.string(payload["message"]).contains("로그인")

        fun isOk(payload: JsonObject): Boolean = HttpSourceSupport.int(payload["status"]) == 1

        /** "N화", keeping the episode subtitle as a suffix unless it is already numbered. */
        fun chapterName(sortno: Int, title: String): String {
            val label = if (sortno > 0) "${sortno}화" else ""
            if (title.isEmpty()) return label.ifEmpty { "Chapter" }
            if (label.isEmpty() || Regex("\\d+\\s*(?:화|회|장|편)").containsMatchIn(title)) return title
            return "$label - $title"
        }

        /** Token from the `signedInfo` localStorage value, unless it has expired. */
        fun tokenFromSignedInfo(raw: String, nowMillis: Long = System.currentTimeMillis()): String? {
            val wrapper = runCatching { HttpSourceSupport.json.parseToJsonElement(raw) }.getOrNull() as? JsonObject ?: return null
            val expire = HttpSourceSupport.double(wrapper["expire"])
            if (expire > 0 && expire < nowMillis) return null
            var inner: JsonElement = wrapper["data"] ?: wrapper
            if (inner is JsonPrimitive && inner.isString) {
                inner = runCatching { HttpSourceSupport.json.parseToJsonElement(inner.content) }.getOrNull() ?: return null
            }
            val token = HttpSourceSupport.string((inner as? JsonObject)?.get("token")).trim()
            return token.ifEmpty { null }
        }

        /** Reads `apiKey`, `version`, `device` and `devicetoken` from the site's main bundle. */
        fun paramsFromBundle(bundle: String): Map<String, String> {
            val anchor = Regex("[\"']https://api\\.${Regex.escape(HOST)}[\"']\\s*,\\s*apiKey\\s*:")
            val start = anchor.find(bundle)?.range?.first ?: return emptyMap()
            val config = bundle.substring(start).take(600)
            val out = linkedMapOf<String, String>()
            for ((key, name) in listOf("api_key" to "apiKey", "ver" to "version", "device" to "device", "devicetoken" to "devicetoken")) {
                HttpSourceSupport.firstMatch("\\b$name\\s*:\\s*[\"']([^\"']+)[\"']", config)?.let { out[key] = it }
            }
            return out
        }

        fun book(payload: JsonObject, code: String): BookInfo {
            val book = HttpSourceSupport.obj(payload["book"]) ?: JsonObject(emptyMap())
            val rows = (HttpSourceSupport.array(book["chapter"]) ?: JsonArray(emptyList()))
                .mapNotNull { it as? JsonObject }
                .filter { HttpSourceSupport.string(it["cid"]).isNotEmpty() }
            if (HttpSourceSupport.string(book["book_code"]) != code || rows.isEmpty()) {
                throw SourceException.Parse("the book has no readable chapter list")
            }
            val paidStore = isTrue(book["is_premium"]) || isTrue(book["is_nobless"]) || isTrue(book["is_finish"])
            val chapters = mutableListOf<ChapterRef>()
            for (row in rows.sortedBy { HttpSourceSupport.int(it["sortno"]) }) {
                val sortno = HttpSourceSupport.int(row["sortno"])
                val title = HttpSourceSupport.unescapeHtml(HttpSourceSupport.string(row["sub_subject"])).trim()
                val cid = HttpSourceSupport.string(row["cid"])
                val isPaid = paidStore && !isTrue(row["is_free"])
                val url = "$ORIGIN/viewer?cid=${HttpSourceSupport.encode(cid)}&bookCode=${HttpSourceSupport.encode(code)}&sortno=$sortno"
                // A paid chapter the account owns (`is_buy`) is still marked paid; the chapter
                // request decides whether it can be read.
                chapters += ChapterRef(cid, chapters.size + 1, chapterName(sortno, title), url, isPaid = isPaid)
            }
            val title = HttpSourceSupport.unescapeHtml(HttpSourceSupport.string(book["subject"])).trim()
            val author = HttpSourceSupport.unescapeHtml(HttpSourceSupport.string(book["writer_name"])).trim()
            val intro = HttpSourceSupport.unescapeHtml(HttpSourceSupport.string(book["intro"])).replace("\r\n", "\n").trim()
            val keywordValue = book["keyword"]
            val keywords = if (keywordValue is JsonArray) {
                keywordValue.map { HttpSourceSupport.string(it) }
            } else {
                HttpSourceSupport.string(keywordValue).split(Regex("[,#\\s]"))
            }
            val tags = mutableListOf<String>()
            for (keyword in keywords) {
                val tag = keyword.trim { it == '#' || it.isWhitespace() }
                if (tag.isNotEmpty() && tag !in tags) tags += tag
            }
            val category = HttpSourceSupport.string(book["category_ko_name"]).trim()
                .ifEmpty { HttpSourceSupport.string(book["category_name"]).trim() }
            if (category.isNotEmpty() && category !in tags) tags.add(0, category)
            return BookInfo(
                source = SourceId.JARA, id = code, url = "$ORIGIN/book/$code",
                title = title.ifEmpty { "Jara Book $code" },
                author = author.ifEmpty { null }, synopsis = intro.ifEmpty { null },
                coverUrl = HttpSourceSupport.string(book["book_img"]).ifEmpty { null }, tags = tags,
                status = if (isTrue(book["chk_finish"])) "완결" else "연재", language = "ko", chapters = chapters,
            )
        }

        fun chapter(text: String, note: String, ref: ChapterRef): ChapterResult {
            val isHtml = Regex("<\\s*/?\\s*(?:p|br|img|div|span)\\b", RegexOption.IGNORE_CASE).containsMatchIn(text)
            val blocks = (if (isHtml) ReaderBlocks.fromHtml(text, HOME) else ReaderBlocks.fromPlainText(text)).toMutableList()
            val trimmedNote = note.replace("\r\n", "\n").trim()
            if (trimmedNote.isNotEmpty()) {
                blocks += ReaderBlock.Text("* * *")
                blocks += ReaderBlocks.fromPlainText(trimmedNote)
            }
            if (blocks.isEmpty()) return ChapterResult.Failed(ref, "empty chapter")
            return ChapterResult.Content(ReaderBlocks.content(blocks, ref, "jara-content"))
        }
    }

    // Book

    override suspend fun parseBook(url: String, context: SourceContext): BookInfo {
        val code = bookCode(url) ?: throw SourceException.UnsupportedUrl(url)
        val token = loadToken(context)
        session.setToken(token)
        if (token != null) context.logger.log(LogLevel.INFO, "Using the saved jara login for purchased and adult chapters.")
        val query = mapOf("book_code" to code, "promotion_code" to "")
        var payload = apiGet("/v1/book/detail.joa", query, context)
        if (!isOk(payload) && token != null && isLoginError(payload)) {
            context.logger.log(LogLevel.WARNING, "The saved jara login was rejected; continuing with free chapters only.")
            session.setToken(null)
            payload = apiGet("/v1/book/detail.joa", query, context)
        }
        if (!isOk(payload)) {
            if (isLoginError(payload)) throw SourceException.LoginRequired(id)
            throw SourceException.Parse(HttpSourceSupport.string(payload["message"]).ifEmpty { "unknown error" })
        }
        return book(payload, code)
    }

    // Chapters

    /** Chapters are fetched one at a time: the site counts quick chapter requests. */
    override suspend fun parseChapters(refs: List<ChapterRef>, book: BookInfo, context: SourceContext): List<ChapterResult> {
        val results = mutableListOf<ChapterResult>()
        for (ref in refs) {
            coroutineContext.ensureActive()
            try {
                results += parseChapter(ref, book, context)
            } catch (e: CancellationException) {
                throw e
            } catch (e: JaraAbort) {
                // Bot check still active: stop asking, report the rest as failed.
                val start = results.size
                results += ChapterResult.Failed(ref, e.message.orEmpty())
                for (rest in refs.drop(results.size)) results += ChapterResult.Failed(rest, e.message.orEmpty())
                results.subList(start, results.size).forEach(context.onChapter)
                return results
            } catch (e: Exception) {
                results += ChapterResult.Failed(ref, e.message ?: e.toString())
            }
            context.onChapter(results.last())
            if (ref !== refs.last()) context.shared(id).pauseBetweenChapters()
        }
        return results
    }

    override suspend fun parseChapter(ref: ChapterRef, book: BookInfo, context: SourceContext): ChapterResult {
        val cid = ref.url?.let { HttpSourceSupport.query(it, "cid") } ?: ref.id
        var decryptFailures = 0
        var captchaChecks = 0
        while (true) {
            session.waitForRequestSlot(MIN_REQUEST_INTERVAL)
            val keyPair = chapterKey(decryptFailures > 0, context)
            session.reserveAfterKeyWait(MIN_REQUEST_INTERVAL)
            val payload = apiGet("/v1/book/chapter.joa", mapOf("cid" to cid), context)
            val data = HttpSourceSupport.obj(payload["chapter"]) ?: JsonObject(emptyMap())
            val redis = HttpSourceSupport.obj(data["redis_data"])
            if (isTrue(payload["is_captcha"]) || isTrue(redis?.get("is_captcha"))) {
                captchaChecks++
                if (captchaChecks > 3) {
                    throw JaraAbort("The bot check is still active. Open the defender page in the jara web profile, complete it, then download the remaining chapters again.")
                }
                if (captchaChecks == 1) {
                    // The desktop clicks the captcha in a headless browser. Here the user
                    // completes it in a visible web view.
                    context.logger.log(LogLevel.WARNING, "jara asked for a captcha; opening it for you to complete.")
                    try {
                        context.web.presentLogin("$ORIGIN/defender", webProfile)
                    } catch (e: CancellationException) {
                        throw e
                    } catch (e: Exception) {
                        throw JaraAbort("Download stopped: the bot check was not completed.")
                    }
                    loadToken(context)?.let { session.setToken(it) }
                } else {
                    HttpSourceSupport.sleep(3.0)
                }
                continue
            }
            if (!isOk(payload) || data.isEmpty()) {
                if (isLoginError(payload)) return ChapterResult.Refused(ref, "needs a login that owns this chapter")
                val message = HttpSourceSupport.string(payload["message"])
                if (ref.isPaid) return ChapterResult.Refused(ref, message.ifEmpty { "paid chapter not owned" })
                return ChapterResult.Failed(ref, message.ifEmpty { "unknown error" })
            }
            val text = try {
                JaraCrypto.decrypt(HttpSourceSupport.string(data["content"]), keyPair.key, keyPair.iv)
            } catch (e: JaraCrypto.Failure) {
                // Encrypted with a key that expired before the server answered: fetch a new key
                // and request the chapter again, as the site's own viewer does.
                decryptFailures++
                if (decryptFailures > DECRYPT_RETRIES) return ChapterResult.Failed(ref, "could not decrypt the chapter")
                HttpSourceSupport.sleep(1.5)
                continue
            }
            return chapter(text, HttpSourceSupport.string(data["episode"]), ref)
        }
    }

    // API

    private suspend fun apiGet(path: String, params: Map<String, String>, context: SourceContext, useToken: Boolean = true): JsonObject {
        val query = publicParams(context).toMutableMap()
        if (useToken) session.token()?.let { query["token"] = it }
        query.putAll(params)
        val url = API + path + "?" + query.toSortedMap().entries.joinToString("&") {
            HttpSourceSupport.encode(it.key) + "=" + HttpSourceSupport.encode(it.value)
        }
        val response = context.http.get(url, headers)
        if (response.status >= 500) throw HttpStatusException(response.status, url)
        return try {
            HttpSourceSupport.jsonObject(response.body)
        } catch (e: SourceException) {
            throw SourceException.Parse("jara returned non-JSON (HTTP ${response.status})")
        }
    }

    private suspend fun publicParams(context: SourceContext): Map<String, String> {
        session.params()?.let { return it }
        val params = defaultParams.toMutableMap()
        try {
            val home = context.http.get(HOME, mapOf("User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT)).text
            val path = HttpSourceSupport.allMatches("src=\"(/static/js/main\\.[A-Za-z0-9]+\\.chunk\\.js)\"", home).lastOrNull()?.firstOrNull()
            val bundleUrl = path?.let { HttpSourceSupport.resolve(it, ORIGIN) }
            if (bundleUrl != null) {
                val bundle = context.http.get(bundleUrl, mapOf("User-Agent" to HttpSourceSupport.DESKTOP_USER_AGENT)).text
                params.putAll(paramsFromBundle(bundle))
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            context.logger.log(LogLevel.DEBUG, "jara: using the built-in API config (${e.message}).")
        }
        // The chapter key is bound to this device ID, so it stays the same for the session.
        params["deviceuid"] = UUID.randomUUID().toString().replace("-", "").lowercase()
        return session.setParamsIfMissing(params)
    }

    private suspend fun fetchKey(context: SourceContext): JaraKey {
        val payload = apiGet("/v1/book/chapter_valid.joa", emptyMap(), context)
        val data = HttpSourceSupport.array(payload["data"])
        if (!isOk(payload) || data == null || data.size < 2) throw SourceException.Parse("jara did not return a chapter key")
        return JaraKey(HttpSourceSupport.string(data[0]), HttpSourceSupport.string(data[1]))
    }

    /**
     * A key that stays live for the next chapter request. `chapter_valid` returns the live
     * key unchanged until it expires and does not report its age, so the age is tracked
     * from the call that first returned it; a stale key is waited out, not reused.
     */
    private suspend fun chapterKey(force: Boolean, context: SourceContext): JaraKey {
        if (!force) session.freshKey(KEY_MAX_AGE)?.let { return it }
        val sent = System.currentTimeMillis()
        val key = fetchKey(context)
        if (session.adoptIfNew(key, sent)) return key
        val born = session.keyBorn() ?: sent
        if (force && (System.currentTimeMillis() - born) / 1000.0 <= KEY_MAX_AGE) return key
        HttpSourceSupport.sleep((born + (KEY_TTL * 1000).toLong() - System.currentTimeMillis()) / 1000.0)
        repeat(3) {
            val nextSent = System.currentTimeMillis()
            val next = fetchKey(context)
            if (session.adoptIfNew(next, nextSent)) return next
            HttpSourceSupport.sleep(1.0)
        }
        return key
    }

    /** Saved token, else the `signedInfo` entry of the web profile. */
    private suspend fun loadToken(context: SourceContext): String? {
        val saved = context.secrets.get(tokenKey)?.trim()
        if (!saved.isNullOrEmpty()) {
            tokenFromSignedInfo(saved)?.let { return it }
            if (!saved.startsWith("{")) return saved
        }
        try {
            val page = context.web.newPage(webProfile)
            try {
                page.load(HOME, WebLoadState.DOM_CONTENT_LOADED, 20)
                val raw = HttpSourceSupport.json.parseToJsonElement(page.evaluateJson("return localStorage.getItem('signedInfo')"))
                if (raw is JsonPrimitive && raw.isString) tokenFromSignedInfo(raw.content)?.let { return it }
            } finally {
                runCatching { page.close() }
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            context.logger.log(LogLevel.DEBUG, "jara: no web login available (${e.message}).")
        }
        return null
    }
}

internal data class JaraKey(val key: String, val iv: String)

internal class JaraAbort(message: String) : Exception(message)

/** Per-job state: API params, token, the live chapter key, and request pacing. */
internal class JaraSession {
    private val mutex = Mutex()
    private var params: Map<String, String>? = null
    private var token: String? = null
    private var key: JaraKey? = null
    private var keyBorn: Long? = null
    private var nextRequestAt = 0L

    suspend fun params(): Map<String, String>? = mutex.withLock { params }

    suspend fun token(): String? = mutex.withLock { token }

    suspend fun keyBorn(): Long? = mutex.withLock { keyBorn }

    suspend fun setToken(value: String?) = mutex.withLock { token = value }

    suspend fun setParamsIfMissing(value: Map<String, String>): Map<String, String> = mutex.withLock {
        params ?: value.also { params = it }
    }

    suspend fun freshKey(maxAgeSeconds: Double): JaraKey? = mutex.withLock {
        val born = keyBorn
        if (key != null && born != null && (System.currentTimeMillis() - born) / 1000.0 <= maxAgeSeconds) key else null
    }

    /** Records `candidate` as the live key if it differs from the current one. */
    suspend fun adoptIfNew(candidate: JaraKey, bornMillis: Long): Boolean = mutex.withLock {
        if (candidate == key) return@withLock false
        key = candidate
        keyBorn = bornMillis
        true
    }

    suspend fun waitForRequestSlot(intervalSeconds: Double) {
        val wait = mutex.withLock {
            val now = System.currentTimeMillis()
            val start = maxOf(now, nextRequestAt)
            nextRequestAt = start + (intervalSeconds * 1000).toLong()
            start - now
        }
        HttpSourceSupport.sleep(wait / 1000.0)
    }

    /** A key wait can outlast the reserved slot; spacing is measured between chapter requests. */
    suspend fun reserveAfterKeyWait(intervalSeconds: Double) = mutex.withLock {
        nextRequestAt = maxOf(nextRequestAt, System.currentTimeMillis() + (intervalSeconds * 1000).toLong())
    }
}
