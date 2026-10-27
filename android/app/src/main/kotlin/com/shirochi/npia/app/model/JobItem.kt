package com.shirochi.npia.app.model

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.SourceId
import com.shirochi.npia.pipeline.JobPhase
import com.shirochi.npia.upload.UploadResult
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import java.io.File
import java.util.UUID

@Serializable
enum class JobState {
    @SerialName("queued") QUEUED,
    @SerialName("running") RUNNING,
    @SerialName("finished") FINISHED,
    @SerialName("failed") FAILED,
    @SerialName("cancelled") CANCELLED;

    val isActive: Boolean get() = this == QUEUED || this == RUNNING
}

/**
 * One download in the queue, persisted between launches (iOS `JobItem`). File names are relative
 * to the output folder so they survive the app's storage path changing.
 */
@Serializable
data class JobItem(
    val id: String = UUID.randomUUID().toString(),
    val url: String,
    val source: SourceId? = null,
    val title: String? = null,
    /** The adapter's id for the book (for npia the novel number); names uploaded files. */
    val bookId: String? = null,
    /** The settings this job runs with (shared settings plus the Add screen's choices). */
    val settings: AppSettings = AppSettings(),
    val state: JobState = JobState.QUEUED,
    val status: String = "Queued",
    val fraction: Double = 0.0,
    val error: String? = null,
    val fileNames: List<String> = emptyList(),
    val uploads: List<UploadResult> = emptyList(),
    val skipped: List<String> = emptyList(),
    val log: List<String> = emptyList(),
    val createdAt: Long = System.currentTimeMillis(),
    val finishedAt: Long? = null,
) {
    val displayTitle: String get() = title ?: url

    fun files(directory: File): List<File> = fileNames.map { File(directory, it) }

    fun applying(phase: JobPhase): JobItem = when (phase) {
        JobPhase.Queued -> copy(status = "Queued", fraction = 0.0)
        JobPhase.FetchingBook -> copy(status = "Reading book info", fraction = 0.02)
        is JobPhase.FetchingChapters -> copy(
            status = "Chapters ${phase.done}/${phase.total}",
            fraction = 0.05 + 0.6 * ratio(phase.done, phase.total),
        )
        is JobPhase.DownloadingImages -> copy(
            status = if (phase.total == 0) "No images" else "Images ${phase.done}/${phase.total}",
            fraction = 0.65 + 0.25 * ratio(phase.done, phase.total),
        )
        is JobPhase.Writing -> copy(status = "Writing ${phase.format.raw.uppercase()}", fraction = maxOf(fraction, 0.92))
        is JobPhase.Uploading -> copy(status = "Uploading ${phase.fileName}", fraction = maxOf(fraction, 0.97))
        is JobPhase.SigningIn -> copy(status = "Sign in to ${phase.host} to continue")
        JobPhase.Finished -> copy(status = "Done", fraction = 1.0)
        is JobPhase.Failed -> copy(status = phase.message, error = phase.message)
    }

    fun appendingLog(line: String): JobItem = copy(log = (log + line).takeLast(MAX_LOG_LINES))

    /** Back to the queue with progress and results cleared. */
    fun resetForRetry(): JobItem = copy(
        state = JobState.QUEUED, status = "Queued", fraction = 0.0, error = null,
        skipped = emptyList(), log = emptyList(), finishedAt = null,
    )

    companion object {
        const val MAX_LOG_LINES = 500

        private fun ratio(done: Int, total: Int) = if (total > 0) minOf(1.0, done.toDouble() / total) else 1.0

        val json = Json {
            ignoreUnknownKeys = true
            encodeDefaults = true
            explicitNulls = false
            coerceInputValues = true
        }

        /**
         * Ids "Stop all" cancels: queued jobs first, so a running job that ends cannot start the
         * next queued one before it is cancelled.
         */
        fun stopOrder(jobs: List<JobItem>): List<String> =
            jobs.filter { it.state == JobState.QUEUED }.map { it.id } + jobs.filter { it.state == JobState.RUNNING }.map { it.id }

        /** Jobs that were running when the process died cannot resume mid-way. */
        fun interrupted(jobs: List<JobItem>): List<JobItem> = jobs.map {
            if (it.state.isActive) it.copy(state = JobState.FAILED, status = "Interrupted; tap Retry") else it
        }
    }
}
