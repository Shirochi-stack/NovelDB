package com.shirochi.npia.app.model

import com.shirochi.npia.upload.UploadResult
import kotlinx.serialization.Serializable
import java.util.UUID

/**
 * What one upload request produced: a link per ZIP (or per lone file), and what failed. It is
 * saved (`last-upload.json`) so the result notification's Share and Copy buttons only carry its id.
 */
@Serializable
data class UploadReport(
    val id: String = UUID.randomUUID().toString(),
    val uploads: List<UploadResult> = emptyList(),
    /** One line per upload that failed: "NovelDB_3_EPUBs.zip: Gofile rejected the upload". */
    val failures: List<String> = emptyList(),
    /** Files the request covered, uploaded or not. */
    val files: Int = 0,
) {
    val links: List<String> get() = uploads.map { it.link }

    private val hasArchives: Boolean get() = uploads.any { it.isArchive }

    /** Files that went up, counting the ones inside ZIPs. */
    val uploadedFiles: Int get() = uploads.sumOf { if (it.isArchive) it.contents.size else 1 }

    /** "Uploaded 11 files in 3 links", "Uploaded 4 of 11 files", "Upload failed". */
    val notificationTitle: String
        get() = when {
            uploads.isEmpty() -> "Upload failed"
            failures.isNotEmpty() -> "Uploaded $uploadedFiles of ${maxOf(files, uploadedFiles)} files"
            uploads.size < uploadedFiles -> "Uploaded ${fileCount(uploadedFiles)} in ${uploads.size} ${if (uploads.size == 1) "link" else "links"}"
            else -> "Uploaded ${fileCount(uploadedFiles)}"
        }

    /** The collapsed notification text: the one link, how many are ready, or the first failure. */
    val notificationText: String
        get() = when {
            failures.isNotEmpty() -> failures.first()
            uploads.size == 1 -> uploads.first().link
            else -> "${uploads.size} links ready"
        }

    /**
     * What Copy and Share links hand over. Plain links, one per line, when no ZIP was made;
     * otherwise each link under what it holds ("NovelDB_3_EPUBs.zip (3 books)").
     */
    val shareText: String
        get() = if (!hasArchives) links.joinToString("\n") else uploads.joinToString("\n\n") { "${it.label}\n${it.link}" }

    /** The expanded notification: the share text, then the failures. */
    val details: String get() = listOf(shareText, failures.joinToString("\n")).filter { it.isNotEmpty() }.joinToString("\n\n")

    private fun fileCount(count: Int) = if (count == 1) "1 file" else "$count files"
}
