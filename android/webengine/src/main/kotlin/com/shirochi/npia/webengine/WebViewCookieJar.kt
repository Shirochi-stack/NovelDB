package com.shirochi.npia.webengine

import android.webkit.CookieManager
import com.shirochi.npia.core.SharedCookieStore
import okhttp3.Cookie
import okhttp3.CookieJar
import okhttp3.HttpUrl

/**
 * The cookie store OkHttp and the web views share. `CookieManager` is the only store WebView
 * reads, so the OkHttp side reads and writes it directly instead of keeping a copy; a login in a
 * web view is visible to the next `:net` request, and cookies from an API login reach the pages.
 */
interface CookieBackend {
    /** `Cookie` header value for `url` (`a=1; b=2`), or null when none apply. */
    fun cookieHeader(url: String): String?

    /** Stores one `Set-Cookie` header value for `url`. */
    fun setCookie(url: String, setCookie: String)

    /** Persists pending writes to disk. */
    fun flush() {}
}

/** `CookieBackend` over the process-wide WebView `CookieManager`. */
object WebViewCookieBackend : CookieBackend {
    private val manager: CookieManager by lazy { CookieManager.getInstance().apply { setAcceptCookie(true) } }

    override fun cookieHeader(url: String): String? = manager.getCookie(url)

    override fun setCookie(url: String, setCookie: String) = manager.setCookie(url, setCookie)

    override fun flush() = manager.flush()
}

/** OkHttp `CookieJar` for `OkHttpFetcher` that shares cookies with the web engine. */
class WebViewCookieJar(private val backend: CookieBackend = WebViewCookieBackend) : CookieJar {
    override fun saveFromResponse(url: HttpUrl, cookies: List<Cookie>) {
        if (cookies.isEmpty()) return
        val target = url.toString()
        cookies.forEach { backend.setCookie(target, it.toString()) }
        backend.flush()
    }

    override fun loadForRequest(url: HttpUrl): List<Cookie> =
        parseCookieHeader(backend.cookieHeader(url.toString()), url)

    companion object {
        /**
         * Turns a `CookieManager` header (`a=1; b=2`, names and values only) back into cookies
         * scoped to `url`'s host. Pairs OkHttp rejects (control characters) are skipped.
         */
        fun parseCookieHeader(header: String?, url: HttpUrl): List<Cookie> {
            if (header.isNullOrBlank()) return emptyList()
            return header.split(';').mapNotNull { part ->
                val pair = part.trim()
                val equals = pair.indexOf('=')
                if (equals <= 0) return@mapNotNull null
                val name = pair.substring(0, equals).trim()
                val value = pair.substring(equals + 1).trim()
                runCatching {
                    Cookie.Builder().name(name).value(value).hostOnlyDomain(url.host).path("/").build()
                }.getOrNull()
            }
        }
    }
}

/** `SharedCookieStore` over a `CookieBackend`; `AndroidWebEngine` installs it in `SharedCookies`. */
class BackendSharedCookies(private val backend: CookieBackend = WebViewCookieBackend) : SharedCookieStore {
    override fun cookies(url: String): Map<String, String> {
        val header = backend.cookieHeader(url) ?: return emptyMap()
        val values = LinkedHashMap<String, String>()
        header.split(';').forEach { part ->
            val pair = part.trim()
            val equals = pair.indexOf('=')
            if (equals > 0) values[pair.substring(0, equals).trim()] = pair.substring(equals + 1).trim()
        }
        return values
    }

    override fun setCookie(url: String, name: String, value: String, domain: String?) {
        val attributes = buildString {
            append(name).append('=').append(value).append("; path=/")
            if (!domain.isNullOrEmpty()) append("; domain=").append(domain)
        }
        backend.setCookie(url, attributes)
        backend.flush()
    }
}
