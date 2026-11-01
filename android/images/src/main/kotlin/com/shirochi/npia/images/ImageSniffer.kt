package com.shirochi.npia.images

import com.shirochi.npia.core.sniffImageExtension

/**
 * Byte-level checks that need no decoder: container type, animation, and GIF first-frame
 * extraction. Android has no GIF or animated WebP encoder, so these keep the desktop rules
 * (`static_only` GIFs keep their `.gif` name) without re-encoding.
 */
object ImageSniffer {
    fun extension(data: ByteArray): String = sniffImageExtension(data)

    /** True for GIFs with more than one frame, animated WebP, and APNG. */
    fun isAnimated(data: ByteArray): Boolean = when (extension(data)) {
        "gif" -> gifFrameCount(data) > 1
        "webp" -> webpFrameCount(data) > 1
        "png" -> apngFrameCount(data) > 1
        else -> false
    }

    // GIF

    fun gifFrameCount(data: ByteArray): Int = walkGif(data, stopAfterFirst = false)?.frames ?: 0

    /**
     * The GIF cut after its first image, without the loop extension. Returns null when the data
     * is not a readable GIF. A static GIF comes back unchanged apart from dropped trailing data.
     */
    fun gifFirstFrame(data: ByteArray): ByteArray? = walkGif(data, stopAfterFirst = true)?.output

    private class GifWalk(val frames: Int, val output: ByteArray?)

    private fun walkGif(data: ByteArray, stopAfterFirst: Boolean): GifWalk? {
        fun u8(i: Int) = if (i < data.size) data[i].toInt() and 0xFF else -1
        if (data.size < 13 || extension(data) != "gif") return null
        val out = java.io.ByteArrayOutputStream(data.size)
        var pos = 13
        val screenFlags = u8(10)
        if (screenFlags and 0x80 != 0) pos += 3 * (1 shl ((screenFlags and 7) + 1))
        if (pos > data.size) return null
        out.write(data, 0, pos)

        /** Skips data sub-blocks starting at `start`; returns the index after the terminator. */
        fun skipSubBlocks(start: Int): Int? {
            var i = start
            while (true) {
                val size = u8(i)
                if (size < 0) return null
                i += 1
                if (size == 0) return i
                i += size
            }
        }

        var frames = 0
        while (pos < data.size) {
            when (u8(pos)) {
                0x21 -> {
                    val label = u8(pos + 1)
                    val end = skipSubBlocks(pos + 2) ?: return null
                    // Application extensions (NETSCAPE looping) are dropped from a single frame.
                    if (!stopAfterFirst || label != 0xFF) out.write(data, pos, end - pos)
                    pos = end
                }
                0x2C -> {
                    if (pos + 10 > data.size) return null
                    val flags = u8(pos + 9)
                    var i = pos + 10
                    if (flags and 0x80 != 0) i += 3 * (1 shl ((flags and 7) + 1))
                    i += 1 // LZW minimum code size
                    val end = skipSubBlocks(i) ?: return null
                    frames += 1
                    out.write(data, pos, end - pos)
                    pos = end
                    if (stopAfterFirst) {
                        out.write(0x3B)
                        return GifWalk(frames, out.toByteArray())
                    }
                }
                0x3B -> break
                else -> return if (frames > 0) GifWalk(frames, null) else null
            }
        }
        if (stopAfterFirst) return null
        return GifWalk(frames, null)
    }

    // WebP

    /** ANMF chunks in an extended WebP; 1 for simple (lossy or lossless) files. */
    fun webpFrameCount(data: ByteArray): Int {
        if (data.size < 16 || extension(data) != "webp") return 0
        var pos = 12
        var frames = 0
        var extended = false
        while (pos + 8 <= data.size) {
            val type = String(data, pos, 4, Charsets.US_ASCII)
            val size = le32(data, pos + 4)
            if (size < 0) break
            when (type) {
                "VP8X" -> extended = true
                "ANMF" -> frames += 1
            }
            pos += 8 + size + (size and 1)
        }
        return if (extended && frames > 0) frames else 1
    }

    // PNG

    /** `acTL` frame count for APNG, 1 for plain PNG. */
    fun apngFrameCount(data: ByteArray): Int {
        if (data.size < 16 || extension(data) != "png") return 0
        var pos = 8
        while (pos + 12 <= data.size) {
            val length = be32(data, pos)
            if (length < 0) break
            when (String(data, pos + 4, 4, Charsets.US_ASCII)) {
                "acTL" -> return if (pos + 12 <= data.size) be32(data, pos + 8).coerceAtLeast(1) else 1
                "IDAT", "IEND" -> return 1
            }
            pos += 12 + length
        }
        return 1
    }

    private fun le32(data: ByteArray, at: Int): Int {
        if (at + 4 > data.size) return -1
        return (data[at].toInt() and 0xFF) or ((data[at + 1].toInt() and 0xFF) shl 8) or
            ((data[at + 2].toInt() and 0xFF) shl 16) or ((data[at + 3].toInt() and 0xFF) shl 24)
    }

    private fun be32(data: ByteArray, at: Int): Int {
        if (at + 4 > data.size) return -1
        return ((data[at].toInt() and 0xFF) shl 24) or ((data[at + 1].toInt() and 0xFF) shl 16) or
            ((data[at + 2].toInt() and 0xFF) shl 8) or (data[at + 3].toInt() and 0xFF)
    }
}
