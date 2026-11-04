package com.shirochi.npia.app.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.ExperimentalFoundationApi
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.combinedClickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
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
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.MoreVert
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ExperimentalMaterial3Api
import androidx.compose.material3.Icon
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.ModalBottomSheet
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
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
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import com.shirochi.npia.app.AppViewModel
import com.shirochi.npia.app.FileActions
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.zip.ZipFile

/** A book in the Library: every output sharing a name ("Title.epub", "Title.txt", a "Title/" TXT folder). */
data class LibraryBook(val name: String, val files: List<File>) {
    val modified: Long get() = files.maxOfOrNull { f -> if (f.isDirectory) f.walkTopDown().maxOf { it.lastModified() } else f.lastModified() } ?: 0L
    val epub: File? get() = files.firstOrNull { it.extension.equals("epub", ignoreCase = true) }
}

private val BOOK_EXTENSIONS = setOf("epub", "pdf", "txt", "html", "htm", "cbz", "zip")

/** The books in the app's download folder, newest first. */
fun scanLibrary(folder: File): List<LibraryBook> {
    val entries = folder.listFiles().orEmpty().filter { file ->
        !file.name.startsWith(".") && when {
            file.isDirectory -> file.listFiles().orEmpty().any { it.isFile && it.extension.lowercase() in setOf("txt", "html", "jpg", "png", "webp") }
            else -> file.extension.lowercase() in BOOK_EXTENSIONS
        }
    }
    return entries.groupBy { (if (it.isDirectory) it.name else it.nameWithoutExtension).lowercase() }
        .map { (_, files) -> LibraryBook(files.first().let { if (it.isDirectory) it.name else it.nameWithoutExtension }, files) }
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

/** "EPUB · TXT · 5.3 MB": a book's formats and size, for its shelf tile. */
private fun formats(book: LibraryBook): String {
    val kinds = book.files.map { if (it.isDirectory) "TXT" else it.extension.uppercase() }.distinct()
    val bytes = book.files.sumOf { f -> if (f.isDirectory) f.walkTopDown().filter { it.isFile }.sumOf { it.length() } else f.length() }
    val size = if (bytes >= 1_048_576) String.format(java.util.Locale.US, "%.1f MB", bytes / 1_048_576.0) else "${maxOf(1, bytes / 1024)} KB"
    return (kinds + size).joinToString(" · ")
}

/**
 * The Library tab: the books downloaded on this device, as a shelf of covers. A tap reads an EPUB in
 * the Catalog's reader; a long press (or ⋮) shows the book's files with the same actions as a
 * finished download, and Delete.
 */
@OptIn(ExperimentalMaterial3Api::class, ExperimentalFoundationApi::class)
@Composable
fun LibraryScreen(model: AppViewModel) {
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    var refresh by remember { mutableIntStateOf(0) }
    var query by remember { mutableStateOf("") }
    var open by remember { mutableStateOf<LibraryBook?>(null) }
    var deleting by remember { mutableStateOf<LibraryBook?>(null) }
    // Read again whenever a download finishes (the jobs change) or a book is deleted.
    val books by produceState(emptyList<LibraryBook>(), jobs.count { !it.state.isActive }, refresh) {
        value = withContext(Dispatchers.IO) { scanLibrary(manager.outputDirectory) }
    }
    val shown = books.filter { query.isBlank() || it.name.contains(query.trim(), ignoreCase = true) }
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
            Text(
                "${shown.size} book${if (shown.size == 1) "" else "s"}",
                style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                modifier = Modifier.padding(start = 12.dp),
            )
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
            modifier = Modifier.fillMaxSize(),
        ) {
            items(shown, key = { it.name.lowercase() }) { book ->
                Column(
                    Modifier.clip(RoundedCornerShape(8.dp)).combinedClickable(
                        onClick = { book.epub?.let { model.readInCatalog(it) } ?: run { open = book } },
                        onLongClick = { open = book },
                    ),
                ) {
                    Box {
                        Cover(book)
                        // The book's files and actions, for a tap that would rather not read.
                        Surface(
                            shape = RoundedCornerShape(12.dp), color = MaterialTheme.colorScheme.surface.copy(alpha = 0.8f),
                            modifier = Modifier.align(Alignment.TopEnd).padding(4.dp).size(28.dp).clip(RoundedCornerShape(12.dp))
                                .clickable { open = book },
                        ) {
                            Box(contentAlignment = Alignment.Center) {
                                Icon(Icons.Filled.MoreVert, contentDescription = "Files and actions", modifier = Modifier.size(18.dp))
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
    }
    open?.let { book ->
        ModalBottomSheet(onDismissRequest = { open = null }) {
            Column(Modifier.fillMaxWidth().padding(horizontal = 16.dp).padding(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
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
                Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    book.epub?.let { epub ->
                        Button(onClick = { open = null; model.readInCatalog(epub) }) { Text("Read") }
                    }
                    OutlinedButton(onClick = { open = null; deleting = book }) { Text("Delete") }
                }
                book.files.forEach { file -> FileRow(model, file, manager.jobOwning(file)?.id) }
            }
        }
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

/** A book's cover on the shelf: its EPUB's own, else its initial on a tinted tile. Fills its width at 2:3. */
@Composable
private fun Cover(book: LibraryBook) {
    val cover by produceState<Bitmap?>(null, book.epub?.absolutePath) {
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
