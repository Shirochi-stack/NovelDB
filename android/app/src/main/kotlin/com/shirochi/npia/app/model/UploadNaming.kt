package com.shirochi.npia.app.model

import java.net.URI

/**
 * The id that names uploaded files whose real name is not plain ASCII (hosts strip the rest, so a
 * Korean title arrives as "epub.epub"): `<id>.epub`. The id comes from the job.
 */
object UploadNaming {
    private val SAFE_ID = Regex("[A-Za-z0-9_-]{1,40}")

    /**
     * The adapter's book id when it is a safe token (for npia that is the novel number), else the
     * last numeric segment of the job URL, else a short ASCII slug of its path, else "book".
     */
    fun bookId(url: String, bookId: String?): String {
        bookId?.trim()?.takeIf { SAFE_ID.matches(it) }?.let { return it }
        val path = runCatching { URI(url.trim()).path }.getOrNull().orEmpty()
        val segments = path.split('/').filter { it.isNotEmpty() }
        segments.lastOrNull { segment -> segment.all { it in '0'..'9' } }?.let { return it }
        val slug = segments.joinToString("-").lowercase().replace(Regex("[^a-z0-9]+"), "-").trim('-').take(32).trim('-')
        return slug.ifEmpty { "book" }
    }

    fun forJob(job: JobItem): String = bookId(job.url, job.bookId)
}
