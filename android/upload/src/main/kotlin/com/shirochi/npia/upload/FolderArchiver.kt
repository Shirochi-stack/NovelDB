package com.shirochi.npia.upload

import java.io.File
import java.util.UUID
import java.util.zip.Deflater
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

/** Zips folder outputs (separate TXT) so hosts that take one file can share them. */
internal object FolderArchiver {
    /**
     * Writes `<folder name>.zip` into a fresh directory under `temporaryDirectory`, with the folder
     * itself as the top entry. The caller deletes it after the upload.
     */
    fun zip(folder: File, temporaryDirectory: File): File {
        val staging = File(temporaryDirectory, "npia-zip-${UUID.randomUUID()}")
        if (!staging.mkdirs()) throw java.io.IOException("Cannot create ${staging.path}")
        val archive = File(staging, folder.name + ".zip")
        ZipOutputStream(archive.outputStream().buffered()).use { zip ->
            zip.setLevel(Deflater.DEFAULT_COMPRESSION)
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
        return archive
    }
}
