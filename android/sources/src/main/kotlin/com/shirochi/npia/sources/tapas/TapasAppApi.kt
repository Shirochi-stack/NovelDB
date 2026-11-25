package com.shirochi.npia.sources.tapas

import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SharedCookieStore
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.session.GenericCookieSession
import java.net.URLEncoder

/**
 * tapas.io's web sign-in: a form POST to `/account/authenticate`, as on the desktop (`_tapas_login` in
 * `external_scraper.py`). tapas is not a `SourceId` adapter on the phone — its books download through the
 * generic reader engine — so the whole signed-in cookie jar is written into the store every request shares
 * (see [GenericCookieSession]). The host is an escape so tracked files carry codenames only.
 */
object TapasAppApi {
    const val DOMAIN = "\u0074\u0061\u0070\u0061\u0073.io"
    val cookieKey = SecretKey("tapas.appCookie")
    val session = GenericCookieSession(cookieKey, listOf("https://$DOMAIN/"), DOMAIN)

    private const val SIGN_IN_URL = "https://$DOMAIN/account/signin"
    private const val AUTH_URL = "https://$DOMAIN/account/authenticate"

    /** A desktop browser's user agent; the sign-in form expects one. */
    private const val USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"

    /**
     * True when an authenticate POST landed signed-in: it redirects away from the sign-in page, so the
     * final URL is no longer `/account/signin` or `/account/authenticate`; a bad login re-renders the
     * sign-in form (which posts to `/account/authenticate`) with a 200. Pure, so it is unit tested.
     */
    fun loggedIn(finalUrl: String, body: String): Boolean {
        val path = HttpSourceSupport.path(finalUrl).lowercase()
        if (path.startsWith("/account/signin") || path.startsWith("/account/authenticate")) return false
        return "/account/authenticate" !in body.take(40000).lowercase()
    }

    /**
     * Signs in through tapas.io's web endpoint and keeps the session cookies. `http` must be the download
     * fetcher (its cookie jar is the shared store), so the sign-in cookies land where downloads read them.
     */
    suspend fun login(
        username: String,
        password: String,
        http: HttpFetching,
        secrets: SecretStore,
        cookies: SharedCookieStore?,
    ): Boolean {
        val user = username.trim()
        if (user.isEmpty() || password.isEmpty()) return false
        // Prime the session cookies the form expects (JSESSIONID, AWSALB); best effort.
        runCatching { HttpSourceSupport.get(SIGN_IN_URL, mapOf("User-Agent" to USER_AGENT), http) }
        val form = "from=&email=${enc(user)}&password=${enc(password)}&offsetTime=0"
        val response = http.send(
            HttpRequest(
                AUTH_URL,
                "POST",
                mapOf(
                    "User-Agent" to USER_AGENT,
                    "Referer" to SIGN_IN_URL,
                    "Content-Type" to "application/x-www-form-urlencoded",
                ),
                form.encodeToByteArray(),
            ),
        )
        if (!loggedIn(response.url, response.text)) return false
        // The cookie jar captured the sign-in cookies; read them back for the saved copy.
        val header = (cookies ?: return false).cookies("https://$DOMAIN/")
            .entries.joinToString("; ") { (name, value) -> "$name=$value" }
        if (header.isEmpty()) return false
        return session.save(header, secrets, cookies)
    }

    private fun enc(value: String): String = URLEncoder.encode(value, "UTF-8")
}
