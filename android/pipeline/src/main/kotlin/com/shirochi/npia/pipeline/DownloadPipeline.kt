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
import com.shirochi.npia.core.ProcessedImage
import com.shirochi.npia.core.SecretStore
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.bareHost
import com.shirochi.npia.core.SourceException
import com.shirochi.npia.core.UnavailableWebEngine
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.sniffImageExtension
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.output.BookBundle
import com.shirochi.npia.output.BookWriter
import com.shirochi.npia.output.OutputSkipped
import com.shirochi.npia.output.Writers
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.upload.PlannedUpload
import com.shirochi.npia.upload.UploadContext
import com.shirochi.npia.upload.UploadItem
import com.shirochi.npia.upload.UploadListener
import com.shirochi.npia.upload.UploadOutcome
import com.shirochi.npia.upload.UploadResult
import com.shirochi.npia.upload.UploadStem
import com.shirochi.npia.upload.UploaderCatalog
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File
import java.util.concurrent.atomic.AtomicInteger
import kotlin.coroutines.coroutineContext

sealed class JobPhase {
    data object Queued : JobPhase()
    data object FetchingBook : JobPhase()
    data class FetchingChapters(val done: Int, val total: Int) : JobPhase()
    data class DownloadingImages(val done: Int, val total: Int) : JobPhase()
    data class Writing(val format: OutputFormat) : JobPhase()
    data class Uploading(val fileName: String) : JobPhase()
    /** Waiting for the user to sign in to `host` in the browser. */
    data class SigningIn(val host: String) : JobPhase()
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
        val started = System.nanoTime()
        val adapter = SourceCatalog.require(url, settings.engines)
        val http = httpFactory(settings.effective(adapter.id))
        val context = SourceContext(http, web, secrets, settings, logger)
        val first = settings.effective(adapter.id)
        logger.log(LogLevel.INFO, "Starting ${adapter.id.raw} download: $url")
        logger.log(
            LogLevel.DEBUG,
            "Settings: formats ${first.formats.joinToString("/") { it.raw }}, ${first.threads} threads, " +
                "${first.maxRetries} retries, pause ${first.intervalRange.first}-${first.intervalRange.second} s, " +
                "cache ${if (first.useCache) "on" else "off"}",
        )

        onPhase(JobPhase.FetchingBook)
        val book = try {
            adapter.parseBook(url, context)
        } catch (e: SourceException.LoginRequired) {
            signIn(url, adapter.id, onPhase)
            onPhase(JobPhase.FetchingBook)
            adapter.parseBook(url, context)
        }
        logger.log(LogLevel.INFO, "${book.title}: ${book.chapters.size} chapters")

        // With several site engines the one that read the book decides the settings that apply.
        val owner = adapter.owner(book)
        val shared = settings.effective(owner.id)
        if (owner.id != adapter.id) logger.log(LogLevel.INFO, "Book read by the ${owner.id.raw} engine")
        var selected = select(book.chapters, shared)
        if (owner.id == SourceId.NPIA && settings.npia.includeNotices) selected = book.notices + selected
        val range = listOfNotNull(shared.rangeFrom?.let { "from $it" }, shared.rangeTo?.let { "to $it" }).joinToString(" ")
        logger.log(
            LogLevel.INFO,
            "Downloading ${selected.size} of ${book.chapters.size} chapters" + (if (range.isNotEmpty()) " ($range)" else ""),
        )
        onPhase(JobPhase.FetchingChapters(0, selected.size))
        val results = adapter.parseChapters(selected, book, chapterContext(context, selected.size, onPhase))

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
        logger.log(
            if (skipped.isEmpty()) LogLevel.INFO else LogLevel.WARNING,
            "Chapters: ${chapters.size} downloaded" + (if (skipped.isNotEmpty()) ", ${skipped.size} skipped or failed" else ""),
        )

        val imageOutput = ImageStage(http, settings, owner.id, images, logger).run(chapters) { done, total ->
            onPhase(JobPhase.DownloadingImages(done, total))
        }
        logger.log(
            if (imageOutput.failed > 0) LogLevel.WARNING else LogLevel.INFO,
            "Images: ${imageOutput.images.size} kept" + (if (imageOutput.failed > 0) ", ${imageOutput.failed} could not be downloaded" else ""),
        )
        chapters.clear()
        chapters += imageOutput.chapters
        val cover = book.coverUrl?.let { coverUrl ->
            runCatching { http.get(coverUrl).body }
                .onFailure { logger.log(LogLevel.WARNING, "Cover could not be downloaded: ${it.message ?: it}") }
                .getOrNull()?.takeIf { it.isNotEmpty() }?.let { data ->
                val processed = runCatching { images.process(data, isCover = true) }.getOrElse { ProcessedImage(data, sniffImageExtension(data)) }
                BookBundle.Cover(processed.data, processed.fileExtension)
            }
        }

        val bundle = BookBundle(book, chapters, imageOutput.images, cover, settings)
        val baseName = Writers.baseName(book, shared)
        val files = shared.formats.mapNotNull { format ->
            onPhase(JobPhase.Writing(format))
            logger.log(LogLevel.INFO, "Writing ${format.raw.uppercase()}")
            val writer = Writers.writer(format)
            var file = try {
                writer.write(bundle, baseName, outputDirectory)
            } catch (e: OutputSkipped) {
                logger.log(LogLevel.WARNING, e.message.orEmpty())
                return@mapNotNull null
            }
            val targetMb = settings.images.recompressTargetMb
            if (targetMb != null && targetMb > 0 && (format == OutputFormat.EPUB || format == OutputFormat.PDF)) {
                file = shrink(file, bundle, writer, baseName, (targetMb * 1_048_576).toLong())
            }
            logger.log(LogLevel.INFO, "Wrote ${file.name} (${sizeText(file)})")
            file
        }

        val uploads = mutableListOf<UploadResult>()
        if (settings.upload.autoUpload && files.isNotEmpty()) {
            // Folder outputs (separate TXT) are zipped by the catalog; several files may share a ZIP.
            // The stem names files whose titles hosts would reduce to nothing (npia_123456.epub).
            val stem = UploadStem.of(owner.id.raw, UploadStem.bookId(url, book.id))
            val upload = settings.upload
            val uploadContext = UploadContext(secrets, upload)
            val plan = UploaderCatalog.plan(files.map { UploadItem(it, stem) }, upload, uploadContext)
            val outcomes = UploaderCatalog.uploadAll(plan, upload, uploadContext, listener = object : UploadListener {
                override fun onStart(position: Int, total: Int, upload: PlannedUpload, name: String) {
                    onPhase(JobPhase.Uploading(name))
                    logger.log(LogLevel.INFO, "Uploading $name ($position of $total)")
                }

                override fun onPreparing(index: Int, total: Int) {
                    logger.log(LogLevel.INFO, "Preparing ZIP $index of $total")
                }

                override fun onHost(name: String) {
                    logger.log(LogLevel.INFO, "Trying $name")
                }

                override fun onFinished(position: Int, outcome: UploadOutcome) {
                    val result = outcome.result
                    if (result != null) {
                        logger.log(LogLevel.INFO, "Uploaded ${result.label}: ${result.link}")
                    } else {
                        logger.log(LogLevel.WARNING, "Upload failed for ${outcome.name}: ${outcome.error}")
                    }
                }
            })
            outcomes.mapNotNullTo(uploads) { it.result }
        }

        onPhase(JobPhase.Finished)
        logger.log(LogLevel.INFO, "Finished in ${durationText((System.nanoTime() - started) / 1_000_000)}")
        return JobOutcome(book, files, uploads, skipped)
    }

    private fun sizeText(file: File): String {
        val bytes = if (file.isDirectory) file.walkTopDown().filter { it.isFile }.sumOf { it.length() } else file.length()
        return when {
            bytes >= 1_048_576 -> String.format(java.util.Locale.US, "%.1f MB", bytes / 1_048_576.0)
            bytes >= 1024 -> "${bytes / 1024} KB"
            else -> "$bytes B"
        }
    }

    private fun durationText(millis: Long): String {
        val seconds = millis / 1000
        return if (seconds >= 60) "${seconds / 60} m ${seconds % 60} s" else String.format(java.util.Locale.US, "%.1f s", millis / 1000.0)
    }

    /**
     * `recompressTargetMb`: re-encode images at falling quality and rewrite until the file fits,
     * at most four passes (desktop `_recompress_to_target_size`).
     */
    private suspend fun shrink(file: File, bundle: BookBundle, writer: BookWriter, baseName: String, targetBytes: Long): File {
        var current = file.length()
        if (current <= targetBytes) return file
        logger.log(LogLevel.INFO, "${file.name} is $current bytes; recompressing images toward $targetBytes")
        var quality = maxOf(10, bundle.settings.images.quality)
        var working = bundle
        var output = file
        for (pass in 1..4) {
            coroutineContext.ensureActive()
            quality = ImageStage.nextRecompressQuality(quality, current, targetBytes) ?: break
            val q = quality
            val reencoded = withContext(Dispatchers.Default) {
                working.images.mapValues { (name, data) -> images.reencode(data, ImageReferences.extension(name), q) }
            }
            val cover = working.cover?.let { BookBundle.Cover(images.reencode(it.data, it.fileExtension, q), it.fileExtension) }
            working = BookBundle(working.book, working.chapters, reencoded, cover, working.settings)
            output = writer.write(working, baseName, outputDirectory)
            current = output.length()
            logger.log(LogLevel.INFO, "Pass $pass at quality $quality: $current bytes")
            if (current <= targetBytes) break
        }
        return output
    }

    /** A context whose `onChapter` advances the progress bar and logs each chapter as it lands. */
    private fun chapterContext(base: SourceContext, total: Int, onPhase: (JobPhase) -> Unit): SourceContext {
        val done = AtomicInteger()
        return SourceContext(base.http, base.web, base.secrets, base.settings, base.logger) { result ->
            val count = done.incrementAndGet().coerceAtMost(total)
            val label = "Chapter ${result.ref.index}: ${result.ref.title}"
            when (result) {
                is ChapterResult.Content -> logger.log(LogLevel.INFO, label)
                is ChapterResult.Refused -> logger.log(LogLevel.WARNING, "$label skipped: ${result.reason}")
                is ChapterResult.Failed -> logger.log(LogLevel.WARNING, "$label failed: ${result.message}")
            }
            onPhase(JobPhase.FetchingChapters(count, total))
        }
    }

    /**
     * Opens the sign-in browser on the book's site and waits for the user to finish. Without a
     * visible screen (app in the background) the job fails with instructions instead.
     */
    private suspend fun signIn(url: String, source: SourceId, onPhase: (JobPhase) -> Unit) {
        val host = bareHost(url).ifEmpty { source.raw }
        logger.log(LogLevel.INFO, "Sign-in needed for $host; opening the browser")
        onPhase(JobPhase.SigningIn(host))
        try {
            web.presentLogin(url, source.raw)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            throw SourceException.Blocked("sign in to $host in the Browser tab, then retry (${e.message})")
        }
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
