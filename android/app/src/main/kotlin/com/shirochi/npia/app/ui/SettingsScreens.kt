package com.shirochi.npia.app.ui

import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ColumnScope
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.text.KeyboardActions
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.FilterChip
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
import androidx.compose.ui.text.input.ImeAction
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.DEFAULT_CATALOG_URL
import com.shirochi.npia.app.validCatalogUrl
import com.shirochi.npia.app.Route
import com.shirochi.npia.app.model.count
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.NamingMode
import com.shirochi.npia.core.SecretKey
import com.shirochi.npia.core.SharedOverrides
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.sources.npia.NpiaFontMapping
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
        NavRow("General", "Format, chapter range, threads, rate limiting, cache") { model.push(Route.General) }
        NavRow("Per-source overrides") { model.push(Route.Overrides) }
        NavRow("Source options", "kpage, ntk, nweb, kobo") { model.push(Route.SourceOptions) }
        NavRow("npia", "Account, notices, tag search") { model.push(Route.Npia) }
        NavRow("Signed-in sites", "Logins saved by the Browser tab") { model.push(Route.Sites) }
        SectionHeader("Output")
        NavRow("Images", "Compression, cover, size target") { model.push(Route.Images) }
        NavRow("Text and PDF") { model.push(Route.Text) }
        SectionHeader("Sharing")
        NavRow("Upload", "Hosts and automatic upload") { model.push(Route.Upload) }
        SectionHeader("App")
        NavRow("Appearance", "Theme and accent colour") { model.push(Route.Appearance) }
        NavRow("Catalog site", "The website shown in the Catalog tab") { model.push(Route.CatalogSite) }
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
        ChoiceRow("File Naming", shared.namingMode, NamingMode.entries, { if (it == NamingMode.TITLE) "Save as Title" else "Save as ID" }, { m -> set { it.copy(namingMode = m) } })
        SwitchRow("Append chapter range to title for ongoing novels", shared.appendRange, { v -> set { it.copy(appendRange = v) } })
        SwitchRow("Separate TXT files", shared.separateTxt, { v -> set { it.copy(separateTxt = v) } })
        SwitchRow("Number chapters", shared.numberChapters, { v -> set { it.copy(numberChapters = v) } })

        SectionHeader("Chapter Range")
        OptionalIntRow("From", shared.rangeFrom, 1) { v -> set { it.copy(rangeFrom = v) } }
        OptionalIntRow("To", shared.rangeTo, shared.rangeFrom ?: 1) { v -> set { it.copy(rangeTo = v) } }
        SwitchRow("Skip paid", shared.skipPaid, { v -> set { it.copy(skipPaid = v) } })
        SwitchRow("Generate on stop", shared.generateOnStop, { v -> set { it.copy(generateOnStop = v) } })

        SectionHeader("Threads and Rate Limiting")
        StepperRow("Threads", shared.threads, 1..16, { v -> set { it.copy(threads = v) } })
        StepperRow("Image Workers", shared.imageWorkers, 1..16, { v -> set { it.copy(imageWorkers = v) } })
        DecimalField("Rate Limiting Min (s)", shared.intervalMin, { v -> set { it.copy(intervalMin = v) } })
        DecimalField("Rate Limiting Max (s)", shared.intervalMax, { v -> set { it.copy(intervalMax = v) } })
        StepperRow("Retries", shared.maxRetries, 0..30, { v -> set { it.copy(maxRetries = v) } })
        StepperRow("Downloads at once", model.manager.preferences.maxConcurrentJobs, 1..3, { model.manager.setMaxConcurrentJobs(it) })

        SectionHeader("Cache")
        SwitchRow("Use Cache", shared.useCache, { v -> set { it.copy(useCache = v) } })
        SwitchRow("Cache Images", shared.cacheImages, { v -> set { it.copy(cacheImages = v) } })
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
        OverrideRow("Threads", overrides.threads, shared.threads, { "$it" }, { v -> set { it.copy(threads = v) } }) { value, change ->
            StepperRow("Threads", value, 1..16, change)
        }
        OverrideRow("Rate Limiting Min", overrides.intervalMin, shared.intervalMin, { "${formatNumber(it)} s" }, { v -> set { it.copy(intervalMin = v) } }) { value, change ->
            DecimalField("Seconds", value, change)
        }
        OverrideRow("Rate Limiting Max", overrides.intervalMax, shared.intervalMax, { "${formatNumber(it)} s" }, { v -> set { it.copy(intervalMax = v) } }) { value, change ->
            DecimalField("Seconds", value, change)
        }
        OverrideRow("From", overrides.rangeFrom, shared.rangeFrom ?: 1, { "$it" }, { v -> set { it.copy(rangeFrom = v) } }) { value, change ->
            IntField("Chapter", value, change)
        }
        OverrideRow("To", overrides.rangeTo, shared.rangeTo ?: 1, { "$it" }, { v -> set { it.copy(rangeTo = v) } }) { value, change ->
            IntField("Chapter", value, change)
        }
        OverrideRow("Use Cache", overrides.useCache, shared.useCache, onOff, { v -> set { it.copy(useCache = v) } }) { value, change ->
            SwitchRow("Enabled", value, change)
        }
        OverrideRow("Cache Images", overrides.cacheImages, shared.cacheImages, onOff, { v -> set { it.copy(cacheImages = v) } }) { value, change ->
            SwitchRow("Enabled", value, change)
        }
        OverrideRow("Number chapters", overrides.numberChapters, shared.numberChapters, onOff, { v -> set { it.copy(numberChapters = v) } }) { value, change ->
            SwitchRow("Enabled", value, change)
        }
        OverrideRow("Separate TXT files", overrides.separateTxt, shared.separateTxt, onOff, { v -> set { it.copy(separateTxt = v) } }) { value, change ->
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

    val context = LocalContext.current
    var mapped by remember { mutableStateOf(NpiaFontMapping.mapping.size) }
    val mappingImporter = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        uri ?: return@rememberLauncherForActivityResult
        val message = runCatching {
            val text = context.contentResolver.openInputStream(uri)?.use { it.readBytes().decodeToString() } ?: error("Could not read the file")
            mapped = NpiaFontMapping.import(text)
            "Font mapping loaded: $mapped characters"
        }.getOrElse { "Font mapping failed: ${it.message}" }
        model.manager.message(message)
    }

    SettingsColumn {
        SectionHeader("Downloads")
        SwitchRow("Download Author Notices", npia.includeNotices, { v -> set { it.copy(includeNotices = v) } })
        SwitchRow("Browser fallback", npia.useBrowserFallback, { v -> set { it.copy(useBrowserFallback = v) } })
        SwitchRow("Auto-login on startup", npia.autoLogin, { v -> set { it.copy(autoLogin = v) } })

        SectionHeader("Font Mapping")
        NavRow("Open...", if (mapped > 0) "$mapped characters mapped" else "No mapping file") {
            mappingImporter.launch(arrayOf("application/json", "text/plain", "application/octet-stream", "*/*"))
        }
        if (mapped > 0) {
            TextButton(onClick = { NpiaFontMapping.clear(); mapped = 0 }, modifier = Modifier.padding(start = 8.dp)) {
                Text("Clear", color = MaterialTheme.colorScheme.error)
            }
        }
        Footnote("The desktop font mapping JSON; npia chapter text is decoded through it.")

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
        SwitchRow("Save Image URLs Only", images.saveImageUrlsOnly, { v -> set { it.copy(saveImageUrlsOnly = v) } })
        SwitchRow("Compress Images", images.compress, { v -> set { it.copy(compress = v) } })
        ChoiceRow("Format", images.format, ImageFormat.entries, { it.label }, { v -> set { it.copy(format = v) } }, enabled = chapterEnabled)
        StepperRow("Quality", images.quality, 10..100, { v -> set { it.copy(quality = v) } }, step = 5, enabled = chapterEnabled)
        SwitchRow("Convert GIFs", images.convertGifs, { v -> set { it.copy(convertGifs = v) } }, enabled = chapterEnabled)
        SwitchRow("Static images only", images.staticOnly, { v -> set { it.copy(staticOnly = v) } }, enabled = chapterEnabled)
        StepperRow("Image Compression Workers", images.compressionWorkers, 1..8, { v -> set { it.copy(compressionWorkers = v) } }, enabled = chapterEnabled)
        Footnote("Animated images keep their animation unless Static images only is on, since Android cannot encode animated WebP or PNG.")

        SectionHeader("Cover")
        SwitchRow("Compress Cover", images.compressCover, { v -> set { it.copy(compressCover = v) } })
        ChoiceRow("Cover format", images.coverFormat, ImageFormat.entries, { it.label }, { v -> set { it.copy(coverFormat = v) } }, enabled = images.compressCover)
        StepperRow("Cover quality", images.coverQuality, 10..100, { v -> set { it.copy(coverQuality = v) } }, step = 5, enabled = images.compressCover)

        SectionHeader("Book")
        OptionalDecimalRow("Recompress to meet target size (MB)", images.recompressTargetMb, 20.0) { v -> set { it.copy(recompressTargetMb = v) } }
        SwitchRow("Long image layout", images.longImageLayout, { v -> set { it.copy(longImageLayout = v) } })
        SwitchRow("ZIP Compress", images.zipCompressImages, { v -> set { it.copy(zipCompressImages = v) } })
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
        SwitchRow("Remove Leading Spaces", text.stripLeadingSpaces, { v -> set { it.copy(stripLeadingSpaces = v) } })
        SwitchRow("Remove Newlines / <br>", text.removeNewlines, { v -> set { it.copy(removeNewlines = v) } })
        SwitchRow("Blank line after each paragraph", text.blankParagraphLines, { v -> set { it.copy(blankParagraphLines = v) } })
        SwitchRow("Also save HTML", text.saveHtml, { v -> set { it.copy(saveHtml = v) } })
        SectionHeader("PDF")
        SwitchRow("Table of Contents", text.pdfToc, { v -> set { it.copy(pdfToc = v) } })
        SwitchRow("TOC Page Numbers", text.pdfCounterLayout, { v -> set { it.copy(pdfCounterLayout = v) } }, enabled = text.pdfToc)
        SwitchRow("Page Numbers", text.pdfPageNumbers, { v -> set { it.copy(pdfPageNumbers = v) } })
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

// Catalog site

@Composable
fun CatalogSiteSettingsScreen(model: AppViewModel) {
    val manager = model.manager
    manager.preferencesFlow.collectAsStateWithLifecycle().value
    val current = manager.preferences.catalogUrl
    var address by remember { mutableStateOf(current) }
    // "example.com" becomes "https://example.com"; incomplete addresses such as "https://" cannot be saved.
    val candidate = validCatalogUrl(com.shirochi.npia.webengine.VisibleBrowser.normalize(address))
    fun save() {
        val url = candidate ?: return
        manager.setCatalogUrl(url)
        address = manager.preferences.catalogUrl
    }
    SettingsColumn {
        OutlinedTextField(
            value = address,
            onValueChange = { address = it },
            label = { Text("Catalog site") },
            placeholder = { Text("https://example.com") },
            singleLine = true,
            isError = address.isNotBlank() && candidate == null,
            supportingText = if (address.isNotBlank() && candidate == null) {
                { Text("Enter a full site address, like https://example.com") }
            } else {
                null
            },
            keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Uri, imeAction = ImeAction.Done),
            keyboardActions = KeyboardActions(onDone = { save() }),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        )
        Row(Modifier.padding(horizontal = 8.dp)) {
            TextButton(onClick = ::save, enabled = candidate != null && candidate != current) { Text("Save") }
            TextButton(
                onClick = {
                    manager.setCatalogUrl(DEFAULT_CATALOG_URL)
                    address = DEFAULT_CATALOG_URL
                },
                enabled = current != DEFAULT_CATALOG_URL,
            ) { Text("Use NovelDB") }
        }
        Footnote(
            "On the NovelDB website, tapping a card opens View Details and the link-cart button on each card adds the book to the Add tab. " +
                "Other catalog sites have no cart buttons, so tapping a link that leaves the site adds that link to the cart instead of opening it.",
        )
    }
}

// Appearance

@Composable
fun AppearanceSettingsScreen(model: AppViewModel) {
    val manager = model.manager
    manager.preferencesFlow.collectAsStateWithLifecycle().value
    val theme = ThemeMode.of(manager.preferences.theme)
    val accent = Accent.of(manager.preferences.accent)
    SettingsColumn {
        SectionHeader("Theme")
        ChipFlow {
            ThemeMode.entries.forEach { mode -> ToggleChip(mode.label, mode == theme) { manager.setTheme(mode.raw) } }
        }
        Footnote("AMOLED is the dark theme with pure-black backgrounds.")
        SectionHeader("Accent")
        ChipFlow {
            Accent.available.forEach { option ->
                FilterChip(
                    selected = option == accent,
                    onClick = { manager.setAccent(option.raw) },
                    label = { Text(option.label, style = MaterialTheme.typography.labelMedium) },
                    leadingIcon = if (option == Accent.WALLPAPER) null else {
                        { Box(Modifier.size(14.dp).background(option.seed, CircleShape)) }
                    },
                    modifier = Modifier.height(32.dp),
                )
            }
        }
        Footnote("Halgakos Rose is the Glossarion theme. Wallpaper follows your phone's wallpaper colours.")
    }
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
                "Some phones still stop background work to save battery; if downloads stop, allow NovelDB to run unrestricted in the system battery settings.",
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
