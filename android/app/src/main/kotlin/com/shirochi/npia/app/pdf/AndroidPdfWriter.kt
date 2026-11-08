package com.shirochi.npia.app.pdf

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import android.graphics.Typeface
import android.graphics.pdf.PdfDocument
import android.text.Layout
import android.text.SpannableStringBuilder
import android.text.Spanned
import android.text.StaticLayout
import android.text.TextPaint
import android.text.TextUtils
import android.text.style.StyleSpan
import com.shirochi.npia.core.OutputFormat
import com.shirochi.npia.images.AndroidImageProcessor
import com.shirochi.npia.output.BookBundle
import com.shirochi.npia.output.BookWriter
import com.shirochi.npia.output.pdf.PdfBlock
import com.shirochi.npia.output.pdf.PdfDocumentPlan
import com.shirochi.npia.output.pdf.PdfHtmlParser
import com.shirochi.npia.output.pdf.PdfRun
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.withContext
import java.io.File

/**
 * PDF output drawn with `android.graphics.pdf.PdfDocument`, matching the desktop WeasyPrint layout
 * and the iOS `PDFWriter`: A4, 1 inch margins, cover page, info page, optional TOC, one chapter
 * per page break. Registered as `Writers.pdfWriter` at app start.
 *
 * - `pdfToc`: a table of contents after the info page.
 * - `pdfCounterLayout`: TOC entries show dot leaders and page numbers (a measuring pass first).
 * - `pdfPageNumbers`: centred page numbers in the footer of every page except the cover.
 *
 * `PdfDocument` has no link annotations, so TOC entries are not clickable.
 */
class AndroidPdfWriter : BookWriter {
    override val format = OutputFormat.PDF

    override suspend fun write(bundle: BookBundle, baseName: String, directory: File): File = withContext(Dispatchers.Default) {
        directory.mkdirs()
        val file = File(directory, "$baseName.pdf")
        val plan = PdfDocumentPlan(bundle)
        val chapterPages = if (plan.counterLayout) PdfRenderer(plan, null, null).render() else null
        val document = PdfDocument()
        try {
            PdfRenderer(plan, document, chapterPages).render()
            file.outputStream().buffered().use { document.writeTo(it) }
        } catch (e: Throwable) {
            file.delete()
            throw e
        } finally {
            document.close()
        }
        file
    }
}

/**
 * Lays out and draws a `PdfDocumentPlan`. With a null document it only measures, which the counter
 * layout uses to learn each chapter's first page before drawing the TOC.
 */
internal class PdfRenderer(
    private val plan: PdfDocumentPlan,
    private val document: PdfDocument?,
    private val tocPageNumbers: List<Int>?,
) {
    companion object {
        const val PAGE_WIDTH = 595
        const val PAGE_HEIGHT = 842
        const val MARGIN = 72f
        const val CONTENT_WIDTH = PAGE_WIDTH - 2 * MARGIN
        const val CONTENT_HEIGHT = PAGE_HEIGHT - 2 * MARGIN
    }

    enum class Style(val size: Float, val bold: Boolean = false, val center: Boolean = false, val after: Float = 6f, val gray: Int = 0) {
        BODY(11f),
        HEADING(16f, bold = true, after = 10f),
        SUBHEADING(13f, bold = true, after = 8f),
        CHAPTER_TITLE(18f, bold = true, after = 18f),
        BOOK_TITLE(22f, bold = true, center = true, after = 12f),
        AUTHOR(13f, center = true, after = 8f),
        DETAIL(10f, center = true, after = 4f, gray = 0x59),
        TOC_TITLE(16f, bold = true, center = true, after = 14f),
        TOC_ENTRY(11f, after = 0f),
        FOOTER(9f, gray = 0x66),
    }

    var pageCount = 0
        private set
    private var page: PdfDocument.Page? = null
    private val canvas: Canvas? get() = page?.canvas
    private var pageOpen = false
    /** Distance already used from the top of the content area on the current page. */
    private var cursor = 0f
    private val remaining get() = CONTENT_HEIGHT - cursor
    private val bitmapPaint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)

    /** Draws everything and returns the first page (1-based) of each chapter. */
    suspend fun render(): List<Int> {
        plan.cover?.let(::drawCover)
        drawInfo()
        if (plan.showToc && plan.chapters.isNotEmpty()) drawToc()
        val starts = mutableListOf<Int>()
        for (chapter in plan.chapters) {
            currentCoroutineContext().ensureActive()
            beginPage()
            starts += pageCount
            drawParagraph(listOf(PdfRun(chapter.title, bold = true)), Style.CHAPTER_TITLE)
            for (block in chapter.blocks) {
                when (block) {
                    is PdfBlock.Paragraph -> drawParagraph(
                        block.runs,
                        when (val level = block.heading) {
                            null -> Style.BODY
                            else -> if (level <= 2) Style.HEADING else Style.SUBHEADING
                        },
                    )
                    is PdfBlock.Image -> plan.image(block.src)?.let(::drawImage)
                }
            }
        }
        endPage()
        return starts
    }

    // Pages

    private fun beginPage(numbered: Boolean = true) {
        endPage()
        pageCount += 1
        pageOpen = true
        cursor = 0f
        val document = document ?: return
        page = document.startPage(PdfDocument.PageInfo.Builder(PAGE_WIDTH, PAGE_HEIGHT, pageCount).create())
        if (numbered && plan.pageNumbers) {
            val paint = paint(Style.FOOTER)
            val label = pageCount.toString()
            canvas?.drawText(label, (PAGE_WIDTH - paint.measureText(label)) / 2, PAGE_HEIGHT - MARGIN / 2, paint)
        }
    }

    private fun endPage() {
        if (!pageOpen) return
        page?.let { document?.finishPage(it) }
        page = null
        pageOpen = false
    }

    private fun ensurePage() {
        if (!pageOpen) beginPage()
    }

    // Sections

    private fun drawCover(data: ByteArray) {
        beginPage(numbered = false)
        val (w, h) = AndroidImageProcessor.pixelSize(data) ?: return
        val scale = minOf(CONTENT_WIDTH / w, CONTENT_HEIGHT / h)
        val width = w * scale
        val height = h * scale
        val left = MARGIN + (CONTENT_WIDTH - width) / 2
        val top = MARGIN + (CONTENT_HEIGHT - height) / 2
        drawBitmap(data, w, width, RectF(left, top, left + width, top + height))
    }

    private fun drawInfo() {
        beginPage()
        drawParagraph(listOf(PdfRun(plan.title, bold = true)), Style.BOOK_TITLE)
        plan.author?.takeIf { it.isNotEmpty() }?.let { drawParagraph(listOf(PdfRun(it)), Style.AUTHOR) }
        plan.details.forEach { drawParagraph(listOf(PdfRun(it)), Style.DETAIL) }
        val synopsis = plan.synopsis?.takeIf { it.isNotBlank() } ?: return
        cursor += 12f
        synopsis.lines().filter { it.isNotBlank() }.forEach { drawParagraph(listOf(PdfRun(it)), Style.BODY) }
    }

    private fun drawToc() {
        beginPage()
        drawParagraph(listOf(PdfRun("Table of Contents", bold = true)), Style.TOC_TITLE)
        val lineHeight = 18f
        val paint = paint(Style.TOC_ENTRY)
        val ascent = -paint.ascent()
        val dotWidth = paint.measureText(" .").coerceAtLeast(1f)
        plan.chapters.forEachIndexed { index, chapter ->
            if (remaining < lineHeight) beginPage()
            val canvas = canvas
            if (canvas != null) {
                val baseline = MARGIN + cursor + ascent
                val number = tocPageNumbers?.getOrNull(index)?.toString()
                val numberWidth = number?.let(paint::measureText) ?: 0f
                val titleWidth = if (number != null) CONTENT_WIDTH - numberWidth - 16 else CONTENT_WIDTH
                val title = TextUtils.ellipsize(chapter.title, paint, titleWidth, TextUtils.TruncateAt.END).toString()
                canvas.drawText(title, MARGIN, baseline, paint)
                if (number != null) {
                    val gapStart = MARGIN + paint.measureText(title) + 4
                    val gapEnd = MARGIN + CONTENT_WIDTH - numberWidth - 4
                    val count = ((gapEnd - gapStart) / dotWidth).toInt()
                    if (count > 0) {
                        val leaders = " .".repeat(count)
                        canvas.drawText(leaders, gapEnd - paint.measureText(leaders), baseline, paint)
                    }
                    canvas.drawText(number, MARGIN + CONTENT_WIDTH - numberWidth, baseline, paint)
                }
            }
            cursor += lineHeight
        }
    }

    // Text

    private fun paint(style: Style) = TextPaint(Paint.ANTI_ALIAS_FLAG).apply {
        textSize = style.size
        typeface = if (style.bold) Typeface.DEFAULT_BOLD else Typeface.DEFAULT
        color = Color.rgb(style.gray, style.gray, style.gray)
    }

    /** Flows one paragraph from the cursor, breaking onto new pages between lines. */
    private fun drawParagraph(runs: List<PdfRun>, style: Style) {
        val text = SpannableStringBuilder()
        for (run in runs) {
            val start = text.length
            text.append(run.text.replace(PdfHtmlParser.LINE_BREAK, '\n'))
            val face = when {
                (run.bold || style.bold) && run.italic -> Typeface.BOLD_ITALIC
                run.bold || style.bold -> Typeface.BOLD
                run.italic -> Typeface.ITALIC
                else -> Typeface.NORMAL
            }
            if (face != Typeface.NORMAL && text.length > start) text.setSpan(StyleSpan(face), start, text.length, Spanned.SPAN_EXCLUSIVE_EXCLUSIVE)
        }
        if (text.isEmpty()) return
        val layout = StaticLayout.Builder.obtain(text, 0, text.length, paint(style), CONTENT_WIDTH.toInt())
            .setAlignment(if (style.center) Layout.Alignment.ALIGN_CENTER else Layout.Alignment.ALIGN_NORMAL)
            .setLineSpacing(if (style == Style.BODY) 3f else 1f, 1f)
            .setIncludePad(false)
            .build()

        ensurePage()
        var line = 0
        while (line < layout.lineCount) {
            if (layout.getLineBottom(line) - layout.getLineTop(line) > remaining) {
                if (cursor == 0f) {
                    // Taller than a page: draw it anyway rather than loop.
                } else {
                    beginPage()
                    continue
                }
            }
            // Every following line that still fits goes on this page in one draw.
            val first = line
            val top = layout.getLineTop(first).toFloat()
            while (line < layout.lineCount && (line == first || layout.getLineBottom(line) - top <= remaining)) line += 1
            val bottom = layout.getLineBottom(line - 1).toFloat()
            canvas?.let { c ->
                c.save()
                c.translate(MARGIN, MARGIN + cursor - top)
                c.clipRect(0f, top, CONTENT_WIDTH, bottom)
                layout.draw(c)
                c.restore()
            }
            cursor += bottom - top
            if (line < layout.lineCount) beginPage()
        }
        cursor += style.after
    }

    // Images

    private fun drawImage(data: ByteArray) {
        val (w, h) = AndroidImageProcessor.pixelSize(data) ?: return
        ensurePage()
        // CSS px (96 dpi) to points, never wider than the text column or taller than a page.
        var width = minOf(CONTENT_WIDTH, w * 0.75f)
        var height = h * width / w
        if (height > CONTENT_HEIGHT) {
            width *= CONTENT_HEIGHT / height
            height = CONTENT_HEIGHT
        }
        if (height > remaining) beginPage()
        val left = MARGIN + (CONTENT_WIDTH - width) / 2
        drawBitmap(data, w, width, RectF(left, MARGIN + cursor, left + width, MARGIN + cursor + height))
        cursor += height + 6
    }

    /** Decodes at about twice the drawn resolution so large sources do not bloat the PDF. */
    private fun drawBitmap(data: ByteArray, pixelWidth: Int, drawnWidth: Float, dest: RectF) {
        val canvas = canvas ?: return
        var sample = 1
        while (pixelWidth / (sample * 2) >= drawnWidth * 2) sample *= 2
        val bitmap = try {
            AndroidImageProcessor.decode(data, sample)
        } catch (e: OutOfMemoryError) {
            null
        } ?: return
        canvas.drawBitmap(bitmap, null, dest, bitmapPaint)
    }
}
