package com.shirochi.npia.images

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.ImageDecoder
import android.os.Build
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.ImageProcessing
import com.shirochi.npia.core.ImageSettings
import com.shirochi.npia.core.ProcessedImage
import java.io.ByteArrayOutputStream
import java.nio.ByteBuffer

/**
 * Recompresses downloaded images per `ImageSettings`, following the desktop `_encode_image_bytes`
 * rules and the iOS `ImageProcessor`:
 *
 * - GIFs stay byte-for-byte unless `convertGifs` is on; with `staticOnly` they keep the `.gif`
 *   extension but drop to the first frame.
 * - `staticOnly` flattens animated sources; JPEG targets always use the first frame.
 * - Android has no animated WebP or APNG encoder, so animated sources bound for WebP or PNG keep
 *   their original bytes instead of losing the animation.
 * - Covers use `compressCover`, `coverQuality`, and `coverFormat`, and are always static.
 * - Anything that fails to decode or encode keeps its original bytes.
 */
class AndroidImageProcessor(private val settings: ImageSettings) : ImageProcessing {
    override fun process(data: ByteArray, isCover: Boolean): ProcessedImage {
        val sourceExtension = ImageSniffer.extension(data)
        val original = ProcessedImage(data, sourceExtension)
        val compress = if (isCover) settings.compressCover else settings.compress
        if (!compress || sourceExtension == "bin") return original
        val format = if (isCover) settings.coverFormat else settings.format
        val quality = if (isCover) settings.coverQuality else settings.quality
        val staticOnly = settings.staticOnly || isCover

        if (sourceExtension == "gif" && !settings.convertGifs && !isCover) {
            if (!settings.staticOnly) return original
            return staticGif(data) ?: original
        }

        val animated = ImageSniffer.isAnimated(data)
        val target = if (format == ImageFormat.ORIGINAL) {
            // Only re-encode when animation has to be dropped.
            if (!(staticOnly && animated)) return original
            when (sourceExtension) {
                "jpg" -> ImageFormat.JPEG
                "png" -> ImageFormat.PNG
                "gif" -> return staticGif(data) ?: original
                else -> ImageFormat.WEBP
            }
        } else {
            format
        }
        if (animated && !staticOnly && target != ImageFormat.JPEG) return original

        val encoded = decodeAndEncode(data, target, quality) ?: return original
        // Re-encoding into the same container must not make the file bigger.
        if (encoded.fileExtension == sourceExtension && encoded.data.size >= data.size) return original
        return encoded
    }

    /**
     * Lower-quality pass for `recompressTargetMb`, keeping the container so names in the book stay
     * valid. GIF and PNG come back unchanged, as on the desktop.
     */
    override fun reencode(data: ByteArray, fileExtension: String, quality: Int): ByteArray {
        val target = when (fileExtension.lowercase()) {
            "jpg", "jpeg" -> ImageFormat.JPEG
            "webp" -> ImageFormat.WEBP
            else -> return data
        }
        if (target == ImageFormat.WEBP && !settings.staticOnly && ImageSniffer.isAnimated(data)) return data
        val encoded = decodeAndEncode(data, target, quality) ?: return data
        return if (encoded.data.size < data.size) encoded.data else data
    }

    private fun staticGif(data: ByteArray): ProcessedImage? {
        if (ImageSniffer.gifFrameCount(data) <= 1) return null
        return ImageSniffer.gifFirstFrame(data)?.let { ProcessedImage(it, "gif") }
    }

    private fun decodeAndEncode(data: ByteArray, target: ImageFormat, quality: Int): ProcessedImage? {
        val bitmap = try {
            decode(data)
        } catch (e: OutOfMemoryError) {
            null
        } ?: return null
        return try {
            encode(bitmap, target, quality)
        } catch (e: OutOfMemoryError) {
            null
        } catch (e: RuntimeException) {
            null
        } finally {
            bitmap.recycle()
        }
    }

    companion object {
        /** WebP's format limit; larger images (long webtoon strips) keep their source bytes. */
        const val WEBP_MAX_DIMENSION = 16_383

        /** Images above this many pixels are left alone rather than risking an OOM on a phone. */
        const val MAX_PIXELS = 60_000_000L

        /** Width and height from the header, without decoding pixels. */
        fun pixelSize(data: ByteArray): Pair<Int, Int>? {
            val options = BitmapFactory.Options().apply { inJustDecodeBounds = true }
            BitmapFactory.decodeByteArray(data, 0, data.size, options)
            if (options.outWidth > 0 && options.outHeight > 0) return options.outWidth to options.outHeight
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                var size: Pair<Int, Int>? = null
                runCatching {
                    ImageDecoder.decodeDrawable(ImageDecoder.createSource(ByteBuffer.wrap(data))) { decoder, info, _ ->
                        size = info.size.width to info.size.height
                        decoder.setTargetSize(1, 1)
                    }
                }
                return size
            }
            return null
        }

        /** First frame as a software ARGB_8888 bitmap; ImageDecoder covers HEIF/AVIF that BitmapFactory misses. */
        fun decode(data: ByteArray, sampleSize: Int = 1): Bitmap? {
            pixelSize(data)?.let { (w, h) -> if (w.toLong() * h / (sampleSize.toLong() * sampleSize) > MAX_PIXELS) return null }
            val options = BitmapFactory.Options().apply {
                inPreferredConfig = Bitmap.Config.ARGB_8888
                inSampleSize = sampleSize.coerceAtLeast(1)
            }
            BitmapFactory.decodeByteArray(data, 0, data.size, options)?.let { return it }
            if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.P) {
                return runCatching {
                    ImageDecoder.decodeBitmap(ImageDecoder.createSource(ByteBuffer.wrap(data))) { decoder, _, _ ->
                        decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
                        if (sampleSize > 1) decoder.setTargetSampleSize(sampleSize)
                    }
                }.getOrNull()
            }
            return null
        }

        fun encode(bitmap: Bitmap, target: ImageFormat, quality: Int): ProcessedImage? {
            val out = ByteArrayOutputStream()
            when (target) {
                ImageFormat.WEBP -> {
                    if (bitmap.width > WEBP_MAX_DIMENSION || bitmap.height > WEBP_MAX_DIMENSION) return null
                    @Suppress("DEPRECATION")
                    val format = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) Bitmap.CompressFormat.WEBP_LOSSY else Bitmap.CompressFormat.WEBP
                    // The pre-30 WEBP format is lossless at 100; cap it so it stays lossy.
                    val q = if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.R) quality.coerceIn(0, 100) else quality.coerceIn(0, 99)
                    if (!bitmap.compress(format, q, out)) return null
                    return ProcessedImage(out.toByteArray(), "webp")
                }
                ImageFormat.JPEG -> {
                    val opaque = flattened(bitmap)
                    try {
                        if (!opaque.compress(Bitmap.CompressFormat.JPEG, quality.coerceIn(1, 100), out)) return null
                    } finally {
                        if (opaque !== bitmap) opaque.recycle()
                    }
                    return ProcessedImage(out.toByteArray(), "jpg")
                }
                ImageFormat.PNG -> {
                    if (!bitmap.compress(Bitmap.CompressFormat.PNG, 100, out)) return null
                    return ProcessedImage(out.toByteArray(), "png")
                }
                ImageFormat.ORIGINAL -> return null
            }
        }

        /** Composites onto white so JPEG output does not turn transparent areas black. */
        fun flattened(bitmap: Bitmap): Bitmap {
            if (!bitmap.hasAlpha()) return bitmap
            val opaque = Bitmap.createBitmap(bitmap.width, bitmap.height, Bitmap.Config.ARGB_8888)
            Canvas(opaque).apply {
                drawColor(Color.WHITE)
                drawBitmap(bitmap, 0f, 0f, null)
            }
            opaque.setHasAlpha(false)
            return opaque
        }
    }
}
