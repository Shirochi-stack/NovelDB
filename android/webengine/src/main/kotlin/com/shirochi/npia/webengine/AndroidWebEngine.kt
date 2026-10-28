package com.shirochi.npia.webengine

import android.annotation.SuppressLint
import android.app.Activity
import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.os.Bundle
import android.view.View
import android.view.ViewGroup
import android.widget.FrameLayout
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebStorage
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.shirochi.npia.core.SharedCookies
import com.shirochi.npia.core.WebEngineException
import com.shirochi.npia.core.WebLoadTimeoutException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.cancel
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.withContext
import kotlinx.coroutines.withTimeout
import kotlinx.coroutines.withTimeoutOrNull
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import java.lang.ref.WeakReference
import java.util.UUID
import java.util.concurrent.ConcurrentHashMap
import kotlin.coroutines.resume

/**
 * Offscreen Android WebView pages, the replacement for the desktop Playwright/CDP driver and the
 * iOS `WKWebEngine`.
 *
 * Profiles: WebView keeps one cookie store and one storage directory per process
 * (`WebView.setDataDirectorySuffix` can only be called once, before the first WebView), so every
 * profile name shares the same `CookieManager`, localStorage and cache. The desktop external
 * downloader already used one browser profile for all login sources, so the only real split is
 * npia against the rest, and both sign in to different hosts. `clearProfile` therefore clears
 * everything. A true split would need a second process with its own suffix.
 *
 * Cookies: `WebViewCookieJar` lets OkHttp read and write `CookieManager` directly, so `:net`
 * requests and pages share one login with nothing to copy.
 *
 * Offscreen pages are attached behind the current activity's content (like the hidden window on
 * iOS), so Chromium treats them as visible and keeps timers and animation frames running. With no
 * activity they run detached with a manual layout.
 */
class AndroidWebEngine(
    context: Context,
    private val loginPresenter: WebLoginPresenting = WebLoginCoordinator,
) : WebEngineProviding {
    private val appContext = context.applicationContext

    init {
        (appContext as? Application)?.let { HiddenHost.install(it) }
        SharedCookies.store = BackendSharedCookies()
    }

    /** A `CookieJar` for `OkHttpFetcher` that shares this engine's cookies. */
    fun cookieJar(): WebViewCookieJar = WebViewCookieJar()

    override suspend fun newPage(profile: String): WebPageDriving = withContext(Dispatchers.Main) {
        AndroidWebPage(WebView(appContext))
    }

    override suspend fun presentLogin(url: String, profile: String) {
        withContext(Dispatchers.Main) { WebLoginSession(url, profile).run(loginPresenter) }
        flushCookies()
    }

    /** Writes cookies to disk so a login survives the app being killed. */
    suspend fun flushCookies() = withContext(Dispatchers.Main) { CookieManager.getInstance().flush() }

    /**
     * Signs out of every site. All profiles share one store on Android (see the class comment), so
     * `profile` is accepted for parity with iOS and does not narrow what is removed.
     */
    suspend fun clearProfile(@Suppress("UNUSED_PARAMETER") profile: String) = withContext(Dispatchers.Main) {
        suspendCancellableCoroutine { continuation ->
            CookieManager.getInstance().removeAllCookies { continuation.resume(Unit) }
        }
        CookieManager.getInstance().flush()
        WebStorage.getInstance().deleteAllData()
    }
}

@SuppressLint("SetJavaScriptEnabled", "JavascriptInterface")
internal class AndroidWebPage(private val webView: WebView) : WebPageDriving {
    private var pending: CompletableDeferred<Unit>? = null
    private var navigationToken = 0

    /** Set by `emulateDesktop`; applied again before every load so even the process's first page gets it. */
    private var initialScale = 0
    private var waitFor = WebLoadState.LOAD
    private val fallbackInitScripts = mutableListOf<String>()
    private val results = ConcurrentHashMap<String, CompletableDeferred<Result<String>>>()
    private val documentStart = WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)

    // `exposeTransport`: the app's answer to `window.__npia_gm_xmlhttp_request`, and what lets a request through.
    private var transport: (suspend (String) -> String)? = null
    private var transportScript: String? = null
    private val transportToken = UUID.randomUUID().toString().replace("-", "")
    private val transportScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    init {
        webView.settings.javaScriptEnabled = true
        webView.settings.domStorageEnabled = true
        webView.settings.mediaPlaybackRequiresUserGesture = false
        CookieManager.getInstance().setAcceptThirdPartyCookies(webView, true)
        webView.addJavascriptInterface(Bridge(), ScriptBridge.BRIDGE_NAME)
        webView.measure(
            View.MeasureSpec.makeMeasureSpec(WIDTH, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(HEIGHT, View.MeasureSpec.EXACTLY),
        )
        webView.layout(0, 0, WIDTH, HEIGHT)
        HiddenHost.attach(webView)
        webView.webViewClient = object : WebViewClient() {
            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                if (!documentStart) fallbackInitScripts.forEach { view.evaluateJavascript(it, null) }
                if (waitFor == WebLoadState.DOM_CONTENT_LOADED) watchReadyState(navigationToken)
            }

            override fun onPageFinished(view: WebView, url: String?) {
                // Covers web views without document-start scripts; the script does nothing when it already ran.
                transportScript?.let { view.evaluateJavascript(it, null) }
                pending?.complete(Unit)
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (request.isForMainFrame) {
                    pending?.completeExceptionally(WebEngineException("Navigation failed: ${error.description}"))
                }
            }

            override fun onRenderProcessGone(view: WebView, detail: android.webkit.RenderProcessGoneDetail?): Boolean {
                pending?.completeExceptionally(WebEngineException("Web content process terminated"))
                return true
            }
        }
    }

    /** JavaScript interface; called on a WebView binder thread. */
    private inner class Bridge {
        @JavascriptInterface
        fun resolve(id: String, ok: Boolean, payload: String?) {
            results.remove(id)?.complete(ScriptBridge.outcome(ok, payload ?: "null"))
        }

        /** A request from `window.__npia_gm_xmlhttp_request`; ignored unless it carries this page's token. */
        @JavascriptInterface
        fun request(token: String?, id: String, payload: String) {
            val handler = transport
            if (handler == null || token != transportToken) return
            transportScope.launch {
                val reply = try {
                    handler(payload)
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    """{"error":${Json.encodeToString(JsonPrimitive.serializer(), JsonPrimitive(e.message ?: e.toString()))}}"""
                }
                // Not `webView.post`: a view that is not attached to a window never runs what it is given.
                withContext(Dispatchers.Main) {
                    runCatching { webView.evaluateJavascript(ScriptBridge.replyScript(transportToken, id, reply), null) }
                }
            }
        }
    }

    // Commit happens before DOMContentLoaded; poll readyState until the load ends or times out. A page
    // whose own scripts are slow to arrive (a first visit, ads) can take well over ten seconds to get there,
    // and `onPageFinished` waits for every last image and ad besides.
    private fun watchReadyState(token: Int) {
        fun check() {
            if (token != navigationToken || pending == null) return
            webView.evaluateJavascript("document.readyState") { raw ->
                if (token != navigationToken) return@evaluateJavascript
                val state = ScriptBridge.decodeString(raw)
                if (state != null && state != "loading") pending?.complete(Unit) else webView.postDelayed({ check() }, 50)
            }
        }
        check()
    }

    override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
        val done = CompletableDeferred<Unit>()
        withContext(Dispatchers.Main) {
            pending?.completeExceptionally(WebEngineException("Navigation superseded"))
            pending = done
            navigationToken++
            waitFor = until
            webView.settings.allowFileAccess = url.startsWith("file:")
            if (initialScale > 0) webView.setInitialScale(initialScale)
            webView.loadUrl(url)
        }
        try {
            withTimeout(timeoutSeconds.coerceAtLeast(1) * 1000) { done.await() }
        } catch (e: TimeoutCancellationException) {
            throw WebLoadTimeoutException(url)
        } finally {
            withContext(Dispatchers.Main) { if (pending === done) pending = null }
        }
    }

    override suspend fun evaluateJson(functionBody: String): String {
        val id = UUID.randomUUID().toString()
        val result = CompletableDeferred<Result<String>>()
        results[id] = result
        try {
            evaluate(ScriptBridge.wrap(id, functionBody))
            // The bridge answers at once; polling covers pages that predate the interface.
            repeat(SCRIPT_TIMEOUT_MS / POLL_MS) {
                withTimeoutOrNull(POLL_MS.toLong()) { result.await() }?.let { return it.getOrThrow() }
                ScriptBridge.decodePolled(evaluate(ScriptBridge.pollScript(id)))?.let { return it.getOrThrow() }
            }
            throw WebEngineException("Script timed out")
        } finally {
            results.remove(id)
        }
    }

    override suspend fun addInitScript(source: String) = withContext(Dispatchers.Main) {
        if (documentStart) {
            WebViewCompat.addDocumentStartJavaScript(webView, source, setOf("*"))
        } else {
            fallbackInitScripts += source
        }
        Unit
    }

    override suspend fun exposeTransport(handler: suspend (String) -> String) {
        transport = handler
        val script = ScriptBridge.transportScript(transportToken)
        transportScript = script
        addInitScript(script)
        evaluate(script)
    }

    override suspend fun emulateDesktop(userAgent: String?, width: Int, height: Int) = withContext(Dispatchers.Main) {
        if (userAgent != null) webView.settings.userAgentString = userAgent
        // The layout viewport is the view's size in CSS pixels: scale the view by the screen density.
        val density = webView.resources.displayMetrics.density
        val pixelWidth = (width * density).toInt().coerceAtLeast(1)
        val pixelHeight = (height * density).toInt().coerceAtLeast(1)
        // Without a wide viewport the layout viewport follows the view's size, whatever the page's viewport tag says;
        // an initial scale of one CSS pixel per `density` device pixels makes that size exactly `width` x `height`.
        webView.settings.useWideViewPort = false
        webView.settings.loadWithOverviewMode = false
        initialScale = (density * 100).toInt()
        webView.setInitialScale(initialScale)
        resize(pixelWidth, pixelHeight)
    }

    private fun resize(widthPx: Int, heightPx: Int) {
        webView.measure(
            View.MeasureSpec.makeMeasureSpec(widthPx, View.MeasureSpec.EXACTLY),
            View.MeasureSpec.makeMeasureSpec(heightPx, View.MeasureSpec.EXACTLY),
        )
        if (webView.parent != null) {
            // The hidden host is the activity's FrameLayout, whose children need margin-capable params.
            webView.layoutParams = FrameLayout.LayoutParams(widthPx, heightPx)
            webView.requestLayout()
        } else {
            webView.layout(0, 0, widthPx, heightPx)
        }
    }

    override suspend fun currentUrl(): String? = withContext(Dispatchers.Main) { webView.url }

    override suspend fun html(): String {
        val json = evaluateJson("return document.documentElement.outerHTML;")
        return ScriptBridge.decodeString(json) ?: ""
    }

    override suspend fun close() = withContext(Dispatchers.Main) {
        pending?.completeExceptionally(WebEngineException("Page closed"))
        pending = null
        results.values.forEach { it.cancel() }
        results.clear()
        transportScope.cancel()
        webView.stopLoading()
        HiddenHost.detach(webView)
        webView.removeJavascriptInterface(ScriptBridge.BRIDGE_NAME)
        webView.destroy()
        CookieManager.getInstance().flush()
    }

    private suspend fun evaluate(script: String): String = withContext(Dispatchers.Main) {
        val result = CompletableDeferred<String>()
        webView.evaluateJavascript(script) { result.complete(it ?: "null") }
        result.await()
    }

    private companion object {
        const val WIDTH = 1280
        const val HEIGHT = 2000
        const val POLL_MS = 250
        const val SCRIPT_TIMEOUT_MS = 60_000
    }
}

/**
 * Keeps offscreen pages inside the current activity's window, below its content view, so they
 * count as visible. Tracks the resumed activity through lifecycle callbacks.
 */
internal object HiddenHost {
    private var installed = false
    private var activity: WeakReference<Activity>? = null

    fun install(application: Application) {
        if (installed) return
        installed = true
        application.registerActivityLifecycleCallbacks(object : Application.ActivityLifecycleCallbacks {
            override fun onActivityResumed(activity: Activity) {
                this@HiddenHost.activity = WeakReference(activity)
            }

            override fun onActivityDestroyed(activity: Activity) {
                if (this@HiddenHost.activity?.get() === activity) this@HiddenHost.activity = null
            }

            override fun onActivityCreated(activity: Activity, savedInstanceState: Bundle?) {}
            override fun onActivityStarted(activity: Activity) {}
            override fun onActivityPaused(activity: Activity) {}
            override fun onActivityStopped(activity: Activity) {}
            override fun onActivitySaveInstanceState(activity: Activity, outState: Bundle) {}
        })
    }

    fun attach(view: WebView) {
        val decor = activity?.get()?.window?.decorView as? ViewGroup ?: return
        view.isFocusable = false
        view.isClickable = false
        view.importantForAccessibility = View.IMPORTANT_FOR_ACCESSIBILITY_NO_HIDE_DESCENDANTS
        runCatching { decor.addView(view, 0, ViewGroup.LayoutParams(view.width.coerceAtLeast(1), view.height.coerceAtLeast(1))) }
    }

    fun detach(view: WebView) {
        (view.parent as? ViewGroup)?.removeView(view)
    }
}
