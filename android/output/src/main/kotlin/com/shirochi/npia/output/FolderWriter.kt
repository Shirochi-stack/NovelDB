package com.shirochi.npia.output

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ImageProcessing
import com.shirochi.npia.core.ImageSettings
import com.shirochi.npia.core.OutputFormat
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.Closeable
import java.io.File
import java.nio.file.Files
import java.util.Locale
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class FolderWriter(override val format: OutputFormat) : BookWriter {
    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File =
        Prepared(directory, bundle.settings.images).use { prepared ->
            for (chapter in OutputSupport.readingOrder(bundle.chapters)) {
                prepared.append(chapter, bundle.images)
            }
            prepared.publish(format, baseName)
        }

    /** A single set of chapter files for Folder and ZIP, kept private until the job publishes it. */
    class Prepared(private val directory: File, private val options: ImageSettings,
                   processor: ImageProcessing? = null) : Closeable {
        private val temporary = Files.createTempDirectory(directory.apply { mkdirs() }.toPath(), ".noveldb-folder-").toFile()
        private val stage = File(temporary, "book").apply { mkdir() }
        private val previous = File(temporary, "previous")
        private val packed = File(temporary, "book.zip")
        private val stitch = if (options.webtoonExport && options.stitchImages && !options.saveImageUrlsOnly) {
            Writers.chapterStitcher
        } else null
        private val subfolders = stitch != null && options.stitchChapterFolders
        private val encode = processor?.takeIf { options.compressAfterStitch }

        suspend fun append(chapter: ChapterContent, available: Map<String, ByteArray>): Unit = withContext(Dispatchers.IO) {
            ensureActive()
            val images = chapter.images.mapIndexedNotNull { index, image ->
                available[image.filename]?.let { Triple(index + 1, extension(image.filename), it) }
            }
            val text = HtmlText.plainText(chapter.html)
            val links = if (options.saveImageUrlsOnly) chapter.images.map { it.url } else emptyList()
            if (images.isEmpty() && text.isBlank() && links.isEmpty()) return@withContext
            val title = CbzWriter.safeName(chapter.ref.title).take(60).trimEnd { it.isHighSurrogate() || it == '.' || it == ' ' }
            val name = "%04d %s".format(Locale.ROOT, maxOf(1, chapter.ref.index), title)
            var folder = stage
            fun useChapterFolder() {
                if (folder != stage) return
                folder = uniqueFile(stage, name, "")
                check(folder.mkdir()) { "Could not create chapter folder." }
            }
            if (subfolders) useChapterFolder()
            fun target(stem: String, ext: String): File {
                val prefix = if (folder == stage) "$name - " else ""
                return uniqueFile(folder, prefix + stem, ".$ext")
            }
            fun save(position: Int, ext: String, bytes: ByteArray) {
                ensureActive()
                val encoded = encode?.process(bytes)
                val finalExt = encoded?.fileExtension?.takeUnless { it == "bin" } ?: ext
                target("%03d".format(Locale.ROOT, position), finalExt).writeBytes(encoded?.data ?: bytes)
            }
            if (stitch != null && images.size > 1 && images.size == chapter.images.size) {
                var index = 0
                val strips = mutableListOf<File>()
                stitch(images.map { it.second to it.third }, options.exportStitchHeight) { ext, multiple ->
                    ensureActive()
                    if (multiple) useChapterFolder()
                    if (encode != null && options.exportStitchHeight != 0) {
                        File(temporary, "strip-${++index}.$ext").also(strips::add)
                    } else target("%03d".format(Locale.ROOT, ++index), ext)
                }
                for ((offset, strip) in strips.withIndex()) {
                    save(offset + 1, strip.extension, strip.readBytes())
                    strip.delete()
                }
            } else {
                for ((index, ext, bytes) in images) {
                    save(index, ext, bytes)
                }
            }
            if (text.isNotBlank()) target("chapter", "txt").writeText(text + "\n")
            if (links.isNotEmpty()) target("images", "txt").writeText(links.joinToString("\n", postfix = "\n"))
        }

        suspend fun pack(): Unit = withContext(Dispatchers.IO) {
            requireContent(OutputFormat.ZIP)
            ZipOutputStream(packed.outputStream().buffered()).use { zip ->
                for (file in stage.walkTopDown().filter(File::isFile).sortedBy { it.path }) {
                    ensureActive()
                    val entry = ZipEntry(file.relativeTo(stage).invariantSeparatorsPath)
                    if (file.extension != "txt") {
                        val crc = CRC32()
                        val buffer = ByteArray(DEFAULT_BUFFER_SIZE)
                        file.inputStream().use { input ->
                            var size = input.read(buffer)
                            while (size > 0) {
                                ensureActive()
                                crc.update(buffer, 0, size)
                                size = input.read(buffer)
                            }
                        }
                        entry.method = ZipEntry.STORED
                        entry.size = file.length()
                        entry.compressedSize = entry.size
                        entry.crc = crc.value
                    }
                    zip.putNextEntry(entry)
                    file.inputStream().use { it.copyTo(zip) }
                    zip.closeEntry()
                }
            }
        }

        suspend fun publish(format: OutputFormat, baseName: String): File = withContext(Dispatchers.IO) {
            val archive = format == OutputFormat.ZIP
            val target = File(directory, "$baseName." + format.raw)
            require(!Files.isSymbolicLink(target.toPath()) && (!target.exists() || target.isDirectory != archive)) {
                "The output path is occupied by a different file type."
            }
            if (archive && !packed.isFile) pack()
            if (!archive) requireContent(format)
            val output = if (archive) packed else stage
            ensureActive()
            if (target.exists()) check(target.renameTo(previous)) { "Could not move the previous output." }
            if (!output.renameTo(target)) {
                if (previous.exists()) check(previous.renameTo(target)) { "Could not restore the previous output." }
                error("Could not save " + format.label)
            }
            previous.deleteRecursively()
            target
        }

        private fun requireContent(format: OutputFormat) {
            if (stage.list().isNullOrEmpty()) throw OutputSkipped("No chapter content for " + format.label)
        }

        override fun close() {
            if (!previous.exists()) temporary.deleteRecursively()
        }

        private fun extension(name: String): String =
            name.substringAfterLast('.', "").lowercase().takeIf { it.matches(Regex("[a-z0-9]{1,8}")) } ?: "jpg"

        private fun uniqueFile(directory: File, stem: String, suffix: String): File {
            var file = File(directory, stem + suffix)
            var duplicate = 2
            while (file.exists()) {
                file = File(directory, "$stem ($duplicate)$suffix")
                duplicate += 1
            }
            return file
        }
    }
}
