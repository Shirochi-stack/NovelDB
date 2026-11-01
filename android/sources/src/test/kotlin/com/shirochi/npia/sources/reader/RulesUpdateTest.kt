package com.shirochi.npia.sources.reader

import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.LogLevel
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SourceContext
import com.shirochi.npia.core.WebEngineProviding
import com.shirochi.npia.core.WebLoadState
import com.shirochi.npia.core.WebPageDriving
import com.shirochi.npia.sources.engines.EngineStore
import com.shirochi.npia.sources.engines.RuleEngine
import com.shirochi.npia.sources.engines.RuleEngines
import kotlinx.coroutines.test.runTest
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test
import java.io.File
import java.nio.file.Files

/**
 * A downloaded novel-downloader rules update is used by both the QuickJS runtime and the web view
 * path, and set aside for the shipped copy when it does not initialise.
 */
class RulesUpdateTest {
    private val engine = RuleEngine.NOVEL_DOWNLOADER
    private val roots = mutableListOf<File>()
    private val lines = mutableListOf<Pair<LogLevel, String>>()
    private val logger = JobLogging { level, message -> lines += level to message }

    @After
    fun restoreStore() {
        RuleEngines.configure(null)
        roots.forEach { it.deleteRecursively() }
    }

    /** Installs `script` as a downloaded rules update newer than the shipped copy. */
    private fun install(script: String): File {
        val root = Files.createTempDirectory("engines").toFile().also { roots += it }
        val folder = File(root, "novel_downloader").apply { mkdirs() }
        val data = script.toByteArray()
        File(folder, "rules-lib.js").writeBytes(data)
        File(folder, "manifest.json").writeText(
            """{"engine":"novel_downloader","api":1,"commit":"cafebabecafebabe","date":"2026-10-30T00:00:00Z","version":"99.0.1",
               "shim":"s2","file":"rules-lib.js","size":${data.size},"sha256":"${EngineStore.sha256(data)}","built":"2026-10-30T01:00:00Z"}""",
        )
        RuleEngines.configure(root)
        return root
    }

    private val shipped: String get() = RuleEngines.store.shipped(engine)!!.read()

    @Test
    fun theQuickJsRuntimeLoadsTheDownloadedRules() = runTest {
        install(shipped + "\nwindow.__UPDATE_MARKER = 'downloaded';\n")
        assertTrue(RuleEngines.store.current(engine)!!.updated)
        val runtime = ReaderRuntime.create(FixtureHttp(emptyMap()), logger)
        try {
            assertEquals("downloaded", runtime.evaluate("window.__UPDATE_MARKER"))
            assertEquals("true", runtime.evaluate("window.__ND_READY === true"))
        } finally {
            runtime.close()
        }
        assertTrue(lines.toString(), lines.none { it.first == LogLevel.WARNING })
    }

    @Test
    fun aDownloadThatDoesNotInitialiseIsSetAsideForTheShippedRules() = runTest {
        val root = install("throw new Error('boom');")
        val runtime = ReaderRuntime.create(FixtureHttp(emptyMap()), logger)
        try {
            assertEquals("true", runtime.evaluate("window.__ND_READY === true"))
            assertEquals("undefined", runtime.evaluate("typeof window.__UPDATE_MARKER"))
        } finally {
            runtime.close()
        }
        assertTrue(
            lines.toString(),
            lines.any { (level, text) -> level == LogLevel.WARNING && text.contains("novel-downloader rules update (99.0.1, 2026-10-30, cafebab, downloaded update) failed to load") && text.contains("using the built-in copy") },
        )
        assertFalse(File(root, "novel_downloader/rules-lib.js").exists())
        assertNull(RuleEngines.store.downloaded(engine))
        assertEquals(setOf("cafebabecafebabe-s2"), RuleEngines.store.rejectedReleases(engine))
        assertFalse(RuleEngines.store.current(engine)!!.updated)
    }

    // The web view path

    /** A page that answers the three scripts of the rules path; a rules script containing `broken` never becomes ready. */
    private class RulesPage(val broken: String? = null) : WebPageDriving {
        val rulesScripts = mutableListOf<String>()
        val bookJson = """{"bookname":"Web Book","chapters":[{"url":"/c/1","name":"One"}],"pageUrl":"https://site.test/b"}"""

        override suspend fun load(url: String, until: WebLoadState, timeoutSeconds: Long) {}

        override suspend fun evaluateJson(functionBody: String): String = when {
            functionBody.contains("__ND_parseBook") -> bookJson
            functionBody.contains("window.__ND_READY") -> {
                rulesScripts += functionBody
                if (broken != null && functionBody.contains(broken)) "false" else "true"
            }
            else -> "true"
        }

        override suspend fun addInitScript(source: String) {}
        override suspend fun currentUrl(): String = "https://site.test/b"
        override suspend fun html() = ""
        override suspend fun close() {}
    }

    private fun context(page: RulesPage): SourceContext {
        val web = object : WebEngineProviding {
            override suspend fun newPage(profile: String): WebPageDriving = page
            override suspend fun presentLogin(url: String, profile: String, userAgentSuffix: String?) = Unit
        }
        return SourceContext(
            FixtureHttp(emptyMap()), web, InMemorySecretStore(),
            AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0)), logger,
        )
    }

    @Test
    fun theWebViewPathInjectsTheDownloadedRules() = runTest {
        install(shipped + "\nwindow.__UPDATE_MARKER = 'downloaded';\n")
        val page = RulesPage()
        val (payload, _) = ReaderWebFallback.openBook(page, "https://site.test/b", context(page))
        assertEquals("Web Book", payload.bookname)
        assertEquals(1, page.rulesScripts.size)
        assertTrue(page.rulesScripts[0].contains("__UPDATE_MARKER"))
    }

    @Test
    fun theWebViewPathFallsBackToTheShippedRulesWhenTheDownloadIsNotReady() = runTest {
        val root = install("/* BROKEN-RULES */ window.__x = 1;")
        val page = RulesPage(broken = "BROKEN-RULES")
        val (payload, _) = ReaderWebFallback.openBook(page, "https://site.test/b", context(page))
        assertEquals("Web Book", payload.bookname)
        assertEquals(2, page.rulesScripts.size)
        assertTrue(page.rulesScripts[0].contains("BROKEN-RULES"))
        assertFalse(page.rulesScripts[1].contains("BROKEN-RULES"))
        assertTrue(lines.toString(), lines.any { it.second.contains("failed to load (rules-lib.js did not initialise); using the built-in copy") })
        assertFalse(File(root, "novel_downloader/rules-lib.js").exists())
        assertEquals(setOf("cafebabecafebabe-s2"), RuleEngines.store.rejectedReleases(engine))
    }
}
