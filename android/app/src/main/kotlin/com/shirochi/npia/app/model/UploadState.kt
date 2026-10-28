package com.shirochi.npia.app.model

import java.util.Locale
import kotlin.math.roundToInt

/** One upload as the screens show it: the file, the host being tried, and byte progress. */
data class UploadState(
    /** Absolute path of the uploaded file; also the key in `DownloadManager.uploadStates`. */
    val path: String,
    val fileName: String,
    val jobId: String? = null,
    /** Names the upload when the file name is not plain ASCII; kept so Retry uploads under the same name. */
    val bookId: String? = null,
    /** Absolute paths of every file this upload carries: just `path` for a file, several for a ZIP. */
    val paths: List<String> = listOf(path),
    /** For a ZIP: the original file names inside it. */
    val contents: List<String> = emptyList(),
    /** "Preparing ZIP 1 of 3" while the ZIP is being built, before a host is tried. */
    val preparing: String? = null,
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
                val what = if (contents.isNotEmpty()) "Uploading $fileName" else "Uploading"
                val target = host?.let { "$what to $it…" } ?: preparing?.let { "$it…" } ?: "Preparing upload…"
                if (totalBytes > 0) "$target $percent% (${sizeProgress(sentBytes, totalBytes)})" else target
            }
            Phase.DONE -> if (contents.isNotEmpty()) "Uploaded in $fileName — ${link.orEmpty()}" else "Uploaded — ${link.orEmpty()}"
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

/** Upload of several files at once (a finished run, or jobs picked in the Queue); each upload is a file or a ZIP. */
data class UploadBatch(
    val runId: String,
    /** Uploads in the batch: files, and ZIPs of several files. */
    val total: Int,
    /** Uploads finished so far, successful or not. */
    val done: Int = 0,
    /** Key (`UploadState.path`) of the upload going on now. */
    val currentPath: String? = null,
    /** "Preparing ZIP 1 of 3" while a ZIP is being built. */
    val preparing: String? = null,
    /** How many of the `total` uploads are ZIPs. */
    val zips: Int = 0,
) {
    /** 0..1 over the whole batch, counting the current file's own progress. */
    fun fraction(current: UploadState?): Float {
        if (total <= 0) return 0f
        val part = if (current != null && current.phase == UploadState.Phase.RUNNING) current.fraction else 0f
        return ((done + part) / total).coerceIn(0f, 1f)
    }

    /** "Uploading 2/5 files… 37%", "Uploading 2/3 ZIPs… 37%" or "Preparing ZIP 1 of 3…". */
    fun statusLine(current: UploadState?): String {
        if (preparing != null) return "$preparing…"
        val index = minOf(done + 1, total)
        val fraction = fraction(current)
        val percent = (fraction * 100).roundToInt().let { if (fraction < 1f) minOf(it, 99) else it }
        val noun = when {
            zips == 0 -> "files"
            zips >= total -> "ZIPs"
            else -> "uploads"
        }
        return "Uploading $index/$total $noun… $percent%"
    }
}
