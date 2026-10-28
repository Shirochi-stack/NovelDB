package com.shirochi.npia.app.ui

import android.content.ClipboardManager
import android.content.Context
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Close
import androidx.compose.material3.Button
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.model.SourceInfo
import com.shirochi.npia.app.model.SourceOptionGroup
import com.shirochi.npia.app.model.editRunSettings
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.SourceCatalog

/**
 * One entry point for every source: paste URLs, see which source handles them, and adjust only
 * the options that matter for it before queueing (iOS `AddURLView`).
 */
@Composable
fun AddScreen(model: AppViewModel, onQueued: () -> Unit) {
    val manager = model.manager
    val settings by manager.settings.collectAsStateWithLifecycle()
    val context = LocalContext.current
    var text by rememberSaveable { mutableStateOf("") }

    LaunchedEffect(model.sharedText) {
        val shared = model.sharedText ?: return@LaunchedEffect
        model.sharedText = null
        val found = SourceInfo.extractUrls(shared).ifEmpty { listOfNotNull(SourceInfo.parseUrl(shared)) }
        if (found.isNotEmpty()) text = (text.lines().filter { it.isNotBlank() } + found).distinct().joinToString("\n")
    }

    val cart by model.cart.items.collectAsStateWithLifecycle()
    val urls = (SourceInfo.urls(text) + cart.map { it.url }).distinct()
    // A link is downloadable when any enabled site engine can try it: the script engines (novel-downloader
    // rules, WebToEpub) try sites that no list names, since WebToEpub also recognises some sites by their page.
    val supported = urls.filter { SourceCatalog.resolve(it, settings.engines) != null }
    val source = supported.firstOrNull()?.let { SourceCatalog.adapterFor(it)?.id }

    // The desktop download options, saved as they change (npia keeps its own values like the
    // desktop main window; other sources share the external downloader's).
    val run = source?.let(settings::effective) ?: settings.shared
    fun edit(change: (SharedSettings) -> SharedSettings) = manager.updateSettings { it.editRunSettings(source, change) }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 16.dp)) {
        OutlinedTextField(
            value = text,
            onValueChange = { text = it },
            label = { Text("Novel URL (one per line)") },
            minLines = 1,
            maxLines = 5,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 8.dp),
        )
        Row(Modifier.padding(horizontal = 8.dp), verticalAlignment = Alignment.CenterVertically) {
            TextButton(onClick = {
                clipboardText(context)?.let { clip ->
                    val found = SourceInfo.extractUrls(clip).ifEmpty { listOf(clip.trim()) }
                    text = (text.lines().filter { it.isNotBlank() } + found).joinToString("\n")
                }
            }) { Text("Paste") }
            if (text.isNotEmpty()) TextButton(onClick = { text = "" }) { Text("Clear") }
        }
        LinkCartRow(cart.size) { model.push(com.shirochi.npia.app.Route.LinkCart) }
        DetectionSummary(urls, settings.engines)

        CompactHeader("Format")
        FormatChips(run.formats) { f -> edit { it.copy(formats = f) } }
        if (OutputFormat.TXT in run.formats) {
            ChipFlow { ToggleChip("Separate TXT files", run.separateTxt) { v -> edit { it.copy(separateTxt = v) } } }
        }

        CompactHeader("Download")
        FieldFlow {
            FieldGroup {
                MiniLabel("Threads")
                MiniStepper(run.threads, 1..16) { v -> edit { it.copy(threads = v) } }
            }
            FieldGroup {
                MiniLabel("Image Workers")
                MiniStepper(run.imageWorkers, 1..16) { v -> edit { it.copy(imageWorkers = v) } }
            }
        }
        FieldFlow {
            FieldGroup {
                MiniLabel("Rate Limiting")
                MiniLabel("Min")
                MiniNumberField(formatNumber(run.intervalMin), "0", decimal = true) { t ->
                    t.trim().replace(',', '.').toDoubleOrNull()?.takeIf { it >= 0 }?.let { v -> edit { it.copy(intervalMin = v) } }
                }
                MiniLabel("Max")
                MiniNumberField(formatNumber(run.intervalMax), "0", decimal = true) { t ->
                    t.trim().replace(',', '.').toDoubleOrNull()?.takeIf { it >= 0 }?.let { v -> edit { it.copy(intervalMax = v) } }
                }
                MiniLabel("s")
            }
        }
        FieldFlow {
            FieldGroup {
                MiniLabel("Chapter Range")
                MiniLabel("From")
                MiniNumberField(run.rangeFrom?.toString().orEmpty(), "1", decimal = false) { t ->
                    if (t.isBlank()) edit { it.copy(rangeFrom = null) } else t.trim().toIntOrNull()?.takeIf { it > 0 }?.let { v -> edit { it.copy(rangeFrom = v) } }
                }
                MiniLabel("To")
                MiniNumberField(run.rangeTo?.toString().orEmpty(), "end", decimal = false) { t ->
                    if (t.isBlank()) edit { it.copy(rangeTo = null) } else t.trim().toIntOrNull()?.takeIf { it > 0 }?.let { v -> edit { it.copy(rangeTo = v) } }
                }
            }
        }
        ChipFlow {
            ToggleChip("Skip paid", run.skipPaid) { v -> edit { it.copy(skipPaid = v) } }
            ToggleChip("Number chapters", run.numberChapters) { v -> edit { it.copy(numberChapters = v) } }
            ToggleChip("Generate on stop", run.generateOnStop) { v -> edit { it.copy(generateOnStop = v) } }
            ToggleChip("Use Cache", run.useCache) { v -> edit { it.copy(useCache = v) } }
            ToggleChip("Cache Images", run.cacheImages) { v -> edit { it.copy(cacheImages = v) } }
        }

        if (source != null) {
            SourceOptions(source, settings) { next ->
                manager.updateSettings { it.copy(sources = next.sources, images = next.images, npia = next.npia) }
            }
        }

        Button(
            onClick = {
                manager.enqueue(supported) { manager.settings.value }
                model.cart.removeAll(supported)
                text = ""
                onQueued()
            },
            enabled = supported.isNotEmpty(),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
        ) { Text(if (supported.size > 1) "Download ${supported.size} books" else "Download") }
        // Like the desktop Download page, sign-ins sit next to the Download button.
        OutlinedButton(
            onClick = { model.push(com.shirochi.npia.app.Route.EnterApp) },
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp),
        ) { Text("Enter App") }
        Footnote("Sign in to sfc, qdn and rbooks to read the chapters your account bought.")
    }
}

/** One row for the link cart; the books are listed on their own screen so Download stays in reach. */
@Composable
private fun LinkCartRow(count: Int, onOpen: () -> Unit) {
    if (count == 0) return
    NavRow("🔗 Link cart ($count)", "Books from the Catalog tab; Download queues them too", onOpen)
}

/** The books in the link cart, each removable. */
@Composable
fun LinkCartScreen(model: AppViewModel, topBar: TopBarState) {
    val items by model.cart.items.collectAsStateWithLifecycle()
    LaunchedEffect(Unit) {
        topBar.actions = {
            TextButton(onClick = { model.cart.clear(); model.pop() }) { Text("Clear cart") }
        }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(start = 16.dp, end = 4.dp, top = 4.dp, bottom = 16.dp)) {
        if (items.isEmpty()) Text("The cart is empty. Tap the 🔗 button on a card in the Catalog tab to add a book.", style = MaterialTheme.typography.bodyMedium)
        items.forEach { item ->
            Row(verticalAlignment = Alignment.CenterVertically) {
                Column(Modifier.weight(1f)) {
                    Text(item.title.ifBlank { item.url }, style = MaterialTheme.typography.bodyMedium, maxLines = 1, overflow = TextOverflow.Ellipsis)
                    Text(
                        listOf(item.source, com.shirochi.npia.core.bareHost(item.url)).filter { it.isNotBlank() }.joinToString(" \u00B7 "),
                        style = MaterialTheme.typography.bodySmall,
                        color = MaterialTheme.colorScheme.onSurfaceVariant,
                        maxLines = 1,
                        overflow = TextOverflow.Ellipsis,
                    )
                }
                IconButton(onClick = { model.cart.remove(item.url) }) { Icon(Icons.Filled.Close, contentDescription = "Remove from cart") }
            }
        }
    }
}

/** A section title with less space than `SectionHeader`. */
@Composable
private fun CompactHeader(title: String) {
    Text(
        title,
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 2.dp),
    )
}

@Composable
private fun DetectionSummary(urls: List<String>, engines: EngineSettings) {
    if (urls.isEmpty()) {
        Footnote("Paste a book page or chapter list URL. The source is detected automatically. You can also share a link to NovelDB from your browser.")
        return
    }
    val unsupported = urls.filter { SourceCatalog.resolve(it, engines) == null }
    // No site list names these, so only the script engines will try them.
    val unlisted = urls.filter { it !in unsupported && SourceCatalog.adapterFor(it) == null }
    if (unsupported.isEmpty() && unlisted.isEmpty()) return
    Column(Modifier.padding(horizontal = 16.dp)) {
        unsupported.forEach { url ->
            Text(
                "\u2717 Not supported: ${com.shirochi.npia.core.bareHost(url).ifEmpty { url }}",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.error,
            )
        }
        unlisted.forEach { url ->
            Text(
                "? ${com.shirochi.npia.core.bareHost(url).ifEmpty { url }} is on no engine's site list; the site engines will still try it",
                style = MaterialTheme.typography.bodySmall,
                color = MaterialTheme.colorScheme.onSurfaceVariant,
            )
        }
    }
}

/** Per-source option groups as compact chips (shared with the settings screens). */
@Composable
fun SourceOptions(source: SourceId, draft: AppSettings, onChange: (AppSettings) -> Unit) {
    val groups = SourceInfo.groups(source)
    if (groups.isEmpty()) return
    Text(
        "${source.raw} options",
        style = MaterialTheme.typography.titleSmall,
        color = MaterialTheme.colorScheme.primary,
        modifier = Modifier.padding(start = 16.dp, end = 16.dp, top = 10.dp, bottom = 2.dp),
    )
    val s = draft.sources
    ChipFlow {
        for (group in groups) {
            when (group) {
                SourceOptionGroup.NPIA -> {
                    ToggleChip("Download Author Notices", draft.npia.includeNotices) { onChange(draft.copy(npia = draft.npia.copy(includeNotices = it))) }
                    ToggleChip("Browser fallback", draft.npia.useBrowserFallback) { onChange(draft.copy(npia = draft.npia.copy(useBrowserFallback = it))) }
                }
                SourceOptionGroup.KPAGE -> {
                    ToggleChip("Skip last page", s.kpage.skipLastPage) { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(skipLastPage = it)))) }
                    ToggleChip("Keep filler", s.kpage.keepFiller) { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(keepFiller = it)))) }
                    ToggleChip("Dedupe images", s.kpage.dedupeImages) { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(dedupeImages = it)))) }
                }
                SourceOptionGroup.NTK_COVER ->
                    ToggleChip("Prefer source cover", s.ntkNpiaCover) { onChange(draft.copy(sources = s.copy(ntkNpiaCover = it))) }
                SourceOptionGroup.NWEB_COVER ->
                    ToggleChip("Prefer store cover", s.nwebStoreCover) { onChange(draft.copy(sources = s.copy(nwebStoreCover = it))) }
                SourceOptionGroup.KOBO_LAYOUT ->
                    ToggleChip("Horizontal Japanese text", s.koboHorizontalLayout) { onChange(draft.copy(sources = s.copy(koboHorizontalLayout = it))) }
                SourceOptionGroup.LONG_IMAGES ->
                    ToggleChip("Long image layout", draft.images.longImageLayout) { onChange(draft.copy(images = draft.images.copy(longImageLayout = it))) }
            }
        }
    }
    if (SourceOptionGroup.KPAGE in groups && s.kpage.dedupeImages) {
        FieldFlow {
            FieldGroup {
                MiniLabel("Leading images to compare")
                MiniStepper(s.kpage.dedupeLeadingImages, 1..10) { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(dedupeLeadingImages = it)))) }
            }
        }
    }
}

private fun clipboardText(context: Context): String? {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    return clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString()?.takeIf { it.isNotBlank() }
}
