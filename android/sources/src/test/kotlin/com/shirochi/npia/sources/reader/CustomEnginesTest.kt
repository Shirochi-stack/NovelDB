package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.sources.SourceCatalog
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Assert.fail
import org.junit.Before
import org.junit.Test
import java.nio.file.Files

class CustomEnginesTest {
    private val engine = """
        |import re
        |
        |class Engine:
        |    label = "Example Novels"
        |    sites = ["novels.example", "www.Mirror.example"]
        |
        |    def __init__(self, fetch, log, render=None):
        |        self.fetch = fetch
        |
        |    def parse_book(self, url):
        |        return {"chapters": []}
        |
        |    def parse_chapter(self, chapter, book):
        |        return {"contentText": ""}
        |
        |def helper():
        |    label = "not the engine's"
    """.trimMargin()

    @Before
    fun folder() {
        CustomEngines.folder = Files.createTempDirectory("custom-engines").toFile()
    }

    @After
    fun clear() {
        CustomEngines.folder?.deleteRecursively()
        CustomEngines.folder = null
    }

    @Test
    fun anEngineFileIsReadForItsLabelAndSites() {
        val info = CustomEngines.add("My Engine.py", engine.toByteArray())
        assertEquals("my_engine", info.id)
        assertEquals("Example Novels", info.label)
        assertEquals(listOf("novels.example", "mirror.example"), info.sites)
        assertEquals(listOf("my_engine"), CustomEngines.list().map { it.id })
        assertTrue(CustomEngines.listsSite("https://www.novels.example/book/1"))
        assertTrue(CustomEngines.listsSite("https://sub.mirror.example/book/1"))
        assertFalse(CustomEngines.listsSite("https://other.example/book/1"))
        CustomEngines.remove("my_engine")
        assertTrue(CustomEngines.list().isEmpty())
    }

    @Test
    fun aFileThatIsNotAnEngineIsRefused() {
        for ((text, why) in listOf("x = 1" to "class Engine", "class Engine:\n    def parse_book(self, url): pass\n" to "parse_chapter")) {
            try {
                CustomEngines.add("bad.py", text.toByteArray())
                fail("accepted: $text")
            } catch (e: IllegalArgumentException) {
                assertTrue(e.message!!, why in e.message!!)
            }
        }
        assertTrue(CustomEngines.list().isEmpty())
    }

    @Test
    fun theCustomEnginesPlaceIsInTheChainOnlyWhenOneReadsTheSite() {
        val url = "https://novels.example/book/1"
        val dynamic = EngineSettings(dynamicPriority = true)
        val before = (SourceCatalog.resolve(url, dynamic) as? SiteEngineChain)?.steps
        assertFalse(before.orEmpty().contains(SiteEngine.CUSTOM))  // none added
        CustomEngines.add("example.py", engine.toByteArray())
        val steps = (SourceCatalog.resolve(url, dynamic) as? SiteEngineChain)?.steps.orEmpty()
        assertEquals(listOf(SiteEngine.CUSTOM, SiteEngine.GENERIC), steps.takeLast(2))
        val elsewhere = (SourceCatalog.resolve("https://other.example/book/1", dynamic) as? SiteEngineChain)?.steps
        assertFalse(elsewhere.orEmpty().contains(SiteEngine.CUSTOM))
        // Switched off, it is never tried.
        val off = EngineSettings(dynamicPriority = true, disabled = listOf("custom"))
        assertNull((SourceCatalog.resolve(url, off) as? SiteEngineChain)?.steps?.firstOrNull { it == SiteEngine.CUSTOM })
    }

    private val userscript = """
        |// ==UserScript==
        |// @name        Test capture
        |// @match       *://reader.example/*
        |// @match       https://*.other.example/read/*
        |// ==/UserScript==
        |(function () {})();
    """.trimMargin()
    private val anywhere = "// ==UserScript==\n// @name Any site\n// @match *://*/*\n// ==/UserScript==\n"

    @Test
    fun aUserscriptIsACustomEngineReadFromItsHeader() {
        val info = CustomEngines.add("Test Capture-1.0.user.js", userscript.toByteArray())
        assertEquals("test_capture_1_0", info.id)
        assertEquals("Test capture", info.label)
        assertEquals(listOf("reader.example", "other.example"), info.sites)
        assertTrue(info.userscript)
        assertFalse(info.everywhere)
        assertEquals("test_capture_1_0.user.js", info.fileName)
        assertTrue(CustomEngines.listsSite("https://www.reader.example/c/1"))
        assertEquals(listOf("test_capture_1_0"), CustomEngines.userscriptsFor("https://jp.other.example/read/2").map { it.id })
        assertTrue(CustomEngines.userscriptsFor("https://elsewhere.example/").isEmpty())
        // The Python page is not handed userscripts.
        assertTrue(CustomEngines.python().isEmpty())

        val any = CustomEngines.add("any.js", anywhere.toByteArray())
        assertTrue(any.everywhere)
        assertTrue(any.sites.isEmpty())
        assertTrue(CustomEngines.anywhere())
        assertFalse(CustomEngines.listsSite("https://elsewhere.example/"))
        // Those that name the site first, then those that match every site.
        assertEquals(listOf("test_capture_1_0", "any"), CustomEngines.userscriptsFor("https://reader.example/c/1").map { it.id })
        assertEquals(listOf("any"), CustomEngines.userscriptsFor("https://elsewhere.example/").map { it.id })

        for ((text, why) in listOf("console.log(1)" to "header", "// ==UserScript==\n// @match *://a.example/*\n// ==/UserScript==" to "@name")) {
            try {
                CustomEngines.add("bad.js", text.toByteArray())
                fail("accepted: $text")
            } catch (e: IllegalArgumentException) {
                assertTrue(e.message!!, why in e.message!!)
            }
        }
        CustomEngines.remove("test_capture_1_0")
        assertEquals(listOf("any"), CustomEngines.list().map { it.id })
    }

    @Test
    fun aUserscriptForEverySiteIsAskedLastAfterTheGenericFallback() {
        val dynamic = EngineSettings(dynamicPriority = true)
        CustomEngines.add("any.js", anywhere.toByteArray())
        val steps = (SourceCatalog.resolve("https://elsewhere.example/c/1", dynamic) as? SiteEngineChain)?.steps.orEmpty()
        assertEquals(listOf(SiteEngine.GENERIC, SiteEngine.CUSTOM), steps.takeLast(2))
        // One that names the site keeps the custom engines' own place, ahead of the fallback.
        CustomEngines.add("capture.user.js", userscript.toByteArray())
        val named = (SourceCatalog.resolve("https://reader.example/c/1", dynamic) as? SiteEngineChain)?.steps.orEmpty()
        assertEquals(listOf(SiteEngine.CUSTOM, SiteEngine.GENERIC), named.takeLast(2))
    }
}
