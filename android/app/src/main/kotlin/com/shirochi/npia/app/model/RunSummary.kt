package com.shirochi.npia.app.model

import kotlinx.serialization.Serializable
import java.util.UUID

/**
 * What one run of the queue produced, from the first download that started until the queue went
 * idle. The summary notification offers to share or upload all of its files at once, so it is
 * saved (`last-run.json`) and its notification actions only carry the run id.
 */
@Serializable
data class RunSummary(
    val id: String = UUID.randomUUID().toString(),
    /** Output files of the successful downloads, relative to the output folder. */
    val fileNames: List<String> = emptyList(),
    /** Upload name id (see `UploadNaming`) of each file in `fileNames`. */
    val fileIds: Map<String, String> = emptyMap(),
    /** Title of the only successful download, for the notification title when there is just one. */
    val title: String? = null,
    val succeeded: Int = 0,
    val failed: Int = 0,
    val cancelled: Int = 0,
    /** Share links of the files uploaded from the notification. */
    val links: List<String> = emptyList(),
) {
    val hasFiles: Boolean get() = succeeded > 0 && fileNames.isNotEmpty()

    val notificationTitle: String
        get() = when {
            succeeded == 1 -> "Downloaded ${title ?: "1 book"}"
            succeeded > 1 -> "Downloaded $succeeded books"
            failed == 1 -> "Download failed"
            else -> "$failed downloads failed"
        }

    /** "3 done, 1 failed". */
    val notificationText: String
        get() = listOfNotNull(
            "$succeeded done".takeIf { succeeded > 0 },
            "$failed failed".takeIf { failed > 0 },
            "$cancelled cancelled".takeIf { cancelled > 0 },
        ).joinToString(", ")

    companion object {
        /**
         * The summary of the jobs that ended in one run, or null when there is nothing to report:
         * nothing succeeded and either nothing failed or the run was stopped.
         */
        fun of(jobs: List<JobItem>): RunSummary? {
            val done = jobs.filter { it.state == JobState.FINISHED }
            val failed = jobs.count { it.state == JobState.FAILED }
            val cancelled = jobs.count { it.state == JobState.CANCELLED }
            if (done.isEmpty() && (failed == 0 || cancelled > 0)) return null
            return RunSummary(
                fileNames = done.flatMap { it.fileNames }.distinct(),
                fileIds = done.flatMap { job -> job.fileNames.map { it to UploadNaming.forJob(job) } }.toMap(),
                title = done.singleOrNull()?.displayTitle,
                succeeded = done.size,
                failed = failed,
                cancelled = cancelled,
            )
        }
    }
}
