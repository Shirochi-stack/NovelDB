package com.shirochi.npia.app.model

import java.util.Locale
import kotlin.math.roundToInt

/** One upload as the screens show it: the file, the host being tried, and byte progress. */
data class UploadState(
    /** Absolute path of the uploaded file; also the key in `DownloadManager.uploadStates`. */
    val path: String,
    val fileName: String,
    val jobId: String? = null,
    val phase: Phase = Phase.RUNNING,
    /** Display name of the host being tried, which changes as the configured hosts are tried in order. */
    val host: String? = null,
    val sentBytes: Long = 0,
    val totalBytes: Long = 0,
    val link: String? = null,
    val error: String? = null,
) {
    enum class Phase { RUNNING, DONE, FAILED }

    val fraction: Float
        get() = if (totalBytes > 0) (sentBytes.toDouble() / totalBytes).coerceIn(0.0, 1.0).toFloat() else 0f

    /** Whole percent; 100 only once every byte is sent. */
    val percent: Int
        get() {
            if (totalBytes <= 0) return 0
            val value = (100.0 * sentBytes / totalBytes).roundToInt()
            return if (sentBytes < totalBytes) minOf(value, 99) else value
        }

    /** "Uploading to Gofile… 42% (6.3 / 15 MB)", "Uploaded — <link>" or "Upload failed: <reason>". */
    val statusLine: String
        get() = when (phase) {
            Phase.RUNNING -> {
                val target = host?.let { "Uploading to $it…" } ?: "Preparing upload…"
                if (totalBytes > 0) "$target $percent% (${sizeProgress(sentBytes, totalBytes)})" else target
            }
            Phase.DONE -> "Uploaded — ${link.orEmpty()}"
            Phase.FAILED -> "Upload failed: ${error ?: "unknown error"}"
        }

    companion object {
        private const val KIB = 1024.0
        private const val MIB = KIB * 1024

        /** "6.3 / 15 MB": both sizes in the unit that suits the total. */
        fun sizeProgress(sent: Long, total: Long): String {
            val (divisor, unit) = when {
                total >= MIB -> MIB to "MB"
                total >= KIB -> KIB to "KB"
                else -> 1.0 to "B"
            }
            return "${amount(sent / divisor, unit)} / ${amount(total / divisor, unit)} $unit"
        }

        private fun amount(value: Double, unit: String): String =
            if (unit == "B") value.toLong().toString() else String.format(Locale.US, "%.1f", value).removeSuffix(".0")
    }
}

/** Upload of every file of one finished run, started from the summary notification. */
data class UploadBatch(
    val runId: String,
    val total: Int,
    /** Files finished so far, successful or not. */
    val done: Int = 0,
    /** Path of the file being uploaded now. */
    val currentPath: String? = null,
) {
    /** 0..1 over the whole batch, counting the current file's own progress. */
    fun fraction(current: UploadState?): Float {
        if (total <= 0) return 0f
        val part = if (current != null && current.phase == UploadState.Phase.RUNNING) current.fraction else 0f
        return ((done + part) / total).coerceIn(0f, 1f)
    }

    /** "Uploading 2/5 files… 37%". */
    fun statusLine(current: UploadState?): String {
        val index = minOf(done + 1, total)
        val fraction = fraction(current)
        val percent = (fraction * 100).roundToInt().let { if (fraction < 1f) minOf(it, 99) else it }
        return "Uploading $index/$total files… $percent%"
    }
}
