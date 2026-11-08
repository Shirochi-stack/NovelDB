package com.shirochi.npia.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.animation.AnimatedVisibility
import androidx.compose.animation.animateColorAsState
import androidx.compose.animation.expandHorizontally
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.shrinkHorizontally
import androidx.compose.foundation.Image
import androidx.compose.foundation.background
import androidx.compose.foundation.selection.selectable
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxHeight
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.navigationBarsPadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.materialIcon
import androidx.compose.material.icons.materialPath
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Surface
import androidx.compose.ui.Alignment
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.semantics.Role
import androidx.compose.ui.unit.dp
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.Scaffold
import androidx.compose.material3.SnackbarHost
import androidx.compose.material3.SnackbarHostState
import androidx.compose.material3.Text
import androidx.compose.material3.TopAppBar
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.platform.LocalView
import androidx.compose.runtime.DisposableEffect
import androidx.core.view.WindowCompat
import androidx.core.view.WindowInsetsCompat
import androidx.core.view.WindowInsetsControllerCompat
import android.app.Activity
import android.content.ContextWrapper
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.res.painterResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.R
import com.shirochi.npia.app.Route
import com.shirochi.npia.app.Tab

/** Actions a screen can put in the top bar. */
class TopBarState {
    var actions by mutableStateOf<(@Composable () -> Unit)?>(null)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RootScreen(model: AppViewModel) {
    val saveableState = rememberSaveableStateHolder()
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val topBar = remember { TopBarState() }
    val stack = model.stack(model.tab)
    val route = stack.lastOrNull()
    val focusManager = LocalFocusManager.current
    val cart by model.cart.items.collectAsStateWithLifecycle()
    val catalog = rememberCatalogWeb(model)
    // Add and Catalog are one bottom tab ("Download"); it reopens whichever of the two was used last.
    var lastDownloadTab by remember { mutableStateOf(Tab.CATALOG) }
    if (model.tab in DOWNLOAD_TABS) lastDownloadTab = model.tab
    val downloadHome = route == null && model.tab in DOWNLOAD_TABS

    LaunchedEffect(Unit) {
        manager.messages.collect { snackbar.showSnackbar(it) }
    }
    BackHandler(enabled = stack.isNotEmpty()) { model.pop() }
    LaunchedEffect(model.tab, route) { topBar.actions = null }
    // The Catalog's reader in full screen: no app bars, no system bars (a swipe shows those for a moment).
    val readerFull = catalog.readerFullScreen && model.tab == Tab.CATALOG && route == null
    BackHandler(enabled = readerFull) { catalog.leaveFullScreen() }
    val rootView = LocalView.current
    DisposableEffect(readerFull) {
        var context = rootView.context
        while (context is ContextWrapper && context !is Activity) context = context.baseContext
        val window = (context as? Activity)?.window
        if (window != null) {
            val controller = WindowCompat.getInsetsController(window, rootView)
            if (readerFull) {
                controller.systemBarsBehavior = WindowInsetsControllerCompat.BEHAVIOR_SHOW_TRANSIENT_BARS_BY_SWIPE
                controller.hide(WindowInsetsCompat.Type.systemBars())
            } else {
                controller.show(WindowInsetsCompat.Type.systemBars())
            }
        }
        onDispose { }
    }

    Scaffold(
        topBar = {
            // The Download tab's own header is its Catalog / Add links switch.
            if (readerFull) {
                // none: the reader has the whole screen
            } else if (downloadHome) {
                DownloadSwitch(model.tab, onSelect = { model.select(it) }, actions = if (model.tab == Tab.CATALOG) topBar.actions else null)
            } else TopAppBar(
                title = {
                    val title = when (route) {
                        null -> if (model.tab == Tab.SETTINGS) "Settings" else model.tab.title
                        is Route.JobDetail -> manager.job(route.id)?.displayTitle ?: route.title
                        else -> route.title
                    }
                    Text(title, maxLines = 1, overflow = TextOverflow.Ellipsis)
                },
                navigationIcon = {
                    if (route != null) {
                        IconButton(onClick = { model.pop() }) { Icon(Icons.AutoMirrored.Filled.ArrowBack, contentDescription = "Back") }
                    }
                },
                actions = { topBar.actions?.invoke() },
            )
        },
        bottomBar = {
            if (!readerFull) {
                val active = jobs.count { it.state.isActive }
                FloatingTabBar(
                    items = BAR_ITEMS,
                    selected = BAR_ITEMS.first { model.tab in it.tabs },
                    badge = { item ->
                        when {
                            Tab.QUEUE in item.tabs -> active
                            Tab.ADD in item.tabs -> cart.size
                            else -> 0
                        }
                    },
                    onSelect = { item ->
                        if (model.tab in item.tabs) {
                            if (model.tab == Tab.CATALOG) catalog.reload()
                            model.select(model.tab)
                        } else {
                            model.select(if (item.tabs == DOWNLOAD_TABS) lastDownloadTab else item.tabs.first())
                        }
                    },
                )
            }
        },
        snackbarHost = { SnackbarHost(snackbar) },
    ) { padding ->
        // Taps that no button, text field or web page handles clear focus, which hides the keyboard.
        Box(
            Modifier
                .padding(padding)
                .fillMaxSize()
                .pointerInput(focusManager) { detectTapGestures(onTap = { focusManager.clearFocus() }) },
        ) {
            when (route) {
                null -> when (model.tab) {
                    Tab.ADD -> saveableState.SaveableStateProvider("add") {
                        AddScreen(model, onQueued = { model.openQueue() })
                    }
                    Tab.CATALOG -> CatalogScreen(catalog, model, topBar)
                    Tab.BROWSER -> BrowserScreen(model, topBar)
                    Tab.LIBRARY -> LibraryScreen(model)
                    Tab.QUEUE -> QueueScreen(model, topBar)
                    Tab.UPLOADS -> UploadsScreen(model, topBar)
                    Tab.SETTINGS -> SettingsScreen(model)
                }
                is Route.JobDetail -> JobDetailScreen(model, route.id)
                Route.General -> GeneralSettingsScreen(model)
                Route.Overrides -> OverridesListScreen(model)
                is Route.OverrideForm -> OverrideFormScreen(model, route.source)
                Route.SourceOptions -> SourceOptionsScreen(model)
                Route.Engines -> EngineSettingsScreen(model)
                Route.AppUpdates -> AppUpdatesScreen(model)
                Route.Npia -> NpiaSettingsScreen(model)
                Route.EnterApp -> EnterAppScreen(model)
                Route.TagSearch -> TagSearchScreen(model)
                Route.Images -> ImageSettingsScreen(model)
                Route.Stitching -> ImageSettingsScreen(model, stitchingOnly = true)
                Route.Text -> TextSettingsScreen(model)
                Route.Upload -> UploadSettingsScreen(model)
                Route.LinkCart -> LinkCartScreen(model, topBar)
                Route.Appearance -> AppearanceSettingsScreen(model)
                Route.CatalogSite -> CatalogSiteSettingsScreen(model)
                Route.BrowserSettings -> BrowserSettingsScreen(model)
                Route.Background -> BackgroundSettingsScreen(model)
                Route.Transfer -> ConfigTransferScreen(model)
                Route.Sites -> SitesScreen(model)
            }
        }
    }
    SignInPrompt()
    AppUpdateDialog(model)
    model.pendingUpload?.let { upload ->
        ShareAgreementDialog(
            onAgree = {
                manager.preferences.shareAgreementAccepted = true
                model.pendingUpload = null
                upload()
            },
            onCancel = { model.pendingUpload = null },
        )
    }
}

private val DOWNLOAD_TABS = setOf(Tab.CATALOG, Tab.ADD)

/** Material's "library books" (the core icon set the app ships has none). */
private val LibraryIcon: ImageVector = materialIcon(name = "Filled.LibraryBooks") {
    materialPath {
        moveTo(4f, 6f); horizontalLineTo(2f); verticalLineToRelative(14f); curveToRelative(0f, 1.1f, 0.9f, 2f, 2f, 2f)
        horizontalLineToRelative(14f); verticalLineToRelative(-2f); horizontalLineTo(4f); verticalLineTo(6f); close()
        moveTo(20f, 2f); horizontalLineTo(8f); curveToRelative(-1.1f, 0f, -2f, 0.9f, -2f, 2f); verticalLineToRelative(12f)
        curveToRelative(0f, 1.1f, 0.9f, 2f, 2f, 2f); horizontalLineToRelative(12f); curveToRelative(1.1f, 0f, 2f, -0.9f, 2f, -2f)
        verticalLineTo(4f); curveToRelative(0f, -1.1f, -0.9f, -2f, -2f, -2f); close()
        moveTo(19f, 11f); horizontalLineTo(9f); verticalLineTo(9f); horizontalLineToRelative(10f); verticalLineToRelative(2f); close()
        moveTo(15f, 15f); horizontalLineTo(9f); verticalLineToRelative(-2f); horizontalLineToRelative(6f); verticalLineToRelative(2f); close()
        moveTo(19f, 7f); horizontalLineTo(9f); verticalLineTo(5f); horizontalLineToRelative(10f); verticalLineToRelative(2f); close()
    }
}

/** Material's "download" arrow (the core icon set the app ships has none). */
private val DownloadIcon: ImageVector = materialIcon(name = "Filled.Download") {
    materialPath {
        moveTo(19f, 9f); horizontalLineToRelative(-4f); verticalLineTo(3f); horizontalLineTo(9f); verticalLineToRelative(6f)
        horizontalLineTo(5f); lineToRelative(7f, 7f); lineToRelative(7f, -7f); close()
        moveTo(5f, 18f); verticalLineToRelative(2f); horizontalLineToRelative(14f); verticalLineToRelative(-2f); horizontalLineTo(5f); close()
    }
}

/** A bottom-bar entry: one or more tabs (Download holds both Catalog and Add). */
private class BarItem(val title: String, val icon: ImageVector, val tabs: Set<Tab>)

private val BAR_ITEMS = listOf(
    BarItem("Download", DownloadIcon, DOWNLOAD_TABS),
    BarItem(Tab.BROWSER.title, Icons.Filled.AccountCircle, setOf(Tab.BROWSER)),
    BarItem(Tab.LIBRARY.title, LibraryIcon, setOf(Tab.LIBRARY)),
    BarItem(Tab.QUEUE.title, Icons.AutoMirrored.Filled.List, setOf(Tab.QUEUE)),
    BarItem(Tab.UPLOADS.title, Icons.Filled.Share, setOf(Tab.UPLOADS)),
    BarItem(Tab.SETTINGS.title, Icons.Filled.Settings, setOf(Tab.SETTINGS)),
)

/** The Download tab's header: find books in the Catalog, or paste links to add. */
@Composable
private fun DownloadSwitch(tab: Tab, onSelect: (Tab) -> Unit, actions: (@Composable () -> Unit)?) {
    Surface(color = MaterialTheme.colorScheme.surface) {
        Row(
            Modifier.fillMaxWidth().statusBarsPadding().padding(start = 12.dp, end = 4.dp, top = 4.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            // A slim pill track; the chosen side is filled. Halgakos mining for the Catalog, in her cart for links.
            val options = listOf(
                Triple(Tab.CATALOG, "Catalog", R.drawable.halgakos_mining),
                Triple(Tab.ADD, "Add links", R.drawable.halgakos_cart),
            )
            Row(
                Modifier.weight(1f).height(40.dp).clip(RoundedCornerShape(20.dp))
                    .background(MaterialTheme.colorScheme.surfaceContainerHigh).padding(3.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                options.forEach { (option, label, art) ->
                    val on = tab == option
                    val fill by animateColorAsState(
                        if (on) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerHigh,
                        label = "download-switch",
                    )
                    Row(
                        Modifier.weight(1f).fillMaxHeight().clip(RoundedCornerShape(17.dp)).background(fill)
                            .selectable(selected = on, role = Role.Tab, onClick = { onSelect(option) }),
                        horizontalArrangement = Arrangement.Center,
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Image(painterResource(art), contentDescription = null, modifier = Modifier.size(28.dp))
                        Spacer(Modifier.width(6.dp))
                        Text(
                            label,
                            style = MaterialTheme.typography.labelLarge,
                            fontWeight = if (on) FontWeight.SemiBold else FontWeight.Normal,
                            color = if (on) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant,
                            maxLines = 1,
                        )
                    }
                }
            }
            actions?.invoke()
        }
    }
}

/**
 * A floating, rounded bottom bar: the selected tab is a pill with its icon and name, the others
 * show their icon (and a count badge: the link cart, the running downloads).
 */
@Composable
private fun FloatingTabBar(items: List<BarItem>, selected: BarItem, badge: (BarItem) -> Int, onSelect: (BarItem) -> Unit) {
    Box(Modifier.fillMaxWidth().navigationBarsPadding().padding(horizontal = 14.dp, vertical = 8.dp)) {
        Surface(
            shape = RoundedCornerShape(30.dp),
            color = MaterialTheme.colorScheme.surfaceContainerHigh,
            tonalElevation = 3.dp,
            shadowElevation = 8.dp,
            modifier = Modifier.fillMaxWidth(),
        ) {
            Row(
                Modifier.height(62.dp).padding(horizontal = 8.dp),
                horizontalArrangement = Arrangement.SpaceBetween,
                verticalAlignment = Alignment.CenterVertically,
            ) {
                items.forEach { item ->
                    val on = item == selected
                    val background by animateColorAsState(
                        if (on) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceContainerHigh,
                        label = "tab",
                    )
                    val tint = if (on) MaterialTheme.colorScheme.onPrimaryContainer else MaterialTheme.colorScheme.onSurfaceVariant
                    Row(
                        Modifier
                            .clip(RoundedCornerShape(22.dp))
                            .background(background)
                            .clickable(role = Role.Tab) { onSelect(item) }
                            .padding(horizontal = if (on) 16.dp else 12.dp, vertical = 10.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        val count = badge(item)
                        if (count > 0) {
                            BadgedBox(badge = { Badge { Text("$count") } }) { Icon(item.icon, contentDescription = item.title, tint = tint) }
                        } else {
                            Icon(item.icon, contentDescription = item.title, tint = tint)
                        }
                        AnimatedVisibility(on, enter = fadeIn() + expandHorizontally(), exit = fadeOut() + shrinkHorizontally()) {
                            Row {
                                Spacer(Modifier.width(8.dp))
                                Text(item.title, style = MaterialTheme.typography.labelLarge, color = tint, maxLines = 1)
                            }
                        }
                    }
                }
            }
        }
    }
}
