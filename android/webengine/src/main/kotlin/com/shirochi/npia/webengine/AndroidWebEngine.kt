package com.shirochi.npia.webengine

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import org.json.JSONArray

/**
 * Offscreen Android WebView pages, the replacement for the desktop Playwright/CDP driver.
 *
 * TODO(webengine workstream): per-profile isolation (WebView `setDataDirectorySuffix` only works
 * per process, so profiles share one cookie store unless split by process), a `CookieJar` for
 * `:net` backed by `CookieManager`, fetch/XHR capture, `presentLogin` as a visible screen, and a
 * page pool honouring `threads`.
 */
class AndroidWebEngine(private val context: Context) : WebEngineProviding {
    override suspend fun newPage(profile: String): WebPageDriving = withContext(Dispatchers.Main) {
        AndroidWebPage(WebView(context.applicationContext))
    }

    override suspend fun presentLogin(url: String, profile: String) {
        throw WebEngineException("Login screens are not available in this build yet.")
    }
}

@SuppressLint("SetJavaScriptEnabled")
internal class AndroidWebPage(private val webView: WebView) : WebPageDriving {
    private var pending: CompletableDeferred<Unit>? = null
    private var waitFor = WebLoadState.LOAD
    private val initScripts = mutableListOf<String>()

    init {
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.layout(0, 0, 1080, 2400)
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                initScripts.forEach { view.evaluateJavascript(it, null) }
            }

            override fun onPageCommitVisible(view: WebView, url: String?) {
                if (waitFor == WebLoadState.DOM_CONTENT_LOADED) pending?.complete(Unit)
            }

            override fun onPageFinished(view: WebView, url: String?) {
                pending?.complete(Unit)
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) pending?.completeExceptionally(WebEngineException(error.description.toString()))
            }
        }
    }

    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
        val done = CompletableDeferred<Unit>()
        withContext(Dispatchers.Main) {
            pending?.cancel()
            pending = done
            waitFor = until
            webView.loadUrl(url)
        }
        try {
            withTimeout(timeoutSeconds * 1000) { done.await() }
        } catch (e: kotlinx.coroutines.TimeoutCancellationException) {
            throw WebEngineException("Timed out loading $url")
        }
    }

    override suspend fun evaluateJson(functionBody: String): String {
        // evaluateJavascript cannot await promises, so the result is parked on window and polled.
        val slot = "__npia_" + System.nanoTime()
        val start = "window.$slot = undefined; (async () => { $functionBody })()" +
            ".then(v => { window.$slot = JSON.stringify(v === undefined ? null : v) ?? 'null'; })" +
            ".catch(e => { window.$slot = JSON.stringify({__error: String(e)}); }); true;"
        evaluate(start)
        repeat(300) {
            val raw = evaluate("window.$slot === undefined ? null : window.$slot")
            if (raw != "null") {
                val value = JSONArray("[$raw]").getString(0)
                if (value.startsWith("{\"__error\"")) throw WebEngineException("Script failed: $value")
                evaluate("delete window.$slot")
                return value
            }
            kotlinx.coroutines.delay(100)
        }
        throw WebEngineException("Script timed out")
    }

    override suspend fun addInitScript(source: String) {
        withContext(Dispatchers.Main) { initScripts += source }
    }

    override suspend fun currentUrl(): String? = withContext(Dispatchers.Main) { webView.url }

    override suspend fun html(): String {
        val json = evaluateJson("return document.documentElement.outerHTML;")
        return JSONArray("[$json]").getString(0)
    }

    override suspend fun close() = withContext(Dispatchers.Main) {
        pending?.cancel()
        webView.stopLoading()
        webView.destroy()
    }

    private suspend fun evaluate(script: String): String = withContext(Dispatchers.Main) {
        val result = CompletableDeferred<String>()
        webView.evaluateJavascript(script) { result.complete(it ?: "null") }
        result.await()
    }
}
