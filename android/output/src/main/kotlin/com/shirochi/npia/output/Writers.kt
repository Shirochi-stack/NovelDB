package com.shirochi.npia.output

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.BookInfo
import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.core.NamingMode
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.core.SharedSettings
import java.io.File

/** Everything a writer needs for one finished book. */
class BookBundle(
    val book: BookInfo,
    /** Chapters in reading order, notices first when included. */
    val chapters: List<ChapterContent>,
    /** Processed image bytes keyed by `ImageRef.filename`. */
    val images: Map<String, ByteArray> = emptyMap(),
    val cover: Cover? = null,
    val settings: AppSettings,
) {
    class Cover(val data: ByteArray, val fileExtension: String)
}

interface BookWriter {
    val format: OutputFormat

    /** Writes the file(s) into `directory` and returns the main output (a folder for separate TXT). */
    suspend fun write(bundle: BookBundle, baseName: String, directory: File): File
}

class OutputNotImplemented(format: OutputFormat) : Exception("${format.raw.uppercase()} output is not available in this build yet.")

object Writers {
    /** PDF needs Android graphics, so the app registers its writer here at startup. */
    @Volatile var pdfWriter: BookWriter = PendingWriter(OutputFormat.PDF)

    fun writer(format: OutputFormat): BookWriter = when (format) {
        OutputFormat.EPUB -> EpubWriter()
        OutputFormat.TXT -> TxtWriter()
        OutputFormat.HTML -> HtmlWriter()
        OutputFormat.PDF -> pdfWriter
    }

    /** Output file name without extension, following `namingMode` and `appendRange`. */
    fun baseName(book: BookInfo, settings: SharedSettings): String {
        var name = if (settings.namingMode == NamingMode.ID) book.id else book.title
        if (settings.appendRange && (settings.rangeFrom != null || settings.rangeTo != null)) {
            name += " (${settings.rangeFrom ?: 1}-${settings.rangeTo ?: "end"})"
        }
        val cleaned = name.map { if (it in "/\\:*?\"<>|" || it.isISOControl()) '_' else it }.joinToString("").trim()
        return if (cleaned.isEmpty()) book.id else cleaned.take(180)
    }
}

class PendingWriter(override val format: OutputFormat) : BookWriter {
    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = throw OutputNotImplemented(format)
}

/** TODO(npia core workstream): EPUB 3 via java.util.zip, matching the desktop `epub_generator.py` and the iOS `EPUBWriter`. */
class EpubWriter : BookWriter {
    override val format = OutputFormat.EPUB
    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = throw OutputNotImplemented(format)
}

/** TODO(npia core workstream): single-file HTML with embedded images. */
class HtmlWriter : BookWriter {
    override val format = OutputFormat.HTML
    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = throw OutputNotImplemented(format)
}
