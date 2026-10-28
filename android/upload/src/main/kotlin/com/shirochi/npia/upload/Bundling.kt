package com.shirochi.npia.upload

import com.shirochi.npia.core.UploadSettings
import java.io.File
import java.io.IOException
import java.util.UUID
import java.util.zip.CRC32
import java.util.zip.Deflater
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/**
 * A file to upload. `stem` names it when its file name is not plain ASCII and its title keeps too
 * few letters (see `UploadName`): `<source>_<book id>`, for example `npia_123456`.
 */
data class UploadItem(val file: File, val stem: String? = null)

/** One upload of a run: a single file, or several files packed into one ZIP. */
sealed class PlannedUpload {
    abstract val items: List<UploadItem>

    /** One file, uploaded as it is (or zipped by `FolderArchiver` when it is a folder). */
    class Single(val item: UploadItem) : PlannedUpload() {
        override val items: List<UploadItem> get() = listOf(item)
    }

    /** Two or more files in a ZIP. `name` is the ASCII ZIP file name, unique within the run. */
    class Archive(override val items: List<UploadItem>, val name: String) : PlannedUpload()

    val isArchive: Boolean get() = this is Archive
}

/**
 * "Bundle several files into ZIP uploads": packing, names and the ZIP file itself. The same rules
 * run on desktop and iOS (`upload_zip_bundle`, `upload_zip_max_mb`).
 */
object ZipBundling {
    /** Allowance per entry for headers, on top of the file size. */
    const val ENTRY_OVERHEAD = 1024L

    /** Classic ZIP offsets and sizes are 32 bit; a ZIP is never planned beyond this. */
    const val MAX_ARCHIVE_BYTES = 4_294_000_000L

    /** Entries that are already compressed are stored as they are; text and anything unknown is deflated. */
    private val STORED_EXTENSIONS = setOf("epub", "cbz", "pdf", "zip")

    /**
     * The ZIP size limit: the setting, or the chosen host's own per-file maximum when that is
     * smaller and known.
     */
    fun limit(settings: UploadSettings, hostMaxBytes: Long?): Long =
        minOf(settings.zipLimitBytes, hostMaxBytes ?: Long.MAX_VALUE, MAX_ARCHIVE_BYTES)

    /**
     * Packs `items` into as few ZIPs as possible by first-fit decreasing on file size: every ZIP's
     * file sizes plus `ENTRY_OVERHEAD` per entry stay within `limitBytes`. A file that does not fit
     * a ZIP on its own is uploaded alone, and so is a file left alone in its ZIP. Folders are never
     * bundled. Fewer than two items are returned as they are.
     */
    fun plan(items: List<UploadItem>, limitBytes: Long): List<PlannedUpload> {
        if (items.size < 2) return items.map { PlannedUpload.Single(it) }
        class Bin(var used: Long, val members: MutableList<Int>, val alone: Boolean)

        val bundleable = items.indices.filter { items[it].file.isFile }
        val sizes = items.map { runCatching { it.file.length() }.getOrDefault(0L) }
        val bins = mutableListOf<Bin>()
        // Largest first; equal sizes keep the order they were given in.
        for (index in bundleable.sortedWith(compareByDescending<Int> { sizes[it] }.thenBy { it })) {
            val cost = sizes[index] + ENTRY_OVERHEAD
            if (cost > limitBytes) {
                bins += Bin(cost, mutableListOf(index), alone = true)
                continue
            }
            val bin = bins.firstOrNull { !it.alone && it.used + cost <= limitBytes }
            if (bin != null) {
                bin.used += cost
                bin.members += index
            } else {
                bins += Bin(cost, mutableListOf(index), alone = false)
            }
        }
        val groups: List<List<UploadItem>> =
            bins.map { bin -> bin.members.sorted().map { items[it] } } + items.indices.filter { it !in bundleable }.map { listOf(items[it]) }
        val names = ArchiveNames()
        return groups.map { group ->
            if (group.size == 1) PlannedUpload.Single(group[0]) else PlannedUpload.Archive(group, names.next(group.map { entryName(it.file) }))
        }
    }

    /** Hands out ZIP names for one upload: a repeat of a name gets `_2`, `_3`, ... before `.zip`. */
    class ArchiveNames {
        private val used = mutableMapOf<String, Int>()

        fun next(entryNames: List<String>): String {
            val base = baseName(entryNames)
            val count = (used[base] ?: 0) + 1
            used[base] = count
            return if (count == 1) "$base.zip" else "${base}_$count.zip"
        }
    }

    /** `NovelDB_3_EPUBs` when every entry is an EPUB, else `NovelDB_3_files`. */
    fun baseName(entryNames: List<String>): String =
        "NovelDB_${entryNames.size}_${if (entryNames.all { extension(it) == "epub" }) "EPUBs" else "files"}"

    /** "3 books" when every entry is an EPUB, else "4 files". */
    fun describe(entryNames: List<String>): String {
        val books = entryNames.isNotEmpty() && entryNames.all { extension(it) == "epub" }
        val count = entryNames.size
        return if (books) (if (count == 1) "1 book" else "$count books") else (if (count == 1) "1 file" else "$count files")
    }

    /** The name an entry gets in the ZIP: the file's own name, in NFC. */
    fun entryName(file: File): String = uploadName(file)

    private fun extension(name: String): String = name.substringAfterLast('.', "").lowercase()

    /**
     * Writes the ZIP: one entry per file under its original name (UTF-8, so Korean titles stay
     * intact), no folders. EPUB, CBZ, PDF and ZIP entries are stored, everything else deflated.
     * Two files with the same name keep both, the later one as `name (2).ext`.
     * Returns the entry names in the order written.
     */
    fun write(items: List<UploadItem>, target: File): List<String> {
        val taken = mutableSetOf<String>()
        val written = mutableListOf<String>()
        target.absoluteFile.parentFile?.mkdirs()
        ZipOutputStream(target.outputStream().buffered(1 shl 16)).use { zip ->
            zip.setLevel(Deflater.DEFAULT_COMPRESSION)
            for (item in items) {
                val name = unique(entryName(item.file), taken)
                val entry = ZipEntry(name)
                if (extension(name) in STORED_EXTENSIONS) {
                    entry.method = ZipEntry.STORED
                    entry.size = item.file.length()
                    entry.compressedSize = entry.size
                    entry.crc = crc(item.file, entry.size)
                } else {
                    entry.method = ZipEntry.DEFLATED
                }
                zip.putNextEntry(entry)
                item.file.inputStream().use { it.copyTo(zip, 1 shl 16) }
                zip.closeEntry()
                written += name
            }
        }
        return written
    }

    private fun unique(name: String, taken: MutableSet<String>): String {
        var candidate = name
        var n = 2
        val dot = name.lastIndexOf('.').takeIf { it > 0 }
        while (!taken.add(candidate)) {
            candidate = if (dot != null) "${name.substring(0, dot)} ($n)${name.substring(dot)}" else "$name ($n)"
            n++
        }
        return candidate
    }

    private fun crc(file: File, expectedSize: Long): Long {
        val crc = CRC32()
        var total = 0L
        file.inputStream().use { input ->
            val buffer = ByteArray(1 shl 16)
            while (true) {
                val count = input.read(buffer)
                if (count < 0) break
                crc.update(buffer, 0, count)
                total += count
            }
        }
        if (total != expectedSize) throw IOException("${file.name} changed while it was being zipped")
        return crc.value
    }

    /** A fresh directory under `temporaryDirectory` for ZIPs under construction; the caller deletes it. */
    fun stagingDirectory(temporaryDirectory: File): File {
        val staging = File(temporaryDirectory, "npia-zip-${UUID.randomUUID()}")
        if (!staging.mkdirs()) throw IOException("Cannot create ${staging.path}")
        return staging
    }
}
