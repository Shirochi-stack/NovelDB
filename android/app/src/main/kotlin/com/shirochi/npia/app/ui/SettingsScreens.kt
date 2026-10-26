package com.shirochi.npia.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.Route
import com.shirochi.npia.app.model.count
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.NamingMode
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SharedOverrides
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.upload.Uploader
import com.shirochi.npia.upload.UploaderCatalog

@Composable
private fun SettingsColumn(content: @Composable ColumnScope.() -> Unit) {
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(bottom = 24.dp), content = content)
}

/** Current settings plus a setter, for the forms below. */
@Composable
private fun settingsState(model: AppViewModel): Pair<AppSettings, ((AppSettings) -> AppSettings) -> Unit> {
    val settings by model.manager.settings.collectAsStateWithLifecycle()
    return settings to model.manager::updateSettings
}

@Composable
fun SettingsScreen(model: AppViewModel) {
    SettingsColumn {
        SectionHeader("Downloads")
        NavRow("General", "Formats, chapters, speed, cache") { model.push(Route.General) }
        NavRow("Per-source overrides") { model.push(Route.Overrides) }
        NavRow("Source options", "kpage, ntk, nweb, kobo") { model.push(Route.SourceOptions) }
        NavRow("npia", "Account, notices, tag search") { model.push(Route.Npia) }
        SectionHeader("Output")
        NavRow("Images", "Compression, cover, size target") { model.push(Route.Images) }
        NavRow("Text and PDF") { model.push(Route.Text) }
        SectionHeader("Sharing")
        NavRow("Upload", "Hosts and automatic upload") { model.push(Route.Upload) }
        SectionHeader("App")
        NavRow("Screen and background") { model.push(Route.Background) }
        NavRow("Import / export", "Desktop config.json and app settings") { model.push(Route.Transfer) }
    }
}

// General

@Composable
fun GeneralSettingsScreen(model: AppViewModel) {
    val (settings, update) = settingsState(model)
    val shared = settings.shared
    fun set(change: (com.shirochi.npia.core.SharedSettings) -> com.shirochi.npia.core.SharedSettings) = update { it.copy(shared = change(it.shared)) }
    model.manager.preferencesFlow.collectAsStateWithLifecycle().value
    SettingsColumn {
        SectionHeader("Output")
        FormatPicker(shared.formats) { f -> set { it.copy(formats = f) } }
        ChoiceRow("File names", shared.namingMode, NamingMode.entries, { if (it == NamingMode.TITLE) "Title" else "Book ID" }, { m -> set { it.copy(namingMode = m) } })
        SwitchRow("Append chapter range to name", shared.appendRange, { v -> set { it.copy(appendRange = v) } })
        SwitchRow("One TXT file per chapter", shared.separateTxt, { v -> set { it.copy(separateTxt = v) } })
        SwitchRow("Number chapter titles", shared.numberChapters, { v -> set { it.copy(numberChapters = v) } })

        SectionHeader("Chapters")
        OptionalIntRow("From chapter", shared.rangeFrom, 1) { v -> set { it.copy(rangeFrom = v) } }
        OptionalIntRow("To chapter", shared.rangeTo, shared.rangeFrom ?: 1) { v -> set { it.copy(rangeTo = v) } }
        SwitchRow("Skip paid chapters", shared.skipPaid, { v -> set { it.copy(skipPaid = v) } })
        SwitchRow("Write output when stopped", shared.generateOnStop, { v -> set { it.copy(generateOnStop = v) } })

        SectionHeader("Speed")
        StepperRow("Chapter threads", shared.threads, 1..16, { v -> set { it.copy(threads = v) } })
        StepperRow("Image downloads", shared.imageWorkers, 1..16, { v -> set { it.copy(imageWorkers = v) } })
        DecimalField("Delay min (s)", shared.intervalMin, { v -> set { it.copy(intervalMin = v) } })
        DecimalField("Delay max (s)", shared.intervalMax, { v -> set { it.copy(intervalMax = v) } })
        StepperRow("Retries", shared.maxRetries, 0..30, { v -> set { it.copy(maxRetries = v) } })
        StepperRow("Downloads at once", model.manager.preferences.maxConcurrentJobs, 1..3, { model.manager.setMaxConcurrentJobs(it) })

        SectionHeader("Cache")
        SwitchRow("Cache chapters", shared.useCache, { v -> set { it.copy(useCache = v) } })
        SwitchRow("Cache images", shared.cacheImages, { v -> set { it.copy(cacheImages = v) } })
    }
}

// Per-source overrides

@Composable
fun OverridesListScreen(model: AppViewModel) {
    val (settings, _) = settingsState(model)
    SettingsColumn {
        SourceId.entries.forEach { source ->
            val count = settings.overrides[source.raw]?.count ?: 0
            NavRow(source.raw, if (count > 0) "$count changed" else null) { model.push(Route.OverrideForm(source)) }
        }
    }
}

@Composable
fun OverrideFormScreen(model: AppViewModel, source: SourceId) {
    val (settings, update) = settingsState(model)
    val shared = settings.shared
    val overrides = settings.overrides[source.raw] ?: SharedOverrides()
    fun set(change: (SharedOverrides) -> SharedOverrides) = update { current ->
        val next = change(current.overrides[source.raw] ?: SharedOverrides())
        current.copy(overrides = current.overrides.toMutableMap().apply { if (next.isEmpty) remove(source.raw) else put(source.raw, next) })
    }
    val onOff = { v: Boolean -> if (v) "on" else "off" }
    SettingsColumn {
        OverrideRow("Formats", overrides.formats, shared.formats, { f -> f.joinToString { it.raw.uppercase() } }, { v -> set { it.copy(formats = v) } }) { value, change ->
            FormatPicker(value, change)
        }
        OverrideRow("Chapter threads", overrides.threads, shared.threads, { "$it" }, { v -> set { it.copy(threads = v) } }) { value, change ->
            StepperRow("Threads", value, 1..16, change)
        }
        OverrideRow("Delay min", overrides.intervalMin, shared.intervalMin, { "${formatNumber(it)} s" }, { v -> set { it.copy(intervalMin = v) } }) { value, change ->
            DecimalField("Seconds", value, change)
        }
        OverrideRow("Delay max", overrides.intervalMax, shared.intervalMax, { "${formatNumber(it)} s" }, { v -> set { it.copy(intervalMax = v) } }) { value, change ->
            DecimalField("Seconds", value, change)
        }
        OverrideRow("From chapter", overrides.rangeFrom, shared.rangeFrom ?: 1, { "$it" }, { v -> set { it.copy(rangeFrom = v) } }) { value, change ->
            IntField("Chapter", value, change)
        }
        OverrideRow("To chapter", overrides.rangeTo, shared.rangeTo ?: 1, { "$it" }, { v -> set { it.copy(rangeTo = v) } }) { value, change ->
            IntField("Chapter", value, change)
        }
        OverrideRow("Cache chapters", overrides.useCache, shared.useCache, onOff, { v -> set { it.copy(useCache = v) } }) { value, change ->
            SwitchRow("Enabled", value, change)
        }
        OverrideRow("Cache images", overrides.cacheImages, shared.cacheImages, onOff, { v -> set { it.copy(cacheImages = v) } }) { value, change ->
            SwitchRow("Enabled", value, change)
        }
        OverrideRow("Number chapter titles", overrides.numberChapters, shared.numberChapters, onOff, { v -> set { it.copy(numberChapters = v) } }) { value, change ->
            SwitchRow("Enabled", value, change)
        }
        OverrideRow("One TXT per chapter", overrides.separateTxt, shared.separateTxt, onOff, { v -> set { it.copy(separateTxt = v) } }) { value, change ->
            SwitchRow("Enabled", value, change)
        }
        Footnote("Switched-off rows follow General settings.")
        if (settings.overrides[source.raw] != null) {
            TextButton(onClick = { set { SharedOverrides() } }, modifier = Modifier.padding(start = 8.dp)) {
                Text("Reset ${source.raw} to shared", color = MaterialTheme.colorScheme.error)
            }
        }
    }
}

// Source options

@Composable
fun SourceOptionsScreen(model: AppViewModel) {
    val (settings, update) = settingsState(model)
    SettingsColumn {
        for (source in listOf(SourceId.KPAGE, SourceId.NTK, SourceId.NWEB, SourceId.KOBO)) {
            SourceOptions(source, settings) { next -> update { it.copy(sources = next.sources, images = next.images, npia = next.npia) } }
        }
    }
}

// npia

@Composable
fun NpiaSettingsScreen(model: AppViewModel) {
    val (settings, update) = settingsState(model)
    val secrets = model.manager.secrets
    var email by remember { mutableStateOf(secrets.get(SecretKey.NPIA_EMAIL).orEmpty()) }
    var password by remember { mutableStateOf("") }
    var hasPassword by remember { mutableStateOf(secrets.get(SecretKey.NPIA_PASSWORD) != null) }
    var hasLoginKey by remember { mutableStateOf(secrets.get(SecretKey.NPIA_LOGIN_KEY) != null) }
    fun reload() {
        email = secrets.get(SecretKey.NPIA_EMAIL).orEmpty()
        hasPassword = secrets.get(SecretKey.NPIA_PASSWORD) != null
        hasLoginKey = secrets.get(SecretKey.NPIA_LOGIN_KEY) != null
    }
    val npia = settings.npia
    fun set(change: (com.shirochi.npia.core.NpiaSettings) -> com.shirochi.npia.core.NpiaSettings) = update { it.copy(npia = change(it.npia)) }

    SettingsColumn {
        SectionHeader("Downloads")
        SwitchRow("Include notices", npia.includeNotices, { v -> set { it.copy(includeNotices = v) } })
        SwitchRow("Browser fallback", npia.useBrowserFallback, { v -> set { it.copy(useBrowserFallback = v) } })
        SwitchRow("Sign in automatically", npia.autoLogin, { v -> set { it.copy(autoLogin = v) } })

        SectionHeader("Account")
        OutlinedTextField(
            value = email, onValueChange = { email = it }, label = { Text("Email") }, singleLine = true,
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )
        OutlinedTextField(
            value = password, onValueChange = { password = it }, label = { Text(if (hasPassword) "Password (saved)" else "Password") },
            singleLine = true, visualTransformation = PasswordVisualTransformation(),
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Password),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )
        Row(Modifier.padding(start = 8.dp)) {
            TextButton(onClick = {
                secrets.set(SecretKey.NPIA_EMAIL, email.trim())
                if (password.isNotEmpty()) secrets.set(SecretKey.NPIA_PASSWORD, password)
                password = ""
                reload()
                model.manager.message("Account saved")
            }) { Text("Save") }
            if (hasLoginKey) {
                TextButton(onClick = {
                    secrets.set(SecretKey.NPIA_LOGIN_KEY, null)
                    reload()
                }) { Text("Forget session key") }
            }
            if (hasPassword || email.isNotEmpty()) {
                TextButton(onClick = {
                    listOf(SecretKey.NPIA_EMAIL, SecretKey.NPIA_PASSWORD, SecretKey.NPIA_LOGIN_KEY).forEach { secrets.set(it, null) }
                    reload()
                }) { Text("Remove account", color = MaterialTheme.colorScheme.error) }
            }
        }
        Footnote("Stored only in encrypted app storage, never in settings files or exports.")

        SectionHeader("Tag search")
        ChoiceRow("Age filter", npia.tagAgeFilter, listOf("", "15", "19"), { when (it) { "" -> "All"; "15" -> "Non-adult"; else -> "Adult" } }, { v -> set { it.copy(tagAgeFilter = v) } })
        OutlinedTextField(
            value = npia.tagCustomTags, onValueChange = { v -> set { it.copy(tagCustomTags = v) } }, label = { Text("Custom tags") },
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
        )
        SwitchRow("Scrape all tags", npia.tagScrapeAll, { v -> set { it.copy(tagScrapeAll = v) } })
        StepperRow("Max queries", npia.scrapeMaxQueries, 1..1000, { v -> set { it.copy(scrapeMaxQueries = v) } }, step = 5)
    }
}

// Images

@Composable
fun ImageSettingsScreen(model: AppViewModel) {
    val (settings, update) = settingsState(model)
    val images = settings.images
    fun set(change: (com.shirochi.npia.core.ImageSettings) -> com.shirochi.npia.core.ImageSettings) = update { it.copy(images = change(it.images)) }
    val chapterEnabled = images.compress && !images.saveImageUrlsOnly
    SettingsColumn {
        SectionHeader("Chapter images")
        SwitchRow("Save image URLs only", images.saveImageUrlsOnly, { v -> set { it.copy(saveImageUrlsOnly = v) } })
        SwitchRow("Compress images", images.compress, { v -> set { it.copy(compress = v) } })
        ChoiceRow("Format", images.format, ImageFormat.entries, { it.label }, { v -> set { it.copy(format = v) } }, enabled = chapterEnabled)
        StepperRow("Quality", images.quality, 10..100, { v -> set { it.copy(quality = v) } }, step = 5, enabled = chapterEnabled)
        SwitchRow("Convert GIFs", images.convertGifs, { v -> set { it.copy(convertGifs = v) } }, enabled = chapterEnabled)
        SwitchRow("Static images only", images.staticOnly, { v -> set { it.copy(staticOnly = v) } }, enabled = chapterEnabled)
        StepperRow("Compression workers", images.compressionWorkers, 1..8, { v -> set { it.copy(compressionWorkers = v) } }, enabled = chapterEnabled)
        Footnote("Animated images keep their animation unless Static images only is on, since Android cannot encode animated WebP or PNG.")

        SectionHeader("Cover")
        SwitchRow("Compress cover", images.compressCover, { v -> set { it.copy(compressCover = v) } })
        ChoiceRow("Cover format", images.coverFormat, ImageFormat.entries, { it.label }, { v -> set { it.copy(coverFormat = v) } }, enabled = images.compressCover)
        StepperRow("Cover quality", images.coverQuality, 10..100, { v -> set { it.copy(coverQuality = v) } }, step = 5, enabled = images.compressCover)

        SectionHeader("Book")
        OptionalDecimalRow("Shrink EPUB/PDF to target size (MB)", images.recompressTargetMb, 20.0) { v -> set { it.copy(recompressTargetMb = v) } }
        SwitchRow("Long image layout", images.longImageLayout, { v -> set { it.copy(longImageLayout = v) } })
        SwitchRow("Compress images inside EPUB zip", images.zipCompressImages, { v -> set { it.copy(zipCompressImages = v) } })
        Footnote("The size target re-encodes JPEG and WebP images at lower quality, up to four passes. Long image layout shows webtoon strips edge to edge without chapter titles.")
    }
}

// Text and PDF

@Composable
fun TextSettingsScreen(model: AppViewModel) {
    val (settings, update) = settingsState(model)
    val text = settings.text
    fun set(change: (com.shirochi.npia.core.TextSettings) -> com.shirochi.npia.core.TextSettings) = update { it.copy(text = change(it.text)) }
    SettingsColumn {
        SectionHeader("Text")
        SwitchRow("Strip leading spaces", text.stripLeadingSpaces, { v -> set { it.copy(stripLeadingSpaces = v) } })
        SwitchRow("Remove blank lines", text.removeNewlines, { v -> set { it.copy(removeNewlines = v) } })
        SwitchRow("Blank line between paragraphs", text.blankParagraphLines, { v -> set { it.copy(blankParagraphLines = v) } })
        SwitchRow("Also save HTML", text.saveHtml, { v -> set { it.copy(saveHtml = v) } })
        SectionHeader("PDF")
        SwitchRow("Table of contents", text.pdfToc, { v -> set { it.copy(pdfToc = v) } })
        SwitchRow("TOC page numbers", text.pdfCounterLayout, { v -> set { it.copy(pdfCounterLayout = v) } }, enabled = text.pdfToc)
        SwitchRow("Page numbers", text.pdfPageNumbers, { v -> set { it.copy(pdfPageNumbers = v) } })
    }
}

// Upload

@Composable
fun UploadSettingsScreen(model: AppViewModel) {
    val (settings, update) = settingsState(model)
    val upload = settings.upload
    fun set(change: (com.shirochi.npia.core.UploadSettings) -> com.shirochi.npia.core.UploadSettings) = update { it.copy(upload = change(it.upload)) }
    var adding by remember { mutableStateOf(false) }
    SettingsColumn {
        SwitchRow("Upload finished files", upload.autoUpload, { v -> set { it.copy(autoUpload = v) } })
        SwitchRow("Copy link to clipboard", upload.copyLinkToClipboard, { v -> set { it.copy(copyLinkToClipboard = v) } })

        SectionHeader("Hosts, in order")
        upload.hosts.forEachIndexed { index, id ->
            Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp), verticalAlignment = Alignment.CenterVertically) {
                val uploader = UploaderCatalog.uploader(id)
                Column(Modifier.weight(1f)) {
                    Text(uploader?.displayName ?: id)
                    uploader?.let { Text(hostDetails(it), style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant) }
                }
                TextButton(onClick = { set { it.copy(hosts = it.hosts.move(index, index - 1)) } }, enabled = index > 0) { Text("\u2191") }
                TextButton(onClick = { set { it.copy(hosts = it.hosts.move(index, index + 1)) } }, enabled = index < upload.hosts.lastIndex) { Text("\u2193") }
                TextButton(onClick = { set { it.copy(hosts = it.hosts - id) } }, enabled = upload.hosts.size > 1) { Text("Remove") }
            }
        }
        val available = UploaderCatalog.all.filter { it.id !in upload.hosts }
        if (available.isNotEmpty()) {
            Box(Modifier.padding(start = 8.dp)) {
                TextButton(onClick = { adding = true }) { Text("Add host") }
                DropdownMenu(expanded = adding, onDismissRequest = { adding = false }) {
                    available.forEach { uploader ->
                        DropdownMenuItem(text = { Text(uploader.displayName) }, onClick = {
                            adding = false
                            set { it.copy(hosts = it.hosts + uploader.id) }
                        })
                    }
                }
            }
        }
        Footnote("If a host refuses the file (too large, offline) the next one is tried.")

        if ("gdrive" in upload.hosts) {
            SectionHeader("Google Drive")
            OutlinedTextField(
                value = upload.googleDriveFolderId.orEmpty(),
                onValueChange = { v -> set { it.copy(googleDriveFolderId = v.trim().ifEmpty { null }) } },
                label = { Text("Folder ID (optional)") },
                singleLine = true,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
            )
            Footnote("Drive needs an account; sign-in is handled by the uploader.")
        }
    }
}

private fun <T> List<T>.move(from: Int, to: Int): List<T> {
    if (to !in indices || from !in indices) return this
    return toMutableList().apply { add(to, removeAt(from)) }
}

private fun hostDetails(uploader: Uploader): String = buildList {
    add(uploader.maxBytes?.let { "up to ${formatBytes(it)}" } ?: "no size limit listed")
    uploader.retentionSeconds?.let { seconds ->
        add(if (seconds >= 86_400) "kept ${seconds / 86_400} days" else "kept ${maxOf(1, seconds / 3_600)} hours")
    }
    if (uploader.requiresAccount) add("account")
}.joinToString(" \u00b7 ")

private fun formatBytes(bytes: Long): String = when {
    bytes >= 1_000_000_000 -> "${formatNumber(bytes / 100_000_000 / 10.0)} GB"
    bytes >= 1_000_000 -> "${bytes / 1_000_000} MB"
    else -> "${bytes / 1_000} KB"
}

// Screen and background

@Composable
fun BackgroundSettingsScreen(model: AppViewModel) {
    val manager = model.manager
    manager.preferencesFlow.collectAsStateWithLifecycle().value
    SettingsColumn {
        SwitchRow("Keep screen awake while downloading", manager.preferences.keepScreenAwake, { manager.setKeepScreenAwake(it) })
        Footnote("Applies while the app is open.")
        Footnote(
            "Downloads keep running in a foreground service with a progress notification after you leave the app or lock the screen. " +
                "Some phones still stop background work to save battery; if downloads stop, allow Npia Mobile to run unrestricted in the system battery settings.",
        )
    }
}

// Import / export

@Composable
fun ConfigTransferScreen(model: AppViewModel) {
    val manager = model.manager
    val context = LocalContext.current
    var confirmReset by remember { mutableStateOf(false) }
    var pendingExport by remember { mutableStateOf<String?>(null) }

    val importer = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        val message = runCatching {
            val text = context.contentResolver.openInputStream(uri)?.use { it.readBytes().decodeToString() } ?: error("Could not read the file")
            manager.importConfig(text)
        }.getOrElse { "Import failed: ${it.message}" }
        manager.message(message)
    }
    val exporter = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/json")) { uri ->
        val text = pendingExport
        pendingExport = null
        if (uri == null || text == null) return@rememberLauncherForActivityResult
        val ok = runCatching { context.contentResolver.openOutputStream(uri, "wt")?.use { it.write(text.toByteArray()) } != null }.getOrDefault(false)
        manager.message(if (ok) "Exported." else "Export failed.")
    }
    fun export(name: String, make: () -> String) {
        runCatching(make).onSuccess {
            pendingExport = it
            exporter.launch(name)
        }.onFailure { manager.message("Export failed: ${it.message}") }
    }

    SettingsColumn {
        SectionHeader("Desktop downloader")
        NavRow("Import desktop config.json or app settings") { importer.launch(arrayOf("application/json", "text/plain", "application/octet-stream", "*/*")) }
        NavRow("Export as desktop config.json") { export("config.json") { manager.exportDesktopConfig() } }
        Footnote("Import merges download, image, text, and source settings. Credentials in the file go to encrypted storage and are never exported.")
        SectionHeader("App settings")
        NavRow("Export app settings") { export("npia-mobile-settings.json") { manager.exportAppSettings() } }
        TextButton(onClick = { confirmReset = true }, modifier = Modifier.padding(start = 8.dp)) {
            Text("Reset all settings", color = MaterialTheme.colorScheme.error)
        }
    }

    if (confirmReset) {
        AlertDialog(
            onDismissRequest = { confirmReset = false },
            title = { Text("Reset all settings to defaults?") },
            confirmButton = {
                TextButton(onClick = {
                    confirmReset = false
                    manager.updateSettings { AppSettings() }
                }) { Text("Reset") }
            },
            dismissButton = { TextButton(onClick = { confirmReset = false }) { Text("Cancel") } },
        )
    }
}
