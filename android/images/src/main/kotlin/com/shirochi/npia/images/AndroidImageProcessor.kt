package com.shirochi.npia.images

import com.shirochi.npia.core.ImageProcessing
import com.shirochi.npia.core.ImageSettings
import com.shirochi.npia.core.ProcessedImage
import com.shirochi.npia.core.sniffImageExtension

/**
 * Recompresses downloaded images per `ImageSettings`.
 *
 * TODO(images workstream): decode with `ImageDecoder`/`BitmapFactory` (WebP, AVIF on API 31+,
 * GIF), encode with `Bitmap.compress` (WEBP_LOSSY on API 30+, JPEG, PNG), GIF-to-static, cover
 * settings, and `recompressTargetMb`. Until then images pass through unchanged.
 */
class AndroidImageProcessor(private val settings: ImageSettings) : ImageProcessing {
    override fun process(data: ByteArray, isCover: Boolean): ProcessedImage =
        ProcessedImage(data, sniffImageExtension(data))
}
