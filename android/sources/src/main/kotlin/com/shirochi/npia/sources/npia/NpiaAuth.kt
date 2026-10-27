package com.shirochi.npia.sources.npia

import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SourceContext
import kotlinx.coroutines.CancellationException
import java.net.URLEncoder
import java.security.SecureRandom

/**
 * Where the `LOGINKEY` cookie goes besides the adapter's own `Cookie` header. The app can pass a
 * setter that writes Android's `CookieManager` so the web engine shares the session; tests record
 * the value instead.
 */
fun interface NpiaCookieSetting {
    fun setLoginKey(key: String)
}

/**
 * Port of `npia_auth.py`: a random `LOGINKEY` cookie that a successful `/proc/login` binds to the
 * account. The key is stored in `SecretStore` (`NPIA_LOGIN_KEY`) and reused later.
 */
object NpiaAuth {
    const val COOKIE_NAME = "LOGINKEY"

    /** "감사합니다", the text the login response contains on success. */
    internal const val SUCCESS_MARKER = "감사합니다"

    /** Mobile fingerprint the desktop client sends with every request. */
    private val baseHeaders = mapOf(
        "User-Agent" to "Mozilla/5.0 (Linux; Android 6.0; Nexus 5 Build/MRA58N) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/112.0.0.0 Mobile Safari/537.36",
        "X-Requested-With" to "XMLHttpRequest",
        "Accept" to "application/json, text/javascript, */*; q=0.01",
        "Origin" to "https://${NpiaParser.HOST}",
        "Referer" to "https://${NpiaParser.HOST}/",
        "Accept-Language" to "ko-KR,ko;q=0.9,en-US;q=0.8,en;q=0.7",
    )

    internal fun headers(loginKey: String?, referer: String? = null, form: Boolean = false): Map<String, String> {
        val value = baseHeaders.toMutableMap()
        if (referer != null) value["Referer"] = referer
        if (form) value["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8"
        if (!loginKey.isNullOrEmpty()) value["Cookie"] = "$COOKIE_NAME=$loginKey"
        return value
    }

    private val random = SecureRandom()

    /** 32 hex chars, `_`, 32 hex chars. */
    fun generateLoginKey(): String {
        val digits = "0123456789abcdef"
        fun part() = String(CharArray(32) { digits[random.nextInt(16)] })
        return part() + "_" + part()
    }

    /** `application/x-www-form-urlencoded`, spaces as `+`, like `requests`. */
    internal fun formBody(fields: List<Pair<String, String>>): ByteArray =
        fields.joinToString("&") { (key, value) ->
            URLEncoder.encode(key, "UTF-8") + "=" + URLEncoder.encode(value, "UTF-8")
        }.encodeToByteArray()

    /** Signs in with email and password. On success the new key is stored and returned. */
    suspend fun login(email: String, password: String, context: SourceContext, cookies: NpiaCookieSetting): String? {
        val key = generateLoginKey()
        cookies.setLoginKey(key)
        val body = formBody(listOf("redirectrurl" to "", "email" to email, "wd" to password))
        val response = context.http.send(
            HttpRequest("https://${NpiaParser.HOST}/proc/login", "POST", headers(key, form = true), body, timeoutSeconds = 15),
        )
        if (!response.text.contains(SUCCESS_MARKER)) {
            context.logger.log(LogLevel.WARNING, SIGN_IN_FAILED)
            return null
        }
        context.secrets.set(SecretKey.NPIA_LOGIN_KEY, key)
        context.logger.log(LogLevel.INFO, "npia login succeeded.")
        return key
    }

    /** Shown whenever a sign-in with the saved account does not succeed. */
    internal const val SIGN_IN_FAILED = "npia sign-in failed; check the email and password in Settings > npia"

    /**
     * Picks the session key for a job: with `autoLogin` and a saved account (like the desktop
     * `_auto_login`) a fresh sign-in; otherwise the saved key; otherwise a sign-in with the saved
     * account, so entering an email and password is enough without the toggle. A failed sign-in
     * logs a warning and falls through. Without any of these a throwaway key is used and only
     * free chapters will be readable.
     */
    suspend fun prepare(context: SourceContext, cookies: NpiaCookieSetting): String {
        val secrets = context.secrets
        val email = secrets.get(SecretKey.NPIA_EMAIL)?.takeIf { it.isNotEmpty() }
        val password = secrets.get(SecretKey.NPIA_PASSWORD)?.takeIf { it.isNotEmpty() }
        val hasAccount = email != null && password != null

        suspend fun signIn(): String? {
            val key = try {
                login(email!!, password!!, context, cookies)
            } catch (e: CancellationException) {
                throw e
            } catch (e: Exception) {
                context.logger.log(LogLevel.WARNING, SIGN_IN_FAILED)
                null
            }
            return key
        }

        var triedSignIn = false
        if (context.settings.npia.autoLogin && hasAccount) {
            triedSignIn = true
            signIn()?.let { return it }
        }
        secrets.get(SecretKey.NPIA_LOGIN_KEY)?.takeIf { it.isNotEmpty() }?.let { key ->
            cookies.setLoginKey(key)
            return key
        }
        if (hasAccount && !triedSignIn) signIn()?.let { return it }
        val key = generateLoginKey()
        cookies.setLoginKey(key)
        context.logger.log(LogLevel.INFO, "npia: no saved login; only free chapters can be read.")
        return key
    }
}
