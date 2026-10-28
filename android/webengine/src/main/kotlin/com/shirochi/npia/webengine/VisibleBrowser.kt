package com.shirochi.npia.webengine

import android.annotation.SuppressLint
import android.graphics.Bitmap
import android.view.View
import android.webkit.CookieManager
import android.webkit.WebChromeClient
import android.webkit.WebView
import android.webkit.WebViewClient
import com.shirochi.npia.core.bareHost
import kotlin.coroutines.resume
import kotlinx.coroutines.suspendCancellableCoroutine

/** Page events from a visible browser view. */
interface VisibleBrowserListener {
    fun onPage(url: String?, title: String?, loading: Boolean, progress: Int)

    /** A page finished loading (not called for same-document changes). */
    fun onFinished(url: String?) {}

    /** A new page painted for the first time; it can be scripted before it finishes loading (some sites never do). */
    fun onCommitted(url: String?) {}

    /** The address changed by a load or by the page itself (history API), as single-page sites do. */
    fun onHistory(url: String?) {}
}

/**
 * Settings shared by every web view the user sees (the Browser tab and sign-in prompts). They
 * share `CookieManager` with the offscreen scraper pages, so signing in here signs the scraper in.
 */
object VisibleBrowser {
    /** Hosts the user has opened; the app persists them to list signed-in sites. */
    @Volatile var onVisit: ((String) -> Unit)? = null

    @SuppressLint("SetJavaScriptEnabled")
    fun configure(webView: WebView, listener: VisibleBrowserListener) {
        webView.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            javaScriptCanOpenWindowsAutomatically = true
            // Sign-in popups load in this view instead of a second window.
            setSupportMultipleWindows(false)
            loadWithOverviewMode = true
            useWideViewPort = true
            builtInZoomControls = true
            displayZoomControls = false
            // Some sites refuse sign-in from embedded browsers that announce themselves with "; wv".
            userAgentString = userAgentString.replace("; wv)", ")")
        }
        // Lets the system password manager (Google, Bitwarden, ...) offer to save and fill logins.
        webView.importantForAutofill = View.IMPORTANT_FOR_AUTOFILL_YES
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                listener.onPage(url, view.title, loading = true, progress = view.progress)
            }

            override fun onPageFinished(view: WebView, url: String?) {
                listener.onPage(url, view.title, loading = false, progress = 100)
                url?.let(::bareHost)?.takeIf { it.isNotEmpty() }?.let { onVisit?.invoke(it) }
                CookieManager.getInstance().flush()
                listener.onFinished(url)
            }

            override fun onPageCommitVisible(view: WebView, url: String?) {
                listener.onCommitted(url)
            }

            /** Fires for every history change, including a single-page site moving to a new address without loading. */
            override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
                listener.onPage(url, view.title, loading = view.progress < 100, progress = view.progress)
                listener.onHistory(url)
            }
        }
        webView.webChromeClient = object : WebChromeClient() {
            override fun onReceivedTitle(view: WebView, title: String?) {
                listener.onPage(view.url, title, loading = view.progress < 100, progress = view.progress)
            }

            override fun onProgressChanged(view: WebView, newProgress: Int) {
                listener.onPage(view.url, view.title, loading = newProgress < 100, progress = newProgress)
            }
        }
    }

    /** Turns typed text into a URL: full URLs pass through, bare domains get https, anything else is a search. */
    fun normalize(input: String): String {
        val text = input.trim()
        if (text.isEmpty()) return text
        if (Regex("^[a-zA-Z][a-zA-Z0-9+.-]*://").containsMatchIn(text)) return text
        if (!text.contains(' ') && Regex("^[^/]+\\.[a-zA-Z]{2,}(/.*)?$").matches(text)) return "https://$text"
        return "https://duckduckgo.com/?q=" + java.net.URLEncoder.encode(text, "UTF-8")
    }
}

/** Per-site cookie helpers. `CookieManager` cannot list domains, so callers pass hosts they recorded. */
object SiteCookies {
    fun hasCookies(host: String): Boolean = !CookieManager.getInstance().getCookie("https://$host").isNullOrBlank()

    /**
     * Expires every cookie visible to `host`, on the host and on its registrable domain. Cookies
     * scoped to a sub-path can survive; "Sign out of all sites" clears those too.
     */
    fun signOut(host: String) {
        val manager = CookieManager.getInstance()
        val url = "https://$host"
        val names = manager.getCookie(url).orEmpty().split(';').map { it.substringBefore('=').trim() }.filter { it.isNotEmpty() }
        val domains = listOf(host, registrableDomain(host)).distinct()
        for (name in names) {
            manager.setCookie(url, "$name=; Max-Age=0; Path=/")
            for (domain in domains) manager.setCookie(url, "$name=; Max-Age=0; Path=/; Domain=.$domain")
        }
        manager.flush()
    }

    suspend fun signOutAll() {
        suspendCancellableCoroutine { continuation ->
            CookieManager.getInstance().removeAllCookies { continuation.resume(Unit) }
        }
        CookieManager.getInstance().flush()
        android.webkit.WebStorage.getInstance().deleteAllData()
    }

    /** `www.a.example.co.kr` -> `example.co.kr`; good enough for cookie domains without a suffix list. */
    fun registrableDomain(host: String): String {
        val labels = host.split('.').filter { it.isNotEmpty() }
        if (labels.size <= 2) return host
        val secondLevel = setOf("co", "com", "ne", "net", "or", "org", "go", "ac", "ed", "gov", "edu")
        val take = if (labels.last().length == 2 && labels[labels.size - 2] in secondLevel) 3 else 2
        return labels.takeLast(take).joinToString(".")
    }
}
