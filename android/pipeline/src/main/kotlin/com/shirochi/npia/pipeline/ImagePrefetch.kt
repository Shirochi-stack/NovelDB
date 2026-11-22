package com.shirochi.npia.pipeline

import com.shirochi.npia.core.ChapterRef
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

    private val reported = ConcurrentHashMap.newKeySet<String>()

    /** How many images were started while the chapters were read: here, or by the source itself (`arrived`). */
    val started: Int get() = jobs.size + reported.size

    /** True when `filename`'s "✓" line is already in the log: it was downloaded here, or its source reported it. */
    fun brought(filename: String): Boolean = filename in arrived

    /**
     * An image its source downloaded itself, as part of reading the chapter (`SourceContext.onImage`):
     * its line is written now, like the ones downloaded here, and the image step does not repeat it.
     */
    fun arrived(ref: ChapterRef, position: Int, total: Int, filename: String, bytes: Int, seconds: Double) {
        reported += filename
        arrived += filename
        logger.log(LogLevel.DEBUG, line(label(ref, position), bytes, seconds))
        count(ref, total, bytes)
    }

    /** One chapter's images as they come in, for its progress lines. */
    private class Progress(var total: Int) {
        var done = 0
        var missed = 0
        var bytes = 0L
        val began = System.nanoTime()
        var said = began
        var quarter = 0
    }

    private val progress = HashMap<String, Progress>()

    /**
     * Counts one image of `ref` (`bytes` below zero: it could not be downloaded here). While a chapter's
     * images come in, a line at every quarter of them (and every three seconds when a quarter takes
     * longer) says how far they are, and one more when the last is in:
     * without them the log is silent from one chapter line to the next, however long the images take.
     * `total`: how many images the chapter has here, when the caller knows (zero: as `offer` counted).
     */
    private fun count(ref: ChapterRef, total: Int, bytes: Int) {
        val line: String
        synchronized(progress) {
            var state = progress.getOrPut(ref.id) { Progress(total) }
            // The chapter is read again (a page failed): counted from the start.
            if (total > 0 && state.done + state.missed >= state.total) state = Progress(total).also { progress[ref.id] = it }
            if (total > 0) state.total = total
            if (bytes >= 0) {
                state.done += 1
                state.bytes += bytes
            } else state.missed += 1
            val now = System.nanoTime()
            val last = state.done + state.missed >= state.total
            // A line at every quarter of the chapter's images, and every three seconds when a quarter takes longer.
            val quarter = state.done * 4 / state.total.coerceAtLeast(1)
            if (!last && (bytes < 0 || (quarter <= state.quarter && now - state.said < PROGRESS_NANOS))) return
            if (last && state.done == 0) return
            state.said = now
            state.quarter = maxOf(quarter, state.quarter)
            val seconds = ((now - state.began) / 1e9).coerceAtLeast(0.001)
            val size = "${ImageStage.formatSize(state.bytes)}, ${ImageStage.formatSize((state.bytes / seconds).toLong())}/s"
            val name = "${if (ref.isNotice) "notice" else "chapter"} ${ref.index}"
            line = if (!last) "  Images of $name: ${state.done}/${state.total} ($size)"
            else "  Images of $name: ${state.done} downloaded ($size)" +
                (if (state.missed > 0) "; ${state.missed} left for the image step" else "")
        }
        logger.log(LogLevel.INFO, line)
    }

    /** The image step's label for an image's log line: "[12] Image #3 [Chapter title]". */
    private fun label(ref: ChapterRef, position: Int) =
        "[${(if (ref.isNotice) "N" else "") + ref.index}] Image #${position + 1} [${ref.title}]"

    private fun line(label: String, bytes: Int, seconds: Double): String {
        val took = seconds.coerceAtLeast(0.001)
        return "  ✓ $label: ${ImageStage.formatSize(bytes.toLong())} [${ImageStage.formatSize((bytes / took).toLong())}/s]"
    }

    /** A chapter that was just read: its images that nobody holds yet start downloading. */
    fun offer(result: ChapterResult) {
        if (result !is ChapterResult.Content || !job.isActive) return
        val page = result.ref.url
        val ref = result.ref
        // The images of this chapter that are started here, counted first: the progress lines need the total.
        val wanted = result.content.images.withIndex().filter { (_, image) ->
            image.data == null && !store.holds(image.filename) && !jobs.containsKey(image.filename) &&
                (image.url.startsWith("http://", ignoreCase = true) || image.url.startsWith("https://", ignoreCase = true))
        }.distinctBy { it.value.filename }
        if (wanted.isEmpty() || store.freeBytes() < reserveBytes) return
        synchronized(progress) { progress[ref.id] = Progress(wanted.size) }
        for ((position, image) in wanted) {
            val label = label(ref, position)
            jobs.computeIfAbsent(image.filename) { scope.launch { slots.withPermit { fetch(image, page, label, ref) } } }
        }
    }

    /** Like the image step: asked again from the chapter's page when the server wants a Referer (fanfox). */
    private suspend fun fetch(image: ImageRef, page: String?, label: String, ref: ChapterRef) {
        val began = System.nanoTime()
        var size = -1
        try {
            var response = http.get(image.url)
            if ((response.status == 401 || response.status == 403) && !page.isNullOrEmpty()) {
                response = http.get(image.url, mapOf("Referer" to page))
            }
            if (response.status in 200..299 && response.body.isNotEmpty()) {
                store.hold(image.filename, response.body)
                arrived += image.filename
                logger.log(LogLevel.DEBUG, line(label, response.body.size, (System.nanoTime() - began) / 1e9))
                size = response.body.size
            }
        } catch (e: CancellationException) {
            throw e
        } catch (_: Exception) {
            // The image step asks again and reports what it cannot get.
        }
        count(ref, 0, size)
    }

    private companion object {
        /** A chapter whose images are still coming in says so this often when a quarter of them takes longer. */
        const val PROGRESS_NANOS = 3_000_000_000L
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
