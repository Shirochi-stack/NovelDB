package com.shirochi.npia.sources.engines

import kotlinx.coroutines.test.runTest
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Test
import java.io.File
import java.io.IOException
import java.nio.file.Files

/** Shipped files from memory, keyed by resource path. */
internal class MapFiles(private val files: Map<String, ByteArray>) : ShippedFiles {
    override fun read(path: String): ByteArray? = files[path]
}

/** The `engines` release from memory; a missing asset is an HTTP 404 like on GitHub. */
internal class FakeRelease(val files: MutableMap<String, ByteArray> = mutableMapOf()) : EngineFetch {
    val requested = mutableListOf<String>()

    override suspend fun get(url: String, timeoutSeconds: Long): ByteArray {
        requested += url
        val name = url.removePrefix(EngineUpdates.RELEASE_URL)
        return files[name] ?: throw IOException("HTTP 404")
    }

    fun publish(engine: RuleEngine, data: ByteArray, edit: (MutableMap<String, Any?>) -> Unit = {}) {
        val fields = mutableMapOf<String, Any?>(
            "engine" to engine.name,
            "api" to engine.api,
            "commit" to "9999999aaaaaaaa",
            "date" to "2026-10-29T00:00:00Z",
            "version" to if (engine === RuleEngine.NOVEL_DOWNLOADER) "5.3.1" else "1.0.15.2",
            "shim" to "shim0001",
            "file" to engine.lib,
            "size" to data.size,
            "sha256" to EngineStore.sha256(data),
            "built" to "2026-10-29T06:00:00Z",
        )
        edit(fields)
        files[engine.releaseManifest] = buildJsonObject {
            fields.forEach { (key, value) ->
                when (value) {
                    null -> Unit
                    is Int -> put(key, value)
                    else -> put(key, value.toString())
                }
            }
        }.toString().toByteArray()
        (fields["file"] as? String)?.let { files[it] = data }
    }
}

class EngineUpdatesTest {
    private lateinit var root: File
    private var now = 1_800_000_000_000L
    private val webToEpub = RuleEngine.WEBTOEPUB
    private val rules = RuleEngine.NOVEL_DOWNLOADER

    private val shippedLib = "/* shipped webtoepub */".toByteArray()
    private val shippedRules = "// ==UserScript==\n// @version        5.2.1285\n// ==/UserScript==\n/* shipped rules */".toByteArray()
    private val shippedFiles = MapFiles(
        mapOf(
            webToEpub.shippedPath to shippedLib,
            webToEpub.shippedManifestPath!! to """
                {"engine": "webtoepub", "api": 1, "commit": "cc1ef36c89f9e8adddf37094dfd67e8bb500ec39", "shim": "6eb45683d5fa",
                 "date": "2026-10-27T04:19:22Z", "version": "1.0.14.61", "built": "2026-10-28T09:52:35Z"}
            """.trimIndent().toByteArray(),
            rules.shippedPath to shippedRules,
        ),
    )

    private lateinit var store: EngineStore
    private lateinit var updates: EngineUpdates
    private val release = FakeRelease()

    @Before
    fun setUp() {
        root = Files.createTempDirectory("engines").toFile()
        store = EngineStore(root, shippedFiles)
        updates = EngineUpdates(store, clock = { now })
    }

    @After
    fun tearDown() {
        root.deleteRecursively()
    }

    private val newBundle = "/* newer webtoepub */".toByteArray()

    // What is shipped

    @Test
    fun shippedBundlesNameTheirVersion() {
        val parsers = store.shipped(webToEpub)!!
        assertEquals("2026-10-27, cc1ef36, built in", parsers.label)
        assertFalse(parsers.updated)
        assertEquals(EngineKey.of("1.0.14.61", "2026-10-27T04:19:22Z", "2026-10-28T09:52:35Z"), parsers.key)
        // novel-downloader's version is the @version line of its script.
        val ruleBundle = store.shipped(rules)!!
        assertEquals("5.2.1285, built in", ruleBundle.label)
        assertEquals(EngineKey.of("5.2.1285"), ruleBundle.key)
    }

    @Test
    fun aShippedManifestForAnotherApiIsNotUsed() {
        val files = MapFiles(
            mapOf(webToEpub.shippedPath to shippedLib, webToEpub.shippedManifestPath!! to """{"api": 2}""".toByteArray()),
        )
        assertNull(EngineStore(root, files).shipped(webToEpub))
    }

    @Test
    fun versionsCompareByTheirDigitsThenDateThenBuildTime() {
        assertTrue(EngineKey.of("1.0.14.61") > EngineKey.of("1.0.14.9"))
        assertTrue(EngineKey.of("1.0.15") > EngineKey.of("1.0.14.99"))
        assertTrue(EngineKey.of("1.0.14.61.1") > EngineKey.of("1.0.14.61"))
        assertTrue(EngineKey.of("1.0.14.61", "2026-10-28") > EngineKey.of("1.0.14.61", "2026-10-27"))
        assertTrue(EngineKey.of("1.0.14.61", "d", "2") > EngineKey.of("1.0.14.61", "d", "1"))
        assertEquals(EngineKey.of("v5.2"), EngineKey.of("5.2"))
        assertTrue(EngineKey.of("5.2.1285", "2026-10-27") > EngineKey.of("5.2.1285"))
    }

    // Updating

    @Test
    fun aNewerReleaseIsDownloadedVerifiedAndStored() = runTest {
        release.publish(webToEpub, newBundle)
        val result = updates.check(webToEpub, release)

        assertEquals(result.message, UpdateResult.Status.UPDATED, result.status)
        assertEquals("Updated the WebToEpub parsers to 2026-10-29, 9999999, downloaded update.", result.message)
        val folder = File(root, "webtoepub")
        assertEquals("newer webtoepub", File(folder, "webtoepub-lib.js").readText().removeSurrounding("/* ", " */"))
        assertEquals("webtoepub-lib.js", store.downloaded(webToEpub)!!.manifest.text("file"))
        assertEquals(listOf("manifest.json", "state.json", "webtoepub-lib.js"), folder.list()!!.sorted())
        assertTrue(store.current(webToEpub)!!.updated)
        assertEquals(String(newBundle), store.current(webToEpub)!!.read())
        // The shipped copy is still there for a fallback.
        assertEquals(String(shippedLib), store.shipped(webToEpub)!!.read())
        assertEquals(
            listOf(EngineUpdates.RELEASE_URL + "webtoepub-manifest.json", EngineUpdates.RELEASE_URL + "webtoepub-lib.js"),
            release.requested,
        )
    }

    @Test
    fun theReleaseFileIsRenamedToTheLibName() = runTest {
        release.publish(webToEpub, newBundle) { it["file"] = "webtoepub-lib-2026-10-29.js" }
        assertEquals(UpdateResult.Status.UPDATED, updates.check(webToEpub, release).status)
        assertTrue(File(root, "webtoepub/webtoepub-lib.js").isFile)
        assertFalse(File(root, "webtoepub/webtoepub-lib-2026-10-29.js").exists())
        assertEquals("webtoepub-lib.js", store.downloaded(webToEpub)!!.manifest.text("file"))
    }

    @Test
    fun anOlderOrEqualReleaseChangesNothing() = runTest {
        release.publish(webToEpub, newBundle) {
            it["version"] = "1.0.14.61"
            it["date"] = "2026-10-27T04:19:22Z"
            it["built"] = "2026-10-28T09:52:35Z"
        }
        val equal = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.CURRENT, equal.status)
        assertEquals("The WebToEpub parsers are up to date (2026-10-27, cc1ef36, built in).", equal.message)

        release.publish(webToEpub, newBundle) { it["version"] = "1.0.13.0" }
        assertEquals(UpdateResult.Status.CURRENT, updates.check(webToEpub, release).status)
        assertNull(store.downloaded(webToEpub))
        assertFalse(release.requested.any { it.endsWith("webtoepub-lib.js") })
    }

    @Test
    fun aRebuildOfTheBundleInUseIsNotDownloaded() = runTest {
        // The same upstream commit and build patch as the shipped copy; only the build time is newer.
        release.publish(webToEpub, newBundle) {
            it["commit"] = "cc1ef36c89f9e8adddf37094dfd67e8bb500ec39"
            it["shim"] = "6eb45683d5fa"
            it["version"] = "1.0.14.61"
            it["date"] = "2026-10-27T04:19:22Z"
            it["built"] = "2026-10-29T10:00:00Z"
        }
        val rebuild = updates.check(webToEpub, release)
        assertEquals(rebuild.message, UpdateResult.Status.CURRENT, rebuild.status)
        assertEquals("The WebToEpub parsers are up to date (2026-10-27, cc1ef36, built in).", rebuild.message)
        assertEquals(listOf(EngineUpdates.RELEASE_URL + "webtoepub-manifest.json"), release.requested)
        assertNull(store.downloaded(webToEpub))

        // Another build patch of the same commit is a real update ...
        release.publish(webToEpub, newBundle) {
            it["commit"] = "cc1ef36c89f9e8adddf37094dfd67e8bb500ec39"
            it["shim"] = "patch2"
            it["version"] = "1.0.14.61"
            it["date"] = "2026-10-27T04:19:22Z"
            it["built"] = "2026-10-29T10:00:00Z"
        }
        assertEquals(UpdateResult.Status.UPDATED, updates.check(webToEpub, release).status)

        // ... and a rebuild of the download is again left alone.
        release.requested.clear()
        release.publish(webToEpub, "/* rebuilt */".toByteArray()) {
            it["commit"] = "cc1ef36c89f9e8adddf37094dfd67e8bb500ec39"
            it["shim"] = "patch2"
            it["version"] = "1.0.14.61"
            it["date"] = "2026-10-27T04:19:22Z"
            it["built"] = "2026-10-30T10:00:00Z"
        }
        val again = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.CURRENT, again.status)
        assertTrue(again.message, again.message.endsWith("downloaded update)."))
        assertEquals(listOf(EngineUpdates.RELEASE_URL + "webtoepub-manifest.json"), release.requested)
        assertEquals(String(newBundle), store.current(webToEpub)!!.read())
    }

    @Test
    fun aBundleWithoutACommitIsNeverTakenForARebuild() = runTest {
        // novel-downloader's shipped copy knows only its version, so the version decides.
        val data = "// ==UserScript==\n// @version 5.2.1289\n// ==/UserScript==\n".toByteArray()
        release.publish(rules, data) {
            it["commit"] = ""
            it["shim"] = ""
            it["version"] = "5.2.1289"
        }
        assertEquals(UpdateResult.Status.UPDATED, updates.check(rules, release).status)
    }

    @Test
    fun aReleaseForAnotherApiIsLeftAlone() = runTest {
        release.publish(webToEpub, newBundle) { it["api"] = 2 }
        val result = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.CURRENT, result.status)
        assertEquals("The newest WebToEpub parsers need a newer NovelDB.", result.message)
        assertNull(store.downloaded(webToEpub))
    }

    @Test
    fun aManifestForAnotherEngineFails() = runTest {
        release.publish(webToEpub, newBundle) { it["engine"] = "novel_downloader" }
        val result = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.FAILED, result.status)
        assertTrue(result.message, result.message.contains("for another engine"))
    }

    @Test
    fun aManifestWithoutAnEngineNameIsAccepted() = runTest {
        release.publish(webToEpub, newBundle) { it.remove("engine") }
        assertEquals(UpdateResult.Status.UPDATED, updates.check(webToEpub, release).status)
    }

    @Test
    fun aDownloadThatDoesNotMatchTheChecksumOrSizeIsDiscarded() = runTest {
        release.publish(webToEpub, newBundle) { it["sha256"] = EngineStore.sha256("something else".toByteArray()) }
        val badSum = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.FAILED, badSum.status)
        assertEquals("The downloaded WebToEpub parsers update did not match its checksum.", badSum.message)

        release.publish(webToEpub, newBundle) { it["size"] = newBundle.size + 1 }
        assertEquals(UpdateResult.Status.FAILED, updates.check(webToEpub, release).status)

        release.publish(webToEpub, newBundle) { it.remove("size") }
        assertEquals(UpdateResult.Status.FAILED, updates.check(webToEpub, release).status)
        assertNull(store.downloaded(webToEpub))
        assertFalse(File(root, "webtoepub/webtoepub-lib.js").exists())
        assertEquals(String(shippedLib), store.current(webToEpub)!!.read())
    }

    @Test
    fun fileNamesWithSeparatorsOrDotsAreRefused() = runTest {
        for (name in listOf("../webtoepub-lib.js", "dir/webtoepub-lib.js", "dir\\webtoepub-lib.js", "a..b.js", "")) {
            val fresh = FakeRelease()
            fresh.publish(webToEpub, newBundle) { it["file"] = name }
            val result = updates.check(webToEpub, fresh)
            assertEquals(name, UpdateResult.Status.FAILED, result.status)
            assertEquals("The WebToEpub parsers update manifest names no valid file.", result.message)
            assertEquals("only the manifest is fetched for $name", 1, fresh.requested.size)
        }
        assertNull(store.downloaded(webToEpub))
    }

    @Test
    fun aMissingReleaseIsACouldNotCheckFailure() = runTest {
        // The release does not exist before the first publish: GitHub answers 404.
        val result = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.FAILED, result.status)
        assertEquals("Could not check for WebToEpub parsers updates: HTTP 404", result.message)
        // The check still counts as made, so a daily check does not hammer a missing release.
        assertNull(updates.checkIfDue(webToEpub, release))
    }

    @Test
    fun aManifestThatIsNotJsonObjectFails() = runTest {
        release.files["webtoepub-manifest.json"] = "[1, 2]".toByteArray()
        val result = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.FAILED, result.status)
        assertTrue(result.message, result.message.startsWith("Could not check for WebToEpub parsers updates"))
    }

    @Test
    fun aDownloadFailureIsReported() = runTest {
        release.publish(webToEpub, newBundle)
        release.files.remove("webtoepub-lib.js")
        val result = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.FAILED, result.status)
        assertEquals("Could not download the WebToEpub parsers update: HTTP 404", result.message)
    }

    // Rejecting

    @Test
    fun aRejectedUpdateIsDroppedNotUsedAndNeverDownloadedAgain() = runTest {
        release.publish(webToEpub, newBundle)
        updates.check(webToEpub, release)
        val update = store.current(webToEpub)!!
        assertTrue(update.updated)

        store.reject(update, "did not load")

        assertFalse(store.current(webToEpub)!!.updated)
        assertNull(store.downloaded(webToEpub))
        assertFalse(File(root, "webtoepub/webtoepub-lib.js").exists())
        assertTrue(File(root, "webtoepub/state.json").readText().contains("did not load"))
        release.requested.clear()
        val again = updates.check(webToEpub, release)
        assertEquals(UpdateResult.Status.CURRENT, again.status)
        assertEquals("The newest WebToEpub parsers failed to load before; keeping these.", again.message)
        assertEquals(listOf(EngineUpdates.RELEASE_URL + "webtoepub-manifest.json"), release.requested)

        // A different build is fetched again.
        release.publish(webToEpub, newBundle) { it["commit"] = "bbbbbbbbbbbbbbb" }
        assertEquals(UpdateResult.Status.UPDATED, updates.check(webToEpub, release).status)
    }

    @Test
    fun rejectingTheShippedCopyDoesNothing() {
        store.reject(store.shipped(webToEpub)!!, "no")
        assertNotNull(store.shipped(webToEpub))
        assertEquals(emptySet<String>(), store.rejectedReleases(webToEpub))
    }

    @Test
    fun aDownloadWhoseFileWasTamperedWithIsNotUsed() = runTest {
        release.publish(webToEpub, newBundle)
        updates.check(webToEpub, release)
        File(root, "webtoepub/webtoepub-lib.js").writeText("/* edited */")
        assertNull(store.downloaded(webToEpub))
        assertFalse(store.current(webToEpub)!!.updated)
    }

    @Test
    fun aDownloadForAnotherApiIsNotUsed() = runTest {
        release.publish(webToEpub, newBundle)
        updates.check(webToEpub, release)
        val manifest = File(root, "webtoepub/manifest.json")
        manifest.writeText(manifest.readText().replace("\"api\": 1", "\"api\": 2"))
        assertNull(store.downloaded(webToEpub))
    }

    @Test
    fun aDownloadThatIsOlderThanTheShippedCopyIsNotUsed() = runTest {
        release.publish(webToEpub, newBundle)
        updates.check(webToEpub, release)
        // The app is updated to a build that ships something newer than the download.
        val newer = MapFiles(
            mapOf(
                webToEpub.shippedPath to shippedLib,
                webToEpub.shippedManifestPath!! to """{"api": 1, "version": "1.0.16.0", "date": "2026-11-01T00:00:00Z"}""".toByteArray(),
            ),
        )
        val upgraded = EngineStore(root, newer)
        assertFalse(upgraded.current(webToEpub)!!.updated)
        assertNotNull(upgraded.downloaded(webToEpub))
    }

    @Test
    fun theNovelDownloaderRulesUpdateLikeTheParsers() = runTest {
        val data = "// ==UserScript==\n// @version 5.3.1\n// ==/UserScript==\n".toByteArray()
        release.publish(rules, data)
        val result = updates.check(rules, release)
        assertEquals(result.message, UpdateResult.Status.UPDATED, result.status)
        assertEquals("Updated the novel-downloader rules to 5.3.1, 2026-10-29, 9999999, downloaded update.", result.message)
        assertTrue(File(root, "novel_downloader/rules-lib.js").isFile)
        assertEquals(String(data), store.current(rules)!!.read())

        // The same build again is current.
        assertEquals(UpdateResult.Status.CURRENT, updates.check(rules, release).status)
    }

    // The daily check

    @Test
    fun theAutomaticCheckRunsAtMostOncePerDay() = runTest {
        release.publish(webToEpub, newBundle) { it["version"] = "1.0.14.61"; it["date"] = ""; it["built"] = "" }
        val logged = mutableListOf<String>()

        val first = updates.checkIfDue(webToEpub, release, logged::add)
        assertNotNull(first)
        assertEquals("a current result is not logged", emptyList<String>(), logged)
        val requests = release.requested.size

        now += 23 * 3600 * 1000L
        assertNull(updates.checkIfDue(webToEpub, release, logged::add))
        assertEquals("no request within 24 hours", requests, release.requested.size)

        now += 2 * 3600 * 1000L
        release.publish(webToEpub, newBundle)
        val second = updates.checkIfDue(webToEpub, release, logged::add)
        assertEquals(UpdateResult.Status.UPDATED, second!!.status)
        assertEquals(listOf(second.message), logged)

        // Failures are logged too.
        now += 25 * 3600 * 1000L
        release.files.clear()
        val failed = updates.checkIfDue(webToEpub, release, logged::add)
        assertEquals(UpdateResult.Status.FAILED, failed!!.status)
        assertEquals(second.message, logged[0])
        assertEquals(failed.message, logged[1])
    }

    @Test
    fun theLastCheckTimeIsStoredPerEngine() = runTest {
        assertEquals(0.0, store.lastChecked(webToEpub), 0.0)
        release.publish(webToEpub, newBundle)
        updates.check(webToEpub, release)
        assertEquals(now / 1000.0, store.lastChecked(webToEpub), 0.001)
        assertEquals(0.0, store.lastChecked(rules), 0.0)
        // A new store over the same folder (an app restart) sees it.
        assertEquals(now / 1000.0, EngineStore(root, shippedFiles).lastChecked(webToEpub), 0.001)
    }

    @Test
    fun withoutAFolderNothingIsDownloaded() = runTest {
        val bare = EngineStore(null, shippedFiles)
        release.publish(webToEpub, newBundle)
        val result = EngineUpdates(bare, clock = { now }).check(webToEpub, release)
        assertEquals(UpdateResult.Status.FAILED, result.status)
        assertNull(bare.downloaded(webToEpub))
        assertEquals(String(shippedLib), bare.current(webToEpub)!!.read())
    }

    @Test
    fun releaseKeysIgnoreMissingFields() {
        assertEquals("-", EngineStore.releaseKey(buildJsonObject { }))
        assertEquals("abc-s1", EngineStore.releaseKey(buildJsonObject { put("commit", "abc"); put("shim", "s1") }))
    }
}
