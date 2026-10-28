package com.shirochi.npia.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonArray
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertSame
import org.junit.Assert.assertTrue
import org.junit.Test

class EngineSettingsTest {
    private val all = listOf(SiteEngine.NATIVE, SiteEngine.NOVEL_DOWNLOADER, SiteEngine.WEBTOEPUB)

    @Test
    fun orderKeepsKnownEnginesAndAppendsMissingOnes() {
        assertEquals(
            listOf(SiteEngine.WEBTOEPUB, SiteEngine.NATIVE, SiteEngine.NOVEL_DOWNLOADER),
            SiteEngine.order(listOf("webtoepub", "bogus", "native")),
        )
        assertEquals(all, SiteEngine.order(null))
        assertEquals(all, SiteEngine.order(listOf("native", "native", "novel_downloader", "webtoepub")))
        assertEquals(
            listOf(SiteEngine.NATIVE, SiteEngine.WEBTOEPUB),
            SiteEngine.order(all.map { it.raw }, listOf("novel_downloader")),
        )
    }

    @Test
    fun defaultsAreNativeThenRulesThenWebToEpubWithUpdatesOn() {
        val engines = AppSettings().engines
        assertEquals(all, engines.enabled())
        assertEquals(all, engines.fullOrder())
        assertTrue(engines.novelDownloaderAutoUpdate)
        assertTrue(engines.webtoepubAutoUpdate)
        assertTrue(engines.autoUpdate(SiteEngine.NOVEL_DOWNLOADER))
        assertTrue(engines.isDefaultOrder)
    }

    @Test
    fun enabledNeverComesBackEmpty() {
        val allOff = EngineSettings(disabled = all.map { it.raw })
        assertEquals(all, allOff.enabled())
        assertEquals(all, EngineSettings(order = emptyList()).enabled())
    }

    @Test
    fun theLastEnabledEngineCannotBeSwitchedOff() {
        var engines = EngineSettings()
        engines = engines.withEnabled(SiteEngine.NATIVE, false).withEnabled(SiteEngine.NOVEL_DOWNLOADER, false)
        assertEquals(listOf(SiteEngine.WEBTOEPUB), engines.enabled())
        assertSame(engines, engines.withEnabled(SiteEngine.WEBTOEPUB, false))
        assertEquals(listOf(SiteEngine.NATIVE, SiteEngine.WEBTOEPUB), engines.withEnabled(SiteEngine.NATIVE, true).enabled())
    }

    @Test
    fun enginesMoveOnePlaceAtATimeAndStopAtTheEnds() {
        val engines = EngineSettings()
        assertEquals(
            listOf(SiteEngine.NOVEL_DOWNLOADER, SiteEngine.NATIVE, SiteEngine.WEBTOEPUB),
            engines.moved(SiteEngine.NOVEL_DOWNLOADER, -1).fullOrder(),
        )
        assertEquals(
            listOf(SiteEngine.NATIVE, SiteEngine.WEBTOEPUB, SiteEngine.NOVEL_DOWNLOADER),
            engines.moved(SiteEngine.NOVEL_DOWNLOADER, 1).fullOrder(),
        )
        assertSame(engines, engines.moved(SiteEngine.NATIVE, -1))
        assertSame(engines, engines.moved(SiteEngine.WEBTOEPUB, 1))
        val moved = engines.moved(SiteEngine.WEBTOEPUB, -2)
        assertFalse(moved.isDefaultOrder)
        assertTrue(moved.resetOrder().isDefaultOrder)
    }

    @Test
    fun resetOrderKeepsTheUpdateChoices() {
        val reset = EngineSettings(
            order = listOf("webtoepub"),
            disabled = listOf("native"),
            webtoepubAutoUpdate = true,
        ).resetOrder()
        assertEquals(all, reset.enabled())
        assertTrue(reset.webtoepubAutoUpdate)
    }

    @Test
    fun settingsRoundTripAndOldFilesGetDefaults() {
        val settings = AppSettings(
            engines = EngineSettings(
                order = listOf("webtoepub", "native", "novel_downloader"),
                disabled = listOf("novel_downloader"),
                webtoepubAutoUpdate = true,
            ),
        )
        assertEquals(settings, SettingsCoding.decode(SettingsCoding.encode(settings)))
        assertEquals(EngineSettings(), SettingsCoding.decode("""{"shared": {"threads": 2}}""").engines)
    }

    @Test
    fun desktopConfigRoundTrip() {
        val (settings, _) = DesktopConfig.import(
            """
            {"source_engine_order": ["webtoepub", "native", "novel_downloader"],
             "source_engines_off": ["novel_downloader"],
             "novel_downloader_auto_update": false}
            """.trimIndent(),
        )
        assertEquals(
            EngineSettings(
                order = listOf("webtoepub", "native", "novel_downloader"),
                disabled = listOf("novel_downloader"),
                novelDownloaderAutoUpdate = false,
            ),
            settings.engines,
        )
        val exported = DesktopConfig.export(settings)
        assertEquals(
            listOf("webtoepub", "native", "novel_downloader"),
            exported["source_engine_order"]!!.jsonArray.map { it.jsonPrimitive.content },
        )
        assertEquals(listOf("novel_downloader"), exported["source_engines_off"]!!.jsonArray.map { it.jsonPrimitive.content })
        assertEquals(true, exported["webtoepub_auto_update"]!!.jsonPrimitive.booleanOrNull)
        assertEquals(false, exported["novel_downloader_auto_update"]!!.jsonPrimitive.booleanOrNull)
        assertEquals(settings.engines, DesktopConfig.import(exported).first.engines)
    }

    @Test
    fun desktopImportDropsUnknownNamesAndAppendsMissingOnes() {
        val (settings, _) = DesktopConfig.import(
            """{"source_engine_order": ["webtoepub", "bogus"], "source_engines_off": ["bogus", "native"]}""",
        )
        assertEquals(listOf("webtoepub", "native", "novel_downloader"), settings.engines.order)
        assertEquals(listOf("native"), settings.engines.disabled)
    }

    @Test
    fun desktopImportNeverLeavesEveryEngineOff() {
        val (settings, _) = DesktopConfig.import("""{"source_engines_off": ["native", "novel_downloader", "webtoepub"]}""")
        assertEquals(emptyList<String>(), settings.engines.disabled)
        assertEquals(all, settings.engines.enabled())
    }

    @Test
    fun desktopImportKeepsCurrentChoicesForMissingKeys() {
        val base = AppSettings(engines = EngineSettings(disabled = listOf("native"), novelDownloaderAutoUpdate = true))
        val (settings, _) = DesktopConfig.import("""{"ext_threads": 3}""", base)
        assertEquals(base.engines, settings.engines)
        // A key of the wrong type is ignored as well.
        val (wrong, _) = DesktopConfig.import("""{"source_engine_order": "webtoepub", "webtoepub_auto_update": "yes"}""", base)
        assertEquals(base.engines, wrong.engines)
    }

    @Test
    fun desktopExportKeepsUnknownKeys() {
        val existing = Json.parseToJsonElement("""{"future_key": 1}""").jsonObject
        val exported = DesktopConfig.export(AppSettings(), existing)
        assertTrue("future_key" in exported)
        assertEquals(JsonArray(all.map { JsonPrimitive(it.raw) }), exported["source_engine_order"]!!.jsonArray)
        assertEquals(JsonArray(emptyList()), exported["source_engines_off"]!!.jsonArray)
    }
}
