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
}
