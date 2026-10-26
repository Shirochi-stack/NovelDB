package com.shirochi.npia.images

import org.junit.Assert.assertArrayEquals
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** Hand-assembled files; no Android classes are touched, so these run as plain JUnit. */
object TestGifs {
    private fun bytes(vararg values: Int) = ByteArray(values.size) { values[it].toByte() }

    private val header = "GIF89a".toByteArray() + bytes(1, 0, 1, 0, 0x80, 0, 0) + bytes(0, 0, 0, 0xFF, 0xFF, 0xFF)
    private val loop = bytes(0x21, 0xFF, 0x0B) + "NETSCAPE2.0".toByteArray() + bytes(3, 1, 0, 0, 0)
    private fun frame(delay: Int) = bytes(0x21, 0xF9, 4, 0, delay, 0, 0, 0) +
        bytes(0x2C, 0, 0, 0, 0, 1, 0, 1, 0, 0) + bytes(2, 2, 0x44, 0x01, 0)

    fun animated(frames: Int = 3): ByteArray =
        (0 until frames).fold(header + loop) { acc, i -> acc + frame(10 + i) } + bytes(0x3B)

    fun static(): ByteArray = header + frame(10) + bytes(0x3B)
}

class ImageSnifferTest {
    @Test
    fun countsGifFrames() {
        assertEquals(3, ImageSniffer.gifFrameCount(TestGifs.animated(3)))
        assertEquals(1, ImageSniffer.gifFrameCount(TestGifs.static()))
        assertTrue(ImageSniffer.isAnimated(TestGifs.animated(2)))
        assertFalse(ImageSniffer.isAnimated(TestGifs.static()))
    }

    @Test
    fun firstFrameDropsLoopAndLaterFrames() {
        val first = ImageSniffer.gifFirstFrame(TestGifs.animated(3))!!
        assertEquals(1, ImageSniffer.gifFrameCount(first))
        assertArrayEquals(TestGifs.static(), first)
    }

    @Test
    fun rejectsNonGif() {
        assertNull(ImageSniffer.gifFirstFrame(byteArrayOf(1, 2, 3)))
        assertEquals(0, ImageSniffer.gifFrameCount(ByteArray(20)))
    }

    @Test
    fun detectsAnimatedWebp() {
        fun chunk(type: String, size: Int) = type.toByteArray() + byteArrayOf(size.toByte(), 0, 0, 0) + ByteArray(size)
        fun riff(body: ByteArray) = "RIFF".toByteArray() + byteArrayOf(0, 0, 0, 0) + "WEBP".toByteArray() + body
        val animated = riff(chunk("VP8X", 10) + chunk("ANIM", 6) + chunk("ANMF", 16) + chunk("ANMF", 16))
        val simple = riff(chunk("VP8 ", 10))
        assertEquals(2, ImageSniffer.webpFrameCount(animated))
        assertEquals(1, ImageSniffer.webpFrameCount(simple))
        assertTrue(ImageSniffer.isAnimated(animated))
        assertFalse(ImageSniffer.isAnimated(simple))
    }

    @Test
    fun detectsApng() {
        fun chunk(type: String, data: ByteArray) =
            byteArrayOf(0, 0, 0, data.size.toByte()) + type.toByteArray() + data + ByteArray(4)
        val signature = byteArrayOf(0x89.toByte(), 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A)
        val ihdr = chunk("IHDR", ByteArray(13))
        val apng = signature + ihdr + chunk("acTL", byteArrayOf(0, 0, 0, 4, 0, 0, 0, 0)) + chunk("IDAT", ByteArray(2))
        val png = signature + ihdr + chunk("IDAT", ByteArray(2))
        assertEquals(4, ImageSniffer.apngFrameCount(apng))
        assertEquals(1, ImageSniffer.apngFrameCount(png))
        assertTrue(ImageSniffer.isAnimated(apng))
        assertFalse(ImageSniffer.isAnimated(png))
    }
}
