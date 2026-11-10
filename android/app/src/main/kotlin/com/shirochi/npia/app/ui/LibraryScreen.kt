package com.shirochi.npia.app.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.ExperimentalLayoutApi
import androidx.compose.foundation.layout.FlowRow
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.aspectRatio
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.grid.GridCells
import androidx.compose.foundation.lazy.grid.LazyVerticalGrid
import androidx.compose.foundation.lazy.grid.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.CheckCircle
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.FilterChip
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.produceState
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.viewinterop.AndroidView
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.FileActions
import com.shirochi.npia.app.GENERIC_CATALOG_URL
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.zip.ZipFile

/** A book in the Library: every output sharing a name ("Title.epub", "Title.txt", a "Title/" TXT folder). */
data class LibraryBook(val name: String, val files: List<File>) {
    val modified: Long get() = files.maxOfOrNull { f -> if (f.isDirectory) f.walkTopDown().maxOf { it.lastModified() } else f.lastModified() } ?: 0L
    val epub: File? get() = files.firstOrNull { it.extension.equals("epub", ignoreCase = true) }
    val readerFile: File? get() = FileActions.readerFile(files)
}

private val BOOK_EXTENSIONS = setOf("epub", "pdf", "txt", "html", "htm", "cbz", "zip")

/** The books in the app's download folder, newest first. */
fun scanLibrary(folder: File): List<LibraryBook> {
    val entries = folder.listFiles().orEmpty().filter { file ->
        !file.name.startsWith(".") && when {
            file.isDirectory -> file.listFiles().orEmpty().any {
                file.extension.equals("folder", ignoreCase = true) ||
                    (it.isFile && it.extension.lowercase() in setOf("txt", "html", "jpg", "png", "webp"))
            }
            else -> file.extension.lowercase() in BOOK_EXTENSIONS
        }
    }
    fun title(file: File) = if (file.isDirectory && !file.extension.equals("folder", ignoreCase = true)) file.name else file.nameWithoutExtension
    return entries.groupBy { title(it).lowercase() }
        .map { (_, files) -> LibraryBook(title(files.first()), files) }
        .sortedByDescending { it.modified }
}

/** An EPUB's cover, decoded small, or null. */
fun epubCover(epub: File, maxSide: Int = 240): Bitmap? = runCatching {
    ZipFile(epub).use { zip ->
        val container = zip.getEntry("META-INF/container.xml")?.let { zip.getInputStream(it).reader().readText() } ?: return null
        val opfPath = Regex("full-path=\"([^\"]+)\"").find(container)?.groupValues?.get(1) ?: return null
        val opf = zip.getInputStream(zip.getEntry(opfPath) ?: return null).reader().readText()
        val base = opfPath.substringBeforeLast('/', "")
        val coverId = Regex("<meta[^>]+name=\"cover\"[^>]+content=\"([^\"]+)\"").find(opf)?.groupValues?.get(1)
        val items = Regex("<item\\b[^>]*>").findAll(opf).map { it.value }.toList()
        fun attr(tag: String, name: String) = Regex("\\b$name=\"([^\"]+)\"").find(tag)?.groupValues?.get(1)
        val item = items.firstOrNull { coverId != null && attr(it, "id") == coverId }
            ?: items.firstOrNull { attr(it, "properties")?.contains("cover-image") == true }
            ?: items.firstOrNull { attr(it, "media-type")?.startsWith("image/") == true && attr(it, "href")?.contains("cover", true) == true }
            ?: return null
        val href = attr(item, "href") ?: return null
        val path = if (base.isEmpty()) href else "$base/$href"
        val bytes = zip.getInputStream(zip.getEntry(path) ?: return null).readBytes()
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, bounds)
        var sample = 1
        while (bounds.outWidth / (sample * 2) >= maxSide && bounds.outHeight / (sample * 2) >= maxSide) sample *= 2
        BitmapFactory.decodeByteArray(bytes, 0, bytes.size, BitmapFactory.Options().apply { inSampleSize = sample })
    }
}.getOrNull()

/** The book as its EPUB names it: its title, author and the address it was downloaded from (`dc:source`). */
fun epubSource(epub: File): com.shirochi.npia.app.CatalogBook? = runCatching {
    ZipFile(epub).use { zip ->
        val container = zip.getEntry("META-INF/container.xml")?.let { zip.getInputStream(it).reader().readText() } ?: return null
        val opfPath = Regex("full-path=\"([^\"]+)\"").find(container)?.groupValues?.get(1) ?: return null
        val opf = zip.getInputStream(zip.getEntry(opfPath) ?: return null).reader().readText()
        fun element(name: String) = Regex("<dc:$name\\b[^>]*>([^<]*)</dc:$name>").find(opf)?.groupValues?.get(1)?.let(::unescapeXml)?.trim()
        com.shirochi.npia.app.CatalogBook(
            title = element("title").orEmpty().ifEmpty { epub.nameWithoutExtension },
            url = element("source")?.takeIf { it.startsWith("http://") || it.startsWith("https://") },
            author = element("creator").orEmpty(),
        )
    }
}.getOrNull()

private fun unescapeXml(text: String) = text.replace("&lt;", "<").replace("&gt;", ">").replace("&quot;", "\"").replace("&#39;", "'").replace("&amp;", "&")

/** "EPUB · TXT · 5.3 MB": a book's formats and size, for its shelf tile. */
private fun formats(book: LibraryBook): String {
    val kinds = book.files.map(::fileKind).distinct()
    val bytes = book.files.sumOf { f -> if (f.isDirectory) f.walkTopDown().filter { it.isFile }.sumOf { it.length() } else f.length() }
    val size = if (bytes >= 1_048_576) String.format(java.util.Locale.US, "%.1f MB", bytes / 1_048_576.0) else "${maxOf(1, bytes / 1024)} KB"
    return (kinds + size).joinToString(" · ")
}

/**
 * The Library tab: the books downloaded on this device, as a shelf of covers. A tap reads an EPUB in
 * the Library's own reader (the built-in Catalog page's reader, opened here: it does not depend on the
 * Catalog tab or its site); ⋮ shows the book's files with the same actions as a finished download, and
 * Delete. A long press selects books (taps then pick more), for their files to go to another app at once, or to be deleted.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class, ExperimentalLayoutApi::class)
@Composable
fun LibraryScreen(model: AppViewModel) {
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    val libraryUpdates by manager.libraryUpdates.collectAsStateWithLifecycle()
    var librarySettings by remember { mutableStateOf(false) }
    var refresh by remember { mutableIntStateOf(0) }
    var query by remember { mutableStateOf("") }
    var open by remember { mutableStateOf<LibraryBook?>(null) }
    var deleting by remember { mutableStateOf<LibraryBook?>(null) }
    var reading by remember { mutableStateOf<File?>(null) }
    // The books picked by a long press (their keys), and the file kinds left out of what they share.
    var selected by remember { mutableStateOf(emptySet<String>()) }
    var kindsOff by remember { mutableStateOf(emptySet<String>()) }
    // The picked books' files (of the kinds left on) waiting for the delete to be confirmed.
    var deletingPicked by remember { mutableStateOf<List<File>?>(null) }
    val context = LocalContext.current
    // Read again whenever a download finishes (the jobs change) or a book is deleted.
    val books by produceState(emptyList<LibraryBook>(), jobs.count { !it.state.isActive }, refresh) {
        value = withContext(Dispatchers.IO) { scanLibrary(manager.outputDirectory) }
    }
    val shown = books.filter { query.isBlank() || it.name.contains(query.trim(), ignoreCase = true) }
    LaunchedEffect(books) { withContext(Dispatchers.IO) { manager.discoverLibrary(books.mapNotNull { it.epub }) } }
    val selecting = selected.isNotEmpty()
    fun toggle(book: LibraryBook) {
        val key = book.name.lowercase()
        selected = if (key in selected) selected - key else selected + key
    }
    BackHandler(enabled = selecting) { selected = emptySet() }
    Column(Modifier.fillMaxSize()) {
        // A slim search field and the count, not a full-size form field.
        Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
            Surface(shape = RoundedCornerShape(20.dp), color = MaterialTheme.colorScheme.surfaceContainerHigh, modifier = Modifier.weight(1f).height(40.dp)) {
                Box(Modifier.padding(horizontal = 16.dp), contentAlignment = Alignment.CenterStart) {
                    if (query.isEmpty()) Text("Find a book", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    BasicTextField(
                        value = query, onValueChange = { query = it }, singleLine = true,
                        textStyle = MaterialTheme.typography.bodyMedium.copy(color = MaterialTheme.colorScheme.onSurface),
                        cursorBrush = SolidColor(MaterialTheme.colorScheme.primary),
                        modifier = Modifier.fillMaxWidth(),
                    )
                }
            }
            if (selecting) {
                val all = shown.all { it.name.lowercase() in selected }
                TextButton(onClick = { selected = if (all) emptySet() else shown.map { it.name.lowercase() }.toSet() }) {
                    Text(if (all) "None" else "All")
                }
                TextButton(onClick = { selected = emptySet() }) { Text("Cancel") }
            } else {
                Text(
                    "${shown.size} book${if (shown.size == 1) "" else "s"}",
                    style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                    modifier = Modifier.padding(start = 12.dp),
                )
            }
            TextButton(onClick = { librarySettings = true }) { Text("Settings") }
        }
        if (shown.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
                Text(
                    if (books.isEmpty()) "No books yet. Downloads are saved here and show up as soon as they finish." else "No book matches.",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        LazyVerticalGrid(
            columns = GridCells.Adaptive(minSize = 104.dp),
            contentPadding = PaddingValues(start = 12.dp, end = 12.dp, top = 4.dp, bottom = 96.dp),
            horizontalArrangement = Arrangement.spacedBy(10.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
            modifier = Modifier.fillMaxWidth().weight(1f),
        ) {
            items(shown, key = { it.name.lowercase() }) { book ->
                val picked = book.name.lowercase() in selected
                Column(
                    Modifier.clip(RoundedCornerShape(8.dp)).combinedClickable(
                        onClick = {
                            if (selecting) toggle(book) else {
                                book.epub?.let(manager::requestLibraryUpdate)
                                book.readerFile?.let { reading = it } ?: run { open = book }
                            }
                        },
                        onLongClick = { toggle(book) },
                    ),
                ) {
                    Box {
                        Cover(book)
                        book.epub?.let { epub ->
                            val updating = jobs.firstOrNull { it.state.isActive && it.libraryTarget == epub.name }
                            val badge = if (updating != null) {
                                if (updating.state == com.shirochi.npia.app.model.JobState.QUEUED) "Update queued" else "Updating…"
                            } else if (libraryUpdates.enabled && libraryUpdates.books[epub.name]?.enabled == true) "Auto-update" else null
                            if (badge != null) Surface(color = MaterialTheme.colorScheme.primary, shape = RoundedCornerShape(6.dp), modifier = Modifier.align(Alignment.BottomStart).padding(4.dp)) {
                                Text(badge, style = MaterialTheme.typography.labelSmall, modifier = Modifier.padding(4.dp))
                            }
                        }
                        // The book's files and actions, for a tap that would rather not read; while books are
                        // being picked, whether this one is.
                        Surface(
                            shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surface.copy(alpha = 0.8f),
                            modifier = Modifier.align(Alignment.TopEnd).padding(4.dp).size(28.dp).clip(RoundedCornerShape(12.dp))
                                .clickable { if (selecting) toggle(book) else open = book },
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                when {
                                    picked -> Icon(Icons.Filled.CheckCircle, contentDescription = "Selected", tint = MaterialTheme.colorScheme.primary, modifier = Modifier.size(22.dp))
                                    selecting -> Box(Modifier.size(19.dp).border(2.dp, MaterialTheme.colorScheme.onSurfaceVariant, CircleShape))  // not picked
                                    else -> Icon(Icons.Filled.MoreVert, contentDescription = "Files and actions", modifier = Modifier.size(18.dp))
                                }
                            }
                        }
                    }
                    Text(
                        book.name, style = MaterialTheme.typography.labelLarge, maxLines = 2, overflow = TextOverflow.Ellipsis,
                        modifier = Modifier.padding(top = 6.dp),
                    )
                    Text(formats(book), style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant, maxLines = 1)
                }
            }
        }
        if (selecting) {
            // The picked books' files, to open in another app together (the share sheet: a reader, cloud
            // storage, a chat), less the kinds switched off here.
            val pickedBooks = books.filter { it.name.lowercase() in selected }
            val kinds = pickedBooks.flatMap { it.files }.map(::fileKind).distinct().sorted()
            val files = pickedBooks.flatMap { it.files }.filter { fileKind(it) !in kindsOff }
            Surface(tonalElevation = 3.dp, modifier = Modifier.fillMaxWidth()) {
                Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp)) {
                    FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        TextButton(onClick = { manager.setLibraryAutoUpdate(pickedBooks.mapNotNull { it.epub }, true) }) { Text("Enable auto-update") }
                        TextButton(onClick = { manager.setLibraryAutoUpdate(pickedBooks.mapNotNull { it.epub }, false) }) { Text("Disable auto-update") }
                    }
                    if (kinds.size > 1) {
                        FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            kinds.forEach { kind ->
                                FilterChip(
                                    selected = kind !in kindsOff,
                                    onClick = { kindsOff = if (kind in kindsOff) kindsOff - kind else kindsOff + kind },
                                    label = { Text(kind) },
                                )
                            }
                        }
                    }
                    Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Text(
                            "${selected.size} book${if (selected.size == 1) "" else "s"} · ${files.size} file${if (files.size == 1) "" else "s"}",
                            Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium,
                        )
                        OutlinedButton(enabled = files.isNotEmpty(), onClick = { deletingPicked = files }) { Text("Delete") }
                        Button(
                            enabled = files.isNotEmpty(),
                            onClick = { if (FileActions.share(context, files)) selected = emptySet() else manager.message("Nothing left to share.") },
                        ) { Text("Open in app") }
                    }
                }
            }
        }
    }
    reading?.let { file -> LibraryReader(file, model) { reading = null } }
    if (librarySettings) AlertDialog(
        onDismissRequest = { librarySettings = false },
        title = { Text("Library settings") },
        text = {
            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Allow Library auto-updates", Modifier.weight(1f))
                    Switch(libraryUpdates.enabled, { enabled -> manager.configureLibrary { it.copy(enabled = enabled) } })
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Background auto-update", Modifier.weight(1f))
                    Switch(libraryUpdates.background, { background -> manager.configureLibrary { it.copy(background = background) } }, enabled = libraryUpdates.enabled)
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text("Upload refreshed EPUBs to Google Drive", Modifier.weight(1f))
                    Switch(libraryUpdates.uploadDrive, { upload ->
                        if (upload) manager.preferences.shareAgreementAccepted = true
                        manager.configureLibrary { it.copy(uploadDrive = upload) }
                    }, enabled = libraryUpdates.enabled)
                }
                Text("Enabled books refresh the full novel. Background refresh runs every 15 minutes while the app is active. Ranged downloads and books without range history start off. Google Drive uploads are shared with anyone who has the link.")
            }
        },
        confirmButton = { TextButton(onClick = { librarySettings = false }) { Text("Done") } },
    )
    open?.let { book ->
        ModalBottomSheet(onDismissRequest = { open = null }) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                book.epub?.let { epub ->
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Text("Auto-update this book", Modifier.weight(1f))
                        val entry = libraryUpdates.books[epub.name]
                        Switch(entry?.enabled == true, { manager.setLibraryAutoUpdate(listOf(epub), it) }, enabled = !entry?.url.isNullOrBlank())
                    }
                }
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Box(Modifier.width(64.dp)) { Cover(book) }
                    Column(Modifier.weight(1f).padding(start = 12.dp)) {
                        Text(book.name, style = MaterialTheme.typography.titleMedium, maxLines = 3, overflow = TextOverflow.Ellipsis)
                        Text(
                            java.text.DateFormat.getDateInstance().format(java.util.Date(book.modified)) + " · " + formats(book),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                }
                // On a narrow phone the buttons take two rows.
                FlowRow(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    book.readerFile?.let { file ->
                        Button(onClick = { book.epub?.let(manager::requestLibraryUpdate); open = null; reading = file }) { Text("Open") }
                    }
                    // The book in the Catalog: its site's page (where it came from) and its title searched, to
                    // download what an unfinished or outdated copy is missing.
                    OutlinedButton(onClick = {
                        open = null
                        val found = book.epub?.let(::epubSource) ?: com.shirochi.npia.app.CatalogBook(book.name, null)
                        model.openInCatalog(found)
                    }) { Text("Open in Catalog") }
                    OutlinedButton(onClick = { open = null; deleting = book }) { Text("Delete") }
                }
                book.files.forEach { file -> FileRow(model, file, manager.jobOwning(file)?.id) }
            }
        }
    }
    deletingPicked?.let { files ->
        val count = books.count { book -> book.files.any { it in files } }
        AlertDialog(
            onDismissRequest = { deletingPicked = null },
            title = { Text("Delete ${files.size} file${if (files.size == 1) "" else "s"}") },
            // The kinds switched off stay: picking EPUB only deletes the EPUBs and keeps the TXT copies.
            text = { Text("Delete the ${files.map(::fileKind).distinct().sorted().joinToString(" and ")} of $count book${if (count == 1) "" else "s"}? This cannot be undone.") },
            confirmButton = {
                TextButton(onClick = {
                    files.forEach { it.deleteRecursively() }
                    deletingPicked = null
                    selected = emptySet()
                    refresh++
                }) { Text("Delete") }
            },
            dismissButton = { TextButton(onClick = { deletingPicked = null }) { Text("Cancel") } },
        )
    }
    deleting?.let { book ->
        AlertDialog(
            onDismissRequest = { deleting = null },
            title = { Text("Delete book") },
            text = { Text("Delete ${book.name}? " + book.files.joinToString(", ") { FileActions.displayName(it) }) },
            confirmButton = {
                TextButton(onClick = {
                    book.files.forEach { it.deleteRecursively() }
                    deleting = null
                    refresh++
                }) { Text("Delete") }
            },
            dismissButton = { TextButton(onClick = { deleting = null }) { Text("Cancel") } },
        )
    }
}

/** "EPUB", "TXT" (a TXT folder too), "PDF": a file's kind, for the selection's filter. */
private fun fileKind(file: File): String = when {
    file.isDirectory && file.extension.equals("folder", ignoreCase = true) -> "Folder"
    file.isDirectory -> "TXT"
    else -> file.extension.uppercase()
}

/** A book's cover on the shelf: its EPUB's own, else its initial on a tinted tile. Fills its width at 2:3. */
@Composable
private fun Cover(book: LibraryBook) {
    val cover by produceState<Bitmap?>(null, book.epub?.absolutePath, book.modified) {
        value = book.epub?.let { withContext(Dispatchers.IO) { epubCover(it, maxSide = 360) } }
    }
    Surface(
        shape = RoundedCornerShape(8.dp), color = MaterialTheme.colorScheme.surfaceContainerHighest, shadowElevation = 2.dp,
        modifier = Modifier.fillMaxWidth().aspectRatio(2f / 3f),
    ) {
        val bitmap = cover
        if (bitmap != null) {
            Image(bitmap.asImageBitmap(), contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize().clip(RoundedCornerShape(8.dp)))
        } else {
            Box(contentAlignment = Alignment.Center) {
                Text(book.name.take(1).uppercase(), style = MaterialTheme.typography.headlineMedium, color = MaterialTheme.colorScheme.primary)
            }
        }
    }
}

/**
 * A Library book in the reader, full screen over the Library: a page of its own with the built-in
 * Catalog page, so the book opens whatever the Catalog tab shows. Closing the reader (or Back) closes it.
 */
@Composable
internal fun LibraryReader(file: File, model: AppViewModel, onClose: () -> Unit) {
    val context = LocalContext.current
    val web = remember(file) { CatalogWeb(context, model) }
    DisposableEffect(web) {
        web.onReaderClosed = onClose
        onDispose { web.onReaderClosed = null; web.destroy() }
    }
    val colors = MaterialTheme.colorScheme
    LaunchedEffect(colors) { web.setTheme(colors) }
    LaunchedEffect(web) {
        web.load(GENERIC_CATALOG_URL, force = true)
        web.readLocal(file)
    }
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false)) {
        BackHandler { if (web.readerFullScreen) web.leaveFullScreen() else onClose() }
        Surface(Modifier.fillMaxSize()) {
            AndroidView(factory = { web.view.fillingParent() }, modifier = Modifier.fillMaxSize())
        }
    }
}

