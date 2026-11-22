package com.shirochi.npia.pipeline

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.HttpFetching
import com.shirochi.npia.core.HttpRequest
import com.shirochi.npia.core.HttpResponse
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.NullLogger
import com.shirochi.npia.output.FolderWriter
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.launch
import java.util.concurrent.ConcurrentHashMap

/**
 * Prepares Folder and ZIP chapters (images encoded, strips stitched) while the other chapters are
 * still being read. The image step and the folder writer used to start only after the last chapter,
 * so a long comic was stitched in one go at the very end.
 *
 * Every chapter that was read is handed over (`offer`); one worker takes them a chapter at a time,
 * runs the image step for that chapter alone (it waits for the chapter's images, which started
 * downloading when the chapter was read) and writes it into the prepared export. The pipeline's own
 * image step then handles only the chapters that are not in `done`.
 *
 * `finish` ends it: the chapter in the works is completed, and the ones still waiting are left to the
 * pipeline's image step, which has the whole book's progress and runs chapters side by side.
 */
class EarlyChapters(
    http: HttpFetching,
    /** The image step as the pipeline builds it, on the client given. */
    private val newStage: (HttpFetching) -> ImageStage,
    private val prepared: FolderWriter.Prepared,
    private val preserveSources: Boolean,
    private val stitching: Boolean,
    private val logger: JobLogging = NullLogger,
) {
    /** A chapter that is already in the prepared export: as the image step returned it. */
    class Done(val chapter: ChapterContent, val images: Map<String, ByteArray>, val failed: Int)

    @Volatile
    private var offline = false

    @Volatile
    private var ending = false

    /** After a stop nothing new is asked for: the chapter in the works is finished from what is held. */
    private val client = object : HttpFetching {
        override suspend fun send(request: HttpRequest): HttpResponse =
            if (offline) HttpResponse(request.url, 404, emptyMap(), byteArrayOf()) else http.send(request)
    }

    // Not a child of the download: on a stop that generates files, the chapter in the works is finished
    // (`finish`). `close` is what ends it at once.
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.Default)
    private val queue = Channel<ChapterContent>(Channel.UNLIMITED)
    private val names = HashSet<String>()
    private val finished = ConcurrentHashMap<String, Done>()
    private val worker = scope.launch {
        for (chapter in queue) {
            if (ending) break
            prepare(chapter)
        }
    }

    /** The chapters written so far, by chapter id. */
    val done: Map<String, Done> get() = finished

    /** A chapter that was just read, from any thread. */
    fun offer(result: ChapterResult) {
        if (result !is ChapterResult.Content || ending) return
        val chapter = result.content
        synchronized(names) {
            val files = chapter.images.map { it.filename }
            // An image two chapters share belongs to the image step that has every chapter at once.
            val shared = files.any { it in names }
            names += files
            if (shared) return
        }
        queue.trySend(chapter)
    }

    private suspend fun prepare(chapter: ChapterContent) {
        try {
            var written = false
            val output = newStage(client).run(listOf(chapter), preserveChapterSources = preserveSources,
                onChapterReady = { ready, bytes ->
                    if (stitching && ready.images.size > 1 && ready.images.all { bytes.containsKey(it.filename) }) {
                        logger.log(LogLevel.INFO, "Stitching chapter ${ready.ref.index}: ${ready.ref.title}")
                    }
                    // `close` (a stop) ends this between two strips; the writer then removes what it wrote
                    // of the chapter, so a chapter is in the export whole or not at all.
                    prepared.append(ready, bytes)
                    written = true
                })
            if (written) finished[chapter.ref.id] = Done(output.chapters.single(), output.images, output.failed)
        } catch (e: CancellationException) {
            throw e
        } catch (e: Exception) {
            logger.log(LogLevel.WARNING, "Chapter ${chapter.ref.index} could not be prepared early (${e.message ?: e}); it is written with the rest.")
        }
    }

    /**
     * Ends the early work and waits for the chapter in the works. `stopped`: the download was stopped,
     * so that chapter is finished without another request.
     */
    suspend fun finish(stopped: Boolean = false) {
        if (stopped) offline = true
        ending = true
        queue.close()
        // A Stop ends this wait (the caller then calls `close`): it can be long, the chapter in the
        // works may still be waiting for its images.
        worker.join()
    }

    /** Ends everything at once, the stitching in progress included (the job failed, or Stop was pressed). */
    fun close() {
        ending = true
        scope.cancel()
    }
}

/** Several image maps read as one (each keeps its bytes on disk until asked). */
internal class JoinedImages(private val parts: List<Map<String, ByteArray>>) : AbstractMap<String, ByteArray>() {
    override val size: Int get() = parts.sumOf { it.size }
    override fun containsKey(key: String): Boolean = parts.any { it.containsKey(key) }
    override fun get(key: String): ByteArray? = parts.firstOrNull { it.containsKey(key) }?.get(key)
    override val keys: Set<String> get() = parts.flatMapTo(LinkedHashSet()) { it.keys }

    override val entries: Set<Map.Entry<String, ByteArray>> = object : AbstractSet<Map.Entry<String, ByteArray>>() {
        override val size: Int get() = this@JoinedImages.size
        override fun iterator(): Iterator<Map.Entry<String, ByteArray>> =
            parts.asSequence().flatMap { it.entries.asSequence() }.iterator()
    }
}
