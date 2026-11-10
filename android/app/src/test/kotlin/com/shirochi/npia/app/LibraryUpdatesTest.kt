package com.shirochi.npia.app

import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.LibraryUpdateEntry
import com.shirochi.npia.app.model.LibraryUpdateState
import com.shirochi.npia.app.model.librarySourceKey
import com.shirochi.npia.app.model.replaceLibraryEpub
import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

class LibraryUpdatesTest {
    @get:Rule val temporary = TemporaryFolder()

    @Test fun rangeDefaultsAndUserChoicesPersist() {
        val full = LibraryUpdateEntry("https://example.test/1", ranged = false)
        val ranged = LibraryUpdateEntry("https://example.test/2", ranged = true)
        assertTrue(full.enabled)
        assertFalse(ranged.enabled)
        assertFalse(LibraryUpdateEntry("https://example.test/old").enabled)
        val state = LibraryUpdateState(enabled = false, background = true, uploadDrive = true,
            books = mapOf("full.epub" to full.copy(autoUpdate = false), "range.epub" to ranged.copy(autoUpdate = true)))
        val saved = JobItem.json.decodeFromString(LibraryUpdateState.serializer(), JobItem.json.encodeToString(LibraryUpdateState.serializer(), state))
        assertEquals(state, saved)
        assertFalse(saved.enabled)
        assertTrue(saved.books.getValue("range.epub").enabled)
        assertFalse(saved.books.getValue("full.epub").enabled)
    }

    @Test fun oldQueueEntriesStillDecode() {
        val job = JobItem.json.decodeFromString(JobItem.serializer(), """{"url":"https://example.test/book"}""")
        assertNull(job.libraryTarget)
    }

    @Test fun sourceAliasesShareAQueue() {
        assertEquals(librarySourceKey("https://www.example.test/book/1"), librarySourceKey("https://011.example.test/book/2"))
        assertNotEquals(librarySourceKey("https://example.test/1"), librarySourceKey("https://other.test/1"))
    }

    private fun epub(name: String, count: Int): File = File(temporary.root, name).apply {
        ZipOutputStream(outputStream()).use { archive ->
            archive.putNextEntry(ZipEntry("META-INF/container.xml"))
            archive.write("<container><rootfile full-path=\"book.opf\"/></container>".toByteArray())
            archive.closeEntry()
            archive.putNextEntry(ZipEntry("book.opf"))
            archive.write(("<package><spine>" + (1..count).joinToString("") { "<itemref idref=\"c$it\"/>" } + "</spine></package>").toByteArray())
            archive.closeEntry()
        }
    }

    @Test fun shorterRefreshPreservesOriginalAndACompleteRefreshKeepsItsName() {
        val target = epub("saved-name.epub", 3)
        val original = target.readBytes()
        val incomplete = epub("shorter.epub", 2)
        assertThrows(IllegalArgumentException::class.java) { replaceLibraryEpub(incomplete, target) }
        assertArrayEquals(original, target.readBytes())
        val candidate = epub("new-title.epub", 4)
        val expected = candidate.readBytes()
        replaceLibraryEpub(candidate, target)
        assertArrayEquals(expected, target.readBytes())
        assertFalse(candidate.exists())
    }
}
