package com.shirochi.npia.output.pdf

import com.shirochi.npia.core.ChapterContent
import com.shirochi.npia.output.BookBundle
import com.shirochi.npia.output.HtmlText
import java.net.URLDecoder

/**
 * `ImageSettings.longImageLayout`: webtoon-style chapters made of tall image strips are laid out
 * edge to edge with no chapter title, as in the desktop `_long_image_layout_css`. EPUB, HTML, and
 * PDF writers can share these rules.
 */
object LongImageLayout {
    /** Marker class the desktop ntk webtoon pages use. */
    const val PAGE_CLASS = "ntk-webtoon-page"

    val css = """
        body { margin: 0; padding: 0; }
        .ntk-webtoon-page { margin: 0; padding: 0; text-align: center; line-height: 0; }
        .ntk-webtoon-page img, .chapter > img, p > img {
          display: block; width: 100%; max-width: 100%; max-height: none; height: auto;
          margin: 0 auto; padding: 0; page-break-inside: avoid; break-inside: avoid;
        }
        .ntk-webtoon-page + .ntk-webtoon-page { margin-top: 0; }
    """.trimIndent()

    /** True when the setting is on and the book looks like image-strip content. */
    fun applies(bundle: BookBundle): Boolean =
        bundle.settings.images.longImageLayout && bundle.chapters.any(::isImageStrip)

    /** A chapter carrying the webtoon marker, or images with almost no text. */
    fun isImageStrip(chapter: ChapterContent): Boolean {
        if (PAGE_CLASS in chapter.html) return true
        if (chapter.images.isEmpty()) return false
        return HtmlText.plainText(chapter.html).count { !it.isWhitespace() } < 40
    }

    /** Chapters with images lose their visible title in this layout. */
    fun hidesTitle(chapter: ChapterContent, layoutApplies: Boolean): Boolean = layoutApplies && chapter.images.isNotEmpty()
}

/**
 * The book reduced to what a PDF renderer draws (port of the iOS `PDFDocumentPlan`). The Android
 * renderer lives in the app because it needs `android.graphics.pdf`.
 */
class PdfDocumentPlan(bundle: BookBundle) {
    class Chapter(val title: String, val showTitle: Boolean, val blocks: List<PdfBlock>)

    val title: String = bundle.book.title
    val author: String? = bundle.book.author
    val details: List<String> = listOfNotNull(bundle.book.status, bundle.book.tags.takeIf { it.isNotEmpty() }?.joinToString(", "))
    val synopsis: String? = bundle.book.synopsis
    val language: String = bundle.book.language
    val cover: ByteArray? = bundle.cover?.data
    val longImageLayout: Boolean = LongImageLayout.applies(bundle)
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
            Chapter(title, !LongImageLayout.hidesTitle(chapter, longImageLayout), PdfHtmlParser.blocks(chapter.html))
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
