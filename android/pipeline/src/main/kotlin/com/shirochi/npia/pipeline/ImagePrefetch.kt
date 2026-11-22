package com.shirochi.npia.pipeline

import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.ImageRef
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Semaphore
import kotlinx.coroutines.sync.withPermit
import java.util.concurrent.ConcurrentHashMap
import kotlin.coroutines.CoroutineContext

/**
 * Downloads a chapter's images as soon as the chapter is read, while the later chapters are still
 * being read. The image step used to start only after the last chapter: a long book waited through
 * every chapter list before its first image, and addresses a site signs for a while (kpage: two
 * hours) could run out first. The desktop does the same for kpage (`KpageImages`).
 *
 * The bytes wait in the job's `ImageStore` like the ones a source hands over itself; the image step
 * (`ImageStage`) waits for a download that is still on its way, takes the held bytes, and asks the
 * site itself only for what was never started or failed here (and reports it).
 */
class ImagePrefetch(
    private val http: HttpFetching,
    private val store: ImageStore,
    workers: Int,
    parent: CoroutineContext,
    /** Left free on the device: with less, images wait for the image step (which stores them compressed). */
    private val reserveBytes: Long = 512L * 1024 * 1024,
    private val logger: JobLogging = NullLogger,
) {
    private val job = SupervisorJob(parent[Job])
    private val scope = CoroutineScope(parent + job)
    private val slots = Semaphore(workers.coerceAtLeast(1))
    private val jobs = ConcurrentHashMap<String, Job>()
    private val arrived = ConcurrentHashMap.newKeySet<String>()

    /** How many images were started here. */
    val started: Int get() = jobs.size

    /** True when `filename` was downloaded here: its "✓" line is already in the log. */
    fun brought(filename: String): Boolean = filename in arrived

    /** A chapter that was just read: its images that nobody holds yet start downloading. */
    fun offer(result: ChapterResult) {
        if (result !is ChapterResult.Content || !job.isActive) return
        val page = result.ref.url
        val number = (if (result.ref.isNotice) "N" else "") + result.ref.index
        result.content.images.forEachIndexed { position, image ->
            if (image.data != null || store.holds(image.filename)) return@forEachIndexed
            if (!image.url.startsWith("http://", ignoreCase = true) && !image.url.startsWith("https://", ignoreCase = true)) return@forEachIndexed
            if (store.freeBytes() < reserveBytes) return
            // The image step's label for the image's log line: "[12] Image #3 [Chapter title]".
            val label = "[$number] Image #${position + 1} [${result.ref.title}]"
            jobs.computeIfAbsent(image.filename) { scope.launch { slots.withPermit { fetch(image, page, label) } } }
        }
    }

    /** Like the image step: asked again from the chapter's page when the server wants a Referer (fanfox). */
    private suspend fun fetch(image: ImageRef, page: String?, label: String) {
        val began = System.nanoTime()
        try {
            var response = http.get(image.url)
            if ((response.status == 401 || response.status == 403) && !page.isNullOrEmpty()) {
                response = http.get(image.url, mapOf("Referer" to page))
            }
            if (response.status in 200..299 && response.body.isNotEmpty()) {
                store.hold(image.filename, response.body)
                arrived += image.filename
                val seconds = ((System.nanoTime() - began) / 1e9).coerceAtLeast(0.001)
                logger.log(LogLevel.DEBUG, "  ✓ $label: ${ImageStage.formatSize(response.body.size.toLong())} " +
                    "[${ImageStage.formatSize((response.body.size / seconds).toLong())}/s]")
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // The image step asks again and reports what it cannot get.
        }
    }

    /** Waits for `filename`'s download when one was started here; its bytes are then held, unless it failed. */
    suspend fun await(filename: String) {
        jobs[filename]?.join()
    }

    /** Stops what is still on its way (the job ended or was stopped). */
    fun close() {
        job.cancel()
    }
}
