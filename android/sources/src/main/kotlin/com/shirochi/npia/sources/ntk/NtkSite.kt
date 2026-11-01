package com.shirochi.npia.sources.ntk

import com.shirochi.npia.sources.http.HttpSourceSupport

/** URL shapes of the ntk site. Its numbered mirror host keeps moving, so it is recognised by a name pattern. */
internal object NtkSite {
    /** The site serves its desktop pages (and the Cloudflare check) to a desktop browser. */
    const val USER_AGENT =
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36"
    const val VIEWPORT_WIDTH = 1280
    const val VIEWPORT_HEIGHT = 900

    private val hostPattern = Regex("^(?:www\\.)?(?:ntk|newtoki|sbxh)\\d+\\.(?:com|org)$", RegexOption.IGNORE_CASE)
    private val pathPattern = Regex("""^/(?:novel|webtoon)/\d+(?:/\d+)?/?$""")

    fun ownsSite(host: String): Boolean = hostPattern.matches(host)

    fun matches(url: String): Boolean {
        val scheme = HttpSourceSupport.scheme(url)?.lowercase()
        if (scheme != "http" && scheme != "https") return false
        return hostPattern.matches(HttpSourceSupport.host(url)) && pathPattern.matches(HttpSourceSupport.path(url))
    }

    /** "novel" or "webtoon" — the two page kinds, which take different content paths. */
    fun kind(url: String): String =
        HttpSourceSupport.firstMatch("""^/(novel|webtoon)/""", HttpSourceSupport.path(url)) ?: "novel"

    fun novelId(url: String): String =
        HttpSourceSupport.firstMatch("""/(?:novel|webtoon)/(\d+)""", HttpSourceSupport.path(url)).orEmpty()

    fun origin(url: String): String =
        "${HttpSourceSupport.scheme(url) ?: "https"}://${HttpSourceSupport.host(url)}"

    /** A chapter link reads under the book-index URL, so identity and the download are the whole book. */
    fun indexUrl(url: String): String {
        val novelId = novelId(url)
        return if (novelId.isEmpty()) url else "${origin(url)}/${kind(url)}/$novelId"
    }
}
