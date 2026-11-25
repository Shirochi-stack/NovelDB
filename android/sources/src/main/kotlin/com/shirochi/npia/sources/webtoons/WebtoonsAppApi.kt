package com.shirochi.npia.sources.webtoons

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SharedCookieStore
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.session.GenericCookieSession
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import java.math.BigInteger
import java.net.URLEncoder
import java.security.KeyFactory
import java.security.spec.RSAPublicKeySpec
import java.util.Base64
import javax.crypto.Cipher
import javax.crypto.Mac
import javax.crypto.spec.SecretKeySpec

/**
 * The WEBTOON app API's email + password sign-in, a port of `login_webtoons_app` in the desktop
 * `external_scraper.py`: `getRsaKey.json` hands back an RSA public key; the password (with the session
 * key and email, length-prefixed) is RSA-encrypted and POSTed to `loginById.json`, which returns the
 * session. Every request is HMAC-SHA1 signed (`msgpad`/`md`). webtoons is not a `SourceId` adapter on the
 * phone — its episodes download through the generic reader engine — so the session cookie is written into
 * the store every request shares (see [GenericCookieSession]).
 *
 * The app API's host, signing key, user agents and session cookie name are kept out of the source as
 * plaintext (a base64 blob decoded at runtime, as on the desktop) per the repository's naming policy;
 * it is not a security measure.
 */
object WebtoonsAppApi {
    const val DOMAIN = "\u0077\u0065\u0062\u0074\u006f\u006f\u006e\u0073.com"
    val cookieKey = SecretKey("webtoons.appCookie")

    // The cookie is valid for the whole domain; downloads use both the desktop and the phone host.
    val session = GenericCookieSession(cookieKey, listOf("https://www.$DOMAIN/", "https://m.$DOMAIN/"), DOMAIN)

    private const val BLOB =
        "eyJhcGkiOiJodHRwczovL2dsb2JhbC5hcGlzLm5hdmVyLmNvbS9saW5lV2VidG9vbi93ZWJ0b29uLyIsInRpbWUiOiJodHRwczovL2dsb2JhbC5hcGlzLm5hdmVyLmNvbS9jdXJyZW50VGltZSIsImtleSI6ImdVdFB6SkZaY2g0WnlBR3ZpaXlIOTRQOTlsUTNwRmRSVHdwSldEbFNHRmZ3Z3ByNnNlczVBTE94V0hPSVQ3UjEiLCJ1YSI6Im5BcHBzIChBbmRyb2lkIDEzOyBsaW5ld2VidG9vbjsgMi4xMi4yKSIsImxvZ2ludWEiOiJBbmRyb2lkLzkgTW9kZWwvU00tTjk3NUYgY29tLm5hdmVyLmxpbmV3ZWJ0b29uLzIuMTIuNSgyMTIwNTAwLHVpZDoxMDA1OSkgTmVvSWRTaWduSW5Nb2QvMC4xLjEyIiwic2VzIjoiTkVPX1NFUyJ9"

    private val cfg: Map<String, String> by lazy {
        val json = Json.parseToJsonElement(String(Base64.getDecoder().decode(BLOB))).jsonObject
        json.mapValues { it.value.jsonPrimitive.content }
    }

    private fun cfg(key: String): String = cfg.getValue(key)

    /** The app API's own clock (raw bytes); both `msgpad` and the signature use it verbatim. */
    suspend fun serverTime(http: HttpFetching): ByteArray =
        http.send(HttpRequest(cfg("time"), headers = mapOf("User-Agent" to cfg("ua")))).body

    /** Appends the app API's `msgpad`/`md` signature (HMAC-SHA1 of the URL's first 255 bytes + time). */
    fun sign(url: String, timeBytes: ByteArray): String {
        val mac = Mac.getInstance("HmacSHA1")
        mac.init(SecretKeySpec(cfg("key").toByteArray(Charsets.UTF_8), "HmacSHA1"))
        val urlBytes = url.toByteArray(Charsets.UTF_8)
        val digest = mac.doFinal(urlBytes.copyOfRange(0, minOf(255, urlBytes.size)) + timeBytes)
        val md = Base64.getEncoder().encodeToString(digest)
        val sep = if ('?' in url) "&" else "?"
        return "$url${sep}msgpad=${quote(String(timeBytes, Charsets.US_ASCII))}&md=${quote(md)}"
    }

    /** RSAES-PKCS1-v1_5 encrypt with the public key (modulus, exponent); returns lowercase hex. */
    fun rsaEncryptHex(message: ByteArray, modulus: BigInteger, exponent: BigInteger): String {
        val key = KeyFactory.getInstance("RSA").generatePublic(RSAPublicKeySpec(modulus, exponent))
        val cipher = Cipher.getInstance("RSA/ECB/PKCS1Padding")
        cipher.init(Cipher.ENCRYPT_MODE, key)
        return cipher.doFinal(message).joinToString("") { "%02x".format(it.toInt() and 0xFF) }
    }

    /**
     * Signs in with an email and password and keeps the `NEO_SES` session cookie. Returns false (and logs
     * the reason) when the reply carried no session.
     */
    suspend fun login(
        email: String,
        password: String,
        http: HttpFetching,
        secrets: SecretStore,
        cookies: SharedCookieStore?,
        logger: JobLogging = NullLogger,
    ): Boolean {
        val user = email.trim()
        if (user.isEmpty() || password.isEmpty()) return false
        val keyUrl = cfg("api") + "getRsaKey.json?v=1&platform=APP_ANDROID&language=en&serviceZone=GLOBAL&locale=en"
        val keyResponse = http.send(HttpRequest(sign(keyUrl, serverTime(http)), headers = mapOf("User-Agent" to cfg("ua"))))
        val keyResult = result(keyResponse.body)
        val sessionKey = str(keyResult, "sessionKey")
        val keyName = str(keyResult, "keyName")
        val nValue = str(keyResult, "nvalue")
        val eValue = str(keyResult, "evalue")
        if (sessionKey.isEmpty() || keyName.isEmpty() || nValue.isEmpty() || eValue.isEmpty()) {
            logger.log(LogLevel.WARNING, "[Webtoons] Could not get a login key (signing or endpoint changed).")
            return false
        }
        val plain = buildString {
            append(sessionKey.length.toChar()); append(sessionKey)
            append(user.length.toChar()); append(user)
            append(password.length.toChar()); append(password)
        }.toByteArray(Charsets.UTF_8)
        // This endpoint labels the exponent "nvalue" and the modulus "evalue" (the reverse of the usual
        // nid naming), so pick them by size: the modulus is always the larger integer.
        val a = BigInteger(nValue, 16)
        val b = BigInteger(eValue, 16)
        val encpw = rsaEncryptHex(plain, a.max(b), a.min(b))
        val loginUrl = cfg("api") + "loginById.json"
        val form = listOf(
            "v" to "2", "encnm" to keyName, "encpw" to encpw,
            "language" to "en", "loginType" to "EMAIL", "serviceZone" to "GLOBAL",
        ).joinToString("&") { (name, value) -> "$name=${URLEncoder.encode(value, "UTF-8")}" }
        val loginResponse = http.send(
            HttpRequest(
                sign(loginUrl, serverTime(http)),
                "POST",
                mapOf("User-Agent" to cfg("loginua"), "Content-Type" to "application/x-www-form-urlencoded"),
                form.encodeToByteArray(),
            ),
        )
        val loginResult = result(loginResponse.body)
        val ses = str(loginResult, "ses")
        if (ses.isEmpty()) {
            val status = HttpSourceSupport.int(loginResult?.get("login_status"))
            val reason = when (status) {
                110, 131 -> "wrong email or password"
                151 -> "a captcha or extra verification is required — sign in with Enter Browser instead"
                else -> "login_status=$status; try the Enter Browser login instead"
            }
            logger.log(LogLevel.WARNING, "[Webtoons] Login failed: $reason")
            return false
        }
        return session.save("${cfg("ses")}=$ses", secrets, cookies)
    }

    private fun result(body: ByteArray): JsonObject? {
        val payload = runCatching { HttpSourceSupport.jsonObject(body) }.getOrNull() ?: return null
        return HttpSourceSupport.obj(payload["message"])?.let { HttpSourceSupport.obj(it["result"]) }
    }

    private fun str(result: JsonObject?, key: String): String = HttpSourceSupport.string(result?.get(key))

    /** Percent-encodes like Python's `urllib.parse.quote` (default safe `/`). */
    private fun quote(value: String): String {
        val out = StringBuilder()
        for (byte in value.toByteArray(Charsets.UTF_8)) {
            val c = (byte.toInt() and 0xFF).toChar()
            if (c in 'A'..'Z' || c in 'a'..'z' || c in '0'..'9' || c in "_.-~/") out.append(c)
            else out.append("%%%02X".format(byte.toInt() and 0xFF))
        }
        return out.toString()
    }
}
