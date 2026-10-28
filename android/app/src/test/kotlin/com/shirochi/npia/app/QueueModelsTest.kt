package com.shirochi.npia.app

import com.shirochi.npia.app.model.JobItem
import com.shirochi.npia.app.model.JobState
import com.shirochi.npia.app.model.LogEntry
import com.shirochi.npia.app.model.LogFilter
import com.shirochi.npia.app.model.LogLines
import com.shirochi.npia.app.model.NotificationTarget
import com.shirochi.npia.app.model.QueueLog
import com.shirochi.npia.app.model.QueueSelection
import com.shirochi.npia.app.model.RunSummary
import com.shirochi.npia.app.model.UploadBatch
import com.shirochi.npia.app.model.UploadNaming
import com.shirochi.npia.app.model.UploadReport
import com.shirochi.npia.app.model.UploadState
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.upload.UploadResult
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import java.io.File
import java.util.TimeZone

class QueueLogTest {
    @get:Rule
    val tmp = TemporaryFolder()

    private fun entry(n: Int, job: String? = "a", level: LogLevel = LogLevel.INFO, text: String = "line $n") =
        LogEntry(time = 1_000L * n, jobId = job, job = "Book $job", level = level, message = text)

    @Test
    fun linesComeBackInTheOrderTheyWereAppended() {
        val log = QueueLog(null)
        // Two jobs writing alternately, as parallel downloads do.
        listOf("a", "b", "a", "b", "b", "a").forEachIndexed { i, job -> log.append(entry(i, job)) }
        val lines = log.snapshot()
        assertEquals(listOf("line 0", "line 1", "line 2", "line 3", "line 4", "line 5"), lines.map { it.message })
        assertEquals(listOf("a", "b", "a", "b", "b", "a"), lines.map { it.jobId })
        assertEquals(lines.map { it.seq }, lines.map { it.seq }.sorted())
        assertEquals(6, lines.map { it.seq }.toSet().size)
    }

    @Test
    fun theBufferKeepsOnlyTheNewestLines() {
        val log = QueueLog(null, capacity = 5)
        repeat(12) { log.append(entry(it)) }
        assertEquals(5, log.size)
        assertEquals((7..11).map { "line $it" }, log.snapshot().map { it.message })
        assertEquals(QueueLog.DEFAULT_CAPACITY, 5000)
    }

    @Test
    fun theVersionMovesWithEveryChange() {
        val log = QueueLog(null)
        val start = log.version.value
        log.append(entry(1))
        log.append(entry(2))
        assertEquals(start + 2, log.version.value)
        log.clear()
        assertEquals(start + 3, log.version.value)
    }

    @Test
    fun theLogSurvivesARestart() {
        val file = File(tmp.root, "queue-log.jsonl")
        val log = QueueLog(file, capacity = 50)
        val written = listOf(
            entry(1, "a", text = "재앙급 진조는 방구석에서 꿀 빱니다: Chapter 1"),
            entry(2, "b", LogLevel.WARNING, "HTTP 503 from example.com/view/2; retry 1 of 10 in 1.2 s"),
            entry(3, null, LogLevel.ERROR, "two\nlines\twith \"quotes\" and \\ slashes"),
            entry(4, "a", LogLevel.DEBUG, "details"),
        )
        written.forEach(log::append)
        log.flush()

        val reopened = QueueLog(file, capacity = 50)
        assertEquals(written.map { it.copy(seq = 0) }, reopened.snapshot().map { it.copy(seq = 0) })
        // New lines continue after the restored ones.
        reopened.append(entry(5))
        reopened.flush()
        assertEquals(5, QueueLog(file, capacity = 50).size)
    }

    @Test
    fun theFileIsTrimmedToTheCapAndNeverGrowsWithoutBound() {
        val file = File(tmp.root, "queue-log.jsonl")
        val log = QueueLog(file, capacity = 5)
        repeat(100) {
            log.append(entry(it))
            log.flush()
            assertTrue("${file.readLines().size} lines", file.readLines().size <= 10)
        }
        val reopened = QueueLog(file, capacity = 5)
        assertEquals((95..99).map { "line $it" }, reopened.snapshot().map { it.message })

        // A file written with a bigger cap is cut to the new one on the next write.
        val big = File(tmp.root, "big.jsonl")
        val writer = QueueLog(big, capacity = 50)
        repeat(30) { writer.append(entry(it)) }
        writer.flush()
        val small = QueueLog(big, capacity = 10)
        assertEquals(10, small.size)
        small.append(entry(30))
        small.flush()
        assertEquals(10, big.readLines().size)
    }

    @Test
    fun clearingRemovesLinesFromTheFileToo() {
        val file = File(tmp.root, "queue-log.jsonl")
        val log = QueueLog(file, capacity = 50)
        listOf("a", "b", "a", "b").forEachIndexed { i, job -> log.append(entry(i, job)) }
        log.flush()
        log.clear("a")
        log.flush()
        assertEquals(listOf("b", "b"), QueueLog(file, capacity = 50).snapshot().map { it.jobId })
        log.clear()
        log.flush()
        assertEquals(0, QueueLog(file, capacity = 50).size)
    }

    @Test
    fun aDamagedLineDoesNotLoseTheRest() {
        val file = File(tmp.root, "queue-log.jsonl")
        val log = QueueLog(file, capacity = 50)
        log.append(entry(1))
        log.flush()
        file.appendText("{not json\n")
        file.appendText("\n")
        val again = QueueLog(file, capacity = 50)
        again.append(entry(2))
        again.flush()
        assertEquals(listOf("line 1", "line 2"), QueueLog(file, capacity = 50).snapshot().map { it.message })
    }

    @Test
    fun theFilterPicksAJobAndHidesDetailsOnRequest() {
        val all = listOf(
            entry(1, "a"), entry(2, "b", LogLevel.DEBUG), entry(3, "a", LogLevel.DEBUG), entry(4, "b", LogLevel.ERROR), entry(5, null, LogLevel.INFO),
        )
        assertEquals(5, LogFilter.apply(all).size)
        assertEquals(listOf(1, 4, 5), LogFilter.apply(all, details = false).map { (it.time / 1000).toInt() })
        assertEquals(listOf(1, 3), LogFilter.apply(all, "a").map { (it.time / 1000).toInt() })
        assertEquals(listOf(4), LogFilter.apply(all, "b", details = false).map { (it.time / 1000).toInt() })
        assertEquals(emptyList<LogEntry>(), LogFilter.apply(all, "missing"))
    }

    @Test
    fun linesReadAsTimeLevelJobAndMessage() {
        val utc = TimeZone.getTimeZone("UTC")
        val line = LogEntry(time = 3_661_000L, jobId = "a", job = "Old title", level = LogLevel.WARNING, message = "Chapter 4 failed: HTTP 503")
        assertEquals("01:01:01 WRN Tower of God: Chapter 4 failed: HTTP 503", LogLines.format(line, mapOf("a" to "Tower of God"), utc))
        // A job that is gone keeps the title written with the line; a line of no job reads "queue".
        assertEquals("01:01:01 WRN Old title: Chapter 4 failed: HTTP 503", LogLines.format(line, emptyMap(), utc))
        assertTrue(LogLines.format(line.copy(jobId = null, job = ""), emptyMap(), utc).contains(" queue: "))
        assertEquals("A very long book…", LogLines.shortTitle("A very long book title that goes on", 17))
        assertEquals("a b", LogLines.shortTitle("  a \n b  "))
        // A job without a title yet shows its address without the scheme, keeping the end that tells books apart.
        val one = LogLines.shortTitle("http://10.0.2.2:8765/novel/1")
        val two = LogLines.shortTitle("http://10.0.2.2:8765/novel/2")
        assertEquals(18, one.length)
        assertTrue(one, one.startsWith("…") && one.endsWith("novel/1"))
        assertTrue(one != two)
        assertEquals("example.com/book/7", LogLines.shortTitle("https://example.com/book/7/"))
        val text = LogLines.text(listOf(line, line.copy(level = LogLevel.ERROR)), emptyMap(), utc)
        assertEquals(2, text.lines().size)
    }
}

class QueueNotificationTest {
    private fun job(id: String, state: JobState = JobState.FINISHED, vararg files: String) =
        JobItem(id = id, url = "https://example.com/$id", title = id, state = state, fileNames = files.toList())

    @Test
    fun severalBooksOpenTheLogAndOneBookOpensItsPage() {
        assertEquals(NotificationTarget.Queue, NotificationTarget.forRun(emptyList()))
        assertEquals(NotificationTarget.Job("a"), NotificationTarget.forRun(listOf("a")))
        assertEquals(NotificationTarget.Job("a"), NotificationTarget.forRun(listOf("a", "a")))
        assertEquals(NotificationTarget.QueueLog, NotificationTarget.forRun(listOf("a", "b")))
        assertEquals(NotificationTarget.QueueLog, NotificationTarget.forRun(setOf("a", "b", "c")))
    }

    @Test
    fun theFinishedNotificationFollowsTheNumberOfBooksInTheRun() {
        // One book with two output formats is still one book.
        val one = RunSummary.of(listOf(job("a", JobState.FINISHED, "A.epub", "A.txt")))!!
        assertEquals(listOf("a"), one.jobIds)
        assertEquals(NotificationTarget.Job("a"), NotificationTarget.forRun(one.jobIds))

        val two = RunSummary.of(listOf(job("a", JobState.FINISHED, "A.epub"), job("b", JobState.FINISHED, "B.epub")))!!
        assertEquals(NotificationTarget.QueueLog, NotificationTarget.forRun(two.jobIds))

        // A failed book counts: a run of one finished and one failed book opens the log with both.
        val mixed = RunSummary.of(listOf(job("a", JobState.FINISHED, "A.epub"), job("b", JobState.FAILED)))!!
        assertEquals(listOf("a", "b"), mixed.jobIds)
        assertEquals(NotificationTarget.QueueLog, NotificationTarget.forRun(mixed.jobIds))

        // A lone failed book opens its page, where the error is.
        val failed = RunSummary.of(listOf(job("x", JobState.FAILED)))!!
        assertEquals(NotificationTarget.Job("x"), NotificationTarget.forRun(failed.jobIds))

        // Summaries saved by older versions have no job ids and open the queue.
        assertEquals(NotificationTarget.Queue, NotificationTarget.forRun(RunSummary().jobIds))
    }

    @Test
    fun theSummaryKeepsItsJobIdsThroughTheFile() {
        val summary = RunSummary.of(listOf(job("a", JobState.FINISHED, "A.epub"), job("b", JobState.FINISHED, "B.epub")))!!
        val back = JobItem.json.decodeFromString(RunSummary.serializer(), JobItem.json.encodeToString(RunSummary.serializer(), summary))
        assertEquals(listOf("a", "b"), back.jobIds)
    }
}

class QueueSelectionTest {
    @get:Rule
    val tmp = TemporaryFolder()

    private fun job(id: String, state: JobState, vararg files: String): JobItem {
        files.forEach { File(tmp.root, it).writeText("x") }
        return JobItem(id = id, url = "https://example.com/$id", title = id, state = state, fileNames = files.toList())
    }

    @Test
    fun onlyFinishedJobsWithFilesCanBeSelected() {
        val jobs = listOf(
            job("a", JobState.FINISHED, "a.epub"),
            job("b", JobState.RUNNING),
            job("c", JobState.FAILED, "c.epub"),
            JobItem(id = "d", url = "u", state = JobState.FINISHED, fileNames = listOf("missing.epub")),
            job("e", JobState.FINISHED, "e.epub", "e.txt"),
        )
        assertEquals(listOf("a", "e"), QueueSelection.selectable(jobs, tmp.root).map { it.id })
    }

    @Test
    fun selectionTogglesSelectsAllAndDropsWhatNoLongerQualifies() {
        var selection = QueueSelection()
        assertFalse(selection.active)
        selection = selection.begin("a")
        assertTrue(selection.active)
        assertEquals(setOf("a"), selection.ids)
        selection = selection.toggle("b").toggle("a")
        assertEquals(setOf("b"), selection.ids)
        // Unticking the last one keeps the mode, so the next tap can tick again.
        selection = selection.toggle("b")
        assertTrue(selection.active)
        assertEquals(0, selection.count)
        selection = selection.selectAll(listOf("a", "b", "c"))
        assertEquals(3, selection.count)
        assertEquals(setOf("a", "c"), selection.pruned(listOf("a", "c", "z")).ids)
        assertEquals(QueueSelection(), selection.clear())
        assertTrue(QueueSelection().begin().ids.isEmpty())
    }

    @Test
    fun theSelectedFilesAreAllOutputsInQueueOrder() {
        val jobs = listOf(job("a", JobState.FINISHED, "a.epub", "a.txt"), job("b", JobState.FINISHED, "b.epub"), job("c", JobState.FINISHED, "c.epub"))
        val files = QueueSelection.files(jobs, QueueSelection(true, setOf("c", "a")), tmp.root)
        assertEquals(listOf("a.epub", "a.txt", "c.epub"), files.map { it.name })
    }
}

class UploadReportTest {
    private fun single(name: String) = UploadResult("tempsh", name, "https://temp.sh/x/$name")
    private fun zip(name: String, vararg books: String) = UploadResult("tempsh", name, "https://temp.sh/z/$name", contents = books.toList())

    @Test
    fun plainLinksStayPlainLinks() {
        val report = UploadReport(uploads = listOf(single("a.epub"), single("b.epub")), files = 2)
        assertEquals("Uploaded 2 files", report.notificationTitle)
        assertEquals("2 links ready", report.notificationText)
        assertEquals("https://temp.sh/x/a.epub\nhttps://temp.sh/x/b.epub", report.shareText)
        assertEquals("Uploaded 1 file", UploadReport(uploads = listOf(single("a.epub")), files = 1).notificationTitle)
        assertEquals("https://temp.sh/x/a.epub", UploadReport(uploads = listOf(single("a.epub")), files = 1).notificationText)
    }

    @Test
    fun zipLinksListWhatTheyHold() {
        val report = UploadReport(
            uploads = listOf(
                zip("NovelDB_3_EPUBs.zip", "재앙급.epub", "소설.epub", "책.epub"),
                zip("NovelDB_2_files.zip", "a.epub", "a.txt"),
                single("lone.epub"),
            ),
            files = 6,
        )
        assertEquals("Uploaded 6 files in 3 links", report.notificationTitle)
        assertEquals("3 links ready", report.notificationText)
        assertEquals(
            "NovelDB_3_EPUBs.zip (3 books)\nhttps://temp.sh/z/NovelDB_3_EPUBs.zip\n\n" +
                "NovelDB_2_files.zip (2 files)\nhttps://temp.sh/z/NovelDB_2_files.zip\n\n" +
                "lone.epub\nhttps://temp.sh/x/lone.epub",
            report.shareText,
        )
        assertEquals(3, report.links.size)
        assertEquals(6, report.uploadedFiles)
    }

    @Test
    fun failuresAreCountedAndListed() {
        val partial = UploadReport(
            uploads = listOf(zip("NovelDB_2_EPUBs.zip", "a.epub", "b.epub")),
            failures = listOf("NovelDB_3_EPUBs_2.zip: Gofile rejected the upload: error"),
            files = 5,
        )
        assertEquals("Uploaded 2 of 5 files", partial.notificationTitle)
        assertEquals("NovelDB_3_EPUBs_2.zip: Gofile rejected the upload: error", partial.notificationText)
        assertTrue(partial.details.endsWith("Gofile rejected the upload: error"))
        val none = UploadReport(failures = listOf("x: failed"), files = 2)
        assertEquals("Upload failed", none.notificationTitle)
    }

    @Test
    fun resultsKeepTheirContentsThroughTheHistoryFile() {
        val result = zip("NovelDB_2_EPUBs.zip", "a.epub", "b.epub")
        val json = JobItem.json.encodeToString(UploadReport.serializer(), UploadReport(uploads = listOf(result), files = 2))
        assertEquals(listOf("a.epub", "b.epub"), JobItem.json.decodeFromString(UploadReport.serializer(), json).uploads.single().contents)
        // History written before ZIP bundling has no contents.
        val old = """{"hostId":"tempsh","fileName":"a.epub","link":"https://temp.sh/x/a.epub","uploadedAt":1}"""
        val decoded = JobItem.json.decodeFromString(UploadResult.serializer(), old)
        assertFalse(decoded.isArchive)
        assertEquals("a.epub", decoded.label)
    }
}

class UploadProgressModelTest {
    @Test
    fun batchProgressNamesZipsAndPreparing() {
        val current = UploadState("zip|b|1", "NovelDB_3_EPUBs.zip", sentBytes = 85, totalBytes = 100)
        assertEquals("Uploading 2/3 ZIPs… 62%", UploadBatch("b", total = 3, done = 1, zips = 3).statusLine(current))
        assertEquals("Uploading 2/3 uploads… 62%", UploadBatch("b", total = 3, done = 1, zips = 1).statusLine(current))
        assertEquals("Uploading 2/3 files… 62%", UploadBatch("b", total = 3, done = 1).statusLine(current))
        assertEquals("Preparing ZIP 1 of 3…", UploadBatch("b", 3, 0, preparing = "Preparing ZIP 1 of 3", zips = 3).statusLine(null))
        val preparing = UploadState("zip|b|1", "NovelDB_3_EPUBs.zip", preparing = "Preparing ZIP 1 of 3")
        assertEquals("Preparing ZIP 1 of 3…", preparing.statusLine)
        assertEquals("Uploading to Gofile…", preparing.copy(host = "Gofile", preparing = null).statusLine)
    }

    @Test
    fun aStateCoversTheFilesItCarries() {
        assertEquals(listOf("/out/a.epub"), UploadState("/out/a.epub", "a.epub").paths)
        val zip = UploadState("zip|b|1", "NovelDB_2_EPUBs.zip", paths = listOf("/out/a.epub", "/out/b.epub"), contents = listOf("a.epub", "b.epub"))
        assertTrue("/out/b.epub" in zip.paths)
    }
}

class UploadStemTest {
    @Test
    fun stemsCarryTheSourceCodename() {
        assertEquals("npia_419368", UploadNaming.stem("npia", "419368"))
        assertEquals("419368", UploadNaming.stem(null, "419368"))
        val job = JobItem(url = "https://example.com/novel/9", source = SourceId.NPIA, bookId = "419368", state = JobState.FINISHED, fileNames = listOf("Tower.epub"))
        assertEquals("npia_419368", UploadNaming.forJob(job))
        assertEquals(mapOf("Tower.epub" to "npia_419368"), RunSummary.of(listOf(job))!!.fileIds)
        assertEquals("kpage_9", UploadNaming.forJob(job.copy(source = SourceId.KPAGE, bookId = null)))
    }
}

class JobLogCapTest {
    @Test
    fun aLongLogKeepsHowTheRunStarted() {
        var job = JobItem(url = "u")
        repeat(JobItem.MAX_LOG_LINES + 300) { job = job.appendingLog("line $it") }
        assertEquals(JobItem.MAX_LOG_LINES, job.log.size)
        // The first lines (source, engine attempts, first errors) stay; the middle is what goes.
        assertEquals((0 until JobItem.KEEP_HEAD_LINES).map { "line $it" }, job.log.take(JobItem.KEEP_HEAD_LINES))
        assertEquals("line ${JobItem.MAX_LOG_LINES + 299}", job.log.last())
        assertEquals("line ${JobItem.MAX_LOG_LINES + 300 - (JobItem.MAX_LOG_LINES - JobItem.KEEP_HEAD_LINES)}", job.log[JobItem.KEEP_HEAD_LINES])
    }
}
