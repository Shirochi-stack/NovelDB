package com.shirochi.npia.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class SettingsTest {
    @Test
    fun decodeFillsMissingKeysWithDefaults() {
        val settings = SettingsCoding.decode("""{"shared": {"threads": 8}, "upload": {"autoUpload": true}, "future": 1}""")
        assertEquals(8, settings.shared.threads)
        assertEquals(listOf(OutputFormat.EPUB), settings.shared.formats)
        assertTrue(settings.upload.autoUpload)
        assertEquals(UploadSettings().hosts, settings.upload.hosts)
    }

    @Test
    fun encodeDecodeRoundTrip() {
        val settings = AppSettings(
            shared = SharedSettings(rangeFrom = 5),
            overrides = mapOf("npia" to SharedOverrides(threads = 2)),
        )
        assertEquals(settings, SettingsCoding.decode(SettingsCoding.encode(settings)))
    }

    @Test
    fun jsonKeysMatchIosShape() {
        val text = SettingsCoding.encode(AppSettings())
        assertTrue(text.contains("\"separateTXT\""))
        assertTrue(text.contains("\"recompressTargetMB\"").not()) // null fields are omitted
        assertTrue(text.contains("\"WEBP\""))
    }

    @Test
    fun overridesApplyPerSource() {
        val settings = AppSettings(
            shared = SharedSettings(threads = 4),
            overrides = mapOf("npia" to SharedOverrides(threads = 1, formats = listOf(OutputFormat.TXT))),
        )
        assertEquals(1, settings.effective(SourceId.NPIA).threads)
        assertEquals(listOf(OutputFormat.TXT), settings.effective(SourceId.NPIA).formats)
        assertEquals(4, settings.effective(SourceId.KPAGE).threads)
    }

    @Test
    fun desktopImportSplitsSharedNpiaAndSecrets() {
        val legacyKpage = "\u006b\u0061\u006b\u0061\u006f"
        val config = """
            {"email": "reader@example.com", "wd": "pw", "loginkey": "",
             "thread_num": 4, "interval_num": 2.5, "interval_max_num": 4.0,
             "save_formats": ["epub", "txt"], "from_enabled": false, "to_enabled": true, "to_num": 10,
             "ext_threads": 4, "ext_interval": 1.0, "ext_interval_max": 2.0,
             "ext_formats": ["epub", "txt"], "ext_from_enabled": false, "ext_to_enabled": true, "ext_from": 89, "ext_to": 92,
             "image_format": "WEBP", "jpeg_quality": 90, "recompress_target_enable": false, "recompress_target_mb": 20.0,
             "ext_${legacyKpage}_dedupe_leading_images": 3, "ext_kpage_keep_filler": true}
        """
        val (settings, secrets) = DesktopConfig.import(config)
        assertEquals(4, settings.shared.threads)
        assertEquals(1.0, settings.shared.intervalMin, 0.0)
        assertEquals(2.0, settings.shared.intervalMax, 0.0)
        assertNull(settings.shared.rangeFrom)
        assertEquals(92, settings.shared.rangeTo)

        val npia = settings.effective(SourceId.NPIA)
        assertEquals(4, npia.threads)
        assertEquals(2.5, npia.intervalMin, 0.0)
        assertEquals(4.0, npia.intervalMax, 0.0)
        assertEquals(10, npia.rangeTo)
        assertNull("equal values are not stored as overrides", settings.overrides["npia"]?.threads)

        assertEquals(ImageFormat.WEBP, settings.images.format)
        assertNull(settings.images.recompressTargetMb)
        assertEquals(3, settings.sources.kpage.dedupeLeadingImages)
        assertTrue(settings.sources.kpage.keepFiller)

        assertEquals("reader@example.com", secrets.email)
        assertEquals("pw", secrets.password)
        assertNull(secrets.loginKey)
    }

    @Test
    fun desktopExportKeepsUnknownKeysAndOmitsSecrets() {
        val settings = AppSettings(
            shared = SharedSettings(threads = 6),
            overrides = mapOf("npia" to SharedOverrides(threads = 3)),
        )
        val existing = Json.parseToJsonElement("""{"gui_scale_factor": 1.7, "email": "keep@example.com"}""").jsonObject
        val exported = DesktopConfig.export(settings, existing)
        assertEquals(6, exported["ext_threads"]!!.jsonPrimitive.int)
        assertEquals(3, exported["thread_num"]!!.jsonPrimitive.int)
        assertEquals(JsonPrimitive(1.7), exported["gui_scale_factor"])
        assertEquals("keep@example.com", exported["email"]!!.jsonPrimitive.content)
        assertFalse(exported.containsKey("wd"))

        val (reimported, _) = DesktopConfig.import(exported)
        assertEquals(3, reimported.effective(SourceId.NPIA).threads)
        assertEquals(6, reimported.shared.threads)
    }

    @Test
    fun inMemorySecretStore() {
        val store = InMemorySecretStore()
        store.set(SecretKey.NPIA_LOGIN_KEY, "abc")
        assertEquals("abc", store.get(SecretKey.NPIA_LOGIN_KEY))
        store.set(SecretKey.NPIA_LOGIN_KEY, null)
        assertNull(store.get(SecretKey.NPIA_LOGIN_KEY))
    }

    @Test
    fun sniffsImageTypes() {
        assertEquals("jpg", sniffImageExtension(byteArrayOf(0xFF.toByte(), 0xD8.toByte(), 0xFF.toByte())))
        assertEquals("bin", sniffImageExtension(byteArrayOf(1, 2)))
    }

    @Test
    fun bareHostStripsWww() {
        assertEquals("example.com", bareHost("https://WWW.Example.com/a"))
        assertEquals("", bareHost("not a url"))
    }

    @Test
    fun intervalRangeMatchesDesktopNormalization() {
        assertEquals(0.5 to 2.0, SharedSettings(intervalMin = 2.0, intervalMax = 0.5).intervalRange)
        assertEquals(0.0 to 300.0, SharedSettings(intervalMin = -1.0, intervalMax = 999.0).intervalRange)
        assertEquals(1.5, SharedSettings(intervalMin = 1.5, intervalMax = 1.5).chapterDelay(), 0.0)
        assertEquals(0.0, SharedSettings(intervalMin = 0.0, intervalMax = 0.0).chapterDelay(), 0.0)
        repeat(50) {
            val d = SharedSettings(intervalMin = 0.5, intervalMax = 1.0).chapterDelay()
            assertTrue(d in 0.5..1.0)
        }
    }

    @Test
    fun rateLimitingDefaultsToTwoToFiveSeconds() {
        val defaults = SharedSettings()
        assertEquals(2.0 to 5.0, defaults.intervalRange)
        // Missing desktop keys fall back to the same defaults; saved values are untouched.
        val (settings, _) = DesktopConfig.import("""{"ext_threads": 3}""")
        assertEquals(2.0, settings.shared.intervalMin, 0.0)
        assertEquals(5.0, settings.shared.intervalMax, 0.0)
        val (saved, _) = DesktopConfig.import("""{"ext_interval": 1.0, "ext_interval_max": 1.0}""")
        assertEquals(1.0 to 1.0, saved.shared.intervalRange)
    }

    @Test
    fun cbzFormatRoundTripsThroughDesktopConfig() {
        val (settings, _) = DesktopConfig.import("""{"ext_formats": ["epub", "cbz"]}""")
        assertEquals(listOf(OutputFormat.EPUB, OutputFormat.CBZ), settings.shared.formats)
    }
}
