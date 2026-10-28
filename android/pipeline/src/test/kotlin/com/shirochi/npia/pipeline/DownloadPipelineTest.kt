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
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import kotlinx.coroutines.test.runTest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

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
    fun unknownUrlFailsFast() = runTest {
        // With the script engines off, no engine can try an unknown site: no page is loaded, nothing is fetched.
        val settings = AppSettings(engines = EngineSettings(disabled = listOf("novel_downloader", "webtoepub")))
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
            has("Images: 1 kept")
            has("Writing EPUB")
            has("Wrote The Legendary Mechanic.epub (")
            has("Finished in ")
            // The settings are a detail; stages are plain information.
            assertEquals(LogLevel.DEBUG, lines.first { it.second.startsWith("Settings: ") }.first)
            assertEquals(LogLevel.INFO, lines.first { it.second.startsWith("Writing EPUB") }.first)
            // In order: the start comes before the book, the book before its chapters, the file last.
            val order = listOf("Starting ", "The Legendary Mechanic: 2", "Downloading 2 of 2", "Chapters: 2 downloaded", "Images: ", "Writing EPUB", "Wrote ", "Finished in")
                .map { prefix -> text.indexOfFirst { it.startsWith(prefix) } }
            assertEquals(order.sorted(), order)
        } finally {
            output.deleteRecursively()
        }
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
    private class W2ePage(val book: String, val chapters: Map<String, String> = emptyMap()) : WebPageDriving {
        val loads = mutableListOf<String>()
        var closed = false

        override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {
            loads += url
        }

        override suspend fun evaluateJson(functionBody: String): String = when {
            functionBody.contains("window.__W2E_READY") -> """{"ready":true,"error":null}"""
            functionBody.contains("__W2E_parseBook") -> book
            else -> chapters.entries.firstOrNull { functionBody.contains(it.key) }?.value ?: """{"error":"no such chapter"}"""
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
        override suspend fun presentLogin(url: String, profile: String) = Unit
    }
}
