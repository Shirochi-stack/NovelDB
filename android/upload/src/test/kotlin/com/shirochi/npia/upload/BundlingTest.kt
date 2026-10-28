package com.shirochi.npia.upload

import com.shirochi.npia.core.UploadSettings
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.util.zip.ZipEntry
import java.util.zip.ZipFile
import java.util.zip.ZipInputStream

class BundlingTest : UploadTestBase() {
    private val kb = 1024

    private fun item(name: String, kilobytes: Int, stem: String? = null) = UploadItem(makeFile(name, bytes = kilobytes * kb), stem)

    private fun names(plan: PlannedUpload) = plan.items.map { it.file.name }

    // Packing

    @Test
    fun firstFitDecreasingPacksAsFewZipsAsPossible() {
        // Sizes 6,5,4,3,2,1 KB cost 1 KB more each: 7,6,5,4,3,2. With a 14 KB limit FFD gives
        // [7+6] and [5+4+3+2].
        val items = listOf(
            item("a.epub", 1), item("b.epub", 2), item("c.epub", 3), item("d.epub", 4), item("e.epub", 5), item("f.epub", 6),
        )
        val plan = ZipBundling.plan(items, 14L * kb)
        assertEquals(2, plan.size)
        assertEquals(listOf("e.epub", "f.epub"), names(plan[0]))
        assertEquals(listOf("a.epub", "b.epub", "c.epub", "d.epub"), names(plan[1]))
        assertTrue(plan.all { it is PlannedUpload.Archive })
        assertEquals(listOf("NovelDB_2_EPUBs.zip", "NovelDB_4_EPUBs.zip"), plan.map { (it as PlannedUpload.Archive).name })
    }

    @Test
    fun everyZipStaysWithinTheLimitCountingTheOverhead() {
        val sizes = listOf(9, 7, 7, 6, 5, 5, 4, 3, 3, 2, 2, 1)
        val items = sizes.mapIndexed { i, size -> item("book$i.epub", size) }
        val limit = 16L * kb
        val plan = ZipBundling.plan(items, limit)
        for (unit in plan) {
            val total = unit.items.sumOf { it.file.length() + ZipBundling.ENTRY_OVERHEAD }
            assertTrue("$total over $limit", total <= limit)
        }
        assertEquals(items.map { it.file.name }.sorted(), plan.flatMap { names(it) }.sorted())
    }

    @Test
    fun aFileTooBigForAZipGoesAlone() {
        val items = listOf(item("big.epub", 20), item("a.epub", 2), item("b.epub", 3))
        val plan = ZipBundling.plan(items, 14L * kb)
        assertEquals(2, plan.size)
        assertTrue(plan[0] is PlannedUpload.Single)
        assertEquals("big.epub", plan[0].items[0].file.name)
        assertEquals(listOf("a.epub", "b.epub"), names(plan[1]))
    }

    @Test
    fun aBundleOfOneFileIsNotZipped() {
        // Neither 10 KB file fits with the other under 14 KB, so each would be a ZIP of one.
        val plan = ZipBundling.plan(listOf(item("a.epub", 10), item("b.epub", 10)), 14L * kb)
        assertEquals(2, plan.size)
        assertTrue(plan.all { it is PlannedUpload.Single })
    }

    @Test
    fun oneFileOrBundlingOffKeepsOneUploadPerFile() {
        val one = listOf(item("a.epub", 1))
        assertTrue(ZipBundling.plan(one, 1L shl 30).single() is PlannedUpload.Single)

        val two = listOf(item("a.epub", 1), item("b.epub", 1))
        val plain = context()
        assertEquals(1, UploaderCatalog.plan(two, UploadSettings(), plain).size)
        val off = UploaderCatalog.plan(two, UploadSettings(zipBundle = false), plain)
        assertEquals(2, off.size)
        assertTrue(off.all { it is PlannedUpload.Single })
    }

    @Test
    fun theHostLimitBeatsTheSettingWhenSmaller() {
        val settings = UploadSettings(zipMaxMb = 1024)
        assertEquals(1024L * 1024 * 1024, ZipBundling.limit(settings, null))
        assertEquals(200_000_000L, ZipBundling.limit(settings, 200_000_000L))
        assertEquals(50L * 1024 * 1024, ZipBundling.limit(UploadSettings(zipMaxMb = 50), 4_000_000_000L))
        // A setting outside 50..4096 (an imported file) is brought back into range.
        assertEquals(50L * 1024 * 1024, ZipBundling.limit(UploadSettings(zipMaxMb = 1), null))
        assertTrue(ZipBundling.limit(UploadSettings(zipMaxMb = 99999), null) <= ZipBundling.MAX_ARCHIVE_BYTES)

        // The first usable host decides: Catbox (200 MB) here, Pixeldrain is skipped without a key.
        val hosts = UploadSettings(hosts = listOf("pixeldrain", "catbox", "tempsh"))
        assertEquals(200_000_000L, UploaderCatalog.archiveLimit(hosts, context()))
        val withKey = context(secrets = mapOf(UploadSecrets.PIXELDRAIN_API_KEY to "k"))
        assertEquals(1024L * 1024 * 1024, UploaderCatalog.archiveLimit(hosts, withKey))
        assertEquals(1024L * 1024 * 1024, UploaderCatalog.archiveLimit(UploadSettings(hosts = listOf("gofile")), context()))
    }

    @Test
    fun foldersAreNeverBundled() {
        val folder = File(directory, "Book TXT").apply { mkdirs() }
        File(folder, "1.txt").writeText("one")
        val plan = ZipBundling.plan(listOf(UploadItem(folder), item("a.epub", 1), item("b.epub", 1)), 1L shl 30)
        assertEquals(2, plan.size)
        assertEquals(listOf("a.epub", "b.epub"), names(plan[0]))
        assertTrue(plan[1] is PlannedUpload.Single)
        assertTrue(plan[1].items[0].file.isDirectory)
    }

    // Names

    @Test
    fun zipNamesCountTheEntriesAndTellEpubsFromOtherFiles() {
        assertEquals("NovelDB_3_EPUBs", ZipBundling.baseName(listOf("a.epub", "b.EPUB", "c.epub")))
        assertEquals("NovelDB_2_files", ZipBundling.baseName(listOf("a.epub", "a.txt")))
        assertEquals("NovelDB_4_files", ZipBundling.baseName(listOf("a.pdf", "b.pdf", "c.cbz", "d.zip")))
        assertEquals("3 books", ZipBundling.describe(listOf("a.epub", "b.epub", "c.epub")))
        assertEquals("2 files", ZipBundling.describe(listOf("a.epub", "a.txt")))
    }

    @Test
    fun repeatedZipNamesGetSuffixes() {
        val names = ZipBundling.ArchiveNames()
        val four = listOf("a.epub", "b.epub", "c.epub", "d.epub")
        assertEquals("NovelDB_4_EPUBs.zip", names.next(four))
        assertEquals("NovelDB_3_EPUBs.zip", names.next(four.take(3)))
        assertEquals("NovelDB_4_EPUBs_2.zip", names.next(four))
        assertEquals("NovelDB_4_EPUBs_3.zip", names.next(four))
        assertEquals("NovelDB_4_files.zip", names.next(listOf("a.txt", "b.epub", "c.epub", "d.epub")))
    }

    @Test
    fun planGivesTheRepeatedNamesSuffixes() {
        // Four 6 KB books at a 14 KB limit: two ZIPs of two.
        val items = (1..4).map { item("book$it.epub", 6) }
        val plan = ZipBundling.plan(items, 14L * kb)
        assertEquals(listOf("NovelDB_2_EPUBs.zip", "NovelDB_2_EPUBs_2.zip"), plan.map { (it as PlannedUpload.Archive).name })
    }

    // The ZIP itself

    private fun zipHeaderFlags(zip: File): List<Int> {
        // Flags of every local file header (signature PK\u0003\u0004): the general-purpose bits at offset 6.
        val bytes = zip.readBytes()
        val flags = mutableListOf<Int>()
        var i = 0
        while (i + 8 < bytes.size) {
            if (bytes[i] == 0x50.toByte() && bytes[i + 1] == 0x4B.toByte() && bytes[i + 2] == 3.toByte() && bytes[i + 3] == 4.toByte()) {
                flags += (bytes[i + 6].toInt() and 0xff) or ((bytes[i + 7].toInt() and 0xff) shl 8)
            }
            i++
        }
        return flags
    }

    @Test
    fun aRealZipKeepsKoreanNamesAndEveryByte() {
        val a = UploadItem(makeFile("재앙급 진조는 방구석에서 꿀 빱니다.epub", bytes = 5_000))
        val b = UploadItem(makeFile("소설 1권.cbz", bytes = 3_000))
        val c = UploadItem(File(directory, "대본.txt").apply { writeText("가나다 ".repeat(2_000)) })
        val d = UploadItem(File(directory, "페이지.html").apply { writeText("<p>가나다</p>".repeat(500)) })
        val e = UploadItem(makeFile("book.pdf", bytes = 2_000))
        val f = UploadItem(makeFile("images.zip", bytes = 1_000))
        val items = listOf(a, b, c, d, e, f)
        val target = File(directory, "out/NovelDB_6_files.zip")

        val written = ZipBundling.write(items, target)

        assertEquals(items.map { it.file.name }, written)
        ZipFile(target).use { zip ->
            assertEquals(items.map { it.file.name }, zip.entries().toList().map { it.name })
            for (item in items) {
                val entry = zip.getEntry(item.file.name)
                assertArrayEquals(item.file.readBytes(), zip.getInputStream(entry).readBytes())
                assertEquals(item.file.length(), entry.size)
                val stored = item.file.extension in setOf("epub", "cbz", "pdf", "zip")
                assertEquals(item.file.name, if (stored) ZipEntry.STORED else ZipEntry.DEFLATED, entry.method)
            }
            // Text compresses; stored entries take exactly their size.
            assertTrue(zip.getEntry("대본.txt").compressedSize < zip.getEntry("대본.txt").size / 2)
            assertEquals(zip.getEntry("book.pdf").size, zip.getEntry("book.pdf").compressedSize)
        }
        // The UTF-8 name flag (bit 11) is set on every entry, so readers show Hangul correctly.
        val flags = zipHeaderFlags(target)
        assertEquals(items.size, flags.size)
        assertTrue(flags.toString(), flags.all { it and 0x800 != 0 })
        // No folders.
        ZipFile(target).use { zip -> assertTrue(zip.entries().toList().none { it.isDirectory || '/' in it.name }) }
    }

    @Test
    fun zipEntriesAreNfcAndDuplicatesKeepBothFiles() {
        val decomposed = String(intArrayOf(0x1112, 0x1161, 0x11AB), 0, 3) + ".epub"
        val composed = String(intArrayOf(0xD55C), 0, 1) + ".epub"
        val one = File(directory, "one").apply { mkdirs() }
        val two = File(directory, "two").apply { mkdirs() }
        val first = File(one, "Same.epub").apply { writeBytes(ByteArray(10) { 1 }) }
        val second = File(two, "Same.epub").apply { writeBytes(ByteArray(10) { 2 }) }
        val hangul = File(one, decomposed).apply { writeBytes(ByteArray(10) { 3 }) }
        val written = ZipBundling.write(listOf(UploadItem(first), UploadItem(second), UploadItem(hangul)), File(directory, "z.zip"))
        assertEquals(listOf("Same.epub", "Same (2).epub", composed), written)
        ZipFile(File(directory, "z.zip")).use { zip ->
            assertEquals(2, zip.getInputStream(zip.getEntry("Same (2).epub")).readBytes()[0].toInt())
        }
    }

    // Through the catalog, against the mock server

    private fun linkHandler(): (UploadTestBase.Seen) -> okhttp3.mockwebserver.MockResponse = { request ->
        text("https://temp.sh/abc${seen.size}/file")
    }

    /** The bytes of the file part of a multipart request. */
    private fun filePart(request: UploadTestBase.Seen): Pair<String, ByteArray> {
        val body = request.body
        val marker = "\r\n\r\n".toByteArray()
        val header = request.text.substringAfter("filename=\"").substringBefore("\"")
        var start = -1
        for (i in 0 until body.size - 3) {
            if (body[i] == marker[0] && body[i + 1] == marker[1] && body[i + 2] == marker[2] && body[i + 3] == marker[3] &&
                String(body, 0, i, Charsets.ISO_8859_1).contains("filename=")
            ) {
                start = i + 4
                break
            }
        }
        val boundary = request.header("Content-Type")!!.substringAfter("boundary=")
        val tail = "\r\n--$boundary--\r\n".toByteArray().size
        return header to body.copyOfRange(start, body.size - tail)
    }

    private class Recorder : UploadListener {
        val events = mutableListOf<String>()
        override fun onStart(position: Int, total: Int, upload: PlannedUpload, name: String) {
            events += "start $position/$total $name"
        }
        override fun onPreparing(index: Int, total: Int) {
            events += "prepare $index/$total"
        }
        override fun onHost(name: String) {
            events += "host $name"
        }
        override fun onFinished(position: Int, outcome: UploadOutcome) {
            events += "done $position ${outcome.succeeded}"
        }
    }

    @Test
    fun uploadAllSendsZipsWithOriginalNamesAndCleansUp() = runBlocking<Unit> {
        handler = linkHandler()
        val titles = listOf("재앙급 진조.epub", "소설 하나.epub", "세번째 책.epub", "네번째.epub", "다섯 번째.epub")
        // 5 books of about 6 KB at a limit just over 14 KB: ZIPs of 2, 2 and a lone file (the smallest).
        val items = titles.mapIndexed { i, t -> UploadItem(makeFile(t, bytes = 6 * kb + i), "npia_${100 + i}") }
        val settings = UploadSettings(hosts = listOf("tempsh"))
        val staging = File(directory, "staging").apply { mkdirs() }
        val ctx = context(settings = settings).copy(temporaryDirectory = staging)
        val plan = ZipBundling.plan(items, 14L * kb + 100)
        assertEquals(listOf(2, 2, 1), plan.map { it.items.size })
        assertEquals(titles[0], plan[2].items[0].file.name)

        val recorder = Recorder()
        val history = UploadHistoryStore(File(directory, "history.json"))
        val outcomes = UploaderCatalog.uploadAll(plan, settings, ctx, history, recorder)

        assertEquals(3, outcomes.size)
        assertTrue(outcomes.all { it.succeeded })
        assertEquals(3, seen.size)
        // The ZIPs: ASCII names on the wire, the Korean entry names inside.
        val (name1, bytes1) = filePart(seen[0])
        assertEquals("NovelDB_2_EPUBs.zip", name1)
        val entries = ZipInputStream(bytes1.inputStream()).use { zip -> generateSequence { zip.nextEntry }.map { it.name }.toList() }
        assertEquals(plan[0].items.map { it.file.name }, entries)
        val (name2, _) = filePart(seen[1])
        assertEquals("NovelDB_2_EPUBs_2.zip", name2)
        // The lone file goes up as itself, under the ASCII name made from its stem.
        val (name3, bytes3) = filePart(seen[2])
        assertEquals("npia_100.epub", name3)
        assertArrayEquals(plan[2].items[0].file.readBytes(), bytes3)

        // Results: ZIP links list what they hold; the lone file does not.
        assertEquals("NovelDB_2_EPUBs.zip (2 books)", outcomes[0].result!!.label)
        assertEquals(plan[0].items.map { it.file.name }, outcomes[0].result!!.contents)
        assertEquals(titles[0], outcomes[2].result!!.label)
        assertEquals(3, history.all.size)
        assertEquals(2, history.all.count { it.isArchive })

        assertEquals(
            listOf(
                "start 1/3 NovelDB_2_EPUBs.zip", "prepare 1/2", "host temp.sh", "done 1 true",
                "start 2/3 NovelDB_2_EPUBs_2.zip", "prepare 2/2", "host temp.sh", "done 2 true",
                "start 3/3 ${titles[0]}", "host temp.sh", "done 3 true",
            ),
            recorder.events,
        )
        // Nothing is left in the staging folder.
        assertEquals(emptyList<String>(), staging.listFiles()!!.map { it.name })
    }

    @Test
    fun aFailedZipIsReportedAndTheRestStillGoUp() = runBlocking<Unit> {
        var calls = 0
        handler = { if (calls++ == 0) text("nope", status = 503) else text("https://temp.sh/x/file") }
        val items = (1..4).map { UploadItem(makeFile("book$it.epub", bytes = 6 * kb)) }
        val settings = UploadSettings(hosts = listOf("tempsh"))
        val staging = File(directory, "staging").apply { mkdirs() }
        val plan = ZipBundling.plan(items, 14L * kb)
        val outcomes = UploaderCatalog.uploadAll(plan, settings, context(settings = settings).copy(temporaryDirectory = staging), null)
        assertEquals(listOf(false, true), outcomes.map { it.succeeded })
        assertTrue(outcomes[0].error!!, outcomes[0].error!!.contains("503"))
        assertNull(outcomes[0].result)
        assertEquals(emptyList<String>(), staging.listFiles()!!.map { it.name })
    }

    @Test
    fun theFirstHostDecidesWhetherFilesAreBundled() = runBlocking<Unit> {
        // The planner looks at the first host only: Gofile has no maximum, so 3 files become one ZIP.
        handler = { text("""{"status":"ok","data":{"downloadPage":"https://gofile.io/d/abc"}}""") }
        val items = (1..3).map { UploadItem(makeFile("b$it.epub", bytes = 2 * kb)) }
        val settings = UploadSettings(hosts = listOf("gofile", "tmpfiles"))
        val ctx = context(settings = settings)
        val plan = UploaderCatalog.plan(items, settings, ctx)
        assertEquals(1, plan.size)
        val outcomes = UploaderCatalog.uploadAll(plan, settings, ctx, null)
        assertEquals("NovelDB_3_EPUBs.zip", outcomes.single().result!!.fileName)
        assertFalse(outcomes.single().result!!.contents.isEmpty())
    }
}
