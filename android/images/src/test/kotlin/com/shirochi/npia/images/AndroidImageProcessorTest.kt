package com.shirochi.npia.images

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Color
import com.shirochi.npia.core.ImageFormat
import com.shirochi.npia.core.ImageSettings
import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test
import org.junit.runner.RunWith
import org.robolectric.RobolectricTestRunner
import org.robolectric.annotation.Config
import org.robolectric.annotation.GraphicsMode
import java.io.ByteArrayOutputStream

/** Real Skia codecs through Robolectric's native graphics; images are generated in memory. */
@RunWith(RobolectricTestRunner::class)
@GraphicsMode(GraphicsMode.Mode.NATIVE)
@Config(sdk = [34])
class AndroidImageProcessorTest {
    private fun bitmap(width: Int, height: Int, alpha: Boolean = false, noise: Boolean = false): Bitmap {
        val bitmap = Bitmap.createBitmap(width, height, Bitmap.Config.ARGB_8888)
        var seed = 12345
        for (y in 0 until height) for (x in 0 until width) {
            val color = if (noise) {
                seed = seed * 1_103_515_245 + 12345
                Color.rgb((seed ushr 16) and 0xFF, (seed ushr 8) and 0xFF, (seed ushr 4) and 0xFF)
            } else {
                Color.rgb(200, 100, 40)
            }
            bitmap.setPixel(x, y, if (alpha) Color.argb(128, Color.red(color), Color.green(color), Color.blue(color)) else color)
        }
        return bitmap
    }

    private fun encode(bitmap: Bitmap, format: Bitmap.CompressFormat, quality: Int = 95): ByteArray =
        ByteArrayOutputStream().also { bitmap.compress(format, quality, it) }.toByteArray()

    private fun png(width: Int = 32, height: Int = 24, alpha: Boolean = false, noise: Boolean = false) =
        encode(bitmap(width, height, alpha, noise), Bitmap.CompressFormat.PNG, 100)

    private fun jpeg(width: Int = 64, height: Int = 64, quality: Int = 95) =
        encode(bitmap(width, height, noise = true), Bitmap.CompressFormat.JPEG, quality)

    @Test
    fun pngBecomesWebpByDefault() {
        val out = AndroidImageProcessor(ImageSettings()).process(png(noise = true))
        assertEquals("webp", out.fileExtension)
        assertEquals("webp", ImageSniffer.extension(out.data))
        val decoded = BitmapFactory.decodeByteArray(out.data, 0, out.data.size)
        assertEquals(32, decoded.width)
        assertEquals(24, decoded.height)
    }

    @Test
    fun compressOffPassesThrough() {
        val data = png()
        val out = AndroidImageProcessor(ImageSettings(compress = false)).process(data)
        assertSame(data, out.data)
        assertEquals("png", out.fileExtension)
    }

    @Test
    fun jpegTargetFlattensAlphaOntoWhite() {
        val out = AndroidImageProcessor(ImageSettings(format = ImageFormat.JPEG)).process(png(alpha = true))
        assertEquals("jpg", out.fileExtension)
        val decoded = BitmapFactory.decodeByteArray(out.data, 0, out.data.size)
        val pixel = decoded.getPixel(5, 5)
        // Half-transparent orange over white is a light orange, never near black.
        assertTrue(Color.red(pixel) > 200)
        assertTrue(Color.blue(pixel) > 100)
    }

    @Test
    fun sameContainerNeverGrows() {
        val small = jpeg(quality = 30)
        val out = AndroidImageProcessor(ImageSettings(format = ImageFormat.JPEG, quality = 100)).process(small)
        assertSame(small, out.data)
    }

    @Test
    fun gifsStayUnlessConverted() {
        val gif = TestGifs.animated(3)
        assertSame(gif, AndroidImageProcessor(ImageSettings()).process(gif).data)
        val first = AndroidImageProcessor(ImageSettings(staticOnly = true)).process(gif)
        assertEquals("gif", first.fileExtension)
        assertEquals(1, ImageSniffer.gifFrameCount(first.data))
    }

    @Test
    fun animatedGifToWebpKeepsAnimationUnlessStaticOnly() {
        val gif = TestGifs.animated(3)
        val kept = AndroidImageProcessor(ImageSettings(convertGifs = true)).process(gif)
        assertArrayEquals(gif, kept.data)
        val flattened = AndroidImageProcessor(ImageSettings(convertGifs = true, staticOnly = true, format = ImageFormat.PNG)).process(gif)
        assertEquals("png", flattened.fileExtension)
    }

    @Test
    fun coverUsesCoverSettings() {
        val data = png(noise = true)
        val settings = ImageSettings(compress = false, compressCover = true, coverFormat = ImageFormat.JPEG, coverQuality = 70)
        val processor = AndroidImageProcessor(settings)
        assertEquals("jpg", processor.process(data, isCover = true).fileExtension)
        assertSame(data, processor.process(data, isCover = false).data)
    }

    @Test
    fun originalFormatKeepsStaticImages() {
        val data = png()
        assertSame(data, AndroidImageProcessor(ImageSettings(format = ImageFormat.ORIGINAL)).process(data).data)
    }

    @Test
    fun reencodeShrinksJpegAndKeepsPng() {
        val processor = AndroidImageProcessor(ImageSettings())
        val data = jpeg(width = 96, height = 96, quality = 95)
        val smaller = processor.reencode(data, "jpg", 20)
        assertTrue(smaller.size < data.size)
        assertEquals("jpg", ImageSniffer.extension(smaller))
        val pngData = png()
        assertSame(pngData, processor.reencode(pngData, "png", 20))
    }

    @Test
    fun undecodableBytesPassThrough() {
        val junk = byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte(), 1, 2, 3)
        val out = AndroidImageProcessor(ImageSettings()).process(junk)
        assertSame(junk, out.data)
        assertEquals("jpg", out.fileExtension)
    }
}
