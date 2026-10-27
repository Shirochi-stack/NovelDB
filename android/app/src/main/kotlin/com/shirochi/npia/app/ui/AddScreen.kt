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
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
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
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.model.SourceInfo
import com.shirochi.npia.app.model.SourceOptionGroup
import com.shirochi.npia.app.model.editRunSettings
import com.shirochi.npia.core.AppSettings
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

    val urls = SourceInfo.urls(text)
    val supported = urls.filter { SourceCatalog.adapterFor(it) != null }
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
        DetectionSummary(urls)

        CompactHeader("Format")
        FormatChips(run.formats) { f -> edit { it.copy(formats = f) } }
        if (OutputFormat.TXT in run.formats) {
            ChipFlow { ToggleChip("Separate TXT files", run.separateTxt) { v -> edit { it.copy(separateTxt = v) } } }
        }

        CompactHeader("Download")
        CompactRow {
            MiniLabel("Threads", Modifier.weight(1f))
            MiniStepper(run.threads, 1..16) { v -> edit { it.copy(threads = v) } }
            MiniLabel("Image Workers", Modifier.weight(1f).padding(start = 8.dp))
            MiniStepper(run.imageWorkers, 1..16) { v -> edit { it.copy(imageWorkers = v) } }
        }
        CompactRow {
            MiniLabel("Rate Limiting", Modifier.weight(1f))
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
        CompactRow {
            MiniLabel("Chapter Range", Modifier.weight(1f))
            MiniLabel("From")
            MiniNumberField(run.rangeFrom?.toString().orEmpty(), "1", decimal = false) { t ->
                if (t.isBlank()) edit { it.copy(rangeFrom = null) } else t.trim().toIntOrNull()?.takeIf { it > 0 }?.let { v -> edit { it.copy(rangeFrom = v) } }
            }
            MiniLabel("To")
            MiniNumberField(run.rangeTo?.toString().orEmpty(), "end", decimal = false) { t ->
                if (t.isBlank()) edit { it.copy(rangeTo = null) } else t.trim().toIntOrNull()?.takeIf { it > 0 }?.let { v -> edit { it.copy(rangeTo = v) } }
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
                text = ""
                onQueued()
            },
            enabled = supported.isNotEmpty(),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
        ) { Text(if (supported.size > 1) "Download ${supported.size} books" else "Download") }
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
private fun DetectionSummary(urls: List<String>) {
    if (urls.isEmpty()) {
        Footnote("Paste a book page or chapter list URL. The source is detected automatically. You can also share a link to NovelDB from your browser.")
        return
    }
    Column(Modifier.padding(horizontal = 16.dp)) {
        urls.forEach { url ->
            val adapter = SourceCatalog.adapterFor(url)
            if (adapter != null) {
                Text("\u2713 ${adapter.id.raw}: ${SourceInfo.tierLabel(adapter.id)}", style = MaterialTheme.typography.bodySmall)
            } else {
                Text(
                    "\u2717 Not supported: ${com.shirochi.npia.core.bareHost(url).ifEmpty { url }}",
                    style = MaterialTheme.typography.bodySmall,
                    color = MaterialTheme.colorScheme.error,
                )
            }
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
        CompactRow {
            MiniLabel("Leading images to compare", Modifier.weight(1f))
            MiniStepper(s.kpage.dedupeLeadingImages, 1..10) { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(dedupeLeadingImages = it)))) }
        }
    }
}

private fun clipboardText(context: Context): String? {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    return clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString()?.takeIf { it.isNotBlank() }
}
