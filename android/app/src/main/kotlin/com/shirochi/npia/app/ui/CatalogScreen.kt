package com.shirochi.npia.app.ui

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.webkit.CookieManager
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.CatalogKind
import com.shirochi.npia.app.DEFAULT_CATALOG_URL
import com.shirochi.npia.app.catalogPageMode
import com.shirochi.npia.app.cleanPageTitle
import com.shirochi.npia.app.model.CartItem
import com.shirochi.npia.app.sameAddress
import com.shirochi.npia.sources.SourceCatalog
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

/**
 * Sizes a WebView to fill its Compose host. `AndroidView` otherwise gives it WRAP_CONTENT, and a
 * WebView with wrap-content height reports every viewport unit (vh, dvh, svh, lvh) as 0, which
 * collapses pages laid out with `height: 100dvh` into a blank screen.
 */
fun WebView.fillingParent(): WebView = apply {
    layoutParams = ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.MATCH_PARENT)
}

private fun origin(url: String): String = Uri.parse(url).let { "${it.scheme}://${it.host}${if (it.port > 0) ":${it.port}" else ""}" }

/**
 * The catalog website in a WebView that outlives tab switches (the site keeps a large catalog in
 * memory, so reloading it on every visit would be slow). `catalog-bridge.js` runs on every page and
 * reports the page address; the app decides what the page is (`catalogPageMode`) every time it
 * changes, single-page sites included:
 * - NovelDB and NovelDB Next: cards open View Details and carry link-cart buttons, outside links
 *   open in the Browser tab.
 * - A site we download from: outside links open in the Browser tab too, and a downloadable page gets
 *   a floating Download button that adds it to the cart.
 * - Any other site: every link that leaves it goes to the cart (setting on) and reads "Download".
 */
class CatalogWeb(context: Context, private val model: AppViewModel) {
    var progress by mutableIntStateOf(0)
        private set
    var canBack by mutableStateOf(false)
        private set
    /** Why the catalog site failed to load; shown instead of a blank page. */
    var error by mutableStateOf<String?>(null)
        private set
    private val main = Handler(Looper.getMainLooper())
    private val bridge = context.assets.open("catalog-bridge.js").bufferedReader(Charsets.UTF_8).use { it.readText() }
    private val documentStart = WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
    var site = model.manager.preferences.catalogUrl
        private set
    /** The page on screen: follows the site's own navigation, so it differs from [site] after the first tap. */
    private var pageUrl = site

    /** The address and title the page's bridge last reported; the Download button is made for these. */
    private var reported: Pair<String, String>? = null
    private val mode get() = catalogPageMode(pageUrl, model.manager.settings.value.engines, model.manager.preferences.catalogCartLinks)

    @SuppressLint("SetJavaScriptEnabled")
    val view: WebView = WebView(context).apply {
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        settings.javaScriptCanOpenWindowsAutomatically = true
        settings.setSupportMultipleWindows(false)
        settings.loadWithOverviewMode = true
        settings.useWideViewPort = true
        // Some sites refuse embedded browsers that announce themselves with "; wv".
        settings.userAgentString = settings.userAgentString.replace("; wv)", ")")
        CookieManager.getInstance().setAcceptCookie(true)
        CookieManager.getInstance().setAcceptThirdPartyCookies(this, true)
        @Suppress("DEPRECATION")
        settings.databaseEnabled = true
        addJavascriptInterface(Bridge(), "NovelDBAndroid")
        if (documentStart) WebViewCompat.addDocumentStartJavaScript(this, bridge, setOf("*"))
        webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                if (request.url.scheme != "http" && request.url.scheme != "https") return true
                // Redirects and script navigation (a site moving to www., a sign-in check) load here;
                // only a tapped link to another site leaves the catalog.
                if (!request.isForMainFrame || request.isRedirect || !request.hasGesture()) return false
                if (origin(url) == origin(view.url ?: site)) return false
                leave(url, "")
                return true
            }

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                this@CatalogWeb.canBack = view.canGoBack()
                navigated(url)
            }

            /** Every history change, same-document ones included (single-page sites push a new address without loading). */
            override fun doUpdateVisitedHistory(view: WebView, url: String?, isReload: Boolean) {
                this@CatalogWeb.canBack = view.canGoBack()
                navigated(url)
            }

            override fun onPageFinished(view: WebView, url: String?) {
                this@CatalogWeb.canBack = view.canGoBack()
                if (!documentStart) view.evaluateJavascript(bridge, null)
            }

            override fun onReceivedError(view: WebView, request: WebResourceRequest, error: WebResourceError) {
                if (!request.isForMainFrame) return
                val description = error.description?.toString().orEmpty()
                this@CatalogWeb.error = when {
                    "CLEARTEXT" in description -> "Android only opens https:// sites here. Use the site's https:// address."
                    description.isNotBlank() -> description
                    else -> "The page could not load."
                }
            }
        }
        webChromeClient = object : WebChromeClient() {
            override fun onProgressChanged(view: WebView, newProgress: Int) {
                this@CatalogWeb.progress = newProgress
            }
        }
        loadUrl(site)
    }

    /** Opens another catalog site (from Settings). */
    fun load(url: String) {
        if (url == site) return
        site = url
        pageUrl = url
        reported = null
        error = null
        view.loadUrl(url)
    }

    /** The view moved to `url`: classify it again. A Download button made for the old address goes away until the page reports the new one. */
    private fun navigated(url: String?) {
        if (url.isNullOrEmpty() || url == "about:blank") return
        pageUrl = url
        if (reported?.first?.let { sameAddress(it, url) } != true) reported = null
        showMode()
    }

    /** The page's bridge reported its address and title. A page can only speak for the site the view shows. */
    private fun located(url: String, title: String) {
        if (!url.startsWith("http")) return
        val shown = view.url
        if (shown != null && shown != "about:blank" && bareHost(shown) != bareHost(url)) return
        pageUrl = url
        reported = url to title
        showMode()
        showCart(model.cart.items.value)
    }

    private fun bareHost(url: String) = Uri.parse(url).host.orEmpty().lowercase().removePrefix("www.")

    /** A link out of the catalog: the link cart on sites we do not know (setting on), else the Browser tab. */
    private fun leave(url: String, title: String) {
        if (!mode.cartLinks) {
            model.openInBrowser(url)
        } else {
            model.cart.add(CartItem(url, title, SourceCatalog.adapterFor(url)?.id?.raw.orEmpty()))
            model.manager.message("Added to link cart")
        }
    }

    /** Tells the page what it is: whether to rename outside links, and the page to offer for download (if any). */
    fun showMode() {
        val now = mode
        val page = reported?.takeIf { now.download && sameAddress(it.first, pageUrl) }
        val download = if (page == null) "null" else buildJsonObject {
            put("url", page.first)
            put("title", cleanPageTitle(page.second, page.first))
        }.toString()
        view.evaluateJavascript("window.NovelDBApp && window.NovelDBApp.setMode({cartLinks: ${now.cartLinks}, download: $download})", null)
    }

    fun showCart(items: List<CartItem>) {
        val known = items.mapTo(LinkedHashSet()) { it.url }
        // The cart keeps a phone-site book under its desktop address; the page's own address counts too.
        pageUrl?.let { if (model.cart.contains(items, it)) known += it }
        val urls = JsonArray(known.map { JsonPrimitive(it) })
        view.evaluateJavascript("window.NovelDBApp && window.NovelDBApp.setCart($urls)", null)
    }

    fun reload() {
        error = null
        if (view.url.isNullOrEmpty() || view.url == "about:blank") view.loadUrl(site) else view.reload()
    }

    fun destroy() = view.destroy()

    private inner class Bridge {
        @JavascriptInterface
        fun postMessage(text: String) {
            val message = runCatching { Json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return
            fun field(name: String) = message[name]?.jsonPrimitive?.content.orEmpty()
            main.post {
                when (field("type")) {
                    "ready" -> {
                        showMode()
                        showCart(model.cart.items.value)
                    }
                    "location" -> located(field("url"), field("title"))
                    "cartAdd" -> field("url").takeIf { it.startsWith("http") }?.let { url ->
                        val source = field("source").ifBlank { SourceCatalog.adapterFor(url)?.id?.raw.orEmpty() }
                        model.cart.add(CartItem(url, field("title"), source))
                    }
                    "cartRemove" -> model.cart.remove(field("url"))
                    "open" -> field("url").takeIf { it.startsWith("http") }?.let { leave(it, field("title")) }
                }
            }
        }
    }
}

/** One `CatalogWeb` for the activity, kept while other tabs are shown. */
@Composable
fun rememberCatalogWeb(model: AppViewModel): CatalogWeb {
    val context = LocalContext.current
    val web = remember { CatalogWeb(context, model) }
    DisposableEffect(web) { onDispose { web.destroy() } }
    return web
}

@Composable
fun CatalogScreen(web: CatalogWeb, model: AppViewModel, topBar: TopBarState) {
    val cart by model.cart.items.collectAsStateWithLifecycle()
    model.manager.preferencesFlow.collectAsStateWithLifecycle().value
    val site = model.manager.preferences.catalogUrl
    val cartLinks = model.manager.preferences.catalogCartLinks
    val engines = model.manager.settings.collectAsStateWithLifecycle().value.engines
    LaunchedEffect(site, cartLinks, engines) {
        web.load(site)
        web.showMode()
    }
    LaunchedEffect(cart) { web.showCart(cart) }
    BackHandler(enabled = web.canBack) { web.view.goBack() }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            if (web.progress in 1..99) LinearProgressIndicator(progress = { web.progress / 100f }, modifier = Modifier.fillMaxWidth())
            AndroidView(
                factory = { web.view.also { (it.parent as? ViewGroup)?.removeView(it) }.fillingParent() },
                modifier = Modifier.fillMaxSize(),
            )
        }
        web.error?.let { message ->
            Surface(Modifier.fillMaxSize()) {
                Column(
                    Modifier.fillMaxSize().padding(24.dp),
                    verticalArrangement = Arrangement.spacedBy(8.dp, Alignment.CenterVertically),
                    horizontalAlignment = Alignment.CenterHorizontally,
                ) {
                    Text("The catalog site could not load", style = MaterialTheme.typography.titleMedium)
                    Text(web.site, style = MaterialTheme.typography.bodySmall, textAlign = TextAlign.Center)
                    Text(message, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.error, textAlign = TextAlign.Center)
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(onClick = web::reload) { Text("Retry") }
                        if (site != DEFAULT_CATALOG_URL) {
                            OutlinedButton(onClick = { model.manager.setCatalogUrl(DEFAULT_CATALOG_URL) }) { Text("Use NovelDB") }
                        }
                    }
                }
            }
        }
    }
}
