package com.shirochi.npia.output

import com.shirochi.npia.core.ChapterRangeText
import java.io.File
import java.util.zip.CRC32
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipOutputStream

/**
 * Cuts chapters out of files a download already saved, for "Upload chapters" (desktop `chapter_cut`).
 *
 * A saved EPUB, chapter ZIP/CBZ or chapter folder names each chapter by its number in the book
 * (`chapter0004.xhtml`, `0004 Title/`), so the chapters of a range are copied out of it into a new
 * file named after that range. Nothing is downloaded: what is not in the saved file is not in the
 * cut. A PDF, a single TXT or an HTML file is one undivided document and cannot be cut.
 */
object ChapterCut {
    private val leading = Regex("^\\s*(\\d+)")
    private val chapterDoc = Regex("(?:chapter|chap|ch)[_\\- ]?(\\d+)\\.x?html?$", RegexOption.IGNORE_CASE)
    private val link = Regex("(?:src|href|xlink:href)\\s*=\\s*[\"']([^\"'#]+)", RegexOption.IGNORE_CASE)
    private val item = Regex("<item\\b[^>]*?/?>(?:\\s*</item>)?", RegexOption.IGNORE_CASE)
    private val itemref = Regex("<itemref\\b[^>]*?/?>(?:\\s*</itemref>)?", RegexOption.IGNORE_CASE)
    private val navPoint = Regex("<navPoint\\b(?:(?!<navPoint\\b).)*?</navPoint>\\s*", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
    private val listItem = Regex("<li\\b(?:(?!<li\\b).)*?</li>\\s*", setOf(RegexOption.IGNORE_CASE, RegexOption.DOT_MATCHES_ALL))
    private val rootfile = Regex("<rootfile\\b[^>]*>", RegexOption.IGNORE_CASE)
    private val ranged = Regex("\\s*\\((\\d+(?:-(?:\\d+|end))?(?:,\\d+(?:-\\d+)?)*)\\)$")

    data class Result(val files: List<File>, val failed: List<String>)

    /** `Title` for a name `Title (4-7)` a ranged download was saved under. */
    fun baseName(name: String): String {
        val found = ranged.find(name) ?: return name
        return name.substring(0, found.range.first).ifBlank { name }
    }

    /** Where the cut of `source` goes: beside it, named after the book and the range. */
    fun target(source: File, from: Int, to: Int, list: String): File {
        val name = source.name
        val dot = name.lastIndexOf('.')
        val hasSuffix = dot > 0 && (!source.isDirectory || name.endsWith(".folder", ignoreCase = true))
        val stem = if (hasSuffix) name.substring(0, dot) else name
        val suffix = if (hasSuffix) name.substring(dot) else ""
        return File(source.parentFile, "${baseName(stem)} (${ChapterRangeText.named(from, to, list)})$suffix")
    }

    /** Cuts every saved file that still exists. A file that already is that range is returned as it is. */
    fun cut(files: List<File>, from: Int, to: Int, list: String): Result {
        val made = mutableListOf<File>()
        val failed = mutableListOf<String>()
        val wanted = { number: Int -> number in from..to && ChapterRangeText.inList(from, to, list, number) }
        for (source in files) {
            if (!source.exists()) continue
            val target = target(source, from, to, list)
            if (target == source) {
                if (source !in made) made.add(source)
                continue
            }
            val part = File(target.parentFile, target.name + ".part")
            val count = try {
                when {
                    source.isDirectory -> cutFolder(source, target, part, wanted)
                    source.extension.equals("epub", ignoreCase = true) -> cutEpub(source, target, part, wanted)
                    source.extension.lowercase() in setOf("zip", "cbz") -> cutArchive(source, target, part, wanted)
                    else -> 0
                }
            } catch (_: Exception) {
                0
            }
            if (count > 0) {
                if (target !in made) made.add(target)
            } else {
                part.deleteRecursively()
                failed.add(source.name)
            }
        }
        return Result(made, failed)
    }

    private fun chapterOf(name: String): Int? = leading.find(name)?.groupValues?.get(1)?.toIntOrNull()

    private fun cutFolder(source: File, target: File, part: File, wanted: (Int) -> Boolean): Int {
        // One folder per chapter (pages), or one file per chapter (separate TXT).
        val keep = (source.listFiles() ?: emptyArray()).sortedBy { it.name }
            .filter { entry -> chapterOf(entry.name)?.let(wanted) == true }
        if (keep.isEmpty()) return 0
        part.deleteRecursively()
        part.mkdirs()
        for (entry in keep) entry.copyRecursively(File(part, entry.name), overwrite = true)
        target.deleteRecursively()
        if (!part.renameTo(target)) return 0
        return keep.size
    }

    private fun cutArchive(source: File, target: File, part: File, wanted: (Int) -> Boolean): Int {
        val kept = mutableSetOf<Int>()
        ZipFile(source).use { archive ->
            ZipOutputStream(part.outputStream().buffered()).use { output ->
                for (entry in archive.entries()) {
                    if (entry.isDirectory) continue
                    // Files beside the chapter folders (a cover, the comic's info) stay.
                    if ('/' in entry.name) {
                        val number = chapterOf(entry.name.substringBefore('/')) ?: continue
                        if (!wanted(number)) continue
                        kept.add(number)
                    }
                    write(output, entry.name, archive.getInputStream(entry).use { it.readBytes() }, entry.method == ZipEntry.STORED)
                }
            }
        }
        if (kept.isEmpty()) return 0
        return if (replace(part, target)) kept.size else 0
    }

    private fun cutEpub(source: File, target: File, part: File, wanted: (Int) -> Boolean): Int {
        var count = 0
        ZipFile(source).use { archive ->
            fun text(name: String): String = archive.getEntry(name)?.let { entry -> archive.getInputStream(entry).use { it.readBytes() }.decodeToString() } ?: ""
            val names = archive.entries().asSequence().filter { !it.isDirectory }.map { it.name }.toList()
            val packageName = rootfile.find(text("META-INF/container.xml"))?.let { attribute(it.value, "full-path") }.orEmpty()
            if (packageName.isEmpty()) return 0
            val base = packageName.substringBeforeLast('/', "")
            var opf = text(packageName)
            val chapters = mutableMapOf<String, Int>()
            for (tag in item.findAll(opf)) {
                val href = attribute(tag.value, "href")
                val number = chapterDoc.find(href.substringAfterLast('/'))?.groupValues?.get(1)?.toIntOrNull() ?: continue
                chapters[resolve(base, href)] = number
            }
            val keep = chapters.filterValues(wanted).keys
            if (keep.isEmpty()) return 0
            val dropped = chapters.keys - keep
            // Files only the dropped chapters use (their pages' images) go with them.
            val used = mutableSetOf<String>()
            for (name in names) {
                val lower = name.lowercase()
                if (name in dropped || name == packageName) continue
                if (lower.endsWith(".xhtml") || lower.endsWith(".html") || lower.endsWith(".htm") || lower.endsWith(".css")) {
                    used += links(text(name), name.substringBeforeLast('/', ""))
                }
            }
            val cover = item.findAll(opf).map { it.value }
                .filter { "cover" in (attribute(it, "id") + attribute(it, "properties")).lowercase() }
                .map { resolve(base, attribute(it, "href")) }.toSet()
            val gone = dropped.toMutableSet()
            for (name in dropped) gone += links(text(name), name.substringBeforeLast('/', "")) - used - cover - keep

            val ids = mutableSetOf<String>()
            opf = item.replace(opf) { found ->
                if (resolve(base, attribute(found.value, "href")) in gone) {
                    ids.add(attribute(found.value, "id"))
                    ""
                } else found.value
            }
            opf = itemref.replace(opf) { found -> if (attribute(found.value, "idref") in ids) "" else found.value }
            val navigation = names.filter { it.lowercase().endsWith(".ncx") }.toMutableSet()
            for (tag in item.findAll(opf)) {
                if ("nav" in attribute(tag.value, "properties").lowercase().split(' ')) navigation += resolve(base, attribute(tag.value, "href"))
            }
            ZipOutputStream(part.outputStream().buffered()).use { output ->
                // The mimetype entry comes first and uncompressed, as readers require.
                val ordered = names.sortedBy { if (it == "mimetype") 0 else 1 }
                for (name in ordered) {
                    if (name in gone) continue
                    val entry = archive.getEntry(name)
                    var data = archive.getInputStream(entry).use { it.readBytes() }
                    if (name == packageName) {
                        data = opf.encodeToByteArray()
                    } else if (name in navigation) {
                        val folder = name.substringBeforeLast('/', "")
                        val pattern = if (name.lowercase().endsWith(".ncx")) navPoint else listItem
                        data = pattern.replace(data.decodeToString()) { found ->
                            if (links(found.value, folder).any { it in gone }) "" else found.value
                        }.encodeToByteArray()
                    }
                    write(output, name, data, name == "mimetype" || entry.method == ZipEntry.STORED)
                }
            }
            count = keep.size
        }
        return if (replace(part, target)) count else 0
    }

    private fun replace(part: File, target: File): Boolean {
        target.delete()
        return part.renameTo(target)
    }

    private fun write(output: ZipOutputStream, name: String, data: ByteArray, stored: Boolean) {
        val entry = ZipEntry(name)
        if (stored) {
            entry.method = ZipEntry.STORED
            entry.size = data.size.toLong()
            entry.compressedSize = data.size.toLong()
            entry.crc = CRC32().apply { update(data) }.value
        }
        output.putNextEntry(entry)
        output.write(data)
        output.closeEntry()
    }

    private fun attribute(tag: String, name: String): String =
        Regex("\\b$name\\s*=\\s*[\"']([^\"']*)[\"']", RegexOption.IGNORE_CASE).find(tag)?.groupValues?.get(1).orEmpty()

    private fun links(text: String, folder: String): Set<String> =
        link.findAll(text).map { it.groupValues[1] }
            .filter { "://" !in it && !it.startsWith("data:") && !it.startsWith("mailto:") }
            .map { resolve(folder, it) }.toSet()

    /** `folder/href` with `.` and `..` worked out, as the name the archive stores. */
    private fun resolve(folder: String, href: String): String {
        val parts = mutableListOf<String>()
        for (piece in (if (folder.isEmpty()) href else "$folder/$href").split('/')) {
            when (piece) {
                "", "." -> Unit
                ".." -> if (parts.isNotEmpty()) parts.removeAt(parts.size - 1)
                else -> parts.add(piece)
            }
        }
        return parts.joinToString("/")
    }
}
