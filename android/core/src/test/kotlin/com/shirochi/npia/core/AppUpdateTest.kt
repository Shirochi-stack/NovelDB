package com.shirochi.npia.core

import kotlinx.serialization.json.Json
import kotlinx.serialization.json.booleanOrNull
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class AppUpdateTest {
    private val sha = "a".repeat(64)

    private fun asset(
        name: String = "NovelDB-universal_hotfix3.apk",
        kind: String = "apk",
        variant: String = "universal",
        arch: String = "universal",
        build: Long = 1793193554,
        label: String = "hotfix3",
        extra: String = "",
    ) = """{"name": "$name", "variant": "$variant", "arch": "$arch", "kind": "$kind", "build": $build,
        "label": "$label", "commit": "abc123", "size": 12974254, "sha256": "$sha"$extra}"""

    private fun manifest(vararg assets: String, schema: String = "1", extra: String = "") =
        """{"schema": $schema, "platform": "android", "tag": "v98",
            "release_url": "https://github.com/Shirochi-stack/NovelDB/releases/tag/v98",
            "assets": [${assets.joinToString(",")}]$extra}"""

    // Parsing

    @Test
    fun parsesTheContractExample() {
        val parsed = UpdateManifests.parse(manifest(asset()))!!
        assertEquals("v98", parsed.tag)
        assertEquals("https://github.com/Shirochi-stack/NovelDB/releases/tag/v98", parsed.releaseUrl)
        val entry = parsed.assets.single()
        assertEquals("NovelDB-universal_hotfix3.apk", entry.name)
        assertEquals("apk", entry.kind)
        assertEquals(1793193554L, entry.build)
        assertEquals(12974254L, entry.size)
        assertEquals("hotfix3", entry.label)
        assertEquals(sha, entry.sha256)
    }

    @Test
    fun unknownFieldsAreIgnored() {
        val text = manifest(asset(extra = """, "future": {"a": [1, 2]}"""), extra = """, "newer_thing": true""")
        val parsed = UpdateManifests.parse(text)!!
        assertEquals(1, parsed.assets.size)
        assertEquals("v98", parsed.tag)
    }

    @Test
    fun aWrongSchemaMeansNoUpdateInformation() {
        assertNull(UpdateManifests.parse(manifest(asset(), schema = "2")))
        assertNull(UpdateManifests.parse(manifest(asset(), schema = "\"1\"")))
        assertNull(UpdateManifests.parse(manifest(asset(), schema = "null")))
        assertNull(UpdateManifests.parse("""{"tag": "v98", "assets": []}"""))
    }

    @Test
    fun aBadManifestMeansNoUpdateInformation() {
        assertNull(UpdateManifests.parse(""))
        assertNull(UpdateManifests.parse("<html>Not Found</html>"))
        assertNull(UpdateManifests.parse("[1, 2]"))
        assertNull(UpdateManifests.parse("""{"schema": 1, "assets": []}"""))
        assertNull(UpdateManifests.parse("""{"schema": 1, "tag": "v9/../x", "assets": []}"""))
    }

    @Test
    fun incompleteEntriesAreSkippedAndGoodOnesKept() {
        val parsed = UpdateManifests.parse(
            manifest(
                """{"name": "a.apk", "kind": "apk", "build": 5, "size": 10}""", // no checksum
                """{"name": "b.apk", "kind": "apk", "build": 5, "size": 10, "sha256": "zz"}""",
                """{"name": "c.apk", "kind": "apk", "build": 5, "size": 0, "sha256": "$sha"}""",
                """{"name": "d.apk", "kind": "apk", "build": "5", "size": 10, "sha256": "$sha"}""",
                """{"name": "../e.apk", "kind": "apk", "build": 5, "size": 10, "sha256": "$sha"}""",
                """{"name": "f/g.apk", "kind": "apk", "build": 5, "size": 10, "sha256": "$sha"}""",
                "42",
                asset(),
            ),
        )!!
        assertEquals(listOf("NovelDB-universal_hotfix3.apk"), parsed.assets.map { it.name })
    }

    @Test
    fun aMissingReleaseUrlFallsBackToTheTagPage() {
        val text = """{"schema": 1, "tag": "v98", "release_url": "javascript:alert(1)", "assets": []}"""
        assertEquals("https://github.com/Shirochi-stack/NovelDB/releases/tag/v98", UpdateManifests.parse(text)!!.releaseUrl)
    }

    @Test
    fun uppercaseChecksumsAreNormalised() {
        val text = manifest(asset().replace(sha, "AB".repeat(32)))
        assertEquals("ab".repeat(32), UpdateManifests.parse(text)!!.assets.single().sha256)
    }

    // Picking the entry and comparing builds

    @Test
    fun theAppsOwnEntryIsTheUniversalApk() {
        val parsed = UpdateManifests.parse(
            manifest(
                asset(name = "NovelDB.ipa", kind = "ipa"),
                asset(name = "NovelDB-arm.apk", arch = "arm64"),
                asset(name = "NovelDB-lite.apk", variant = "lite"),
                asset(name = "NovelDB.apk", build = 1793193000),
            ),
        )!!
        assertEquals("NovelDB.apk", AppUpdates.entry(parsed)!!.name)
    }

    @Test
    fun withSeveralMatchingEntriesTheNewestBuildWins() {
        val parsed = UpdateManifests.parse(manifest(asset(name = "old.apk", build = 100), asset(name = "new.apk", build = 300), asset(name = "mid.apk", build = 200)))!!
        assertEquals("new.apk", AppUpdates.entry(parsed)!!.name)
    }

    @Test
    fun aManifestWithoutTheAppsEntryOffersNothing() {
        val parsed = UpdateManifests.parse(manifest(asset(kind = "ipa"), asset(kind = "dmg")))!!
        assertNull(AppUpdates.entry(parsed))
        assertNull(AppUpdates.offer(parsed, AppBuild(1, "v1")))
        assertNull(AppUpdates.offer(UpdateManifests.parse(manifest())!!, AppBuild(1, "v1")))
    }

    @Test
    fun anUpdateExistsOnlyForAStrictlyLargerBuild() {
        val parsed = UpdateManifests.parse(manifest(asset(build = 1000)))!!
        assertNotNull(AppUpdates.offer(parsed, AppBuild(999, "v1")))
        assertNull(AppUpdates.offer(parsed, AppBuild(1000, "v98")))
        assertNull(AppUpdates.offer(parsed, AppBuild(1001, "v99")))
    }

    @Test
    fun aDevelopmentBuildIsNeverOfferedAnUpdate() {
        val parsed = UpdateManifests.parse(manifest(asset(build = 1000)))!!
        assertNull(AppUpdates.offer(parsed, AppBuild()))
        assertNull(AppUpdates.offer(parsed, AppBuild(0, "v1", "none")))
        assertTrue(AppBuild().isDevelopment)
        assertFalse(AppBuild(1, "v1").isDevelopment)
    }

    @Test
    fun theOfferNamesTheVersionAndLabel() {
        val parsed = UpdateManifests.parse(manifest(asset()))!!
        val offer = AppUpdates.offer(parsed, AppBuild(1, "v97"))!!
        assertEquals("v98 hotfix3", offer.version)
        assertEquals("NovelDB v98 hotfix3 is available", offer.title)
        assertEquals(1793193554L, offer.build)
        val plain = AppUpdates.offer(UpdateManifests.parse(manifest(asset(label = "none")))!!, AppBuild(1, "v97"))!!
        assertEquals("NovelDB v98 is available", plain.title)
    }

    @Test
    fun buildDescriptionsReadAsTheContractShowsThem() {
        assertEquals("v98 hotfix3", AppBuild(1793193554, "v98", "hotfix3").version)
        assertEquals("v98", AppBuild(1793193554, "v98", "none").version)
        assertEquals("v98", AppBuild(1793193554, "v98", "").version)
        assertEquals("v98 hotfix3, build 1793193554", AppBuild(1793193554, "v98", "hotfix3").statusLine)
        assertEquals("v98, build 1793193554", AppBuild(1793193554, "v98", "none").statusLine)
        assertEquals("Development build: app updates are off", AppBuild().statusLine)
        assertEquals("build 5", AppBuild(5).statusLine)
    }

    // Addresses

    @Test
    fun urlsFollowTheContractAndTakeAnOverride() {
        val github = AppUpdateUrls()
        assertEquals("https://github.com/Shirochi-stack/NovelDB/releases/latest/download/update-android.json", github.manifest())
        assertEquals("https://github.com/Shirochi-stack/NovelDB/releases/download/v98/NovelDB-universal.apk", github.asset("v98", "NovelDB-universal.apk"))
        val local = AppUpdateUrls(" http://10.0.2.2:8000/ ")
        assertEquals("http://10.0.2.2:8000/latest/download/update-android.json", local.manifest())
        assertEquals("http://10.0.2.2:8000/download/v2/a%20b.apk", local.asset("v2", "a b.apk"))
        assertEquals(AppUpdateUrls.DEFAULT_BASE, AppUpdateUrls("").base)
        assertEquals(AppUpdateUrls.DEFAULT_BASE, AppUpdateUrls(null).base)
    }

    // The 24-hour rule

    @Test
    fun theAutomaticCheckRunsAtMostOncePerDay() {
        val hour = 3600_000L
        val now = 1_800_000_000_000L
        assertTrue(UpdateSchedule.due(0, now))
        assertFalse(UpdateSchedule.due(now - 1, now))
        assertFalse(UpdateSchedule.due(now - 23 * hour, now))
        assertFalse(UpdateSchedule.due(now - 24 * hour + 1, now))
        assertTrue(UpdateSchedule.due(now - 24 * hour, now))
        assertTrue(UpdateSchedule.due(now - 25 * hour, now))
        // A clock set back past the last check must not block checks for days.
        assertTrue(UpdateSchedule.due(now + hour, now))
    }

    @Test
    fun theAutomaticCheckNeedsTheSettingAndARealBuild() {
        val real = AppBuild(1793193554, "v98")
        val now = 1_800_000_000_000L
        assertTrue(UpdateSchedule.automaticCheckDue(true, real, 0, now))
        assertFalse(UpdateSchedule.automaticCheckDue(false, real, 0, now))
        assertFalse(UpdateSchedule.automaticCheckDue(true, AppBuild(), 0, now))
        assertFalse(UpdateSchedule.automaticCheckDue(true, real, now - 1000, now))
    }

    // The setting

    @Test
    fun theSettingIsOnByDefaultAndOldFilesGetIt() {
        assertTrue(AppSettings().appUpdateCheck)
        assertTrue(SettingsCoding.decode("""{"shared": {"threads": 2}}""").appUpdateCheck)
        assertTrue(DesktopConfig.import("""{"ext_threads": 3}""").first.appUpdateCheck)
    }

    @Test
    fun theSettingRoundTripsThroughTheDesktopConfig() {
        val (off, _) = DesktopConfig.import("""{"app_update_check": false}""")
        assertFalse(off.appUpdateCheck)
        val exportedOff = DesktopConfig.export(off)
        assertEquals(false, exportedOff["app_update_check"]!!.jsonPrimitive.booleanOrNull)
        assertFalse(DesktopConfig.import(exportedOff).first.appUpdateCheck)

        val (on, _) = DesktopConfig.import("""{"app_update_check": true}""", off)
        assertTrue(on.appUpdateCheck)
        assertEquals(true, DesktopConfig.export(on)["app_update_check"]!!.jsonPrimitive.booleanOrNull)
        // The default is exported, so a desktop file always says what the app does.
        assertEquals(true, DesktopConfig.export(AppSettings())["app_update_check"]!!.jsonPrimitive.booleanOrNull)
    }

    @Test
    fun theSettingKeepsItsValueWhenTheFileHasNoKeyOrTheWrongType() {
        val base = AppSettings(appUpdateCheck = false)
        assertFalse(DesktopConfig.import("""{"ext_threads": 3}""", base).first.appUpdateCheck)
        assertFalse(DesktopConfig.import("""{"app_update_check": "yes"}""", base).first.appUpdateCheck)
        assertFalse(DesktopConfig.import("""{"app_update_check": null}""", base).first.appUpdateCheck)
    }

    @Test
    fun theSettingRoundTripsThroughTheAppSettingsFile() {
        val off = AppSettings(appUpdateCheck = false)
        assertEquals(off, SettingsCoding.decode(SettingsCoding.encode(off)))
        assertEquals(false, Json.parseToJsonElement(SettingsCoding.encode(off)).jsonObject["appUpdateCheck"]!!.jsonPrimitive.booleanOrNull)
        assertEquals(true, Json.parseToJsonElement(SettingsCoding.encode(AppSettings())).jsonObject["appUpdateCheck"]!!.jsonPrimitive.booleanOrNull)
    }
}
