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
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.model.SourceInfo
import com.shirochi.npia.app.model.SourceOptionGroup
import com.shirochi.npia.app.model.difference
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.SharedOverrides
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

    // Working copies: `shared` holds the effective values for the detected source.
    var draft by remember(source) { mutableStateOf(settings) }
    var shared by remember(source) { mutableStateOf(source?.let(settings::effective) ?: settings.shared) }
    var rememberDefaults by remember(source) { mutableStateOf(false) }
    var showMore by remember { mutableStateOf(false) }

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp)) {
        OutlinedTextField(
            value = text,
            onValueChange = { text = it },
            label = { Text("Novel URL (one per line)") },
            minLines = 2,
            maxLines = 6,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri),
            modifier = Modifier.fillMaxWidth().padding(16.dp),
        )
        Row(Modifier.padding(horizontal = 8.dp), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            TextButton(onClick = {
                clipboardText(context)?.let { clip ->
                    val found = SourceInfo.extractUrls(clip).ifEmpty { listOf(clip.trim()) }
                    text = (text.lines().filter { it.isNotBlank() } + found).joinToString("\n")
                }
            }) { Text("Paste") }
            if (text.isNotEmpty()) TextButton(onClick = { text = "" }) { Text("Clear") }
        }
        DetectionSummary(urls)

        if (source == null) return@Column

        SectionHeader("Output")
        FormatPicker(shared.formats) { shared = shared.copy(formats = it) }
        if (OutputFormat.TXT in shared.formats) {
            SwitchRow("One TXT file per chapter", shared.separateTxt, { shared = shared.copy(separateTxt = it) })
        }

        SectionHeader("Chapters")
        OptionalIntRow("From chapter", shared.rangeFrom, 1) { shared = shared.copy(rangeFrom = it) }
        OptionalIntRow("To chapter", shared.rangeTo, shared.rangeFrom ?: 1) { shared = shared.copy(rangeTo = it) }
        SwitchRow("Skip paid chapters", shared.skipPaid, { shared = shared.copy(skipPaid = it) })
        SwitchRow("Number chapter titles", shared.numberChapters, { shared = shared.copy(numberChapters = it) })

        SourceOptions(source, draft) { draft = it }

        SwitchRow("Speed and images", showMore, { showMore = it })
        if (showMore) {
            StepperRow("Chapter threads", shared.threads, 1..16, { shared = shared.copy(threads = it) })
            StepperRow("Image downloads", shared.imageWorkers, 1..16, { shared = shared.copy(imageWorkers = it) })
            DecimalField("Delay min (s)", shared.intervalMin, { shared = shared.copy(intervalMin = it) })
            DecimalField("Delay max (s)", shared.intervalMax, { shared = shared.copy(intervalMax = it) })
            SwitchRow("Use chapter cache", shared.useCache, { shared = shared.copy(useCache = it) })
            SwitchRow("Compress images", draft.images.compress, { draft = draft.copy(images = draft.images.copy(compress = it)) })
            if (draft.images.compress) {
                ChoiceRow("Image format", draft.images.format, ImageFormat.entries, { it.label }, { draft = draft.copy(images = draft.images.copy(format = it)) })
            }
        }

        SwitchRow("Save as defaults for ${source.raw}", rememberDefaults, { rememberDefaults = it })
        Button(
            onClick = {
                queue(model, supported, source, draft, shared, rememberDefaults)
                text = ""
                onQueued()
            },
            modifier = Modifier.fillMaxWidth().padding(16.dp),
        ) { Text(if (supported.size > 1) "Download ${supported.size} books" else "Download") }
        Footnote("Defaults are stored as per-source overrides; skip-paid and image choices apply to this download only.")
    }
}

@Composable
private fun DetectionSummary(urls: List<String>) {
    if (urls.isEmpty()) {
        Footnote("Paste a book page or chapter list URL. The source is detected automatically. You can also share a link to Npia Mobile from your browser.")
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

/** Per-source option groups (shared with the settings screens). */
@Composable
fun SourceOptions(source: SourceId, draft: AppSettings, onChange: (AppSettings) -> Unit) {
    val groups = SourceInfo.groups(source)
    if (groups.isEmpty()) return
    SectionHeader("${source.raw} options")
    val s = draft.sources
    for (group in groups) {
        when (group) {
            SourceOptionGroup.NPIA -> {
                SwitchRow("Include notices", draft.npia.includeNotices, { onChange(draft.copy(npia = draft.npia.copy(includeNotices = it))) })
                SwitchRow("Browser fallback", draft.npia.useBrowserFallback, { onChange(draft.copy(npia = draft.npia.copy(useBrowserFallback = it))) })
            }
            SourceOptionGroup.KPAGE -> {
                SwitchRow("Skip last page", s.kpage.skipLastPage, { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(skipLastPage = it)))) })
                SwitchRow("Keep filler pages", s.kpage.keepFiller, { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(keepFiller = it)))) })
                SwitchRow("Remove duplicate images", s.kpage.dedupeImages, { onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(dedupeImages = it)))) })
                if (s.kpage.dedupeImages) {
                    StepperRow("Leading images to compare", s.kpage.dedupeLeadingImages, 1..10, {
                        onChange(draft.copy(sources = s.copy(kpage = s.kpage.copy(dedupeLeadingImages = it))))
                    })
                }
            }
            SourceOptionGroup.NTK_COVER ->
                SwitchRow("Use npia cover when available", s.ntkNpiaCover, { onChange(draft.copy(sources = s.copy(ntkNpiaCover = it))) })
            SourceOptionGroup.NWEB_COVER ->
                SwitchRow("Prefer store cover", s.nwebStoreCover, { onChange(draft.copy(sources = s.copy(nwebStoreCover = it))) })
            SourceOptionGroup.KOBO_LAYOUT ->
                SwitchRow("Horizontal layout", s.koboHorizontalLayout, { onChange(draft.copy(sources = s.copy(koboHorizontalLayout = it))) })
            SourceOptionGroup.LONG_IMAGES ->
                SwitchRow("Long image layout", draft.images.longImageLayout, { onChange(draft.copy(images = draft.images.copy(longImageLayout = it))) })
        }
    }
}

private fun queue(
    model: AppViewModel,
    urls: List<String>,
    source: SourceId,
    draft: AppSettings,
    shared: com.shirochi.npia.core.SharedSettings,
    rememberDefaults: Boolean,
) {
    model.manager.enqueue(urls) { jobSource ->
        var settings = draft.copy(shared = draft.shared.copy(skipPaid = shared.skipPaid, imageWorkers = shared.imageWorkers))
        if (jobSource != null) {
            val difference = SharedOverrides.difference(shared, settings.shared)
            settings = settings.copy(overrides = settings.overrides.toMutableMap().apply {
                if (difference.isEmpty) remove(jobSource.raw) else put(jobSource.raw, difference)
            })
        }
        settings
    }
    if (rememberDefaults) {
        model.manager.updateSettings { saved ->
            val difference = SharedOverrides.difference(shared, saved.shared)
            saved.copy(
                overrides = saved.overrides.toMutableMap().apply {
                    if (difference.isEmpty) remove(source.raw) else put(source.raw, difference)
                },
                npia = draft.npia,
                sources = draft.sources,
                images = saved.images.copy(longImageLayout = draft.images.longImageLayout),
            )
        }
    }
}

private fun clipboardText(context: Context): String? {
    val clipboard = context.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
    return clipboard.primaryClip?.takeIf { it.itemCount > 0 }?.getItemAt(0)?.coerceToText(context)?.toString()?.takeIf { it.isNotBlank() }
}
