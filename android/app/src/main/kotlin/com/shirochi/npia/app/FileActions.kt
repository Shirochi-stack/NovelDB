package com.shirochi.npia.app

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Opening, sharing, and copying output files through `FileProvider` content URIs. */
object FileActions {
    fun mimeType(file: File): String = if (file.isDirectory) "application/zip" else when (file.extension.lowercase()) {
        "epub" -> "application/epub+zip"
        "pdf" -> "application/pdf"
        "txt" -> "text/plain"
        "html", "htm" -> "text/html"
        "json" -> "application/json"
        "zip" -> "application/zip"
        else -> "application/octet-stream"
    }

    fun uri(context: Context, file: File): Uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)

    /**
     * Files to hand to other apps. A separate-TXT folder goes as one ZIP (written to the cache's
     * `share` folder) when it holds more than one file, else as its one file.
     */
    fun shareable(context: Context, files: List<File>): List<File> =
        files.flatMap { file ->
            if (!file.isDirectory) return@flatMap listOf(file)
            val inside = file.listFiles()?.filter(File::isFile)?.sortedBy(File::getName).orEmpty()
            if (inside.size > 1) listOfNotNull(zipForSharing(context, file)) else inside
        }.filter { it.exists() }

    private fun zipForSharing(context: Context, folder: File): File? = runCatching {
        val directory = File(context.cacheDir, "share").apply { mkdirs() }
        // Only the latest shared copies are kept.
        directory.listFiles()?.filter { System.currentTimeMillis() - it.lastModified() > 24 * 3600_000L }?.forEach { it.delete() }
        File(directory, "${folder.name}.zip").also { target -> target.outputStream().use { zipFolder(folder, it) } }
    }.getOrNull()

    fun open(context: Context, file: File): Boolean {
        if (file.isDirectory) return share(context, listOf(file))
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri(context, file), mimeType(file))
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        return launch(context, Intent.createChooser(intent, "Open ${file.name}"))
    }

    fun share(context: Context, files: List<File>): Boolean {
        val items = shareable(context, files)
        if (items.isEmpty()) return false
        val uris = items.map { uri(context, it) }
        val intent = if (uris.size == 1) {
            Intent(Intent.ACTION_SEND).setType(mimeType(items[0])).putExtra(Intent.EXTRA_STREAM, uris[0])
        } else {
            val types = items.map(::mimeType).distinct()
            Intent(Intent.ACTION_SEND_MULTIPLE)
                .setType(if (types.size == 1) types[0] else "*/*")
                .putParcelableArrayListExtra(Intent.EXTRA_STREAM, ArrayList(uris))
        }
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        intent.clipData = ClipData.newRawUri(null, uris[0]).apply { uris.drop(1).forEach { addItem(ClipData.Item(it)) } }
        return launch(context, Intent.createChooser(intent, null))
    }

    fun shareText(context: Context, text: String): Boolean =
        launch(context, Intent.createChooser(Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text), null))

    fun openUrl(context: Context, url: String): Boolean = launch(context, Intent(Intent.ACTION_VIEW, Uri.parse(url)))

    /**
     * The name a finished output is listed under. A folder (separate TXT) has no extension of its
     * own, so it is listed with the extension and count of the files it holds: `Book (12 .txt files)`.
     */
    fun displayName(file: File): String {
        if (!file.isDirectory) return file.name
        val files = file.listFiles()?.filter { it.isFile }.orEmpty()
        if (files.isEmpty()) return "${file.name}/"
        val extensions = files.map { it.extension.lowercase() }.filter { it.isNotEmpty() }.distinct().sorted()
            .joinToString("/") { ".$it" }
        val noun = if (files.size == 1) "file" else "files"
        return "${file.name} (${files.size} ${if (extensions.isEmpty()) "" else "$extensions "}$noun)"
    }

    /** The name a saved copy gets: a folder (separate TXT) is saved as `<folder>.zip`. */
    fun copyName(file: File): String = if (file.isDirectory) "${file.name}.zip" else file.name

    /**
     * Copies a finished file to a location the user picked with the Storage Access Framework; a
     * folder (separate TXT) is written there as one ZIP with the folder as its top entry.
     */
    fun copyTo(context: Context, file: File, target: Uri): Boolean = runCatching {
        context.contentResolver.openOutputStream(target, "w")?.use { out ->
            if (file.isDirectory) zipFolder(file, out) else file.inputStream().use { it.copyTo(out) }
        } != null
    }.getOrDefault(false)

    private fun zipFolder(folder: File, out: java.io.OutputStream) {
        ZipOutputStream(out.buffered()).use { zip ->
            folder.walkTopDown().sortedBy { it.path }.forEach { item ->
                val relative = item.relativeTo(folder).invariantSeparatorsPath
                val name = if (relative.isEmpty()) folder.name else "${folder.name}/$relative"
                if (item.isDirectory) {
                    zip.putNextEntry(ZipEntry("$name/"))
                } else {
                    zip.putNextEntry(ZipEntry(name))
                    item.inputStream().use { it.copyTo(zip, 1 shl 16) }
                }
                zip.closeEntry()
            }
        }
    }

    private fun launch(context: Context, intent: Intent): Boolean = try {
        if (context !is android.app.Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        true
    } catch (e: ActivityNotFoundException) {
        false
    }
}
