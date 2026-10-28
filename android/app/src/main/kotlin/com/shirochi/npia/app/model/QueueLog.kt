package com.shirochi.npia.app.model

import com.shirochi.npia.core.LogLevel
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.serialization.Serializable
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import java.util.TimeZone
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledExecutorService
import java.util.concurrent.TimeUnit

/** One line of the combined queue log. */
@Serializable
data class LogEntry(
    /** Epoch millis. */
    val time: Long,
    val jobId: String? = null,
    /** The job's title (else its address) when the line was written; shown if the job is gone. */
    val job: String = "",
    val level: LogLevel = LogLevel.INFO,
    val message: String,
    /** Position in the log, counting up; a view uses it to keep its place. Assigned by `QueueLog`. */
    val seq: Long = 0,
)

/**
 * The log of every job in the queue in time order: a ring buffer of the last `capacity` lines
 * that is also kept in a rolling file, so it survives a restart. Appending is cheap (the file is
 * written a moment later, in batches); `version` changes whenever the lines do.
 */
class QueueLog(
    private val file: File?,
    val capacity: Int = DEFAULT_CAPACITY,
    private val flushDelayMillis: Long = 400,
) {
    private val lock = Any()
    private val ring = ArrayDeque<LogEntry>()
    private val pending = ArrayList<LogEntry>()
    private var fileLines = 0
    private var rewrite = false
    private var flushScheduled = false
    private var nextSeq = 1L

    private val _version = MutableStateFlow(0L)
    /** Counts changes; read `snapshot()` when it moves. */
    val version: StateFlow<Long> = _version.asStateFlow()

    init {
        load()
    }

    fun append(entry: LogEntry) {
        synchronized(lock) {
            val numbered = entry.copy(seq = nextSeq++)
            ring.addLast(numbered)
            while (ring.size > capacity) ring.removeFirst()
            pending += numbered
            scheduleFlush()
        }
        _version.update { it + 1 }
    }

    fun append(level: LogLevel, message: String, jobId: String? = null, job: String = "", time: Long = System.currentTimeMillis()) =
        append(LogEntry(time, jobId, job, level, message))

    /** All lines, oldest first. */
    fun snapshot(): List<LogEntry> = synchronized(lock) { ring.toList() }

    val size: Int get() = synchronized(lock) { ring.size }

    /** Removes the lines of one job, or every line when `jobId` is null. */
    fun clear(jobId: String? = null) {
        synchronized(lock) {
            if (jobId == null) ring.clear() else ring.removeAll { it.jobId == jobId }
            pending.clear()
            rewrite = true
            scheduleFlush()
        }
        _version.update { it + 1 }
    }

    /** Writes what is waiting to the file now. */
    fun flush() {
        synchronized(lock) {
            flushScheduled = false
            val target = file ?: run {
                pending.clear()
                return
            }
            try {
                target.absoluteFile.parentFile?.mkdirs()
                if (rewrite || fileLines + pending.size > capacity * 2) {
                    val staged = File(target.path + ".tmp")
                    staged.bufferedWriter(Charsets.UTF_8).use { out ->
                        for (entry in ring) {
                            out.write(JobItem.json.encodeToString(LogEntry.serializer(), entry))
                            out.newLine()
                        }
                    }
                    if (!staged.renameTo(target)) {
                        target.delete()
                        staged.renameTo(target)
                    }
                    fileLines = ring.size
                    rewrite = false
                } else if (pending.isNotEmpty()) {
                    java.io.FileOutputStream(target, true).bufferedWriter(Charsets.UTF_8).use { out ->
                        for (entry in pending) {
                            out.write(JobItem.json.encodeToString(LogEntry.serializer(), entry))
                            out.newLine()
                        }
                    }
                    fileLines += pending.size
                }
            } catch (_: Exception) {
                // The log is a convenience; a failed write must not disturb a download.
            }
            pending.clear()
        }
    }

    private fun scheduleFlush() {
        if (file == null || flushScheduled) return
        flushScheduled = true
        executor.schedule({ flush() }, flushDelayMillis, TimeUnit.MILLISECONDS)
    }

    private fun load() {
        val source = file?.takeIf { it.isFile } ?: return
        val loaded = ArrayDeque<LogEntry>()
        var lines = 0
        try {
            source.bufferedReader(Charsets.UTF_8).useLines { sequence ->
                for (line in sequence) {
                    if (line.isBlank()) continue
                    lines++
                    runCatching { JobItem.json.decodeFromString(LogEntry.serializer(), line) }.getOrNull()?.let {
                        loaded.addLast(it)
                        if (loaded.size > capacity) loaded.removeFirst()
                    }
                }
            }
        } catch (_: Exception) {
            // A damaged file starts the log over.
        }
        loaded.forEach { ring.addLast(it.copy(seq = nextSeq++)) }
        fileLines = lines
        // Lines that did not parse or are over the cap are dropped by the next write.
        rewrite = lines != loaded.size
    }

    companion object {
        const val DEFAULT_CAPACITY = 5000

        private val executor: ScheduledExecutorService by lazy {
            Executors.newSingleThreadScheduledExecutor { runnable -> Thread(runnable, "queue-log").apply { isDaemon = true } }
        }
    }
}

/** Which lines the log view shows. */
object LogFilter {
    /** The lines of `jobId` (every job when null), without the debug-level "details" unless `details`. */
    fun apply(entries: List<LogEntry>, jobId: String? = null, details: Boolean = true): List<LogEntry> =
        entries.filter { (jobId == null || it.jobId == jobId) && (details || it.level != LogLevel.DEBUG) }
}

/** Text forms of log lines. */
object LogLines {
    fun clock(time: Long, zone: TimeZone = TimeZone.getDefault()): String =
        SimpleDateFormat("HH:mm:ss", Locale.US).apply { timeZone = zone }.format(Date(time))

    /** Three letters for the level column. */
    fun tag(level: LogLevel): String = when (level) {
        LogLevel.DEBUG -> "DBG"
        LogLevel.INFO -> "INF"
        LogLevel.WARNING -> "WRN"
        LogLevel.ERROR -> "ERR"
    }

    /**
     * `title` cut to `max` characters with an ellipsis. An address (a job that has no title yet)
     * loses its scheme and keeps its end, which is the part that tells two books apart.
     */
    fun shortTitle(title: String, max: Int = 18): String {
        val clean = title.trim().replace(Regex("\\s+"), " ")
        if (clean.startsWith("http://") || clean.startsWith("https://")) {
            val address = clean.substringAfter("://").trimEnd('/')
            return if (address.length <= max) address else "…" + address.takeLast(max - 1)
        }
        return if (clean.length <= max) clean else clean.take(max - 1).trimEnd() + "…"
    }

    /**
     * "12:03:41 WRN Tower of God: Chapter 4 failed: HTTP 503". `titles` maps job ids to their
     * current titles; a job that is gone keeps the title written with the line.
     */
    fun format(entry: LogEntry, titles: Map<String, String> = emptyMap(), zone: TimeZone = TimeZone.getDefault()): String {
        val title = shortTitle(entry.jobId?.let { titles[it] } ?: entry.job.ifEmpty { "queue" })
        return "${clock(entry.time, zone)} ${tag(entry.level)} $title: ${entry.message}"
    }

    /** The text Copy and Share hand over: one formatted line per entry. */
    fun text(entries: List<LogEntry>, titles: Map<String, String> = emptyMap(), zone: TimeZone = TimeZone.getDefault()): String =
        entries.joinToString("\n") { format(it, titles, zone) }
}
