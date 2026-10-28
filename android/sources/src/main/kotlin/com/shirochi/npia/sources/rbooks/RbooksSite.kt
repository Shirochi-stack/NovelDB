package com.shirochi.npia.sources.rbooks

import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.session.SessionSite

/** Addresses, cookie names and URL shapes of rbooks. Names are escapes so tracked files carry codenames only. */
internal object RbooksSite {
    const val DOMAIN = "\u0072\u0069\u0064\u0069\u0062\u006f\u006f\u006b\u0073.com"
    const val ORIGIN = "https://$DOMAIN"
    const val API_ORIGIN = "https://book-api.$DOMAIN"
    const val LIBRARY_ORIGIN = "https://library.$DOMAIN"
    const val ACCOUNT_ORIGIN = "https://account.$DOMAIN"

    /** The viewer and the book API both want a normal desktop browser. */
    const val USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    const val VIEWPORT_WIDTH = 1280
    const val VIEWPORT_HEIGHT = 900

    const val SIGN_IN_URL = "$ORIGIN/account/login"

    /** Access token, refresh token, and the global-service token. */
    const val ACCESS_COOKIE = "\u0072\u0069\u0064\u0069-at"
    const val REFRESH_COOKIE = "\u0072\u0069\u0064\u0069-rt"
    const val GLOBAL_COOKIE = "\u0072\u0069\u0064\u0069-gs-at"
    val LOGIN_COOKIES = listOf(ACCESS_COOKIE, REFRESH_COOKIE, GLOBAL_COOKIE)

    val session = SessionSite(
        source = SourceId.RBOOKS,
        signInUrl = SIGN_IN_URL,
        cookieUrls = listOf("$ORIGIN/", "$ACCOUNT_ORIGIN/", "$LIBRARY_ORIGIN/"),
        domain = DOMAIN,
        cookieNames = LOGIN_COOKIES,
        requirement = "$ACCESS_COOKIE or $REFRESH_COOKIE",
    ) { present -> LOGIN_COOKIES.any { it in present } }

    fun bookUrl(bookId: String) = "$ORIGIN/books/$bookId"

    fun viewUrl(bookId: String) = "$ORIGIN/books/$bookId/view"

    private fun hostMatches(host: String) = host == DOMAIN || host.endsWith(".$DOMAIN")

    /** Product, viewer and library book URLs. */
    fun matches(url: String): Boolean {
        if (!hostMatches(HttpSourceSupport.host(url))) return false
        return Regex("^/(?:books/\\d+(?:/view)?|library/books/\\d+)/?$", RegexOption.IGNORE_CASE)
            .matches(HttpSourceSupport.path(url))
    }

    /** `/books/<id>` anywhere in the path. */
    fun bookId(url: String): String =
        HttpSourceSupport.firstMatch("/books/(\\d+)", HttpSourceSupport.path(url), RegexOption.IGNORE_CASE).orEmpty()

    fun isLibrary(url: String): Boolean =
        Regex("^/library/books/\\d+/?$", RegexOption.IGNORE_CASE).matches(HttpSourceSupport.path(url))

    /** The reader itself (`/books/<id>/view`, `/library/books/<id>`), as opposed to a product page. */
    fun isViewerUrl(url: String): Boolean =
        Regex("(?:^/books/\\d+/view|^/library/books/\\d+)/?$", RegexOption.IGNORE_CASE).containsMatchIn(HttpSourceSupport.path(url))

    /** The login or adult-verification pages the site sends a signed-out visitor to. */
    fun isAccountGate(url: String): Boolean =
        Regex("/(?:account/login|account/verify-adult)", RegexOption.IGNORE_CASE).containsMatchIn(HttpSourceSupport.path(url)) ||
            HttpSourceSupport.host(url).startsWith("account.")
}
