package com.shirochi.npia.app

import android.app.Application
import android.util.Log
import androidx.test.core.app.ApplicationProvider
import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import com.shirochi.npia.core.AppSettings
import com.shirochi.npia.core.ChapterResult
import com.shirochi.npia.core.EngineSettings
import com.shirochi.npia.core.InMemorySecretStore
import com.shirochi.npia.core.JobLogging
import com.shirochi.npia.core.SharedSettings
import com.shirochi.npia.core.SiteEngine
import com.shirochi.npia.net.OkHttpFetcher
import com.shirochi.npia.sources.SourceCatalog
import com.shirochi.npia.sources.engines.RuleEngines
import com.shirochi.npia.webengine.AndroidWebEngine
import kotlinx.coroutines.runBlocking
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith
import java.io.File

/**
 * One site engine alone (every other one off, no dynamic priority) reads a book and its first two
 * chapters through the real web view. Needs the internet, so it only runs when asked:
 *
 *     adb shell am instrument -w -e live true -e engine webtoepub -e url https://kakuyomu.jp/works/1 \
 *         -e class com.shirochi.npia.app.EngineLiveTest com.shirochi.npiamobile.test/androidx.test.runner.AndroidJUnitRunner
 *
 * `engine` is a `SiteEngine.raw` name (novel_downloader, webtoepub, lncrawl, fanficfare, gallerydl, ...).
 */
@RunWith(AndroidJUnit4::class)
class EngineLiveTest {
    @Test
    fun oneEngineReadsABookAndItsFirstChapters() {
        runBlocking { readOneBook() }
    }

    private suspend fun readOneBook() {
        val args = InstrumentationRegistry.getArguments()
        assumeTrue("pass -e live true to run", args.getString("live") == "true")
        val engine = args.getString("engine") ?: "webtoepub"
        val url = args.getString("url") ?: "https://kakuyomu.jp/works/2912051603596422650"
        val app = ApplicationProvider.getApplicationContext<Application>()
        RuleEngines.configure(File(app.cacheDir, "live-engines"))
        val web = AndroidWebEngine(app)
        val http = OkHttpFetcher(cookieJar = web.cookieJar(), maxRetries = 1)
        val log = JobLogging { level, message -> Log.i(TAG, "[$level] $message") }
        val engines = EngineSettings(
            order = listOf(engine),
            disabled = SiteEngine.entries.map { it.raw }.filter { it != engine },
            dynamicPriority = false,
        )
        val settings = AppSettings(shared = SharedSettings(intervalMin = 0.0, intervalMax = 0.0, threads = 1), engines = engines)
        val context = com.shirochi.npia.core.SourceContext(http, web, InMemorySecretStore(), settings, log)

        val adapter = SourceCatalog.require(url, settings.engines)
        val book = adapter.parseBook(url, context)
        Log.i(TAG, "RESULT book engine=$engine url=$url title=${book.title} chapters=${book.chapters.size}")
        assertTrue("the book has chapters", book.chapters.isNotEmpty())

        val results = adapter.parseChapters(book.chapters.take(2), book, context)
        results.forEachIndexed { index, result ->
            val html = (result as? ChapterResult.Content)?.content?.html.orEmpty()
            val text = html.replace(Regex("<[^>]+>"), "").trim()
            val images = Regex("<img").findAll(html).count()
            Log.i(TAG, "RESULT chapter ${index + 1} engine=$engine chars=${text.length} images=$images start=${text.take(60)}")
            assertTrue("chapter ${index + 1} has text or images: $result", text.length > 200 || images >= 3)
        }
        Log.i(TAG, "RESULT ok engine=$engine url=$url")
    }

    private companion object {
        const val TAG = "EngineLive"
    }
}
