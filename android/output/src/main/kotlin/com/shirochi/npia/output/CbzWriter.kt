package com.shirochi.npia.output

import com.shirochi.npia.core.OutputFormat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Raised when a format has nothing to write for this book; the pipeline logs it and moves on. */
class OutputSkipped(message: String) : Exception(message)

/**
 * Comic archive laid out like the desktop CBZ export: `0000_cover.<ext>`, `ComicInfo.xml`, then
 * `NNNN_<chapter title>/NNNN.<ext>` for each chapter's images in reading order. Chapters
 * without images are left out. Images are stored unless `zipCompressImages` is on.
 */
class CbzWriter : BookWriter {
    override val format = OutputFormat.CBZ

    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = withContext(Dispatchers.IO) {
        val entries = entries(bundle)
        if (entries.none { it.first.contains('/') }) throw OutputSkipped("CBZ skipped: no images in this book")
        directory.mkdirs()
        val file = File(directory, "$baseName.cbz")
        val temp = File(directory, "$baseName.cbz.part")
        val compressImages = bundle.settings.images.zipCompressImages
        ZipOutputStream(temp.outputStream().buffered()).use { zip ->
            for ((path, data) in entries) {
                val entry = ZipEntry(path)
                if (path == "ComicInfo.xml" || compressImages) {
                    entry.method = ZipEntry.DEFLATED
                } else {
                    entry.method = ZipEntry.STORED
                    entry.size = data.size.toLong()
                    entry.compressedSize = data.size.toLong()
                    entry.crc = CRC32().apply { update(data) }.value
                }
                zip.putNextEntry(entry)
                zip.write(data)
                zip.closeEntry()
            }
        }
        if (file.exists()) file.delete()
        temp.renameTo(file)
        file
    }

    companion object {
        /** Archive entries in write order. */
        internal fun entries(bundle: BookBundle): List<Pair<String, ByteArray>> {
            val book = bundle.book
            val out = mutableListOf<Pair<String, ByteArray>>()
            bundle.cover?.let { out += "0000_cover.${it.fileExtension.lowercase()}" to it.data }
            val info = """
                <?xml version="1.0" encoding="utf-8"?>
                <ComicInfo>
                  <Title>${OutputSupport.escape(book.title)}</Title>
                  <Writer>${OutputSupport.escape(book.author.orEmpty())}</Writer>
                  <Summary>${OutputSupport.escape(book.synopsis.orEmpty())}</Summary>
                </ComicInfo>
            """.trimIndent() + "\n"
            out += "ComicInfo.xml" to info.encodeToByteArray()
            val numbered = bundle.settings.effective(book.source).numberChapters
            val used = mutableSetOf<String>()
            for (chapter in OutputSupport.readingOrder(bundle.chapters)) {
                val images = chapter.images.mapNotNull { ref -> bundle.images[ref.filename]?.let { ref.filename to it } }
                if (images.isEmpty()) continue
                var number = maxOf(1, chapter.ref.index)
                val title = safeName(OutputSupport.displayTitle(chapter.ref, numbered))
                var folder = "${pad4(number)}_$title"
                while (folder in used) folder = "${pad4(++number)}_$title"
                used += folder
                images.forEachIndexed { i, (name, data) ->
                    val ext = name.substringAfterLast('.', "jpg").lowercase()
                    out += "$folder/${pad4(i + 1)}.$ext" to data
                }
            }
            return out
        }

        private fun pad4(n: Int) = n.toString().padStart(4, '0')

        /** `_sanitize_filename`: path and reserved characters become `_`. */
        internal fun safeName(name: String): String {
            val cleaned = name.map { if (it in "/\\:*?\"<>|" || it.isISOControl()) '_' else it }.joinToString("").trim().trimEnd('.')
            return cleaned.ifEmpty { "chapter" }.take(100)
        }
    }
}
