package com.shirochi.npia.app.model

import com.shirochi.npia.upload.UploadStem

/**
 * What names a job's uploaded files when hosts would reduce the real name to nothing (a Korean
 * title arrives as `epub` or `-_.epub`): the stem `<source>_<book id>`, for example `npia_123456`.
 * A title that keeps at least three ASCII letters or digits is uploaded under those instead (see
 * `UploadNames` in the upload module). The id comes from the job.
 */
object UploadNaming {
    /**
     * The adapter's book id when it is a safe token (for npia that is the novel number), else the
     * last numeric segment of the job URL, else a short ASCII slug of its path, else "book".
     */
    fun bookId(url: String, bookId: String?): String = UploadStem.bookId(url, bookId)

    /** `<source>_<id>`, or just the id when the source is unknown. */
    fun stem(source: String?, id: String): String = UploadStem.of(source, id)

    /** The stem of a job's files. */
    fun forJob(job: JobItem): String = stem(job.source?.raw, bookId(job.url, job.bookId))
}
