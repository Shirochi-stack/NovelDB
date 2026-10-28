package com.shirochi.npia.sources.engines

import kotlinx.serialization.json.JsonPrimitive
import kotlinx.serialization.json.int
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** The engine files that ship inside the app. */
class BundledEnginesTest {
    private val resources = "/com/shirochi/npia/sources/reader/webtoepub/"

    private fun bytes(name: String) = BundledEnginesTest::class.java.getResourceAsStream(resources + name)!!.use { it.readBytes() }

    @Test
    fun theWebToEpubBundleMatchesItsManifestByteForByte() {
        val manifest = EngineStore.parseObject(bytes("manifest.json"))!!
        val lib = bytes("webtoepub-lib.js")
        assertEquals(manifest["sha256"]!!.jsonPrimitive.content, EngineStore.sha256(lib))
        assertEquals(manifest["size"]!!.jsonPrimitive.int, lib.size)
        assertEquals("webtoepub-lib.js", manifest["file"]!!.jsonPrimitive.content)
        assertEquals("webtoepub", manifest["engine"]!!.jsonPrimitive.content)
        assertEquals(RuleEngine.WEBTOEPUB.api, manifest["api"]!!.jsonPrimitive.int)
        assertTrue("LF line endings", !lib.decodeToString().contains("\r\n"))
    }

    @Test
    fun theBundleSpeaksTheContractTheAppUses() {
        val text = bytes("webtoepub-lib.js").decodeToString()
        for (entry in listOf("__W2E_parseBook", "__W2E_parseChapter", "__W2E_parseChapterBatch", "__W2E_READY", "__W2E_API", "__W2E_LOAD_ERROR")) {
            assertTrue("$entry is defined", text.contains(entry))
        }
        // The transport the app provides, tried after the page's own fetch.
        assertTrue(text.contains("__W2E_transport"))
        assertTrue(text.contains("__npia_gm_xmlhttp_request"))
    }

    @Test
    fun theLicenseTravelsWithTheBundle() {
        val license = bytes("LICENSE.md").decodeToString()
        assertTrue(license.contains("GNU GENERAL PUBLIC LICENSE"))
        assertTrue(license.contains("Version 3"))
        assertTrue(bytes("webtoepub-lib.js").decodeToString().take(600).contains("GPL-3.0"))
    }

    @Test
    fun theStoreFindsBothShippedBundles() {
        val parsers = RuleEngines.store.shipped(RuleEngine.WEBTOEPUB)
        assertNotNull(parsers)
        assertFalse(parsers!!.updated)
        assertTrue(parsers.label, parsers.label.endsWith("built in"))
        assertEquals(1, parsers.manifest["api"]!!.jsonPrimitive.int)
        assertEquals(JsonPrimitive("webtoepub-lib.js"), parsers.manifest.jsonObject["file"])

        val rules = RuleEngines.store.shipped(RuleEngine.NOVEL_DOWNLOADER)
        assertNotNull(rules)
        assertTrue(rules!!.label, rules.label.matches(Regex("""\d+(\.\d+)+, built in""")))
        // With no folder configured the shipped copies are the ones in use.
        assertFalse(RuleEngines.store.current(RuleEngine.WEBTOEPUB)!!.updated)
        assertFalse(RuleEngines.store.current(RuleEngine.NOVEL_DOWNLOADER)!!.updated)
    }
}
