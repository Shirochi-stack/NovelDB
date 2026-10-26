package com.shirochi.npia.sources.sfc

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.http.ReaderBlocks
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import java.security.MessageDigest
import java.util.UUID

/**
 * The sfc Android app API: request signing, login, and chapter text decoding.
 * Port of `_sfc_sign`, `_sfc_headers`, `_sfc_api_response`, `_sfc_login` and
 * `_sfc_content_to_outputs` in the desktop `external_scraper.py`. The desktop could also
 * pull the session out of an Android emulator; here the user signs in with the app API.
 */
internal object SfcAppApi {
    const val HOST = "api.\u0073\u0066\u0061\u0063\u0067.com"
    const val BASE = "https://$HOST"
    const val DEVICE_TOKEN = "910D166A-736E-3231-8B21-8D12DFD75F16"
    const val SALT = "lPQDb9AKO7\$LjkPG"
    val cookieKey = SecretKey.source(SourceId.SFC, "appCookie")
    const val SESSION_COOKIE_NAME = "session_APP"
    const val COMMUNITY_COOKIE_NAME = ".SFCommunity"

    /** The app's request signature (MD5 over a nonce-scrambled auth string). */
    fun sign(nonce: String, timestamp: Long, deviceToken: String = DEVICE_TOKEN, salt: String = SALT): String {
        // Real nonces are 36-character UUIDs; the modulo only keeps odd input from trapping.
        val longNonce = (if (nonce.isEmpty()) "0" else nonce).repeat(4).encodeToByteArray().map { it.toInt() and 0xFF }
        fun index(position: Int) = longNonce[position % longNonce.size] % 36
        fun slice(start: Int, length: Int): List<Int> =
            longNonce.subList(minOf(start, longNonce.size), minOf(start + length, longNonce.size))
        val reorder = slice(index(1), 13) + slice(index(2), 16) + slice(index(3), 36) + slice(index(4), 36)
        val auth = "$timestamp$salt$deviceToken$nonce".encodeToByteArray().map { it.toInt() and 0xFF }
        val mixed = (0 until minOf(auth.size, reorder.size)).map { (auth[it] + reorder[it]) shr 1 }
        fun part(from: Int, to: Int?): List<Int> {
            val lower = minOf(from, mixed.size)
            val upper = minOf(to ?: mixed.size, mixed.size)
            return mixed.subList(lower, maxOf(lower, upper))
        }
        val reordered = part(65, null) + part(0, 13) + part(29, 65) + part(13, 29)
        val final = ByteArray(reordered.size)
        reordered.forEachIndexed { position, value ->
            final[position] = when {
                value < 0x30 -> if (0x39 < value + 19 && value + 19 < 0x41) 0x39 else value + 19
                (0x39 < value && value < 0x41) || (0x5A < value && value < 0x61) -> value + 19
                else -> value
            }.toByte()
        }
        return MessageDigest.getInstance("MD5").digest(final).joinToString("") { "%02X".format(it.toInt() and 0xFF) }
    }

    fun headers(nonce: String, timestamp: Long, method: String = "GET"): Map<String, String> {
        val signature = sign(nonce, timestamp)
        val headers = mutableMapOf(
            "accept" to "application/vnd.\u0073\u0066\u0061\u0063\u0067.api+json;version=1",
            "accept-charset" to "UTF-8",
            "authorization" to "Basic YW5kcm9pZHVzZXI6MWEjJDUxLXl0Njk7KkFjdkBxeHE=",
            "sfsecurity" to "nonce=$nonce&timestamp=$timestamp&devicetoken=$DEVICE_TOKEN&sign=$signature",
            "user-agent" to "boluobao/5.2.16(android;35)/OPPO/${DEVICE_TOKEN.lowercase()}/OPPO",
        )
        if (method.uppercase() != "GET") headers["content-type"] = "application/json; charset=UTF-8"
        return headers
    }

    fun statusCode(payload: JsonObject): Int? = HttpSourceSupport.obj(payload["status"])?.let { HttpSourceSupport.int(it["httpCode"]) }

    /**
     * One signed request. HTTP code 417 inside the JSON means the signature was rejected
     * (not a purchase check), so it is retried with a fresh nonce, up to 20 times.
     */
    suspend fun send(path: String, method: String = "GET", body: ByteArray? = null, cookie: String?, http: HttpFetching): Pair<HttpResponse, JsonObject> {
        val url = HttpSourceSupport.resolve(path, BASE)!!
        var last: Pair<HttpResponse, JsonObject>? = null
        for (attempt in 1..20) {
            val nonce = UUID.randomUUID().toString().uppercase()
            val headers = headers(nonce, System.currentTimeMillis(), method).toMutableMap()
            if (!cookie.isNullOrEmpty()) headers["Cookie"] = cookie
            val response = http.send(HttpRequest(url, method, headers, body))
            val payload = runCatching { HttpSourceSupport.jsonObject(response.body) }.getOrElse { JsonObject(emptyMap()) }
            last = response to payload
            if (statusCode(payload) != 417) return last
            if (attempt < 20) HttpSourceSupport.sleep(0.25)
        }
        return last!!
    }

    // Session cookie

    fun cookieNames(cookie: String): Set<String> =
        cookie.split(';').mapNotNull { item ->
            val trimmed = item.trim()
            if (!trimmed.contains('=')) null else trimmed.substringBefore('=')
        }.toSet()

    /** The saved app cookie, if it carries `session_APP`. */
    fun savedCookie(secrets: SecretStore): String? {
        val cookie = secrets.get(cookieKey)?.trim() ?: return null
        if (SESSION_COOKIE_NAME !in cookieNames(cookie)) return null
        return cookie.split(';').map { it.trim() }.filter { it.contains('=') }.joinToString("; ")
    }

    /** Stores a user-supplied cookie header; false when `session_APP` is missing. */
    fun saveCookie(cookie: String, secrets: SecretStore): Boolean {
        val trimmed = cookie.trim()
        if (SESSION_COOKIE_NAME !in cookieNames(trimmed)) return false
        secrets.set(cookieKey, trimmed)
        return true
    }

    /** `.SFCommunity` and `session_APP` from (possibly comma-joined) `Set-Cookie` headers. */
    fun sessionCookie(setCookie: String): String? {
        fun value(name: String) = HttpSourceSupport.firstMatch("(?:^|[,;\\s])" + Regex.escape(name) + "=([^;,\\s]+)", setCookie)
        val community = value(COMMUNITY_COOKIE_NAME) ?: return null
        val session = value(SESSION_COOKIE_NAME) ?: return null
        return "$COMMUNITY_COOKIE_NAME=$community; $SESSION_COOKIE_NAME=$session"
    }

    /** Signs in through the app API and stores the session cookie in `secrets`. */
    suspend fun login(username: String, password: String, http: HttpFetching, secrets: SecretStore): Boolean {
        val user = username.trim()
        if (user.isEmpty() || password.isEmpty()) return false
        val body = buildJsonObject {
            put("password", password)
            put("shuMeiId", "")
            put("username", user)
        }.toString().encodeToByteArray()
        val (response, payload) = send("/sessions", "POST", body, null, http)
        if (statusCode(payload) != 200) {
            val message = HttpSourceSupport.string(HttpSourceSupport.obj(payload["status"])?.get("msg"))
            throw SourceException.Blocked(message.ifEmpty { "sfc login failed (HTTP ${response.status})" })
        }
        // Cookies that OkHttp moved into the app's cookie jar are not visible here; the
        // app's jar keeps them for later requests either way.
        val cookie = response.headers["set-cookie"]?.let(::sessionCookie) ?: return false
        secrets.set(cookieKey, cookie)
        return true
    }

    // Chapter text

    private val charMap: Map<Int, Int> by lazy {
        SfcCharTable.obfuscated.codePoints().toArray().zip(SfcCharTable.replacement.codePoints().toArray()).toMap()
    }

    /** Undoes the app API's character substitution. */
    fun decode(text: String): String {
        val out = StringBuilder(text.length)
        text.codePoints().forEach { out.appendCodePoint(charMap[it] ?: it) }
        return out.toString()
    }

    private val imagePattern = Regex(
        "\\[img(?:=[^\\]]*)?\\](https?://.*?)\\[/img\\]",
        setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL),
    )

    /** Chapter text to XHTML; `[img]url[/img]` markers become images. */
    fun content(raw: String, ref: ChapterRef): ChapterContent {
        val text = decode(raw)
        val images = mutableListOf<ImageRef>()
        val names = mutableMapOf<String, String>()
        val parts = mutableListOf<String>()
        for (line in text.replace("\r\n", "\n").split('\n', '\r')) {
            var cursor = 0
            val pieces = mutableListOf<String>()
            for (match in imagePattern.findAll(line)) {
                val before = line.substring(cursor, match.range.first)
                if (before.isNotEmpty()) pieces += ReaderBlocks.escape(before)
                val address = HttpSourceSupport.unescapeHtml(match.groupValues[1].trim())
                if (address.isNotEmpty()) {
                    val name = names.getOrPut(address) {
                        ReaderBlocks.imageFilename(address, ref.index, images.size + 1).also { images += ImageRef(address, it) }
                    }
                    pieces += "<img src=\"${ReaderBlocks.escape(name)}\" alt=\"\" />"
                }
                cursor = match.range.last + 1
            }
            val tail = line.substring(cursor)
            if (tail.isNotEmpty()) pieces += ReaderBlocks.escape(tail)
            parts += if (pieces.isEmpty()) "<p><br/></p>" else "<p>${pieces.joinToString("")}</p>"
        }
        return ChapterContent(ref, "<div>${parts.joinToString("")}</div>", images)
    }

    sealed class ChapterOutcome {
        data class Content(val content: ChapterContent) : ChapterOutcome()

        /** 401/403: the session cannot read this chapter. */
        data class NoAccess(val status: Int) : ChapterOutcome()
        data object Unavailable : ChapterOutcome()
    }

    suspend fun chapter(ref: ChapterRef, chapterId: String, cookie: String, http: HttpFetching): ChapterOutcome {
        val (_, payload) = send("/Chaps/$chapterId?expand=content%2Cexpand.content", cookie = cookie, http = http)
        return chapter(payload, ref)
    }

    fun chapter(payload: JsonObject, ref: ChapterRef): ChapterOutcome {
        val status = statusCode(payload)
        if (status == 401 || status == 403) return ChapterOutcome.NoAccess(status)
        val data = HttpSourceSupport.obj(payload["data"])
        if (status != 200 || data == null) return ChapterOutcome.Unavailable
        val text = listOf(
            HttpSourceSupport.string(data["content"]),
            HttpSourceSupport.string(HttpSourceSupport.obj(data["expand"])?.get("content")),
        ).filter { it.isNotEmpty() }.joinToString("\n").trim()
        if (text.isEmpty()) return ChapterOutcome.Unavailable
        val title = HttpSourceSupport.string(data["title"])
        val named = if (title.isNotEmpty()) ref.copy(title = title) else ref
        return ChapterOutcome.Content(content(text, named))
    }
}
