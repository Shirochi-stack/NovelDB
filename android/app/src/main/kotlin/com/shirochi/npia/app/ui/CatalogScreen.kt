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
import android.webkit.RenderProcessGoneDetail
import android.webkit.WebChromeClient
import android.webkit.WebResourceError
import android.webkit.WebResourceRequest
import android.webkit.WebResourceResponse
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
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.luminance
import androidx.compose.ui.graphics.toArgb
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.key
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
import androidx.webkit.WebViewAssetLoader
import androidx.webkit.WebViewCompat
import androidx.webkit.WebViewFeature
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.CatalogKind
import com.shirochi.npia.app.DEFAULT_CATALOG_URL
import com.shirochi.npia.app.catalogPage
import com.shirochi.npia.app.isGenericPage
import com.shirochi.npia.app.catalogPageMode
import com.shirochi.npia.app.cleanPageTitle
import com.shirochi.npia.app.model.CartItem
import com.shirochi.npia.app.sameAddress
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.sources.npia.NpiaAuth
import com.shirochi.npia.sources.reader.CatalogTransport
import java.io.File
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
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
 * - The generic search (the default): the desktop's page, from the app's assets. Its requests go
 *   through the app's transport (`NovelDBCatalogTransport`, answered only while that page is shown);
 *   its Download button queues the book, and Login opens the Browser tab.
 */
class CatalogWeb(private val context: Context, private val model: AppViewModel) {
    var progress by mutableIntStateOf(0)
        private set
    var canBack by mutableStateOf(false)
        private set
    /** Why the catalog site failed to load; shown instead of a blank page. */
    var error by mutableStateOf<String?>(null)
        private set
    /** The generic page's reader is full screen: the app hides its own bars and the system's. */
    var readerFullScreen by mutableStateOf(false)
        private set
    /** Called when the reader of a Library book closes (the Library's own reader closes with it). */
    var onReaderClosed: (() -> Unit)? = null
    var localReaderReady by mutableStateOf(false)
        private set

    /** Leaves the reader's full screen (the back gesture): the page hears Escape, as from a keyboard. */
    fun leaveFullScreen() {
        readerFullScreen = false
        view.evaluateJavascript("document.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape'}))", null)
    }
    private val main = Handler(Looper.getMainLooper())
    init {
        // The reader opens Library EPUBs from the download folder only.
        CatalogTransport.libraryFolder = model.manager.outputDirectory
        com.shirochi.npia.sources.reader.CatalogData.directory = java.io.File(context.filesDir, "catalogue-data")
    }
    private val bridge = context.assets.open("catalog-bridge.js").bufferedReader(Charsets.UTF_8).use { it.readText() }
    private val documentStart = WebViewFeature.isFeatureSupported(WebViewFeature.DOCUMENT_START_SCRIPT)
    var site = model.manager.preferences.catalogUrl
        private set
    /** The page on screen: follows the site's own navigation, so it differs from [site] after the first tap. */
    private var pageUrl = catalogPage(site)

    /** Serves the generic search page from the assets, on an https origin of its own. */
    private val assets = WebViewAssetLoader.Builder()
        .addPathHandler("/assets/", WebViewAssetLoader.AssetsPathHandler(context))
        .build()

    /** The generic search page is on screen: only then does its transport answer. */
    @Volatile private var onGenericPage = isGenericPage(pageUrl)
        set(value) { field = value; genericShown = value }
    /** [onGenericPage] for the screen: that page can show the app's section menu (see [setSections]). */
    var genericShown by mutableStateOf(isGenericPage(pageUrl))
        private set
    private val transportScope = CoroutineScope(SupervisorJob() + Dispatchers.IO)

    /** The address and title the page's bridge last reported; the Download button is made for these. */
    private var reported: Pair<String, String>? = null
    private val mode get() = catalogPageMode(pageUrl, model.manager.settings.value.engines, model.manager.preferences.catalogCartLinks)

    /**
     * The page's web view. Android ends its renderer when the phone runs short of memory (a long search's
     * page grows); a renderer gone that no web view answers for takes the whole app with it, so a new view
     * takes this one's place (`onRenderProcessGone`) and loads the catalog page again.
     */
    var view: WebView by mutableStateOf(newView())
        private set

    private fun replaceView() {
        val old = view
        (old.parent as? ViewGroup)?.removeView(old)
        runCatching { old.destroy() }
        view = newView()
        model.manager.message("The catalog page ran out of memory and was opened again.")
    }

    @SuppressLint("SetJavaScriptEnabled")
    private fun newView(): WebView = WebView(context).apply {
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
        addJavascriptInterface(Transport(), "NovelDBCatalogTransport")
        if (documentStart) WebViewCompat.addDocumentStartJavaScript(this, bridge, setOf("*"))
        webViewClient = object : WebViewClient() {
            override fun shouldInterceptRequest(view: WebView, request: WebResourceRequest): WebResourceResponse? =
                assets.shouldInterceptRequest(request.url)

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
                showTheme()
                showSections()
                showRead()
                showBook()
            }

            override fun onRenderProcessGone(view: WebView, detail: RenderProcessGoneDetail): Boolean {
                // Answered, so Android keeps the app; the dead view cannot be used again.
                main.post { if (view === this@CatalogWeb.view) replaceView() else runCatching { view.destroy() } }
                return true
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
        loadUrl(catalogPage(site))
    }

    /** Opens another catalog site (from Settings). */
    /** Shows `url`'s catalog page; `force` loads it even when it is the site already set (a new view). */
    fun load(url: String, force: Boolean = false) {
        if (url == site && !force) return
        shareLogins()
        site = url
        pageUrl = catalogPage(url)
        onGenericPage = isGenericPage(pageUrl)
        reported = null
        error = null
        view.loadUrl(pageUrl)
    }

    /** The view moved to `url`: classify it again. A Download button made for the old address goes away until the page reports the new one. */
    private fun navigated(url: String?) {
        if (url.isNullOrEmpty() || url == "about:blank") return
        pageUrl = url
        onGenericPage = isGenericPage(url)
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

    /** A Library EPUB waiting for the generic page to be ready (its reader opens it). */
    private var pendingRead: File? = null
    private var localFile: File? = null
    private var localRevision = 0L
    private var pendingBook: com.shirochi.npia.app.CatalogBook? = null

    /** Finds a Library book in the generic page (its site's page and a search for its title). */
    fun openBook(book: com.shirochi.npia.app.CatalogBook) {
        if (!isGenericPage(pageUrl)) load(com.shirochi.npia.app.GENERIC_CATALOG_URL, force = true)
        pendingBook = book
        showBook()
    }

    private fun showBook() {
        val book = pendingBook ?: return
        val json = buildJsonObject {
            put("title", book.title)
            put("url", book.url.orEmpty())
            put("author", book.author)
        }
        view.evaluateJavascript("(function (b) { if (!window.NovelDBGeneric || !window.NovelDBGeneric.openBook) return false; " +
            "window.NovelDBGeneric.openBook(b); return true; })($json)") { done -> if (done == "true") pendingBook = null }
    }

    /** Opens a Library EPUB in the generic page's reader; on another catalog site, says why it cannot. */
    fun readLocal(epub: File) {
        localReaderReady = false
        localFile = epub
        localRevision = model.manager.libraryRevisions.value[epub.name] ?: 0L
        if (!isGenericPage(pageUrl)) {
            model.manager.message("Books open in the reader of the built-in Catalog page (Settings > Catalog site)")
            return
        }
        pendingRead = epub
        showRead()
    }

    private fun showRead() {
        val epub = pendingRead ?: return
        val book = buildJsonObject {
            put("url", epub.toURI().toString())
            put("title", epub.nameWithoutExtension)
        }
        view.evaluateJavascript("(function (b) { if (!window.NovelDBGeneric || !window.NovelDBGeneric.readLocal) return false; " +
            "window.NovelDBGeneric.readLocal(b); return true; })($book)") { done ->
            if (done == "true" && localFile == epub) { pendingRead = null; localReaderReady = true; refreshLocal() }
        }
    }

    fun back(onRoot: () -> Unit) {
        val currentView = view
        val currentTab = model.tab
        currentView.evaluateJavascript("!!(window.NovelDBGeneric && window.NovelDBGeneric.back && window.NovelDBGeneric.back())") { handled ->
            if (view !== currentView || model.tab != currentTab) return@evaluateJavascript
            if (handled != "true") {
                if (view.canGoBack()) view.goBack() else onRoot()
            }
        }
    }

    fun refreshLocal() {
        val file = localFile ?: return
        val revision = model.manager.libraryRevisions.value[file.name] ?: return
        if (revision == localRevision) return
        val url = JsonPrimitive(file.toURI().toString())
        view.evaluateJavascript("window.NovelDBGeneric && window.NovelDBGeneric.refreshLocal($url)") { done ->
            if (done == "true" && localFile == file) localRevision = maxOf(localRevision, revision)
        }
    }

    /** The app theme's colours as the generic page takes them ({dark, bg, panel, ...} as #rrggbb). */
    private var theme: String? = null

    fun setTheme(colors: ColorScheme) {
        fun hex(color: Color) = String.format("#%06X", color.toArgb() and 0xFFFFFF)
        theme = buildJsonObject {
            put("dark", colors.background.luminance() < 0.5f)
            put("bg", hex(colors.background))
            put("panel", hex(colors.surfaceContainer))
            put("ink", hex(colors.onBackground))
            put("ink2", hex(colors.onSurfaceVariant))
            put("line", hex(colors.outlineVariant))
            put("accent", hex(colors.primary))
            put("accentInk", hex(colors.onPrimary))
            put("chip", hex(colors.surfaceContainerHighest))
        }.toString()
        showTheme()
    }

    /** The Download panel's button the generic page shows beside Sites: `{on, cart, open}` (a wide screen has no switch row). */
    private var sections = "{\"on\":false}"

    fun setSections(on: Boolean, cart: Int, open: Boolean) {
        sections = buildJsonObject { put("on", on); put("cart", cart); put("open", open) }.toString()
        showSections()
    }

    private fun showSections() {
        view.evaluateJavascript("window.NovelDBGeneric && window.NovelDBGeneric.sections && window.NovelDBGeneric.sections($sections)", null)
    }

    /** The generic page (only it has `NovelDBGeneric`) takes the app's theme. */
    private fun showTheme() {
        val colors = theme ?: return
        view.evaluateJavascript("window.NovelDBGeneric && window.NovelDBGeneric.theme && window.NovelDBGeneric.theme($colors)", null)
    }

    fun reload() {
        error = null
        if (view.url.isNullOrEmpty() || view.url == "about:blank") view.loadUrl(catalogPage(site)) else view.reload()
    }

    fun destroy() = view.destroy()

    /** The npia login key last given to the web view's cookies (see [shareLogins]). */
    @Volatile private var sharedNpiaKey: String? = null

    /**
     * The app's own npia login (Settings) goes into the web view's cookies, which the catalog's requests
     * and pages share, so a signed-in user's searches and book pages show what npia shows them (19+
     * books, members' figures). Only a key the app has not shared yet is written: a login made later in
     * the web view itself stays until the app's key changes.
     */
    private fun shareLogins() {
        val key = model.manager.secrets.get(SecretKey.NPIA_LOGIN_KEY)?.trim().orEmpty()
        if (key.isEmpty() || key == sharedNpiaKey) return
        sharedNpiaKey = key
        val cookies = CookieManager.getInstance()
        cookies.setCookie("https://${NpiaAuth.HOST}", "${NpiaAuth.COOKIE_NAME}=$key; Domain=.${NpiaAuth.HOST}; Path=/; Secure")
        cookies.flush()
    }

    /** `window.NovelDBTransport` of the generic search page; the reply goes to `__noveldbTransportReply(id, json)`. */
    private inner class Transport {
        @JavascriptInterface
        fun request(id: String, json: String) {
            if (!onGenericPage) return
            transportScope.launch {
                shareLogins()
                val context = model.manager.sourceContext()
                val reply = CatalogTransport.handle(json, context.http, context.web, context)
                main.post {
                    if (onGenericPage) {
                        view.evaluateJavascript(
                            "window.__noveldbTransportReply && window.__noveldbTransportReply(${JsonPrimitive(id)}, ${JsonPrimitive(reply)})",
                            null,
                        )
                    }
                }
            }
        }
    }

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
                        showTheme()
                        showSections()
                        showRead()
                        showBook()
                    }
                    "location" -> located(field("url"), field("title"))
                    "cartAdd" -> field("url").takeIf { it.startsWith("http") }?.let { url ->
                        val source = field("source").ifBlank { SourceCatalog.adapterFor(url)?.id?.raw.orEmpty() }
                        model.cart.add(CartItem(url, field("title"), source))
                    }
                    "cartRemove" -> model.cart.remove(field("url"))
                    "open" -> field("url").takeIf { it.startsWith("http") }?.let { leave(it, field("title")) }
                    // The generic search page's own buttons.
                    "genericDownload" -> if (onGenericPage) field("url").takeIf { it.startsWith("http") }?.let { url ->
                        model.manager.enqueue(listOf(url)) { model.manager.settings.value }
                        model.manager.message("Added to the queue: ${field("title").ifBlank { url }}")
                    }
                    "genericLogin" -> if (onGenericPage) field("url").takeIf { it.startsWith("http") }?.let { model.openInBrowser(it) }
                    // The page's Download button: the Add links panel beside the Catalog, opened or closed.
                    "genericSection" -> if (onGenericPage) {
                        val add = com.shirochi.npia.app.Tab.ADD
                        model.select(if (model.tab == add) com.shirochi.npia.app.Tab.CATALOG else add)
                    }
                    "genericFullscreen" -> readerFullScreen = onGenericPage && field("on") == "true"
                    "genericReaderClosed" -> { localFile = null; onReaderClosed?.invoke() }
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
    val colors = MaterialTheme.colorScheme
    LaunchedEffect(colors) { web.setTheme(colors) }
    // A Library book to find again (to download what it is missing): the generic page opens it once ready.
    val finding = model.catalogBookRequest
    LaunchedEffect(finding) {
        if (finding != null) {
            web.openBook(finding)
            model.catalogBookRequest = null
        }
    }
    // A Library book to read: the page's reader opens it (once the page is ready).
    val reading = model.readRequest
    val libraryRevisions by model.manager.libraryRevisions.collectAsStateWithLifecycle()
    LaunchedEffect(libraryRevisions) { web.refreshLocal() }
    LaunchedEffect(reading) {
        if (reading != null) {
            web.readLocal(reading)
            model.readRequest = null
        }
    }
    BackHandler { web.back { model.back() } }
    Box(Modifier.fillMaxSize()) {
        Column(Modifier.fillMaxSize()) {
            if (web.progress in 1..99) LinearProgressIndicator(progress = { web.progress / 100f }, modifier = Modifier.fillMaxWidth())
            // A new web view (the old one's renderer ended) is attached in the old one's place.
            key(web.view) { AndroidView(
                factory = {
                    web.view.also { view ->
                        (view.parent as? ViewGroup)?.removeView(view)
                        // Back from another tab: the web view is attached again. It can stay blank until
                        // something makes it draw: it is resumed, laid out and asked to draw again.
                        view.onResume()
                        view.post {
                            view.requestLayout()
                            view.invalidate()
                            view.evaluateJavascript("window.dispatchEvent(new Event('resize'))", null)
                        }
                    }.fillingParent()
                },
                modifier = Modifier.fillMaxSize(),
            ) }
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
