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
    fun zipBundlingDefaultsToOnWithOneGigabyte() {
        val defaults = UploadSettings()
        assertTrue(defaults.zipBundle)
        assertEquals(1024, defaults.zipMaxMb)
        assertEquals(1024L * 1024 * 1024, defaults.zipLimitBytes)
        // Older settings files have neither key.
        val old = SettingsCoding.decode("""{"upload": {"autoUpload": true}}""")
        assertTrue(old.upload.zipBundle)
        assertEquals(1024, old.upload.zipMaxMb)
        val text = SettingsCoding.encode(AppSettings())
        assertTrue(text, text.contains("\"zipBundle\": true") && text.contains("\"zipMaxMB\": 1024"))
        val custom = AppSettings(upload = UploadSettings(zipBundle = false, zipMaxMb = 300))
        assertEquals(custom, SettingsCoding.decode(SettingsCoding.encode(custom)))
        // Out-of-range values are brought back into 50..4096 when used.
        assertEquals(50L * 1024 * 1024, UploadSettings(zipMaxMb = 1).zipLimitBytes)
        assertEquals(4096L * 1024 * 1024, UploadSettings(zipMaxMb = 100000).zipLimitBytes)
    }

    @Test
    fun zipBundlingRoundTripsThroughTheDesktopConfig() {
        val (imported, _) = DesktopConfig.import("""{"upload_zip_bundle": false, "upload_zip_max_mb": 250}""")
        assertFalse(imported.upload.zipBundle)
        assertEquals(250, imported.upload.zipMaxMb)
        val exported = DesktopConfig.export(imported)
        assertEquals(false, exported["upload_zip_bundle"]!!.jsonPrimitive.content.toBoolean())
        assertEquals(250, exported["upload_zip_max_mb"]!!.jsonPrimitive.int)
        val (again, _) = DesktopConfig.import(exported)
        assertEquals(imported.upload, again.upload)

        // A config without the keys leaves the app's values alone; the defaults export as on / 1024.
        val (untouched, _) = DesktopConfig.import("""{"ext_threads": 3}""", imported)
        assertEquals(imported.upload, untouched.upload)
        val fresh = DesktopConfig.export(AppSettings())
        assertEquals(true, fresh["upload_zip_bundle"]!!.jsonPrimitive.content.toBoolean())
        assertEquals(1024, fresh["upload_zip_max_mb"]!!.jsonPrimitive.int)
        // Limits: below 50 and above 4096 are clamped on import.
        assertEquals(50, DesktopConfig.import("""{"upload_zip_max_mb": 5}""").first.upload.zipMaxMb)
        assertEquals(4096, DesktopConfig.import("""{"upload_zip_max_mb": 9000}""").first.upload.zipMaxMb)
        // Unknown keys of the desktop file survive an export onto it.
        val onto = DesktopConfig.export(imported, kotlinx.serialization.json.JsonObject(mapOf("future_key" to JsonPrimitive(1))))
        assertEquals(1, onto["future_key"]!!.jsonPrimitive.int)
    }

    @Test
    fun timeoutsRoundTripThroughTheDesktopConfig() {
        val fresh = DesktopConfig.export(AppSettings())
        assertEquals(60, fresh["request_timeout"]!!.jsonPrimitive.int)
        assertEquals(60, fresh["page_load_timeout"]!!.jsonPrimitive.int)
        val (imported, _) = DesktopConfig.import("""{"request_timeout": 90, "page_load_timeout": 120}""")
        assertEquals(90, imported.shared.requestTimeoutSeconds)
        assertEquals(120, imported.shared.pageLoadTimeoutSeconds)
        val (again, _) = DesktopConfig.import(DesktopConfig.export(imported))
        assertEquals(imported.shared, again.shared)
        // Missing keys leave the values alone; out-of-range ones are clamped to 10-600.
        assertEquals(90, DesktopConfig.import("""{"ext_threads": 3}""", imported).first.shared.requestTimeoutSeconds)
        val clamped = DesktopConfig.import("""{"request_timeout": 5, "page_load_timeout": 9999}""").first.shared
        assertEquals(10, clamped.requestTimeoutSeconds)
        assertEquals(600, clamped.pageLoadTimeoutSeconds)
        // Settings saved before the keys existed decode with the defaults.
        assertEquals(60, SettingsCoding.decode("""{"shared": {"threads": 2}}""").shared.requestTimeoutSeconds)
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
    fun queryGroupsRoundTripThroughDesktopConfig() {
        val (imported, _) = DesktopConfig.import("""{"tag_query_groups": [["TS"], ["약피폐", "피폐"], []], "tag_selected_tags": ["백합"]}""")
        assertEquals(listOf(listOf("TS"), listOf("약피폐", "피폐")), imported.npia.tagQueryGroups)
        assertEquals(listOf("백합"), imported.npia.tagSelectedTags)

        val exported = DesktopConfig.export(imported)
        val groups = exported["tag_query_groups"] as kotlinx.serialization.json.JsonArray
        assertEquals(2, groups.size)
        assertEquals(listOf("약피폐", "피폐"), (groups[1] as kotlinx.serialization.json.JsonArray).map { it.jsonPrimitive.content })
        val (again, _) = DesktopConfig.import(exported)
        assertEquals(imported.npia.tagQueryGroups, again.npia.tagQueryGroups)

        // A file without the key keeps the groups already stored.
        val (kept, _) = DesktopConfig.import("""{"ext_threads": 2}""", imported)
        assertEquals(imported.npia.tagQueryGroups, kept.npia.tagQueryGroups)
        // The app's own settings file carries them too.
        assertEquals(imported, SettingsCoding.decode(SettingsCoding.encode(imported)))
    }

    @Test
    fun searchQueriesAreClampedToTheTermCount() {
        assertEquals(165, NpiaSettings().scrapeMaxQueries)
        assertEquals(165, DesktopConfig.import("""{"scrape_max_queries": 1000}""").first.npia.scrapeMaxQueries)
        assertEquals(1, DesktopConfig.import("""{"scrape_max_queries": 0}""").first.npia.scrapeMaxQueries)
        assertEquals(42, DesktopConfig.import("""{"scrape_max_queries": 42}""").first.npia.scrapeMaxQueries)
        // The desktop's old default of 100 means "all" again.
        assertEquals(165, DesktopConfig.import("""{"scrape_max_queries": 100}""").first.npia.scrapeMaxQueries)
        // A stored value from the old 1..1000 stepper is limited wherever it is used and exported.
        val old = NpiaSettings(scrapeMaxQueries = 800)
        assertEquals(165, old.queryCount)
        assertEquals(165, DesktopConfig.export(AppSettings(npia = old))["scrape_max_queries"]!!.jsonPrimitive.int)
    }

    @Test
    fun cbzFormatRoundTripsThroughDesktopConfig() {
        val (settings, _) = DesktopConfig.import("""{"ext_formats": ["epub", "cbz"]}""")
        assertEquals(listOf(OutputFormat.EPUB, OutputFormat.CBZ), settings.shared.formats)
    }
}
