package com.shirochi.npia.sources.qdn

import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.http.HttpSourceSupport
import com.shirochi.npia.sources.session.SessionSite

/** Addresses, cookie names and URL shapes of qdn. Names are escapes so tracked files carry codenames only. */
internal object QdnSite {
    const val DOMAIN = "\u0071\u0069\u0064\u0069\u0061\u006e.com"
    const val HOST = "www.$DOMAIN"
    const val ORIGIN = "https://$HOST"

    /** A phone is sent to this host; its book and chapter paths have the same shape as the desktop ones. */
    const val MOBILE_HOST = "m.$DOMAIN"

    /** The reader needs a desktop browser: a phone user agent is sent to a different site. */
    const val USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/137.0.0.0 Safari/537.36"
    const val VIEWPORT_WIDTH = 1280
    const val VIEWPORT_HEIGHT = 800

    /**
     * The phone site's account page. Signed out, it sends the browser to the site's own sign-in with the
     * return address set, so the login cookies end up on the site itself (a bare sign-in page would not set them).
     */
    const val SIGN_IN_URL = "https://$MOBILE_HOST/user/"

    /** `ywkey` and `ywguid` are session cookies; `ywopenid` goes with them. */
    val LOGIN_COOKIES = listOf("ywkey", "ywguid", "ywopenid")

    val session = SessionSite(
        source = SourceId.QDN,
        signInUrl = SIGN_IN_URL,
        // The phone site shares the parent domain's cookies; reading both hosts covers a login made on either.
        cookieUrls = listOf("$ORIGIN/", "https://$MOBILE_HOST/"),
        domain = DOMAIN,
        cookieNames = LOGIN_COOKIES,
        requirement = "ywkey and ywguid",
    ) { present -> "ywkey" in present && "ywguid" in present }

    fun bookUrl(bookId: String) = "$ORIGIN/book/$bookId/"

    private fun hostMatches(host: String) = host == DOMAIN || host.endsWith(".$DOMAIN")

    /** Book or chapter URLs: `/book/<id>` and `/chapter/<book id>/<chapter id>`. */
    fun matches(url: String): Boolean {
        if (!hostMatches(HttpSourceSupport.host(url))) return false
        return Regex("^/(book|chapter)/\\d+").containsMatchIn(HttpSourceSupport.path(url))
    }

    /**
     * The desktop book address (`https://www.<domain>/book/<id>/`) for any book or chapter link on the
     * desktop or phone host; other links come back unchanged.
     */
    fun canonicalUrl(url: String): String = bookId(url)?.let(::bookUrl) ?: url

    fun bookId(url: String): String? {
        if (!hostMatches(HttpSourceSupport.host(url))) return null
        return HttpSourceSupport.firstMatch("^/(?:book|chapter)/(\\d+)", HttpSourceSupport.path(url))
    }

    /** The last number in a chapter URL's path, or null. */
    fun chapterId(url: String): String? =
        HttpSourceSupport.firstMatch("^/chapter/\\d+/(\\d+)", HttpSourceSupport.path(url))
}
