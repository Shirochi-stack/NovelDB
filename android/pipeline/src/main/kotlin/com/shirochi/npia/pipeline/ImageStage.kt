package com.shirochi.npia.pipeline

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.ImageProcessing
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import com.shirochi.npia.core.ProcessedImage
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.core.sniffImageExtension
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.sync.withPermit
import kotlinx.coroutines.withContext
import kotlin.coroutines.coroutineContext
import kotlin.math.sqrt

/**
 * Downloads and recompresses chapter images with bounded concurrency (`imageWorkers` downloads,
 * `compressionWorkers` encodes), then points the chapter HTML at the final file names.
 * Mirrors the iOS `ImageStage`.
 */
class ImageStage(
    private val http: HttpFetching,
    private val settings: AppSettings,
    private val source: SourceId,
    private val processor: ImageProcessing,
    private val logger: JobLogging = NullLogger,
) {
    class Output(
        val chapters: List<ChapterContent>,
        /** Processed bytes keyed by the (possibly renamed) `ImageRef.filename`. */
        val images: Map<String, ByteArray>,
        val failed: Int,
    )

    suspend fun run(chapters: List<ChapterContent>, onProgress: (done: Int, total: Int) -> Unit = { _, _ -> }): Output {
        if (settings.images.saveImageUrlsOnly) {
            return Output(chapters.map(::remoteOnly), emptyMap(), 0)
        }

        // One download per file name, in reading order.
        val unique = chapters.flatMap { it.images }.distinctBy { it.filename }
        val downloads = Semaphore(settings.effective(source).imageWorkers.coerceAtLeast(1))
        val encodes = Semaphore(settings.images.compressionWorkers.coerceAtLeast(1))
        val progress = Mutex()
        var done = 0
        onProgress(0, unique.size)

        val results = coroutineScope {
            unique.map { ref ->
                async {
                    // The download slot is held through encoding so raw bytes never pile up.
                    val image = downloads.withPermit {
                        download(ref)?.let { data -> encodes.withPermit { encode(data) } }
                    }
                    progress.withLock {
                        done += 1
                        onProgress(done, unique.size)
                    }
                    ref.filename to image
                }
            }.awaitAll()
        }

        val processed = results.mapNotNull { (name, image) -> image?.let { name to it } }.toMap()
        val failed = results.count { it.second == null }
        val renames = renames(unique.map { it.filename }, processed)
        val images = processed.entries.associate { (name, image) -> (renames[name] ?: name) to image.data }
        return Output(chapters.map { apply(renames, it, processed.keys) }, images, failed)
    }

    private suspend fun download(ref: ImageRef): ByteArray? {
        coroutineContext.ensureActive()
        ref.data?.let { return it }
        return try {
            val response = http.get(ref.url)
            if (response.status !in 200..299 || response.body.isEmpty()) {
                logger.log(LogLevel.WARNING, "Image failed (${response.status}): ${ref.url}")
                null
            } else {
                response.body
            }
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            logger.log(LogLevel.WARNING, "Image failed: ${ref.url}: ${e.message}")
            null
        }
    }

    private suspend fun encode(data: ByteArray): ProcessedImage = withContext(Dispatchers.Default) {
        try {
            processor.process(data)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            ProcessedImage(data, sniffImageExtension(data))
        }
    }

    companion object {
        /**
         * Old name -> new name for every image whose extension changed. Collisions get a numeric
         * suffix so two sources never overwrite each other.
         */
        fun renames(names: List<String>, processed: Map<String, ProcessedImage>): Map<String, String> {
            val taken = names.toMutableSet()
            val renames = mutableMapOf<String, String>()
            for (name in names) {
                val image = processed[name] ?: continue
                if (image.fileExtension == "bin") continue
                val current = ImageReferences.extension(name)
                if (current == image.fileExtension || (current == "jpeg" && image.fileExtension == "jpg")) continue
                var candidate = ImageReferences.renamed(name, image.fileExtension)
                var counter = 2
                while (candidate in taken) {
                    val stem = ImageReferences.renamed(name, "x").removeSuffix(".x")
                    candidate = "${stem}_$counter.${image.fileExtension}"
                    counter += 1
                }
                taken += candidate
                renames[name] = candidate
            }
            return renames
        }

        /** Renames refs and HTML references; drops refs whose download failed. */
        fun apply(renames: Map<String, String>, chapter: ChapterContent, kept: Set<String>): ChapterContent {
            val local = chapter.images.mapNotNull { ref -> renames[ref.filename]?.let { ref.filename to it } }.toMap()
            val html = if (local.isEmpty()) chapter.html else ImageReferences.rewrite(chapter.html, local)
            val images = chapter.images.filter { it.filename in kept }.map { ref ->
                ImageRef(ref.url, renames[ref.filename] ?: ref.filename)
            }
            return chapter.copy(html = html, images = images)
        }

        /** `saveImageUrlsOnly`: point every reference at the remote URL and download nothing. */
        fun remoteOnly(chapter: ChapterContent): ChapterContent {
            val remote = chapter.images
                .filter { it.url.startsWith("http://", ignoreCase = true) || it.url.startsWith("https://", ignoreCase = true) }
                .associate { it.filename to it.url }
            // The whole value becomes the remote URL, including any `../` prefix.
            val html = if (remote.isEmpty()) chapter.html else ImageReferences.rewriteValues(chapter.html) { value ->
                remote[value] ?: remote.entries.firstOrNull { value.endsWith("/" + it.key) }?.value
            }
            return chapter.copy(html = html, images = emptyList())
        }

        /**
         * Next quality for one `recompressTargetMb` pass, or null when it cannot go lower. Mirrors
         * the desktop heuristic: quality * sqrt(target / size), at least 10 lower per pass, floor 10.
         */
        fun nextRecompressQuality(current: Int, size: Long, target: Long): Int? {
            val minimum = 10
            val ratio = target.toDouble() / size.coerceAtLeast(1)
            var next = maxOf(minimum, (current * sqrt(ratio)).toInt())
            if (next >= current) next = maxOf(minimum, current - 10)
            return if (next == current) null else next
        }
    }
}
