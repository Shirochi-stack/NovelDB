package com.shirochi.npia.upload

import com.shirochi.npia.core.UploadSettings
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertThrows
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Test
import java.io.File
import java.util.zip.ZipFile

class UploaderCatalogTest : UploadTestBase() {
    @Test
    fun idsAreUnique() {
        val ids = UploaderCatalog.all.map { it.id }
        assertEquals(ids.size, ids.toSet().size)
    }

    @Test
    fun defaultHostsExist() {
        UploadSettings().hosts.forEach { assertNotNull(it, UploaderCatalog.uploader(it)) }
    }

    @Test
    fun renamedIdsResolve() {
        assertEquals("sendnow", UploaderCatalog.uploader("sendcm")?.id)
        assertEquals("gdrive", UploaderCatalog.uploader("google_drive")?.id)
        assertNull(UploaderCatalog.uploader("transfersh"))
    }

    @Test
    fun tooLargeFileIsRejectedBeforeUpload() {
        val file = makeFile(bytes = 2048)
        val tiny = object : Uploader {
            override val id = "tiny"
            override val displayName = "Tiny"
            override val maxBytes: Long? = 1024
            override val retentionSeconds: Long? = null
            override val requiresAccount = false
            override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult =
                throw AssertionError("should not upload")
        }
        val error = assertThrows(UploadException.TooLarge::class.java) { tiny.check(file) }
        assertEquals(UploadException.TooLarge("Tiny", 1024), error)
    }

    @Test
    fun fallsBackToNextHostAndRecordsHistory() = runBlocking<Unit> {
        val file = makeFile()
        handler = { request ->
            when (request.url.host) {
                "temp.sh" -> text("busy", status = 503)
                "upload.gofile.io" -> json("""{"status":"ok","data":{"downloadPage":"https://gofile.io/d/AbC123","guestToken":"t"}}""")
                else -> unexpected("unexpected ${request.url}")
            }
        }
        val settings = UploadSettings(hosts = listOf("tempsh", "gofile"))
        val history = UploadHistoryStore(File(directory, "history.json"))

        val result = UploaderCatalog.upload(file, settings, context(settings = settings), history)

        assertEquals("gofile", result.hostId)
        assertEquals("https://gofile.io/d/AbC123", result.link)
        assertEquals(fixedNow + 10 * 86_400_000L, result.expiresAt)
        assertEquals(listOf("temp.sh", "upload.gofile.io"), seen.map { it.url.host })
        assertEquals(listOf(result), history.all)
    }

    @Test
    fun allHostsFailingThrowsLastError() = runBlocking<Unit> {
        handler = { text("nope", status = 500) }
        val settings = UploadSettings(hosts = listOf("tempsh"))
        try {
            UploaderCatalog.upload(makeFile(), settings, context(settings = settings), history = null)
            fail("expected failure")
        } catch (e: UploadException) {
            assertEquals(UploadException.BadResponse("temp.sh", 500, "nope"), e)
        }
    }

    @Test
    fun accountHostWithoutCredentialsIsSkipped() = runBlocking<Unit> {
        handler = { unexpected("no request expected") }
        val settings = UploadSettings(hosts = listOf("pixeldrain", "gdrive"))
        try {
            UploaderCatalog.upload(makeFile(), settings, context(settings = settings), history = null)
            fail("expected failure")
        } catch (e: UploadException) {
            assertEquals(UploadException.NotSignedIn("Google Drive"), e)
        }
        assertTrue(seen.isEmpty())
    }

    @Test
    fun folderOutputIsZippedBeforeUpload() = runBlocking<Unit> {
        val folder = File(directory, "Book TXT").apply { mkdirs() }
        File(folder, "001.txt").writeText("one")
        File(folder, "002.txt").writeText("two")
        handler = { text("https://temp.sh/xyz/Book TXT.zip") }
        val settings = UploadSettings(hosts = listOf("tempsh"))

        val result = UploaderCatalog.upload(folder, settings, context(settings = settings), history = null)

        assertEquals("Book TXT.zip", result.fileName)
        assertEquals("https://temp.sh/xyz/Book%20TXT.zip", result.link)
        assertTrue(seen.first().text.contains("filename=\"Book TXT.zip\""))
        val leftovers = directory.list().orEmpty().filter { it.startsWith("npia-") }
        assertEquals("staged zips are removed", emptyList<String>(), leftovers)
    }

    @Test
    fun folderArchiverKeepsFiles() {
        val folder = File(directory, "parts").apply { mkdirs() }
        File(folder, "a.txt").writeText("hello")
        File(folder, "sub").mkdirs()
        File(folder, "sub/b.txt").writeText("nested")
        val zip = FolderArchiver.zip(folder, directory)
        assertEquals("parts.zip", zip.name)
        ZipFile(zip).use { archive ->
            assertEquals("hello", archive.getInputStream(archive.getEntry("parts/a.txt")).readBytes().toString(Charsets.UTF_8))
            assertEquals("nested", archive.getInputStream(archive.getEntry("parts/sub/b.txt")).readBytes().toString(Charsets.UTF_8))
            assertNotNull(archive.getEntry("parts/"))
        }
    }
}

class UploadHistoryTest : UploadTestBase() {
    @Test
    fun persistsNewestFirstAndPrunes() {
        val file = File(directory, "h/history.json")
        val store = UploadHistoryStore(file)
        val now = 1_800_000_000_000L
        val old = UploadResult("tempsh", "a.epub", "https://temp.sh/a", expiresAt = now - 1, uploadedAt = now - 10_000)
        val fresh = UploadResult("gdrive", "b.epub", "https://drive.google.com/file/d/b/view", uploadedAt = now)
        store.add(old)
        store.add(fresh)
        store.add(fresh)

        val reloaded = UploadHistoryStore(file)
        assertEquals(listOf("b.epub", "a.epub"), reloaded.all.map { it.fileName })
        assertEquals(now, reloaded.all.first().uploadedAt)
        assertEquals(listOf("b.epub"), reloaded.active(at = now).map { it.fileName })

        reloaded.pruneExpired(at = now)
        assertEquals(listOf("b.epub"), UploadHistoryStore(file).all.map { it.fileName })
        assertEquals(reloaded.all, reloaded.entries.value)
    }

    @Test
    fun limitDropsOldest() {
        val store = UploadHistoryStore(File(directory, "h.json"), limit = 2)
        for (index in 0 until 3) store.add(UploadResult("x", "$index", "https://e.test/$index"))
        assertEquals(listOf("2", "1"), store.all.map { it.fileName })
    }

    @Test
    fun removeAndClear() {
        val store = UploadHistoryStore(File(directory, "h.json"))
        val a = UploadResult("x", "a", "https://e.test/a")
        val b = UploadResult("x", "b", "https://e.test/b")
        store.add(a)
        store.add(b)
        store.remove(a)
        assertEquals(listOf(b), store.all)
        store.clear()
        assertTrue(UploadHistoryStore(File(directory, "h.json")).all.isEmpty())
    }
}
