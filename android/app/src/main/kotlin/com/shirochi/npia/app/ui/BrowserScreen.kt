package com.shirochi.npia.app.ui

import android.annotation.SuppressLint
import android.webkit.WebView
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.ArrowForward
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.LinearProgressIndicator
import androidx.compose.material3.ListItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Scaffold
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TopAppBar
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.Route
import com.shirochi.npia.app.Tab
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.webengine.SiteCookies
import com.shirochi.npia.webengine.VisibleBrowser
import com.shirochi.npia.webengine.VisibleBrowserListener
import com.shirochi.npia.webengine.WebLoginCoordinator
import com.shirochi.npia.webengine.WebLoginSession
import com.shirochi.npia.webengine.WebLoginWebView
import kotlinx.coroutines.launch

/**
 * A plain browser for signing in to any site. Logins land in the cookie store the scraper uses,
 * and the system password manager offers to save and fill credentials.
 */
@SuppressLint("SetJavaScriptEnabled")
@Composable
fun BrowserScreen(model: AppViewModel, topBar: TopBarState) {
    val context = LocalContext.current
    var address by remember { mutableStateOf(model.browserUrl.orEmpty()) }
    var pageUrl by remember { mutableStateOf(model.browserUrl) }
    var loading by remember { mutableStateOf(false) }
    var progress by remember { mutableIntStateOf(0) }
    var canBack by remember { mutableStateOf(false) }
    var canForward by remember { mutableStateOf(false) }
    val webView = remember {
        WebView(context).also { view ->
            VisibleBrowser.configure(view, object : VisibleBrowserListener {
                override fun onPage(url: String?, title: String?, loading: Boolean, progress: Int) {
                    if (url != null && url != "about:blank") {
                        pageUrl = url
                        address = url
                        model.browserUrl = url
                    }
                    canBack = view.canGoBack()
                    canForward = view.canGoForward()
                    setLoading(loading, progress)
                }

                private fun setLoading(isLoading: Boolean, value: Int) {
                    loading = isLoading
                    progress = value
                }
            })
            model.browserUrl?.let(view::loadUrl)
        }
    }
    DisposableEffect(Unit) {
        onDispose { webView.destroy() }
    }
    BackHandler(enabled = canBack) { webView.goBack() }

    val downloadable = pageUrl?.let { SourceCatalog.adapterFor(it) } != null
    LaunchedEffect(Unit) {
        topBar.actions = {
            TextButton(onClick = { model.push(Route.Sites) }) { Text("Sites") }
        }
    }

    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            IconButton(onClick = { webView.goBack() }, enabled = canBack) {
                Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back")
            }
            IconButton(onClick = { webView.goForward() }, enabled = canForward) {
                Icon(Icons.AutoMirrored.Filled.ArrowForward, contentDescription = "Forward")
            }
            OutlinedTextField(
                value = address,
                onValueChange = { address = it },
                singleLine = true,
                placeholder = { Text("Site address or search") },
                keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Go),
                keyboardActions = KeyboardActions(onGo = {
                    val url = VisibleBrowser.normalize(address)
                    if (url.isNotEmpty()) webView.loadUrl(url)
                }),
                modifier = Modifier.weight(1f),
            )
            IconButton(onClick = { if (loading) webView.stopLoading() else webView.reload() }, enabled = pageUrl != null) {
                Icon(if (loading) Icons.Filled.Close else Icons.Filled.Refresh, contentDescription = if (loading) "Stop" else "Reload")
            }
        }
        if (loading) LinearProgressIndicator(progress = { progress / 100f }, modifier = Modifier.fillMaxWidth())
        if (downloadable) {
            Button(
                onClick = {
                    model.sharedText = pageUrl
                    model.select(Tab.ADD)
                },
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
            ) { Text("Download this book") }
        }
        if (pageUrl == null) {
            BrowserStartPage(model) { url -> webView.loadUrl(url) }
        } else {
            AndroidView(factory = { webView }, modifier = Modifier.weight(1f).fillMaxWidth())
        }
    }
}

@Composable
private fun BrowserStartPage(model: AppViewModel, open: (String) -> Unit) {
    val hosts by model.sites.hosts.collectAsStateWithLifecycle()
    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Sign in to sites", style = MaterialTheme.typography.titleMedium)
                Text(
                    "Open any site and log in as you would in a normal browser. Downloads from that site then use " +
                        "your login. Your phone's password manager can save and fill your passwords here.",
                    style = MaterialTheme.typography.bodyMedium,
                )
            }
        }
        if (hosts.isNotEmpty()) {
            item { SectionHeader("Recent sites") }
            items(hosts, key = { it }) { host ->
                ListItem(
                    headlineContent = { Text(host) },
                    supportingContent = { if (SiteCookies.hasCookies(host)) Text("Has saved login cookies") },
                    modifier = Modifier.clickable { open("https://$host") },
                )
                HorizontalDivider()
            }
        }
    }
}

/** Sites with saved cookies, with per-site and global sign-out. */
@Composable
fun SitesScreen(model: AppViewModel) {
    val hosts by model.sites.hosts.collectAsStateWithLifecycle()
    var refresh by remember { mutableIntStateOf(0) }
    var confirmAll by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    val signedIn = remember(hosts, refresh) { hosts.filter(SiteCookies::hasCookies) }

    LazyColumn(Modifier.fillMaxSize()) {
        item {
            Footnote("Sites you opened in the browser that still hold cookies. Signing out removes that site's cookies, including its login.")
        }
        if (signedIn.isEmpty()) {
            item { Text("No saved logins yet.", Modifier.padding(16.dp), style = MaterialTheme.typography.bodyMedium) }
        }
        items(signedIn, key = { it }) { host ->
            ListItem(
                headlineContent = { Text(host, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                leadingContent = { Icon(Icons.Filled.AccountCircle, contentDescription = null) },
                trailingContent = {
                    TextButton(onClick = {
                        SiteCookies.signOut(host)
                        refresh++
                    }) { Text("Sign out") }
                },
                modifier = Modifier.clickable {
                    model.browserUrl = "https://$host"
                    model.select(Tab.BROWSER)
                },
            )
            HorizontalDivider()
        }
        item {
            TextButton(onClick = { confirmAll = true }, modifier = Modifier.padding(8.dp)) { Text("Sign out of all sites") }
        }
    }
    if (confirmAll) {
        AlertDialog(
            onDismissRequest = { confirmAll = false },
            title = { Text("Sign out of all sites?") },
            text = { Text("This removes every saved login and cookie in the app's browser. Saved passwords in your password manager are kept.") },
            confirmButton = {
                TextButton(onClick = {
                    confirmAll = false
                    scope.launch {
                        SiteCookies.signOutAll()
                        refresh++
                    }
                }) { Text("Sign out") }
            },
            dismissButton = { TextButton(onClick = { confirmAll = false }) { Text("Cancel") } },
        )
    }
}

/**
 * Shows sign-in requests from downloads (`WebEngineProviding.presentLogin`) as a full-screen
 * browser. Registers as attached while composed, so jobs only wait for a login when it can be shown.
 */
@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun SignInPrompt() {
    DisposableEffect(Unit) {
        WebLoginCoordinator.isAttached = true
        onDispose { WebLoginCoordinator.isAttached = false }
    }
    val session by WebLoginCoordinator.session.collectAsStateWithLifecycle()
    val current = session ?: return
    SignInDialog(current)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
private fun SignInDialog(session: WebLoginSession) {
    val url by session.currentUrl.collectAsStateWithLifecycle()
    val loading by session.isLoading.collectAsStateWithLifecycle()
    Dialog(onDismissRequest = { session.cancel() }, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        Scaffold(
            topBar = {
                TopAppBar(
                    title = {
                        Column {
                            Text("Sign in", style = MaterialTheme.typography.titleMedium)
                            Text(url.orEmpty(), style = MaterialTheme.typography.bodySmall, maxLines = 1, overflow = TextOverflow.Ellipsis)
                        }
                    },
                    navigationIcon = {
                        IconButton(onClick = { session.cancel() }) { Icon(Icons.Filled.Close, contentDescription = "Cancel") }
                    },
                    actions = { TextButton(onClick = { session.finish() }) { Text("Done") } },
                )
            },
        ) { padding ->
            Column(Modifier.padding(padding).fillMaxSize()) {
                if (loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                Footnote("Log in, then tap Done. The download continues with your login.")
                AndroidView(
                    factory = { context -> WebLoginWebView.create(context, session) },
                    onRelease = { it.destroy() },
                    modifier = Modifier.weight(1f).fillMaxWidth(),
                )
            }
        }
    }
}
