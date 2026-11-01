package com.shirochi.npia.app

import android.content.ActivityNotFoundException
import android.content.ClipData
import android.content.Context
import android.content.Intent
import android.net.Uri
import androidx.core.content.FileProvider
import java.io.File

/** Opening, sharing, and copying output files through `FileProvider` content URIs. */
object FileActions {
    fun mimeType(file: File): String = when (file.extension.lowercase()) {
        "epub" -> "application/epub+zip"
        "pdf" -> "application/pdf"
        "txt" -> "text/plain"
        "html", "htm" -> "text/html"
        "json" -> "application/json"
        "zip" -> "application/zip"
        else -> "application/octet-stream"
    }

    fun uri(context: Context, file: File): Uri = FileProvider.getUriForFile(context, "${context.packageName}.files", file)

    /** Files to hand to other apps; a separate-TXT folder expands to its chapter files. */
    fun shareable(files: List<File>): List<File> =
        files.flatMap { if (it.isDirectory) it.listFiles()?.filter(File::isFile)?.sortedBy(File::getName).orEmpty() else listOf(it) }
            .filter { it.exists() }

    fun open(context: Context, file: File): Boolean {
        if (file.isDirectory) return share(context, listOf(file))
        val intent = Intent(Intent.ACTION_VIEW)
            .setDataAndType(uri(context, file), mimeType(file))
            .addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
        return launch(context, Intent.createChooser(intent, "Open ${file.name}"))
    }

    fun share(context: Context, files: List<File>): Boolean {
        val items = shareable(files)
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

    /** Copies a finished file to a location the user picked with the Storage Access Framework. */
    fun copyTo(context: Context, file: File, target: Uri): Boolean = runCatching {
        context.contentResolver.openOutputStream(target, "w")?.use { out -> file.inputStream().use { it.copyTo(out) } } != null
    }.getOrDefault(false)

    private fun launch(context: Context, intent: Intent): Boolean = try {
        if (context !is android.app.Activity) intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
        context.startActivity(intent)
        true
    } catch (e: ActivityNotFoundException) {
        false
    }
}
