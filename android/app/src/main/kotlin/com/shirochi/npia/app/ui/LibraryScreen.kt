package com.shirochi.npia.app.ui

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import androidx.compose.foundation.Image
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
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

/** The Library tab: the books downloaded on this device, with the same actions as a finished download. */
@Composable
fun LibraryScreen(model: AppViewModel) {
    val manager = model.manager
    val jobs by manager.jobs.collectAsStateWithLifecycle()
    var refresh by remember { mutableIntStateOf(0) }
    var query by remember { mutableStateOf("") }
    var deleting by remember { mutableStateOf<LibraryBook?>(null) }
    // Read again whenever a download finishes (the jobs change) or a book is deleted.
    val books by produceState(emptyList<LibraryBook>(), jobs.count { !it.state.isActive }, refresh) {
        value = withContext(Dispatchers.IO) { scanLibrary(manager.outputDirectory) }
    }
    val shown = books.filter { query.isBlank() || it.name.contains(query.trim(), ignoreCase = true) }
    Column(Modifier.fillMaxSize()) {
        OutlinedTextField(
            value = query, onValueChange = { query = it }, singleLine = true,
            label = { Text("Find a book") },
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 8.dp),
        )
        if (shown.isEmpty()) {
            Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
                Text(
                    if (books.isEmpty()) "No books yet. Downloads are saved here and show up as soon as they finish." else "No book matches.",
                    style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant,
                )
            }
        }
        LazyColumn(Modifier.fillMaxSize()) {
            items(shown, key = { it.name.lowercase() }) { book ->
                Row(Modifier.fillMaxWidth().padding(start = 16.dp, end = 16.dp, top = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Cover(book)
                    Column(Modifier.weight(1f).padding(start = 12.dp), verticalArrangement = Arrangement.spacedBy(2.dp)) {
                        Text(book.name, style = MaterialTheme.typography.titleSmall, maxLines = 3, overflow = TextOverflow.Ellipsis)
                        Text(
                            java.text.DateFormat.getDateInstance().format(java.util.Date(book.modified)),
                            style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant,
                        )
                    }
                    TextButton(onClick = { deleting = book }) { Text("Delete") }
                }
                book.files.forEach { file -> FileRow(model, file, manager.jobOwning(file)?.id) }
                HorizontalDivider(Modifier.padding(top = 6.dp))
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

@Composable
private fun Cover(book: LibraryBook) {
    val cover by produceState<Bitmap?>(null, book.epub?.absolutePath) {
        value = book.epub?.let { withContext(Dispatchers.IO) { epubCover(it) } }
    }
    Surface(shape = RoundedCornerShape(6.dp), color = MaterialTheme.colorScheme.surfaceContainerHighest, modifier = Modifier.size(56.dp, 80.dp)) {
        val bitmap = cover
        if (bitmap != null) {
            Image(bitmap.asImageBitmap(), contentDescription = null, contentScale = ContentScale.Crop,
                modifier = Modifier.fillMaxSize().clip(RoundedCornerShape(6.dp)))
        } else {
            Box(contentAlignment = Alignment.Center) { Text(book.name.take(1).uppercase(), style = MaterialTheme.typography.titleLarge) }
        }
    }
}
