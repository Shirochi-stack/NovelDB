package com.shirochi.npia.sources.mpia

import com.shirochi.npia.sources.http.HttpSourceSupport

/** Addresses and URL shapes of the mpia site. The host is an escape so tracked files carry codenames only. */
internal object MpiaSite {
    const val DOMAIN = "\u006d\u0075\u006e\u0070\u0069\u0061.com"
    const val WWW_ORIGIN = "https://www.$DOMAIN"

    /** The site serves its desktop reader to a desktop browser, which is what the page logic expects. */
    const val USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    const val VIEWPORT_WIDTH = 1280
    const val VIEWPORT_HEIGHT = 900

    fun bookUrl(novelId: String) = "$WWW_ORIGIN/novel/detail/$novelId"

    fun viewerUrl(novelId: String, chapterId: String) = "$WWW_ORIGIN/novel/viewer/$novelId/$chapterId"

    /** Book-detail and viewer URLs, plus the legacy per-id host that now redirects to the www site. */
    fun matches(url: String): Boolean {
        val scheme = HttpSourceSupport.scheme(url)?.lowercase()
        if (scheme != "http" && scheme != "https") return false
        val host = HttpSourceSupport.host(url)
        val path = HttpSourceSupport.path(url)
        return when (host) {
            DOMAIN, "www.$DOMAIN" -> Regex("""^/novel/(?:detail/\d+|viewer/\d+/\d+)/?$""").matches(path)
            "novel.$DOMAIN" -> Regex("""^/\d+(?:/page/\d+)?(?:/neSrl/\d+)?/?$""").matches(path)
            else -> false
        }
    }

    /** Novel id from any mpia URL, or "" when there is none. */
    fun novelId(url: String): String =
        HttpSourceSupport.firstMatch("""^/(?:novel/(?:detail|viewer)/)?(\d+)(?:/|$)""", HttpSourceSupport.path(url)).orEmpty()
}
