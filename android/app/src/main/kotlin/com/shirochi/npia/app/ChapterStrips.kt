package com.shirochi.npia.app

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Rect
import com.shirochi.npia.core.ImageProcessing
import com.shirochi.npia.images.AndroidImageProcessor
import java.io.ByteArrayOutputStream
import java.io.DataOutputStream
import java.io.File
import java.io.OutputStream
import java.nio.ByteBuffer
import java.util.concurrent.Callable
import java.util.concurrent.Executors
import java.util.concurrent.Future
import java.util.zip.CRC32
import java.util.zip.Deflater
import java.util.zip.DeflaterOutputStream

internal object ChapterStrips {
    private data class Panel(val image: Pair<String, ByteArray>, val height: Int)

    fun write(pages: List<Pair<String, ByteArray>>, maxHeight: Int, target: (String, Boolean) -> File) {
        if (maxHeight == 0 && writeFullStrip(pages) { target(it, false) }) return
        val strips = (if (maxHeight == 0) pages.asSequence() else stitch(pages, maxHeight)).iterator()
        if (!strips.hasNext()) return
        val (firstExtension, firstData) = strips.next()
        val multiple = strips.hasNext()
        target(firstExtension, multiple).writeBytes(firstData)
        for ((extension, data) in strips) target(extension, multiple).writeBytes(data)
    }

    /**
     * The same strips, each encoded as chapter images are (`processor`), straight from the bitmap. A
     * strip used to be saved as a PNG, read back, decoded and encoded again: most of the stitching time.
     * Two strips are rendered at a time where the device has the cores for it; they are written in order.
     */
    fun writeEncoded(pages: List<Pair<String, ByteArray>>, maxHeight: Int, processor: ImageProcessing, target: (String, Boolean) -> File) {
        val jobs = strips(pages, maxHeight, processor)
        if (jobs.isEmpty()) return
        val workers = if (Runtime.getRuntime().availableProcessors() >= 4) 2 else 1
        val pool = if (workers > 1 && jobs.size > 1) Executors.newFixedThreadPool(workers) else null
        try {
            val results = sequence {
                if (pool == null) {
                    for (job in jobs) yieldAll(job())
                } else {
                    // One strip ahead of the one being written, not the whole chapter.
                    val waiting = ArrayDeque<Future<List<Pair<String, ByteArray>>>>()
                    for (job in jobs) {
                        waiting += pool.submit(Callable { job() })
                        if (waiting.size > workers) yieldAll(waiting.removeFirst().get())
                    }
                    while (waiting.isNotEmpty()) yieldAll(waiting.removeFirst().get())
                }
            }.iterator()
            if (!results.hasNext()) return
            val (firstExtension, firstData) = results.next()
            val multiple = results.hasNext()
            target(firstExtension, multiple).writeBytes(firstData)
            for ((extension, data) in results) target(extension, multiple).writeBytes(data)
        } finally {
            // A stop (the target refuses) leaves here: nothing more is started.
            pool?.shutdownNow()
        }
    }

    /** A page that is not stitched, as the processor encodes chapter images. */
    private fun processed(page: Pair<String, ByteArray>, processor: ImageProcessing): Pair<String, ByteArray> {
        val encoded = runCatching { processor.process(page.second) }.getOrNull() ?: return page
        return (encoded.fileExtension.takeUnless { it == "bin" } ?: page.first) to encoded.data
    }

    /** The strips to make, in order, each as a call that returns its files. */
    private fun strips(pages: List<Pair<String, ByteArray>>, maxHeight: Int, processor: ImageProcessing): List<() -> List<Pair<String, ByteArray>>> {
        val jobs = mutableListOf<() -> List<Pair<String, ByteArray>>>()
        var group = mutableListOf<Panel>()
        var width = 0
        var height = 0
        fun flush() {
            if (group.isEmpty()) return
            val panels = group.toList()
            val (w, h) = width to height
            jobs += { render(panels, w, h, processor) }
            group = mutableListOf()
            width = 0
            height = 0
        }
        for (page in pages) {
            val dimensions = bounds(page.second)
            val w = dimensions.outWidth
            val h = dimensions.outHeight
            var limit = minOf(maxHeight, 24_000_000 / maxOf(1, width, w))
            if (group.isNotEmpty() && (w <= 0 || h <= 0 || height + h > limit)) {
                flush()
                limit = minOf(maxHeight, 24_000_000 / maxOf(1, w))
            }
            if (w <= 0 || h <= 0 || h > limit) {
                jobs += { listOf(processed(page, processor)) }
                continue
            }
            width = maxOf(width, w)
            height += h
            group += Panel(page, h)
        }
        flush()
        return jobs
    }

    private fun bounds(data: ByteArray) = BitmapFactory.Options().apply {
        inJustDecodeBounds = true
        BitmapFactory.decodeByteArray(data, 0, data.size, this)
    }

    private fun stitch(pages: List<Pair<String, ByteArray>>, maxHeight: Int): Sequence<Pair<String, ByteArray>> = sequence {
        val group = mutableListOf<Panel>()
        var width = 0
        var height = 0
        for (page in pages) {
            val dimensions = bounds(page.second)
            val w = dimensions.outWidth
            val h = dimensions.outHeight
            var limit = minOf(maxHeight, 24_000_000 / maxOf(1, width, w))
            if (group.isNotEmpty() && (w <= 0 || h <= 0 || height + h > limit)) {
                yieldAll(render(group, width, height))
                group.clear()
                width = 0
                height = 0
                limit = minOf(maxHeight, 24_000_000 / maxOf(1, w))
            }
            if (w <= 0 || h <= 0 || h > limit) {
                yield(page)
                continue
            }
            width = maxOf(width, w)
            height += h
            group += Panel(page, h)
        }
        if (group.isNotEmpty()) yieldAll(render(group, width, height))
    }

    private fun writeFullStrip(pages: List<Pair<String, ByteArray>>, target: (String) -> File): Boolean {
        val dimensions = pages.map { bounds(it.second) }
        if (dimensions.any { it.outWidth <= 0 || it.outHeight <= 0 }) return false
        val width = dimensions.maxOf { it.outWidth }
        val height = dimensions.sumOf { it.outHeight.toLong() }
        require(height <= Int.MAX_VALUE && width <= (Int.MAX_VALUE - 1) / 3) {
            "This chapter exceeds PNG's maximum dimensions."
        }
        DataOutputStream(target("png").outputStream().buffered()).use { output ->
            output.writeLong(0x89504E470D0A1A0AuL.toLong())
            output.chunk("IHDR", ByteBuffer.allocate(13).putInt(width).putInt(height.toInt())
                .put(byteArrayOf(8, 2, 0, 0, 0)).array())
            val deflater = Deflater(3)
            try {
                val chunks = object : OutputStream() {
                    override fun write(value: Int) = write(byteArrayOf(value.toByte()), 0, 1)
                    override fun write(data: ByteArray, offset: Int, size: Int) {
                        output.chunk("IDAT", data, offset, size)
                    }
                }
                val compressed = DeflaterOutputStream(chunks, deflater, 64 * 1024)
                for ((_, data) in pages) {
                    val panel = checkNotNull(BitmapFactory.decodeByteArray(data, 0, data.size)) {
                        "Could not decode a chapter image."
                    }
                    try {
                        val pixels = IntArray(panel.width)
                        val row = ByteArray(1 + width * 3) { 0xFF.toByte() }.apply { this[0] = 0 }
                        val left = (width - panel.width) / 2
                        for (y in 0 until panel.height) {
                            panel.getPixels(pixels, 0, panel.width, 0, y, panel.width, 1)
                            for ((x, color) in pixels.withIndex()) {
                                val alpha = Color.alpha(color)
                                val offset = 1 + (left + x) * 3
                                row[offset] = overWhite(Color.red(color), alpha)
                                row[offset + 1] = overWhite(Color.green(color), alpha)
                                row[offset + 2] = overWhite(Color.blue(color), alpha)
                            }
                            compressed.write(row)
                        }
                    } finally {
                        panel.recycle()
                    }
                }
                compressed.finish()
            } finally {
                deflater.end()
            }
            output.chunk("IEND", byteArrayOf())
        }
        return true
    }

    private fun overWhite(channel: Int, alpha: Int): Byte = (255 - ((255 - channel) * alpha + 127) / 255).toByte()

    private fun DataOutputStream.chunk(type: String, data: ByteArray, offset: Int = 0, size: Int = data.size) {
        val name = type.toByteArray(Charsets.US_ASCII)
        val crc = CRC32().apply {
            update(name)
            update(data, offset, size)
        }
        writeInt(size)
        write(name)
        write(data, offset, size)
        writeInt(crc.value.toInt())
    }

    private fun render(group: List<Panel>, width: Int, height: Int, processor: ImageProcessing? = null): List<Pair<String, ByteArray>> {
        val originals = if (processor == null) group.map { it.image } else group.map { processed(it.image, processor) }
        if (group.size == 1) return originals
        // Out of memory for the strip: its pages are kept as they are instead.
        val strip = try {
            Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        } catch (e: OutOfMemoryError) {
            return originals
        }
        try {
            val canvas = Canvas(strip).apply { drawColor(Color.WHITE) }
            var top = 0
            for ((image, size) in group) {
                val panel = BitmapFactory.decodeByteArray(image.second, 0, image.second.size) ?: return originals
                val left = (width - panel.width) / 2
                try {
                    canvas.drawBitmap(panel, null, Rect(left, top, left + panel.width, top + size), null)
                } finally {
                    panel.recycle()
                }
                top += size
            }
            if (processor != null) {
                // Encoded as chapter images are, straight from the bitmap.
                (processor as? AndroidImageProcessor)?.encodeBitmap(strip)?.takeUnless { it.fileExtension == "bin" }
                    ?.let { return listOf(it.fileExtension to it.data) }
            }
            val encoded = ByteArrayOutputStream()
            if (!strip.compress(Bitmap.CompressFormat.PNG, 100, encoded)) return originals
            val png = "png" to encoded.toByteArray()
            return listOf(if (processor == null) png else processed(png, processor))
        } catch (e: OutOfMemoryError) {
            return originals
        } finally {
            strip.recycle()
        }
    }
}
