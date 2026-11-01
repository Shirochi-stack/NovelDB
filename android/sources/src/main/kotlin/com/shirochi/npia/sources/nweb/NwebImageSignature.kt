package com.shirochi.npia.sources.nweb

import kotlin.math.abs

/** Decoded pixels of an image: ARGB, row-major. */
class DecodedPixels(val width: Int, val height: Int, val argb: IntArray)

/**
 * Aspect ratio plus a 64x8 grayscale thumbnail, used to recognise the publisher logo
 * banner that closes every episode of some works (`_nweb_fetch_signature`).
 */
data class NwebImageSignature(val aspect: Double, val pixels: List<Int>) {
    val isBanner: Boolean get() = aspect >= LOGO_MIN_ASPECT

    /** Same image: same shape and the same marks on a white banner. */
    fun matches(other: NwebImageSignature): Boolean {
        if (aspect <= 0 || abs(aspect - other.aspect) > aspect * 0.08) return false
        var total = 0
        var count = 0
        for ((a, b) in pixels.zip(other.pixels)) {
            if (a < 235 || b < 235) {
                total += abs(a - b)
                count++
            }
        }
        return count == 0 || total.toDouble() / count < 30
    }

    companion object {
        const val WIDTH = 64
        const val HEIGHT = 8

        /** Banners are much wider than tall; covers and illustrations never are. */
        const val LOGO_MIN_ASPECT = 3.5

        /**
         * Turns image bytes into pixels. The JVM default uses `javax.imageio`, which Android
         * lacks; the app installs a `BitmapFactory` decoder here. When none works, logo
         * detection is skipped.
         */
        @Volatile
        var decoder: (ByteArray) -> DecodedPixels? = ::decodeWithImageIo

        fun fromImageData(data: ByteArray): NwebImageSignature? {
            val decoded = runCatching { decoder(data) }.getOrNull() ?: return null
            return fromPixels(decoded)
        }

        /** Area-averages the image, composited on white, into a 64x8 grayscale thumbnail. */
        fun fromPixels(image: DecodedPixels): NwebImageSignature? {
            val width = image.width
            val height = image.height
            if (width <= 0 || height <= 0 || image.argb.size < width * height) return null
            val sums = DoubleArray(WIDTH * HEIGHT)
            val counts = IntArray(WIDTH * HEIGHT)
            for (y in 0 until height) {
                val cellY = (y.toLong() * HEIGHT / height).toInt()
                for (x in 0 until width) {
                    val cell = cellY * WIDTH + (x.toLong() * WIDTH / width).toInt()
                    val color = image.argb[y * width + x]
                    val alpha = (color ushr 24 and 0xFF) / 255.0
                    val gray = 0.299 * (color shr 16 and 0xFF) + 0.587 * (color shr 8 and 0xFF) + 0.114 * (color and 0xFF)
                    sums[cell] += gray * alpha + 255 * (1 - alpha)
                    counts[cell]++
                }
            }
            val pixels = sums.indices.map { if (counts[it] == 0) 255 else (sums[it] / counts[it]).toInt().coerceIn(0, 255) }
            return NwebImageSignature(width.toDouble() / height, pixels)
        }

        private fun decodeWithImageIo(data: ByteArray): DecodedPixels? = try {
            ImageIoDecoder.decode(data)
        } catch (e: LinkageError) {
            null
        }
    }
}

/** Kept in its own class so Android never loads `javax.imageio`. */
private object ImageIoDecoder {
    fun decode(data: ByteArray): DecodedPixels? {
        val image = javax.imageio.ImageIO.read(java.io.ByteArrayInputStream(data)) ?: return null
        val pixels = image.getRGB(0, 0, image.width, image.height, null, 0, image.width)
        return DecodedPixels(image.width, image.height, pixels)
    }
}
