package com.shirochi.npia.pipeline

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterRef
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.ImageProcessing
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.PassthroughImages
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.UnavailableWebEngine
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.output.BookBundle
import com.shirochi.npia.output.Writers
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.upload.UploadContext
import com.shirochi.npia.upload.UploadResult
import com.shirochi.npia.upload.UploaderCatalog
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.ensureActive
import java.io.File
import kotlin.coroutines.coroutineContext

sealed class JobPhase {
    data object Queued : JobPhase()
    data object FetchingBook : JobPhase()
    data class FetchingChapters(val done: Int, val total: Int) : JobPhase()
    data class DownloadingImages(val done: Int, val total: Int) : JobPhase()
    data class Writing(val format: OutputFormat) : JobPhase()
    data class Uploading(val fileName: String) : JobPhase()
    data object Finished : JobPhase()
    data class Failed(val message: String) : JobPhase()
}

data class JobOutcome(
    val book: BookInfo,
    val files: List<File>,
    val uploads: List<UploadResult>,
    /** Chapters that were refused or failed, with the reason. */
    val skipped: List<Pair<ChapterRef, String>>,
)

/**
 * Runs one download end to end: detect source, parse book, fetch chapters and images, write each
 * output format, and optionally upload. Mirrors the iOS `DownloadPipeline`.
 */
class DownloadPipeline(
    private val secrets: SecretStore,
    private val outputDirectory: File,
    private val web: WebEngineProviding = UnavailableWebEngine,
    private val images: ImageProcessing = PassthroughImages,
    private val logger: JobLogging = NullLogger,
    private val httpFactory: (SharedSettings) -> HttpFetching = { OkHttpFetcher(it) },
) {
    suspend fun run(url: String, settings: AppSettings, onPhase: (JobPhase) -> Unit = {}): JobOutcome {
        val adapter = SourceCatalog.require(url)
        val shared = settings.effective(adapter.id)
        val http = httpFactory(shared)
        val context = SourceContext(http, web, secrets, settings, logger)

        onPhase(JobPhase.FetchingBook)
        val book = adapter.parseBook(url, context)
        logger.log(LogLevel.INFO, "${book.title}: ${book.chapters.size} chapters")

        var selected = select(book.chapters, shared)
        if (adapter.id == SourceId.NPIA && settings.npia.includeNotices) selected = book.notices + selected
        onPhase(JobPhase.FetchingChapters(0, selected.size))
        val results = adapter.parseChapters(selected, book, context)

        val chapters = mutableListOf<ChapterContent>()
        val skipped = mutableListOf<Pair<ChapterRef, String>>()
        for (result in results) {
            when (result) {
                is ChapterResult.Content -> chapters += result.content
                is ChapterResult.Refused -> skipped += result.ref to result.reason
                is ChapterResult.Failed -> skipped += result.ref to result.message
            }
        }
        onPhase(JobPhase.FetchingChapters(chapters.size, selected.size))

        val imageBytes = fetchImages(chapters, http, onPhase)
        val cover = book.coverUrl?.let { coverUrl ->
            runCatching { http.get(coverUrl).body }.getOrNull()?.let { data ->
                val processed = images.process(data, isCover = true)
                BookBundle.Cover(processed.data, processed.fileExtension)
            }
        }

        val bundle = BookBundle(book, chapters, imageBytes, cover, settings)
        val baseName = Writers.baseName(book, shared)
        val files = shared.formats.map { format ->
            onPhase(JobPhase.Writing(format))
            Writers.writer(format).write(bundle, baseName, outputDirectory)
        }

        val uploads = mutableListOf<UploadResult>()
        if (settings.upload.autoUpload) {
            // Folder outputs (separate TXT) are zipped by the catalog.
            for (file in files) {
                onPhase(JobPhase.Uploading(file.name))
                try {
                    uploads += UploaderCatalog.upload(file, settings.upload, UploadContext(secrets, settings.upload))
                } catch (e: CancellationException) {
                    throw e
                } catch (e: Exception) {
                    logger.log(LogLevel.WARNING, "Upload failed for ${file.name}: ${e.message}")
                }
            }
        }

        onPhase(JobPhase.Finished)
        return JobOutcome(book, files, uploads, skipped)
    }

    // TODO(images workstream): bounded concurrency (`imageWorkers`) and rewriting filenames when
    // recompression changes the extension.
    private suspend fun fetchImages(chapters: List<ChapterContent>, http: HttpFetching, onPhase: (JobPhase) -> Unit): Map<String, ByteArray> {
        val refs = chapters.flatMap { it.images }
        val out = mutableMapOf<String, ByteArray>()
        refs.forEachIndexed { index, ref ->
            coroutineContext.ensureActive()
            val data = ref.data ?: runCatching { http.get(ref.url).body }.getOrNull()
            if (data != null) {
                out[ref.filename] = runCatching { images.process(data).data }.getOrDefault(data)
            } else {
                logger.log(LogLevel.WARNING, "Image failed: ${ref.url}")
            }
            onPhase(JobPhase.DownloadingImages(index + 1, refs.size))
        }
        return out
    }

    companion object {
        /** Applies the chapter range and paid filter. */
        fun select(chapters: List<ChapterRef>, shared: SharedSettings): List<ChapterRef> {
            val from = shared.rangeFrom ?: Int.MIN_VALUE
            val to = shared.rangeTo ?: Int.MAX_VALUE
            return chapters.filter { c -> c.index in from..to && !(shared.skipPaid && c.isPaid) }
        }
    }
}
