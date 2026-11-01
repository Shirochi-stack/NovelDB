package com.shirochi.npia.app.model

import java.io.File

/** Where tapping a queue notification opens the app. */
sealed interface NotificationTarget {
    /** Queue, Jobs view. */
    data object Queue : NotificationTarget

    /** Queue, Log view, all jobs. */
    data object QueueLog : NotificationTarget

    /** One book's job detail page. */
    data class Job(val id: String) : NotificationTarget

    companion object {
        /**
         * The target for a run made of the jobs `jobIds` (downloading, waiting or just ended): the
         * combined log, unless the run is one book, which opens that book. No jobs, as when only
         * an upload runs, opens the queue.
         */
        fun forRun(jobIds: Collection<String>): NotificationTarget {
            val ids = jobIds.distinct()
            return when (ids.size) {
                0 -> Queue
                1 -> Job(ids.single())
                else -> QueueLog
            }
        }
    }
}

/** The two views of the Queue tab. */
enum class QueueView { JOBS, LOG }

/**
 * Which finished jobs are ticked in the Queue's selection mode. Only finished jobs with files left
 * on disk can be selected; `pruned` drops ids that stopped qualifying (removed, files deleted).
 */
data class QueueSelection(val active: Boolean = false, val ids: Set<String> = emptySet()) {
    val count: Int get() = ids.size

    fun isSelected(id: String): Boolean = id in ids

    /** Enters selection mode, with `id` ticked when given (long-press on a row). */
    fun begin(id: String? = null): QueueSelection = QueueSelection(true, if (id != null) setOf(id) else emptySet())

    fun toggle(id: String): QueueSelection = copy(active = true, ids = if (id in ids) ids - id else ids + id)

    /** Ticks every id in `selectable`. */
    fun selectAll(selectable: Collection<String>): QueueSelection = copy(active = true, ids = selectable.toSet())

    fun clear(): QueueSelection = QueueSelection()

    fun pruned(selectable: Collection<String>): QueueSelection = copy(ids = ids.filter { it in selectable }.toSet())

    companion object {
        /** Finished jobs that still have at least one output file, in the order the queue shows them. */
        fun selectable(jobs: List<JobItem>, outputDirectory: File): List<JobItem> =
            jobs.filter { it.state == JobState.FINISHED && it.files(outputDirectory).any { file -> file.exists() } }

        /** The existing files of the selected jobs, in queue order. */
        fun files(jobs: List<JobItem>, selection: QueueSelection, outputDirectory: File): List<File> =
            jobs.filter { it.id in selection.ids }.flatMap { it.files(outputDirectory) }.filter { it.exists() }
    }
}
