package com.shirochi.npia.output.pdf

import com.shirochi.npia.output.BookBundle
import java.net.URLDecoder

/**
 * The book reduced to what a PDF renderer draws (port of the iOS `PDFDocumentPlan`). The Android
 * renderer lives in the app because it needs `android.graphics.pdf`.
 */
class PdfDocumentPlan(bundle: BookBundle) {
    class Chapter(val title: String, val blocks: List<PdfBlock>)

    val title: String = bundle.book.title
    val author: String? = bundle.book.author
    val details: List<String> = listOfNotNull(bundle.book.status, bundle.book.tags.takeIf { it.isNotEmpty() }?.joinToString(", "))
    val synopsis: String? = bundle.book.synopsis
    val language: String = bundle.book.language
    val cover: ByteArray? = bundle.cover?.data
    val showToc: Boolean = bundle.settings.text.pdfToc
    /** TOC entries carry dot leaders and page numbers (two-pass layout). */
    val counterLayout: Boolean = bundle.settings.text.pdfToc && bundle.settings.text.pdfCounterLayout
    val pageNumbers: Boolean = bundle.settings.text.pdfPageNumbers
    val chapters: List<Chapter>

    private val images: Map<String, ByteArray> = bundle.images
    // File name -> full name, so finding an image by its leaf reads only that image.
    private val imagesByLeaf: Map<String, String> = bundle.images.keys.associateBy { it.substringAfterLast('/') }

    init {
        val shared = bundle.settings.effective(bundle.book.source)
        chapters = bundle.chapters.map { chapter ->
            val title = if (shared.numberChapters && !chapter.ref.isNotice) "${chapter.ref.index}. ${chapter.ref.title}" else chapter.ref.title
            Chapter(title, PdfHtmlParser.blocks(chapter.html))
        }
    }

    /** Resolves an `<img src>` against the bundle: exact, without `../` or `./`, then by file name. */
    fun image(src: String): ByteArray? {
        images[src]?.let { return it }
        var trimmed = src
        while (trimmed.startsWith("../")) trimmed = trimmed.substring(3)
        while (trimmed.startsWith("./")) trimmed = trimmed.substring(2)
        images[trimmed]?.let { return it }
        val leaf = src.substringAfterLast('/')
        val name = imagesByLeaf[leaf] ?: runCatching { imagesByLeaf[URLDecoder.decode(leaf, "UTF-8")] }.getOrNull()
        return name?.let { images[it] }
    }
}
