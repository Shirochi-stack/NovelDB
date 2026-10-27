package com.shirochi.npia.app.ui

import android.annotation.SuppressLint
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.Handler
import android.os.Looper
import android.view.ViewGroup
import android.webkit.JavascriptInterface
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.DEFAULT_CATALOG_URL
import com.shirochi.npia.app.model.CartItem
import com.shirochi.npia.sources.SourceCatalog
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive

private fun origin(url: String): String = Uri.parse(url).let { "${it.scheme}://${it.host}${if (it.port > 0) ":${it.port}" else ""}" }

/**
 * The catalog website in a WebView that outlives tab switches (the site keeps a large catalog in
 * memory, so reloading it on every visit would be slow). On the NovelDB website
 * `catalog-bridge.js` makes cards open View Details instead of the novel page, sends outside
 * links to the Browser tab and adds the link-cart button. Another site picked in Settings has no
 * cart buttons, so every link that leaves it goes to the cart instead.
 */
class CatalogWeb(context: Context, private val model: AppViewModel) {
    var progress by mutableIntStateOf(0)
        private set
    var canBack by mutableStateOf(false)
        private set
    private val main = Handler(Looper.getMainLooper())
    private val bridge = context.assets.open("catalog-bridge.js").bufferedReader(Charsets.UTF_8).use { it.readText() }
    private val documentStart = WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
    var site = model.manager.preferences.catalogUrl
        private set
    private val isNovelDb get() = site == DEFAULT_CATALOG_URL

    @SuppressLint("SetJavaScriptEnabled")
    val view: WebView = WebView(context).apply {
        settings.javaScriptEnabled = true
        settings.domStorageEnabled = true
        @Suppress("DEPRECATION")
        settings.databaseEnabled = true
        addJavascriptInterface(Bridge(), "NovelDBAndroid")
        if (documentStart) WebViewCompat.addDocumentStartJavaScript(this, bridge, setOf("*"))
        webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url.toString()
                if (request.url.scheme != "http" && request.url.scheme != "https") return true
                if (origin(url) == origin(site)) return false
                leave(url, "")
                return true
            }

            override fun onPageStarted(view: WebView, url: String?, favicon: Bitmap?) {
                this@CatalogWeb.canBack = view.canGoBack()
            }

            override fun onPageFinished(view: WebView, url: String?) {
                this@CatalogWeb.canBack = view.canGoBack()
                if (!documentStart) view.evaluateJavascript(bridge, null)
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
        view.loadUrl(url)
    }

    /** A link out of the catalog: the Browser tab on NovelDB, the link cart on other sites. */
    private fun leave(url: String, title: String) {
        if (isNovelDb) {
            model.openInBrowser(url)
        } else {
            model.cart.add(CartItem(url, title, SourceCatalog.adapterFor(url)?.id?.raw.orEmpty()))
            model.manager.message("Added to link cart")
        }
    }

    /** Tells the page which cards are in the cart, so their buttons show it. */
    fun showCart(items: List<CartItem>) {
        val urls = JsonArray(items.map { JsonPrimitive(it.url) })
        view.evaluateJavascript("window.NovelDBApp && window.NovelDBApp.setCart($urls)", null)
    }

    fun reload() = view.reload()

    fun destroy() = view.destroy()

    private inner class Bridge {
        @JavascriptInterface
        fun postMessage(text: String) {
            val message = runCatching { Json.parseToJsonElement(text).jsonObject }.getOrNull() ?: return
            fun field(name: String) = message[name]?.jsonPrimitive?.content.orEmpty()
            main.post {
                when (field("type")) {
                    "ready" -> showCart(model.cart.items.value)
                    "cartAdd" -> field("url").takeIf { it.startsWith("http") }?.let { model.cart.add(CartItem(it, field("title"), field("source"))) }
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
    LaunchedEffect(site) { web.load(site) }
    LaunchedEffect(cart) { web.showCart(cart) }
    LaunchedEffect(Unit) {
        topBar.actions = {
            IconButton(onClick = web::reload) { Icon(Icons.Filled.Refresh, contentDescription = "Reload") }
        }
    }
    BackHandler(enabled = web.canBack) { web.view.goBack() }
    Column(Modifier.fillMaxSize()) {
        if (web.progress in 1..99) LinearProgressIndicator(progress = { web.progress / 100f }, modifier = Modifier.fillMaxWidth())
        AndroidView(
            factory = { web.view.also { (it.parent as? ViewGroup)?.removeView(it) } },
            modifier = Modifier.fillMaxSize(),
        )
    }
}
