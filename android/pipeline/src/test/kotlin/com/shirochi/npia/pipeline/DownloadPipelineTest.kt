package com.shirochi.npia.pipeline

import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.UploadSettings
import com.shirochi.npia.upload.UploadContext
import com.shirochi.npia.upload.UploadProgress
import com.shirochi.npia.upload.UploadResult
import com.shirochi.npia.upload.Uploader
import com.shirochi.npia.upload.UploaderCatalog
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SharedOverrides
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.output.Writers
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.launch
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.withTimeout
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files
import java.util.concurrent.atomic.AtomicInteger
import java.util.zip.ZipFile

class DownloadPipelineTest {
    /** PNG signature plus filler; enough for extension sniffing. */
    private val PNG = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A) + ByteArray(32) { 7 }

    private val chapters = (1..6).map { ChapterRef("$it", it, "C$it", isPaid = it > 4) }

    @Test
    fun selectAppliesRangeAndPaidFilter() {
        assertEquals(listOf(2, 3, 4, 5), DownloadPipeline.select(chapters, SharedSettings(rangeFrom = 2, rangeTo = 5)).map { it.index })
        assertEquals(listOf(1, 2, 3, 4), DownloadPipeline.select(chapters, SharedSettings(skipPaid = true)).map { it.index })
    }

    @Test
    fun aFromAfterToKeepsJustTheFromChapter() {
        assertEquals(listOf(4), DownloadPipeline.select(chapters, SharedSettings(rangeFrom = 4, rangeTo = 2)).map { it.index })
    }

    @Test
    fun unknownUrlFailsFast() = runTest {
        // With the script engines off, no engine can try an unknown site: no page is loaded, nothing is fetched.
        val settings = AppSettings(engines = EngineSettings(disabled = listOf("novel_downloader", "webtoepub", "lncrawl", "lnreader", "fanficfare", "mangapeak", "tako", "haruneko", "gallerydl", "custom", "generic")))
        val pipeline = DownloadPipeline(InMemorySecretStore(), File("build/tmp"), httpFactory = { error("no network for an unsupported site") })
        val error = runCatching { pipeline.run("https://example.com/none", settings) }.exceptionOrNull()
        assertEquals(SourceException.UnsupportedUrl::class, error!!::class)
    }

    @Test
    fun unknownUrlIsTriedByEveryScriptEngineBeforeItIsGivenUp() = runTest {
        // WebToEpub also recognises some sites by their page, so a site no list names still gets a look.
        val page = W2ePage(book = """{"error":"WebToEpub has no parser for this site","noParser":true}""")
        val pipeline = DownloadPipeline(InMemorySecretStore(), File("build/tmp"), web = WebOf(page), httpFactory = { Images(emptyMap()) })
        val error = runCatching { pipeline.run("https://example.com/none", AppSettings()) }.exceptionOrNull()
        assertEquals(SourceException.UnsupportedUrl::class, error!!::class)
        assertEquals(listOf("https://example.com/none"), page.loads)
        assertTrue("the page nobody kept is closed", page.closed)
    }

    @Test
    fun aWebToEpubBookIsDownloadedWithItsInlineImages() = runTest {
        val url = "https://novelfull.com/the-legendary-mechanic.html"
        val page = W2ePage(
            book = """{"api":1,"engine":"webtoepub","parser":"NovelFullParser","match":"site","bookUrl":"$url","bookname":"The Legendary Mechanic",
                "author":"Writer","introduction":"","coverUrl":null,"tags":[],"language":"en","chapterCount":2,
                "chapters":[{"url":"https://novelfull.com/the-legendary-mechanic/chapter-1.html","name":"Chapter 1","number":1,"isVIP":false,"isPaid":false},
                            {"url":"https://novelfull.com/the-legendary-mechanic/chapter-2.html","name":"Chapter 2","number":2,"isVIP":false,"isPaid":false}],
                "warnings":[]}""",
            chapters = mapOf(
                "chapter-1" to """{"chapterName":"Chapter 1","contentHtml":"<p>One.</p><p><img src=\"https://img.example/a.png\"></p>","contentText":"One.","imageCount":0,"images":[]}""",
                "chapter-2" to """{"chapterName":"Chapter 2","contentHtml":"<p>Two.</p>","contentText":"Two.","imageCount":0,"images":[]}""",
            ),
        )
        val http = Images(mapOf("https://img.example/a.png" to PNG))
        val output = Files.createTempDirectory("w2e-out").toFile()
        try {
            val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0))
            val outcome = DownloadPipeline(InMemorySecretStore(), output, web = WebOf(page), httpFactory = { http }).run(url, settings)

            assertEquals("The Legendary Mechanic", outcome.book.title)
            assertEquals(2, outcome.book.chapters.size)
            assertEquals(emptyList<Any>(), outcome.skipped)
            // The inline image is fetched by the pipeline's own image stage, like novel-downloader's images.
            assertEquals(listOf("https://img.example/a.png"), http.requests)
            val epub = outcome.files.single { it.extension == "epub" }
            val entries = java.util.zip.ZipFile(epub).use { zip -> zip.entries().toList().map { it.name } }
            assertTrue(entries.toString(), entries.any { it.contains("images/") && it.endsWith(".png") })
        } finally {
            output.deleteRecursively()
        }
    }

    @Test
    fun aChaptersImagesStartDownloadingBeforeTheNextChapterIsRead() = runTest {
        val url = "https://novelfull.com/the-legendary-mechanic.html"
        val imageAsked = CompletableDeferred<Unit>()
        val http = object : HttpFetching {
            val requests = java.util.Collections.synchronizedList(mutableListOf<String>())
            override suspend fun send(request: HttpRequest): HttpResponse {
                requests += request.url
                if (request.url == "https://img.example/a.png") imageAsked.complete(Unit)
                return HttpResponse(request.url, 200, emptyMap(), PNG)
            }
        }
        val page = W2ePage(
            book = """{"api":1,"engine":"webtoepub","parser":"NovelFullParser","match":"site","bookUrl":"$url","bookname":"The Legendary Mechanic",
                "author":"Writer","introduction":"","coverUrl":null,"tags":[],"language":"en","chapterCount":2,
                "chapters":[{"url":"https://novelfull.com/the-legendary-mechanic/chapter-1.html","name":"Chapter 1","number":1,"isVIP":false,"isPaid":false},
                            {"url":"https://novelfull.com/the-legendary-mechanic/chapter-2.html","name":"Chapter 2","number":2,"isVIP":false,"isPaid":false}],
                "warnings":[]}""",
            chapters = mapOf(
                "chapter-1" to """{"chapterName":"Chapter 1","contentHtml":"<p>One.</p><p><img src=\"https://img.example/a.png\"></p>","contentText":"One.","imageCount":0,"images":[]}""",
                "chapter-2" to """{"chapterName":"Chapter 2","contentHtml":"<p>Two.</p>","contentText":"Two.","imageCount":0,"images":[]}""",
            ),
            // The second chapter is not read until the first chapter's image was asked for: an image step
            // that waits for the last chapter would never get there.
            beforeChapter = { body -> if (body.contains("chapter-2")) withTimeout(10_000) { imageAsked.await() } },
        )
        val output = Files.createTempDirectory("early-images").toFile()
        try {
            val lines = java.util.Collections.synchronizedList(mutableListOf<String>())
            val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, threads = 1))
            val outcome = DownloadPipeline(InMemorySecretStore(), output, web = WebOf(page), httpFactory = { http },
                logger = JobLogging { _, message -> lines += message }).run(url, settings)
            assertEquals(emptyList<Any>(), outcome.skipped)
            // Asked for once: the image step takes what was downloaded early.
            assertEquals(listOf("https://img.example/a.png"), http.requests)
            assertTrue(lines.toString(), lines.any { it.contains("1 started while the chapters were read") })
            val epub = outcome.files.single { it.extension == "epub" }
            val entries = java.util.zip.ZipFile(epub).use { zip -> zip.entries().toList().map { it.name } }
            assertTrue(entries.toString(), entries.any { it.contains("images/") && it.endsWith(".png") })
        } finally {
            output.deleteRecursively()
        }
    }

    @Test
    fun aFolderChapterIsWrittenBeforeTheNextChapterIsRead() = runTest {
        val url = "https://novelfull.com/the-legendary-mechanic.html"
        val http = object : HttpFetching {
            override suspend fun send(request: HttpRequest) = HttpResponse(request.url, 200, emptyMap(), PNG)
        }
        val output = Files.createTempDirectory("early-folder").toFile()
        // What the Folder export holds so far (it is prepared beside the output, then moved into place).
        fun prepared() = output.listFiles { file -> file.name.startsWith(".noveldb-folder-") }.orEmpty()
            .flatMap { java.io.File(it, "book").walkTopDown().filter(java.io.File::isFile).toList() }
        val page = W2ePage(
            book = """{"api":1,"engine":"webtoepub","parser":"NovelFullParser","match":"site","bookUrl":"$url","bookname":"The Legendary Mechanic",
                "author":"Writer","introduction":"","coverUrl":null,"tags":[],"language":"en","chapterCount":2,
                "chapters":[{"url":"https://novelfull.com/the-legendary-mechanic/chapter-1.html","name":"Chapter 1","number":1,"isVIP":false,"isPaid":false},
                            {"url":"https://novelfull.com/the-legendary-mechanic/chapter-2.html","name":"Chapter 2","number":2,"isVIP":false,"isPaid":false}],
                "warnings":[]}""",
            chapters = mapOf(
                "chapter-1" to """{"chapterName":"Chapter 1","contentHtml":"<p>One.</p><p><img src=\"https://img.example/a.png\"></p>","contentText":"One.","imageCount":0,"images":[]}""",
                "chapter-2" to """{"chapterName":"Chapter 2","contentHtml":"<p>Two.</p><p><img src=\"https://img.example/b.png\"></p>","contentText":"Two.","imageCount":0,"images":[]}""",
            ),
            // The second chapter is not read until the first one is in the export: a folder writer that
            // waits for the last chapter would never get there.
            beforeChapter = { body ->
                if (body.contains("chapter-2")) kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.Default) {
                    withTimeout(10_000) { while (prepared().none { it.name.startsWith("0001") }) kotlinx.coroutines.delay(20) }
                }
            },
        )
        try {
            val lines = java.util.Collections.synchronizedList(mutableListOf<String>())
            val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, threads = 1,
                formats = listOf(OutputFormat.FOLDER)))
            val outcome = DownloadPipeline(InMemorySecretStore(), output, web = WebOf(page), httpFactory = { http },
                logger = JobLogging { _, message -> lines += message }).run(url, settings)
            assertEquals(emptyList<Any>(), outcome.skipped)
            assertTrue(lines.toString(), lines.any { Regex("(1 chapter was|2 chapters were) prepared for the Folder/ZIP output").containsMatchIn(it) })
            // Both chapters are in the folder once, whichever step wrote the second.
            val folder = outcome.files.single()
            assertTrue(folder.name, folder.isDirectory && folder.name.endsWith(".folder"))
            val names = folder.walkTopDown().filter { it.isFile }.map { it.name }.toList()
            assertEquals(names.toString(), 1, names.count { it.startsWith("0001") && !it.endsWith(".txt") })
            assertEquals(names.toString(), 1, names.count { it.startsWith("0002") && !it.endsWith(".txt") })
            assertTrue(prepared().isEmpty())
        } finally {
            output.deleteRecursively()
        }
    }

    @Test
    fun autoPurchaseAsksOnlyForLockedChaptersInItsRangeAndNeverFailsTheDownload() = runTest {
        val fixture = mechanicPage()
        val output = Files.createTempDirectory("purchase").toFile()
        try {
            for ((purchase, expected) in listOf(
                SharedSettings(autoPurchase = true, purchaseFrom = 2, purchaseTo = 2) to "is not available for",
                SharedSettings(autoPurchase = true, purchaseFrom = 1, purchaseTo = 1) to "no locked chapter in that range",
                SharedSettings(autoPurchase = true) to "has no chapter range",
            )) {
                val lines = mutableListOf<String>()
                // Chapter 2 is locked; the script engines have no purchase step, so it stays locked.
                val page = W2ePage(fixture.book.replace("\"number\":2,\"isVIP\":false,\"isPaid\":false", "\"number\":2,\"isVIP\":true,\"isPaid\":true"), fixture.chapters)
                val settings = AppSettings(shared = purchase.copy(intervalMin = 0.0, intervalMax = 0.0, formats = listOf(OutputFormat.TXT)))
                val outcome = DownloadPipeline(InMemorySecretStore(), output, web = WebOf(page),
                    httpFactory = { Images(emptyMap()) }, logger = JobLogging { _, message -> lines += message }).run(mechanicUrl, settings)
                assertTrue(lines.toString(), lines.any { it.startsWith("Auto purchase") && it.contains(expected) })
                assertTrue(outcome.files.isNotEmpty())
            }
        } finally {
            output.deleteRecursively()
        }
    }

    private val mechanicUrl = "https://novelfull.com/the-legendary-mechanic.html"

    private fun mechanicPage() = W2ePage(
        book = """{"api":1,"engine":"webtoepub","parser":"NovelFullParser","match":"site","bookUrl":"$mechanicUrl","bookname":"The Legendary Mechanic",
            "author":"Writer","introduction":"","coverUrl":null,"tags":[],"language":"en","chapterCount":2,
            "chapters":[{"url":"https://novelfull.com/the-legendary-mechanic/chapter-1.html","name":"Chapter 1","number":1,"isVIP":false,"isPaid":false},
                        {"url":"https://novelfull.com/the-legendary-mechanic/chapter-2.html","name":"Chapter 2","number":2,"isVIP":false,"isPaid":false}],
            "warnings":[]}""",
        chapters = mapOf(
            "chapter-1" to """{"chapterName":"Chapter 1","contentHtml":"<p>One.</p><p><img src=\"https://img.example/a.png\"></p>","contentText":"One.","imageCount":0,"images":[]}""",
            "chapter-2" to """{"chapterName":"Chapter 2","contentHtml":"<p>Two.</p>","contentText":"Two.","imageCount":0,"images":[]}""",
        ),
    )

    @Test
    fun scriptEngineKeepsTheRangeChosenForTheUrlSource() = runTest {
        val url = "https://\u006e\u006f\u0076\u0065\u006c\u0070\u0069\u0061.com/novel/123"
        for (selected in 1..2) {
            val output = Files.createTempDirectory("fallback-range").toFile()
            val requests = mutableListOf<String>()
            val lines = mutableListOf<String>()
            val phases = mutableListOf<JobPhase.FetchingChapters>()
            val fixture = mechanicPage()
            val page = W2ePage(fixture.book.replace(mechanicUrl, url), fixture.chapters) {
                if (it.contains("__W2E_parseChapter")) requests += it
            }
            val settings = AppSettings(
                shared = SharedSettings(rangeFrom = 3 - selected, rangeTo = 3 - selected, appendRange = true,
                    intervalMin = 0.0, intervalMax = 0.0, formats = listOf(OutputFormat.TXT)),
                overrides = mapOf(SourceId.NPIA.raw to SharedOverrides(rangeFrom = selected, rangeTo = selected)),
                engines = EngineSettings(order = listOf("webtoepub"), disabled = listOf("native"), dynamicPriority = false),
            )
            try {
                val outcome = DownloadPipeline(InMemorySecretStore(), output, web = WebOf(page),
                    httpFactory = { Images(emptyMap()) }, logger = JobLogging { _, message -> lines += message })
                    .run(url, settings) { if (it is JobPhase.FetchingChapters) phases += it }

                assertEquals(SourceId.READER, outcome.book.source)
                assertEquals(1, requests.size)
                assertTrue(requests.single().contains("chapter-$selected"))
                assertEquals(listOf("Chapter $selected: Chapter $selected"), lines.filter { it.startsWith("Chapter ") }.map { it.substringBefore(" (") })
                assertTrue(phases.toString(), phases.all { it.total == 1 && it.done in 0..1 })
                val text = outcome.files.single().readText()
                // A ranged download is named after its range, a single chapter as just its number.
                assertTrue(outcome.files.single().name, outcome.files.single().name.endsWith(" ($selected).txt"))
                assertTrue(text.contains(if (selected == 1) "One." else "Two."))
                assertFalse(text.contains(if (selected == 1) "Two." else "One."))
            } finally {
                output.deleteRecursively()
            }
        }
    }

    @Test
    fun stitchesWhileTheNextChapterDownloadsAndSharesTheStitchesWithZip() = runBlocking {
        val output = Files.createTempDirectory("overlapped-chapters").toFile()
        val originalStitcher = Writers.chapterStitcher
        val nextRequest = CompletableDeferred<Unit>()
        val releaseNext = CompletableDeferred<Unit>()
        val firstStitch = CompletableDeferred<Unit>()
        val stitches = AtomicInteger()
        try {
            val pages = W2ePage(mechanicPage().book, (1..2).associate { chapter ->
                "chapter-$chapter" to
                    """{"chapterName":"Chapter $chapter","contentHtml":"<img src=\"https://img.example/$chapter/a.png\"><img src=\"https://img.example/$chapter/b.png\">","contentText":"","imageCount":0,"images":[]}"""
            })
            val http = object : HttpFetching {
                override suspend fun send(request: HttpRequest): HttpResponse {
                    if (request.url.contains("/2/")) {
                        nextRequest.complete(Unit)
                        releaseNext.await()
                    }
                    return HttpResponse(request.url, 200, emptyMap(), PNG)
                }
            }
            Writers.chapterStitcher = { images, _, target ->
                if (stitches.incrementAndGet() == 1) firstStitch.complete(Unit)
                target("png", false).writeBytes(images.flatMap { it.second.asIterable() }.toByteArray())
            }
            val settings = AppSettings().let {
                it.copy(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, imageWorkers = 1,
                    formats = listOf(OutputFormat.FOLDER, OutputFormat.ZIP)),
                    images = it.images.copy(webtoonExport = true))
            }
            val job = async {
                DownloadPipeline(InMemorySecretStore(), output, web = WebOf(pages), httpFactory = { http })
                    .run(mechanicUrl, settings)
            }
            try {
                withTimeout(10_000) {
                    nextRequest.await()
                    firstStitch.await()
                    assertFalse(job.isCompleted)
                    assertTrue(output.listFiles().orEmpty().none { !it.name.startsWith(".") })
                }
            } finally {
                releaseNext.complete(Unit)
            }
            val files = job.await().files
            assertEquals(2, stitches.get())
            val folder = files.single { it.isDirectory }
            ZipFile(files.single { it.extension == "zip" }).use { zip ->
                val names = zip.entries().toList().map { it.name }
                assertEquals(2, names.size)
                for (name in names) {
                    assertArrayEquals(File(folder, name).readBytes(), zip.getInputStream(zip.getEntry(name)).use { it.readBytes() })
                }
            }
        } finally {
            Writers.chapterStitcher = originalStitcher
            output.deleteRecursively()
        }
    }

    @Test
    fun aDownloadLogsEveryStageInDetail() = runTest {
        val lines = java.util.Collections.synchronizedList(mutableListOf<Pair<LogLevel, String>>())
        val output = Files.createTempDirectory("log-out").toFile()
        try {
            val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0))
            DownloadPipeline(
                InMemorySecretStore(), output, web = WebOf(mechanicPage()),
                httpFactory = { Images(mapOf("https://img.example/a.png" to PNG)) },
                logger = JobLogging { level, message -> lines += level to message },
            ).run(mechanicUrl, settings)

            val text = lines.map { it.second }
            fun has(prefix: String) = assertTrue("no line starts with \"$prefix\" in $text", text.any { it.startsWith(prefix) })
            has("Starting ")
            has("Settings: formats epub")
            has("The Legendary Mechanic: 2 chapters")
            has("Downloading 2 of 2 chapters")
            has("Chapter 1: Chapter 1")
            has("Chapters: 2 downloaded")
            // The image step says what it is about to count, so its progress is not a mystery.
            has("Downloading 1 image from 1 of 2 chapters")
            // Each image has its own line, as on the desktop: its chapter, its place, its size and speed.
            val arrived = Regex("""  ✓ \[\d+] Image #1 \[Chapter \d+]: \d+ B \[[\d.]+ (?:B|KB|MB)/s]""")
            assertTrue("no image line in $text", text.any { arrived.matches(it) })
            assertEquals(1, text.count { it.startsWith("  ✓ ") })
            // An image that arrived is a detail (the log's Details switch hides it); the step's own lines are not.
            assertEquals(LogLevel.DEBUG, lines.first { it.second.startsWith("  ✓ ") }.first)
            assertEquals(LogLevel.INFO, lines.first { it.second.startsWith("Downloading 1 image") }.first)
            assertEquals(listOf("512 B", "19.5 KB", "1.20 MB"), listOf(512L, 19_968L, 1_258_291L).map(ImageStage::formatSize))
            has("Images: 1 kept")
            has("Writing EPUB")
            has("Wrote The Legendary Mechanic.epub (")
            has("Finished in ")
            // The settings are a detail; stages are plain information.
            assertEquals(LogLevel.DEBUG, lines.first { it.second.startsWith("Settings: ") }.first)
            assertEquals(LogLevel.INFO, lines.first { it.second.startsWith("Writing EPUB") }.first)
            // In order: the start comes before the book, the book before its chapters, the file last.
            val order = listOf("Starting ", "The Legendary Mechanic: 2", "Downloading 2 of 2", "Chapters: 2 downloaded", "Downloading 1 image", "Images: ", "Writing EPUB", "Wrote ", "Finished in")
                .map { prefix -> text.indexOfFirst { it.startsWith(prefix) } }
            assertEquals(order.sorted(), order)
        } finally {
            output.deleteRecursively()
        }
    }

    @Test
    fun completedChapterIsLoggedWhileAnEarlierChapterIsStillDownloading() = runBlocking {
        val output = Files.createTempDirectory("live-chapter-log").toFile()
        val releaseFirst = CompletableDeferred<Unit>()
        val laterFinished = CompletableDeferred<Unit>()
        val lines = java.util.Collections.synchronizedList(mutableListOf<String>())
        val fixture = mechanicPage()
        val page = W2ePage(fixture.book, fixture.chapters) { script ->
            if (script.contains("chapter-1")) releaseFirst.await()
        }
        val settings = AppSettings(shared = SharedSettings(
            threads = 2, intervalMin = 0.0, intervalMax = 0.0, formats = listOf(OutputFormat.TXT),
        ))
        try {
            val job = async {
                DownloadPipeline(
                    InMemorySecretStore(), output, web = WebOf(page), httpFactory = { Images(emptyMap()) },
                    logger = JobLogging { _, message -> lines += message },
                ).run(mechanicUrl, settings) { phase ->
                    if (phase == JobPhase.FetchingChapters(1, 2)) laterFinished.complete(Unit)
                }
            }
            try {
                withTimeout(10_000) { laterFinished.await() }
                assertFalse(job.isCompleted)
                assertTrue("The completed chapter should be visible immediately: $lines", lines.any { it.startsWith("Chapter 2: Chapter 2 (") })
                assertFalse(lines.any { it.startsWith("Chapter 1: Chapter 1 (") })
            } finally {
                releaseFirst.complete(Unit)
            }
            val text = job.await().files.single().readText()
            assertTrue(text.contains("One.") && text.contains("Two."))
            assertTrue("Exported chapters stay in reading order", text.indexOf("One.") < text.indexOf("Two."))
            assertEquals(listOf("Chapter 2: Chapter 2", "Chapter 1: Chapter 1"), lines.filter { it.startsWith("Chapter ") }.map { it.substringBefore(" (") })
        } finally {
            releaseFirst.complete(Unit)
            output.deleteRecursively()
        }
    }

    @Test
    fun generateOnStopSavesOnlyCompletedChapters() = runBlocking {
        for (generate in listOf(false, true)) {
            val output = Files.createTempDirectory("generate-on-stop").toFile()
            val firstDone = CompletableDeferred<Unit>()
            val neverFinish = CompletableDeferred<Unit>()
            val fixture = mechanicPage()
            val page = W2ePage(fixture.book, fixture.chapters) { script ->
                if (script.contains("chapter-2")) neverFinish.await()
            }
            val saved = CompletableDeferred<JobOutcome>()
            val settings = AppSettings(shared = SharedSettings(threads = 2, intervalMin = 0.0, intervalMax = 0.0,
                formats = listOf(OutputFormat.TXT, OutputFormat.EPUB), generateOnStop = generate))
            try {
                val job = launch {
                    val outcome = DownloadPipeline(InMemorySecretStore(), output, web = WebOf(page), httpFactory = { Images(emptyMap()) })
                        .run(mechanicUrl, settings) { if (it == JobPhase.FetchingChapters(1, 2)) firstDone.complete(Unit) }
                    saved.complete(outcome)
                }
                withTimeout(10_000) { firstDone.await() }
                withTimeout(10_000) { job.cancelAndJoin() }
                assertEquals(generate, saved.isCompleted)
                if (generate) {
                    val outcome = saved.await()
                    assertTrue(outcome.stopped)
                    val text = outcome.files.single { it.extension == "txt" }.readText()
                    assertTrue(text.contains("One."))
                    assertFalse(text.contains("Two."))
                    val epub = outcome.files.single { it.extension == "epub" }
                    ZipFile(epub).use { zip ->
                        val html = zip.entries().toList().filter { it.name.endsWith(".xhtml") }
                            .joinToString { zip.getInputStream(it).bufferedReader().use { reader -> reader.readText() } }
                        assertTrue(html.contains("One."))
                        assertFalse(html.contains("Two."))
                    }
                    assertTrue(outcome.uploads.isEmpty())
                } else assertTrue(output.listFiles().orEmpty().isEmpty())
            } finally { output.deleteRecursively() }
        }
    }

    private fun twoChaptersWithAPicture(url: String, beforeChapter: suspend (String) -> Unit = {}) = W2ePage(
        book = """{"api":1,"engine":"webtoepub","parser":"NovelFullParser","match":"site","bookUrl":"$url","bookname":"The Legendary Mechanic",
            "author":"Writer","introduction":"","coverUrl":null,"tags":[],"language":"en","chapterCount":2,
            "chapters":[{"url":"https://novelfull.com/the-legendary-mechanic/chapter-1.html","name":"Chapter 1","number":1,"isVIP":false,"isPaid":false},
                        {"url":"https://novelfull.com/the-legendary-mechanic/chapter-2.html","name":"Chapter 2","number":2,"isVIP":false,"isPaid":false}],
            "warnings":[]}""",
        chapters = mapOf(
            "chapter-1" to """{"chapterName":"Chapter 1","contentHtml":"<p>One.</p><p><img src=\"https://img.example/a.png\"></p>","contentText":"One.","imageCount":0,"images":[]}""",
            "chapter-2" to """{"chapterName":"Chapter 2","contentHtml":"<p>Two.</p>","contentText":"Two.","imageCount":0,"images":[]}""",
        ),
        beforeChapter = beforeChapter,
    )

    @Test
    fun aStopWhileTheFilesAreMadeEndsTheJobInsteadOfStartingTheBuildAgain() = runBlocking {
        // With "Generate on stop" on, a Stop during the image step or the stitching used to start the
        // whole build again, in a way no Stop could end.
        val url = "https://novelfull.com/the-legendary-mechanic.html"
        val output = Files.createTempDirectory("stop-while-building").toFile()
        val never = CompletableDeferred<Unit>()
        val http = object : HttpFetching {
            override suspend fun send(request: HttpRequest): HttpResponse {
                never.await()  // the picture never arrives: the image step stays busy
                error("not reached")
            }
        }
        val making = CompletableDeferred<Unit>()
        val saved = CompletableDeferred<JobOutcome>()
        val lines = java.util.Collections.synchronizedList(mutableListOf<String>())
        val settings = AppSettings(shared = SharedSettings(threads = 1, intervalMin = 0.0, intervalMax = 0.0,
            formats = listOf(OutputFormat.EPUB), generateOnStop = true))
        try {
            val job = launch {
                saved.complete(DownloadPipeline(InMemorySecretStore(), output, web = WebOf(twoChaptersWithAPicture(url)),
                    httpFactory = { http }, logger = JobLogging { _, message -> lines += message })
                    .run(url, settings) { if (it is JobPhase.DownloadingImages) making.complete(Unit) })
            }
            withTimeout(10_000) { making.await() }
            withTimeout(10_000) { job.cancelAndJoin() }
            assertFalse(saved.isCompleted)
            assertFalse(lines.toString(), lines.any { it.contains("generating files") })
            assertTrue(output.listFiles().orEmpty().toList().toString(), output.listFiles().orEmpty().isEmpty())
        } finally { output.deleteRecursively() }
    }

    @Test
    fun aSecondStopEndsTheFilesAStoppedDownloadIsGenerating() = runBlocking {
        val url = "https://novelfull.com/the-legendary-mechanic.html"
        val output = Files.createTempDirectory("second-stop").toFile()
        val firstDone = CompletableDeferred<Unit>()
        val neverRead = CompletableDeferred<Unit>()
        val encoding = CompletableDeferred<Unit>()
        val release = java.util.concurrent.CountDownLatch(1)
        // The build is busy with the first chapter's picture until the test lets it go.
        val slow = object : com.shirochi.npia.core.ImageProcessing {
            override fun process(data: ByteArray, isCover: Boolean): com.shirochi.npia.core.ProcessedImage {
                encoding.complete(Unit)
                release.await(10, java.util.concurrent.TimeUnit.SECONDS)
                return com.shirochi.npia.core.ProcessedImage(data, "png")
            }
        }
        val http = object : HttpFetching {
            override suspend fun send(request: HttpRequest) = HttpResponse(request.url, 200, emptyMap(), PNG)
        }
        val saved = CompletableDeferred<JobOutcome>()
        val lines = java.util.Collections.synchronizedList(mutableListOf<String>())
        val settings = AppSettings(shared = SharedSettings(threads = 2, intervalMin = 0.0, intervalMax = 0.0,
            formats = listOf(OutputFormat.EPUB), generateOnStop = true))
        try {
            val page = twoChaptersWithAPicture(url) { body -> if (body.contains("chapter-2")) neverRead.await() }
            val pipeline = DownloadPipeline(InMemorySecretStore(), output, web = WebOf(page), images = slow,
                httpFactory = { http }, logger = JobLogging { _, message -> lines += message })
            val job = launch {
                saved.complete(pipeline.run(url, settings) { if (it == JobPhase.FetchingChapters(1, 2)) firstDone.complete(Unit) })
            }
            withTimeout(10_000) { firstDone.await() }
            job.cancel()  // Stop: the files are generated from the chapter that was read
            withTimeout(10_000) { encoding.await() }
            assertTrue(lines.toString(), lines.any { it.contains("generating files") })
            pipeline.stopOutput()  // Stop again
            release.countDown()
            withTimeout(10_000) { job.join() }
            assertFalse(saved.isCompleted)
            assertTrue(output.listFiles().orEmpty().toList().toString(), output.listFiles().orEmpty().isEmpty())
        } finally {
            release.countDown()
            output.deleteRecursively()
        }
    }

    @Test
    fun chapterTimingIsOptionalAndIncludesTheTotalDuration() = runTest {
        for (enabled in listOf(false, true)) {
            val lines = mutableListOf<String>()
            val output = Files.createTempDirectory("chapter-timing").toFile()
            try {
                val settings = AppSettings(shared = SharedSettings(chapterTiming = enabled, intervalMin = 0.0, intervalMax = 0.0,
                    formats = listOf(OutputFormat.TXT)))
                DownloadPipeline(InMemorySecretStore(), output, web = WebOf(mechanicPage()),
                    httpFactory = { Images(emptyMap()) }, logger = JobLogging { _, message -> lines += message })
                    .run(mechanicUrl, settings)
                val chapters = lines.filter { it.startsWith("Chapter ") }
                assertEquals(2, chapters.size)
                assertTrue(lines.toString(), chapters.all { Regex(".*\\(\\d+\\.\\d s\\)$").matches(it) == enabled })
                assertTrue(lines.any { it.startsWith("Finished in ") })
            } finally { output.deleteRecursively() }
        }
    }

    @Test
    fun cancellingAnUploadKeepsFilesAlreadyGenerated() = runBlocking {
        val output = Files.createTempDirectory("cancel-upload").toFile()
        val uploading = CompletableDeferred<Unit>()
        val saved = CompletableDeferred<JobOutcome>()
        UploaderCatalog.testHost = object : Uploader {
            override val id = "localtest"
            override val displayName = "Held upload"
            override val maxBytes: Long? = null
            override val retentionSeconds: Long? = null
            override val requiresAccount = false
            override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
                uploading.complete(Unit)
                kotlinx.coroutines.awaitCancellation()
            }
        }
        try {
            val settings = AppSettings(shared = SharedSettings(formats = listOf(OutputFormat.TXT), intervalMin = 0.0, intervalMax = 0.0),
                upload = UploadSettings(autoUpload = true, hosts = listOf("localtest")))
            val job = launch {
                saved.complete(DownloadPipeline(InMemorySecretStore(), output, web = WebOf(mechanicPage()),
                    httpFactory = { Images(emptyMap()) }).run(mechanicUrl, settings))
            }
            withTimeout(10_000) { uploading.await() }
            withTimeout(10_000) { job.cancelAndJoin() }
            val result = saved.await()
            assertTrue(result.stopped)
            val file = result.files.single()
            assertFalse(file.name.contains("_partial"))
            assertTrue(file.readText().contains("Two."))
        } finally { UploaderCatalog.testHost = null; output.deleteRecursively() }
    }

    /** A host that records what it was asked to upload. */
    private class RecordingHost : Uploader {
        override val id = "localtest"
        override val displayName = "Recording host"
        override val maxBytes: Long? = null
        override val retentionSeconds: Long? = null
        override val requiresAccount = false
        val uploads = mutableListOf<Triple<String, String?, List<String>>>()

        override suspend fun upload(file: File, context: UploadContext, progress: ((UploadProgress) -> Unit)?): UploadResult {
            val entries = if (file.extension == "zip") java.util.zip.ZipFile(file).use { zip -> zip.entries().toList().map { it.name } } else emptyList()
            uploads += Triple(file.name, context.fileId, entries)
            return result(file, "https://example.test/${uploads.size}", context)
        }
    }

    @Test
    fun autoUploadNamesFilesByStemAndPacksSeveralFormatsIntoOneZip() = runTest {
        val host = RecordingHost()
        UploaderCatalog.testHost = host
        val output = Files.createTempDirectory("auto-out").toFile()
        try {
            val base = AppSettings(
                shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, formats = listOf(OutputFormat.EPUB, OutputFormat.TXT)),
                upload = UploadSettings(autoUpload = true, hosts = listOf("localtest")),
            )
            val lines = java.util.Collections.synchronizedList(mutableListOf<String>())
            val logger = JobLogging { _, message -> lines += message }
            val outcome = DownloadPipeline(
                InMemorySecretStore(), output, web = WebOf(mechanicPage()), httpFactory = { Images(mapOf("https://img.example/a.png" to PNG)) }, logger = logger,
            ).run(mechanicUrl, base)

            // Two output files, bundling on: one ZIP holding both, under the ASCII ZIP name.
            assertEquals(1, host.uploads.size)
            val (name, _, entries) = host.uploads.single()
            assertEquals("NovelDB_2_files.zip", name)
            assertEquals(outcome.files.map { it.name }.sorted(), entries.sorted())
            assertEquals(1, outcome.uploads.size)
            assertEquals("NovelDB_2_files.zip (2 files)", outcome.uploads.single().label)
            assertTrue(lines.toString(), lines.any { it.startsWith("Preparing ZIP 1 of 1") })
            assertTrue(lines.toString(), lines.any { it.startsWith("Uploaded NovelDB_2_files.zip (2 files): https://example.test/1") })

            // Bundling off: one upload per file, each carrying the stem (source codename and book id).
            host.uploads.clear()
            DownloadPipeline(
                InMemorySecretStore(), output, web = WebOf(mechanicPage()), httpFactory = { Images(mapOf("https://img.example/a.png" to PNG)) },
            ).run(mechanicUrl, base.copy(upload = base.upload.copy(zipBundle = false)))
            assertEquals(2, host.uploads.size)
            val stems = host.uploads.map { it.second }
            assertTrue(stems.toString(), stems.all { it != null && Regex("[a-z_]+_[A-Za-z0-9_-]+").matches(it) })

            // Settings changed after queueing supply the current host order when files are ready.
            host.uploads.clear()
            DownloadPipeline(InMemorySecretStore(), output, web = WebOf(mechanicPage()),
                httpFactory = { Images(emptyMap()) }, currentUploadSettings = { base.upload })
                .run(mechanicUrl, base.copy(upload = base.upload.copy(hosts = listOf("outdated-host"))))
            assertEquals(1, host.uploads.size)
        } finally {
            UploaderCatalog.testHost = null
            output.deleteRecursively()
        }
    }

    /** Serves image bytes by URL; everything else is a 404. */
    private class Images(private val files: Map<String, ByteArray>) : HttpFetching {
        val requests = java.util.Collections.synchronizedList(mutableListOf<String>())

        override suspend fun send(request: HttpRequest): HttpResponse {
            requests += request.url
            val body = files[request.url] ?: return HttpResponse(request.url, 404, emptyMap(), ByteArray(0))
            return HttpResponse(request.url, 200, emptyMap(), body)
        }
    }

    /** A page that answers WebToEpub's bridge: the bundle is ready, then the book, then each chapter by URL. */
    private class W2ePage(
        val book: String,
        val chapters: Map<String, String> = emptyMap(),
        val beforeChapter: suspend (String) -> Unit = {},
    ) : WebPageDriving {
        val loads = mutableListOf<String>()
        var closed = false

        override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
            loads += url
        }

        override suspend fun evaluateJson(functionBody: String): String = when {
            functionBody.contains("window.__W2E_READY") -> """{"ready":true,"error":null}"""
            functionBody.contains("__W2E_parseBook") -> book
            // The generic fallback, which finds no chapter list on these pages.
            functionBody.contains("window.__GEN_READY") -> """{"ready":true,"error":null}"""
            functionBody.contains("__GEN_parseBook") -> """{"error":"The generic engine found no chapter list on this page.","noParser":true}"""
            else -> {
                beforeChapter(functionBody)
                chapters.entries.firstOrNull { functionBody.contains(it.key) }?.value ?: """{"error":"no such chapter"}"""
            }
        }

        override suspend fun addInitScript(source: String) {}
        override suspend fun currentUrl(): String? = loads.lastOrNull()
        override suspend fun html() = ""
        override suspend fun close() {
            closed = true
        }
    }

    private class WebOf(private val page: WebPageDriving) : WebEngineProviding {
        override suspend fun newPage(profile: String) = page
        override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) = Unit
    }
}
