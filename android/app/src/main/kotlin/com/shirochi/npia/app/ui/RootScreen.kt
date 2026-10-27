package com.shirochi.npia.app.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.ArrowBack
import androidx.compose.material.icons.automirrored.filled.List
import androidx.compose.material.icons.filled.AccountCircle
import androidx.compose.material.icons.filled.Add
import androidx.compose.material.icons.filled.Search
import androidx.compose.material.icons.filled.Settings
import androidx.compose.material.icons.filled.Share
import androidx.compose.material3.Badge
import androidx.compose.material3.BadgedBox
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
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
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.input.pointer.pointerInput
import androidx.compose.foundation.gestures.detectTapGestures
import androidx.compose.ui.text.style.TextOverflow
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.Route
import com.shirochi.npia.app.Tab

/** Actions a screen can put in the top bar. */
class TopBarState {
    var actions by mutableStateOf<(@Composable () -> Unit)?>(null)
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun RootScreen(model: AppViewModel) {
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    val snackbar = remember { SnackbarHostState() }
    val topBar = remember { TopBarState() }
    val stack = model.stack(model.tab)
    val route = stack.lastOrNull()
    val focusManager = LocalFocusManager.current
    val cart by model.cart.items.collectAsStateWithLifecycle()
    val catalog = rememberCatalogWeb(model)

    LaunchedEffect(Unit) {
        manager.messages.collect { snackbar.showSnackbar(it) }
    }
    BackHandler(enabled = stack.isNotEmpty()) { model.pop() }
    LaunchedEffect(model.tab, route) { topBar.actions = null }

    Scaffold(
        topBar = {
            TopAppBar(
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
            NavigationBar {
                Tab.entries.forEach { tab ->
                    NavigationBarItem(
                        selected = model.tab == tab,
                        onClick = { model.select(tab) },
                        label = { Text(tab.title) },
                        icon = {
                            val active = jobs.count { it.state.isActive }
                            val count = when (tab) {
                                Tab.QUEUE -> active
                                Tab.ADD -> cart.size
                                else -> 0
                            }
                            if (count > 0) {
                                BadgedBox(badge = { Badge { Text("$count") } }) { Icon(tab.icon, contentDescription = null) }
                            } else {
                                Icon(tab.icon, contentDescription = null)
                            }
                        },
                    )
                }
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
                    Tab.ADD -> AddScreen(model, onQueued = { model.select(Tab.QUEUE) })
                    Tab.CATALOG -> CatalogScreen(catalog, model, topBar)
                    Tab.BROWSER -> BrowserScreen(model, topBar)
                    Tab.QUEUE -> QueueScreen(model, topBar)
                    Tab.UPLOADS -> UploadsScreen(model, topBar)
                    Tab.SETTINGS -> SettingsScreen(model)
                }
                is Route.JobDetail -> JobDetailScreen(model, route.id)
                Route.General -> GeneralSettingsScreen(model)
                Route.Overrides -> OverridesListScreen(model)
                is Route.OverrideForm -> OverrideFormScreen(model, route.source)
                Route.SourceOptions -> SourceOptionsScreen(model)
                Route.Npia -> NpiaSettingsScreen(model)
                Route.Images -> ImageSettingsScreen(model)
                Route.Text -> TextSettingsScreen(model)
                Route.Upload -> UploadSettingsScreen(model)
                Route.Appearance -> AppearanceSettingsScreen(model)
                Route.Background -> BackgroundSettingsScreen(model)
                Route.Transfer -> ConfigTransferScreen(model)
                Route.Sites -> SitesScreen(model)
            }
        }
    }
    SignInPrompt()
}

private val Tab.icon
    get() = when (this) {
        Tab.ADD -> Icons.Filled.Add
        Tab.CATALOG -> Icons.Filled.Search
        Tab.BROWSER -> Icons.Filled.AccountCircle
        Tab.QUEUE -> Icons.AutoMirrored.Filled.List
        Tab.UPLOADS -> Icons.Filled.Share
        Tab.SETTINGS -> Icons.Filled.Settings
    }
